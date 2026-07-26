"""렌더 플랜 영속화 (promo_plans 테이블, v3 마이그레이션).

plan/render 2단계 사이에서 RenderPlan 을 SQLite 에 보존한다.
payload_json 에 템플릿 원본 dict 를 통째로 넣는다 — plan 승인과 render
실행 사이에 템플릿 파일이 변해도 플랜은 불변이어야 한다 (승인 게이트의
전제: 사용자가 본 것과 렌더되는 것이 같아야 함).

상태 전이: planned -> rendering -> rendered | failed.
failed 는 재렌더 가능 (rendering 재진입은 mark_rendering 이 차단).
"""

from __future__ import annotations

import json
import sqlite3

from app.models.schema import MaterialInfo
from app.promo.pipeline import RenderPlan
from app.promo.quality import GateResult
from app.promo.templates.schema import validate_template
from app.promo.timing import NarrationTiming, SectionTiming

STATUS_PLANNED = "planned"
STATUS_RENDERING = "rendering"
STATUS_RENDERED = "rendered"
STATUS_FAILED = "failed"


def _gate_to_dict(gate: GateResult) -> dict:
    return {
        "passed": gate.passed,
        "failures": list(gate.failures),
        "warnings": list(gate.warnings),
    }


def _gate_from_dict(data: dict) -> GateResult:
    return GateResult(
        passed=bool(data["passed"]),
        failures=list(data["failures"]),
        warnings=list(data["warnings"]),
    )


def _narration_to_dict(narration: NarrationTiming) -> dict:
    return {
        "sections": [
            {
                "role": section.role,
                "text": section.text,
                "target_s": section.target_s,
                "measured_s": section.measured_s,
                "start_s": section.start_s,
            }
            for section in narration.sections
        ]
    }


def _narration_from_dict(data: dict) -> NarrationTiming:
    return NarrationTiming(
        sections=tuple(
            SectionTiming(
                role=section["role"],
                text=section["text"],
                target_s=float(section["target_s"]),
                measured_s=float(section["measured_s"]),
                start_s=float(section["start_s"]),
            )
            for section in data["sections"]
        )
    )


def _materials_from_payload(payload: dict) -> tuple[MaterialInfo, ...]:
    """소재 목록을 복원한다. 구버전 payload(material_urls)도 그대로 읽는다."""
    entries = payload.get("materials")
    if entries is None:
        return tuple(
            MaterialInfo(provider="local", url=url, duration=0)
            for url in payload["material_urls"]
        )
    return tuple(
        MaterialInfo(
            provider="local",
            url=entry["url"],
            duration=int(entry.get("duration", 0)),
        )
        for entry in entries
    )


def _clip_seconds_from_payload(payload: dict) -> tuple[float, ...]:
    """리타이밍 클립 길이(초)를 복원한다. 구버전/미리타이밍 플랜은 빈 튜플."""
    entries = payload.get("materials") or []
    seconds = [entry.get("seconds") for entry in entries]
    if not seconds or any(value is None for value in seconds):
        return ()
    return tuple(float(value) for value in seconds)


def save_plan(conn: sqlite3.Connection, plan: RenderPlan, template_raw: dict) -> None:
    """플랜을 planned 상태로 저장한다. plan_id 중복이면 sqlite3.IntegrityError."""
    payload = {
        "template": template_raw,
        "script": plan.script,
        # 리타이밍 이후 소재는 길이(초)까지 의미를 갖는다 — url 만 저장하면
        # 복원된 플랜이 승인 시점과 달라진다.
        "materials": [
            {"url": m.url, "duration": int(m.duration), "seconds": seconds}
            for m, seconds in zip(
                plan.materials, plan.clip_seconds or [None] * len(plan.materials)
            )
        ],
        "used_brand_count": plan.used_brand_count,
        "photo_warning": plan.photo_warning,
        "structural": _gate_to_dict(plan.structural),
        "voice_name": plan.voice_name,
        "font_name": plan.font_name,
        "language": plan.language,
        "subject": plan.subject,
        # 실측 타임라인은 재측정하면 값이 흔들린다 — 승인 시점 값을 그대로 보존한다.
        "narration": _narration_to_dict(plan.narration) if plan.narration else None,
        "timeline": _gate_to_dict(plan.timeline) if plan.timeline else None,
    }
    conn.execute(
        "INSERT INTO promo_plans (plan_id, status, template_id, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (
            plan.plan_id,
            STATUS_PLANNED,
            plan.template.template_id,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_row(conn: sqlite3.Connection, plan_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM promo_plans WHERE plan_id = ?", (plan_id,)
    ).fetchone()


def restore_plan(row: sqlite3.Row) -> RenderPlan:
    """저장된 행에서 RenderPlan 을 복원한다 (템플릿은 payload 원본 재검증)."""
    payload = json.loads(row["payload_json"])
    template = validate_template(
        payload["template"], source=f"promo_plans:{row['plan_id']}"
    )
    return RenderPlan(
        plan_id=row["plan_id"],
        template=template,
        script=payload["script"],
        materials=_materials_from_payload(payload),
        clip_seconds=_clip_seconds_from_payload(payload),
        used_brand_count=int(payload["used_brand_count"]),
        photo_warning=bool(payload["photo_warning"]),
        structural=_gate_from_dict(payload["structural"]),
        voice_name=payload["voice_name"],
        font_name=payload["font_name"],
        language=payload["language"],
        subject=payload["subject"],
        narration=(
            _narration_from_dict(payload["narration"])
            if payload.get("narration")
            else None
        ),
        timeline=(
            _gate_from_dict(payload["timeline"]) if payload.get("timeline") else None
        ),
    )


def mark_rendering(conn: sqlite3.Connection, plan_id: str, task_id: str) -> bool:
    """rendering 상태로 전이한다. 이미 rendering 이면 False (중복 실행 차단)."""
    cursor = conn.execute(
        "UPDATE promo_plans SET status = ?, task_id = ?, "
        "updated_at = datetime('now') "
        "WHERE plan_id = ? AND status != ?",
        (STATUS_RENDERING, task_id, plan_id, STATUS_RENDERING),
    )
    conn.commit()
    return cursor.rowcount == 1


def finish(
    conn: sqlite3.Connection, plan_id: str, status: str, result: dict
) -> None:
    """렌더 종료 상태(rendered|failed)와 결과를 기록한다."""
    if status not in (STATUS_RENDERED, STATUS_FAILED):
        raise ValueError(f"종료 상태가 아닙니다: {status}")
    conn.execute(
        "UPDATE promo_plans SET status = ?, result_json = ?, "
        "updated_at = datetime('now') WHERE plan_id = ?",
        (status, json.dumps(result, ensure_ascii=False), plan_id),
    )
    conn.commit()
