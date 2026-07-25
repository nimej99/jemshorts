"""주간 스케줄 + 오토파일럿 (v1 `schedule` 테이블).

- 스케줄은 단일행(id=1): freq_per_week 와 미리 계산된 next_runs(UTC ISO
  목록), 실패 이력 missed_runs.
- next_runs 는 균등 간격(7일/freq)으로 채워두고 tick() 이 만기 런을
  소비한다. 실패 런은 missed_runs 에 사유와 함께 적재하고 v1
  fallback_log 에도 기록한다 (조용한 실패 금지).
- 오토파일럿 1회 = 상한 확인 -> 템플릿 로테이션 -> LLM 스크립트 ->
  플랜(사전 구조 게이트) -> 렌더(기술 게이트) -> 업로드 -> delivered 기록.
  플랜/결과는 promo_plans 에 남아 webui/API 에서 추적된다.
- 데몬이 아니라 cron 친화 설계: `python -m app.promo.scheduler` 가 tick
  1회를 실행한다 (코어 asgi 수정 없이 자동화 — FORK_NOTES 원칙).
  API 로는 POST /api/v1/promo/schedule/tick 이 같은 일을 한다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from loguru import logger

from app.promo import db as promo_db
from app.promo import pipeline, plans, trends, uploads
from app.promo.brandkit import store as brandkit_store
from app.promo.research import build_script_prompt
from app.promo.templates.schema import load_all_raw, validate_template
from app.utils import utils

# freq 하드 상한 (일일 상한과 별개의 최종 가드 — 3/일 * 7일)
MAX_FREQ_PER_WEEK = 21
# missed_runs 무한 적재 방지 (최근 것만 유지)
MAX_MISSED_KEPT = 50


class SchedulerError(RuntimeError):
    """오토파일럿 실행 실패 (missed 로 기록되는 사유)."""


class SkipRun(RuntimeError):
    """이번 런을 건너뛰는 정상 사유 (일일 상한 도달 등 — missed 아님)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _interval(freq_per_week: int) -> timedelta:
    return timedelta(days=7) / freq_per_week


@dataclass(frozen=True)
class Schedule:
    freq_per_week: int
    next_runs: list[str]
    missed_runs: list[str]

    def to_dict(self) -> dict:
        return {
            "freq_per_week": self.freq_per_week,
            "next_runs": list(self.next_runs),
            "missed_runs": list(self.missed_runs),
        }


def get_schedule(conn: sqlite3.Connection) -> Schedule | None:
    row = conn.execute(
        "SELECT freq_per_week, next_runs, missed_runs FROM schedule WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return Schedule(
        freq_per_week=int(row["freq_per_week"]),
        next_runs=json.loads(row["next_runs"] or "[]"),
        missed_runs=json.loads(row["missed_runs"] or "[]"),
    )


def _save(conn: sqlite3.Connection, schedule: Schedule) -> None:
    conn.execute(
        """
        INSERT INTO schedule (id, freq_per_week, next_runs, missed_runs)
        VALUES (1, :freq, :next_runs, :missed_runs)
        ON CONFLICT (id) DO UPDATE SET
            freq_per_week = :freq,
            next_runs = :next_runs,
            missed_runs = :missed_runs
        """,
        {
            "freq": schedule.freq_per_week,
            "next_runs": json.dumps(schedule.next_runs),
            "missed_runs": json.dumps(schedule.missed_runs),
        },
    )
    conn.commit()


def set_frequency(
    conn: sqlite3.Connection, freq_per_week: int, now: datetime | None = None
) -> Schedule:
    """주간 빈도를 설정하고 next_runs 를 균등 간격으로 재계산한다.

    missed_runs 이력은 보존한다 (진단 가치 — 빈도 변경으로 실패 이력을
    지우지 않는다).
    """
    if not isinstance(freq_per_week, int) or not (
        1 <= freq_per_week <= MAX_FREQ_PER_WEEK
    ):
        raise ValueError(
            f"freq_per_week 는 1~{MAX_FREQ_PER_WEEK} 정수여야 합니다: {freq_per_week!r}"
        )
    now = now or _now()
    interval = _interval(freq_per_week)
    next_runs = [_iso(now + interval * (i + 1)) for i in range(freq_per_week)]
    existing = get_schedule(conn)
    schedule = Schedule(
        freq_per_week=freq_per_week,
        next_runs=next_runs,
        missed_runs=existing.missed_runs if existing else [],
    )
    _save(conn, schedule)
    return schedule


def due_runs(schedule: Schedule, now: datetime | None = None) -> list[str]:
    now = now or _now()
    return [run for run in schedule.next_runs if _parse(run) <= now]


def _refill(next_runs: list[str], freq_per_week: int, now: datetime) -> list[str]:
    """소비 후 남은 next_runs 를 freq 개수까지 균등 간격으로 보충한다."""
    interval = _interval(freq_per_week)
    runs = list(next_runs)
    while len(runs) < freq_per_week:
        anchor = _parse(runs[-1]) if runs else now
        runs.append(_iso(anchor + interval))
    return runs


def _log_fallback(conn: sqlite3.Connection, cause: str) -> None:
    conn.execute(
        "INSERT INTO fallback_log (component, from_value, to_value, cause) "
        "VALUES ('scheduler', 'run', 'missed', ?)",
        (cause,),
    )
    conn.commit()


def templates_data_dir() -> str:
    return os.path.join(utils.root_dir(), "templates-data")


def run_autopilot_once(conn: sqlite3.Connection) -> dict:
    """생성->렌더->업로드 1회를 끝까지 실행한다.

    실패는 SchedulerError(missed 기록), 상한 도달은 SkipRun 으로 던진다.
    """
    kit = brandkit_store.load(conn)
    if kit is None:
        raise SchedulerError("브랜드킷이 없습니다 — 오토파일럿을 실행할 수 없습니다")
    if uploads.cap_reached(conn):
        raise SkipRun(
            f"일일 업로드 상한 도달 ({uploads.count_delivered_today(conn)}"
            f"/{uploads.daily_cap()})"
        )

    raws = load_all_raw(templates_data_dir())
    if not raws:
        raise SchedulerError("템플릿이 없습니다")
    delivered_total = int(
        conn.execute(
            "SELECT COUNT(*) FROM videos WHERE status = ?",
            (uploads.STATUS_DELIVERED,),
        ).fetchone()[0]
    )
    raw = raws[delivered_total % len(raws)]  # 누적 업로드 수 기준 로테이션
    template = validate_template(raw, source=raw["template_id"])

    trend_keywords = trends.latest_keywords(conn)
    prompt = build_script_prompt(template, kit, trend_keywords=trend_keywords or None)
    from app.services import llm  # 지연 임포트

    response = llm._generate_response(prompt)
    script = (response or "").strip()
    if not script or script.startswith("Error:"):
        raise SchedulerError(f"스크립트 생성 실패: {script or '빈 응답'}")

    local_dir = utils.storage_dir("local_videos", create=True)
    plan = pipeline.plan_render(template, kit, [], script, local_dir)
    if not plan.approved_ready:
        raise SchedulerError(f"사전 구조 게이트 실패: {plan.structural.failures}")
    plans.save_plan(conn, plan, raw)

    task_id = f"promo-auto-{plan.plan_id}-{uuid.uuid4().hex[:6]}"
    plans.mark_rendering(conn, plan.plan_id, task_id)
    try:
        result = pipeline.execute_render(plan, task_id=task_id)
    except Exception as exc:
        plans.finish(conn, plan.plan_id, plans.STATUS_FAILED, {"error": str(exc)})
        raise SchedulerError(f"렌더 실패: {exc}") from exc
    plans.finish(
        conn,
        plan.plan_id,
        plans.STATUS_RENDERED,
        {
            "task_id": result.task_id,
            "videos": list(result.videos),
            "render_seconds": result.render_seconds,
            "technical_gate": {
                "passed": result.technical.passed,
                "failures": list(result.technical.failures),
                "warnings": list(result.technical.warnings),
            },
            "warnings": result.warnings,
        },
    )
    if not result.technical.passed:
        raise SchedulerError(f"기술 게이트 실패: {result.technical.failures}")

    hashtags = [f"#{tag}" for tag in template.hashtags_base]
    description = f"{plan.subject}\n\n{' '.join(hashtags)}"
    from app.services import upload_post  # 지연 임포트

    upload_result = upload_post.cross_post_video(
        result.videos[0],
        plan.subject,
        platforms=["youtube"],
        youtube_extra={
            "youtube_title": plan.subject,
            "youtube_description": description,
            "tags": [tag.lstrip("#") for tag in hashtags],
            "privacyStatus": "public",
        },
    )
    if not upload_result.get("success"):
        raise SchedulerError(
            f"업로드 실패: {upload_result.get('error') or upload_result}"
        )
    uploads.record_delivered(
        conn, result.task_id, template.template_id, description, hashtags
    )
    logger.info(f"autopilot[{plan.plan_id}] 완료: {result.videos[0]}")
    return {
        "plan_id": plan.plan_id,
        "task_id": result.task_id,
        "video": result.videos[0],
        "request_id": upload_result.get("request_id"),
    }


def tick(
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
    runner: Callable[[sqlite3.Connection], dict] | None = None,
) -> dict:
    """만기 런을 전부 처리하고 next_runs 를 보충한다. cron/CLI/API 공용."""
    own_conn = conn is None
    if own_conn:
        conn = promo_db.connect()
    try:
        now = now or _now()
        if runner is None:
            runner = run_autopilot_once

        # 트렌드 폴링 피기백 — tick 이 cron 하트비트이므로 스케줄 유무와
        # 무관하게 stale 갱신을 시도한다 (실패는 삼킴 — 부가 정보).
        trends_status = trends.maybe_refresh(conn, now=now)

        schedule = get_schedule(conn)
        if schedule is None:
            return {
                "status": "no-schedule",
                "ran": [],
                "missed": [],
                "skipped": [],
                "trends": trends_status,
            }

        due = due_runs(schedule, now)
        ran: list[dict] = []
        missed: list[dict] = []
        skipped: list[dict] = []
        for run_iso in due:
            try:
                outcome = runner(conn)
                ran.append({"run": run_iso, **(outcome or {})})
            except SkipRun as exc:
                skipped.append({"run": run_iso, "reason": str(exc)})
                logger.info(f"scheduler run {run_iso} 건너뜀: {exc}")
            except Exception as exc:
                missed.append({"run": run_iso, "cause": str(exc)})
                _log_fallback(conn, f"{run_iso}: {exc}")
                logger.error(f"scheduler run {run_iso} 실패: {exc}")

        remaining = [run for run in schedule.next_runs if run not in due]
        new_missed = (
            schedule.missed_runs + [entry["run"] for entry in missed]
        )[-MAX_MISSED_KEPT:]
        updated = Schedule(
            freq_per_week=schedule.freq_per_week,
            next_runs=_refill(remaining, schedule.freq_per_week, now),
            missed_runs=new_missed,
        )
        _save(conn, updated)
        return {
            "status": "ok",
            "ran": ran,
            "missed": missed,
            "skipped": skipped,
            "next_runs": updated.next_runs,
            "trends": trends_status,
        }
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    report = tick()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report.get("missed") else 1


if __name__ == "__main__":
    sys.exit(main())
