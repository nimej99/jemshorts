"""렌더 플랜 영속화(promo_plans) 테스트 — 실 렌더 없음.

저장/복원 왕복 동등성, rendering 중복 진입 차단, 종료 상태 기록,
failed 재렌더 허용을 검증한다.
"""

import json

import pytest

from app.models.schema import MaterialInfo
from app.promo.materials import RetimedClip
from app.promo import db as promo_db
from app.promo import plans
from app.promo.brandkit.models import BrandKit
from app.promo.pipeline import plan_render
from app.promo.templates.schema import validate_template

TEMPLATE_DATA = {
    "template_id": "plans-test-v1",
    "name": "플랜 저장 테스트",
    "version": 1,
    "mood": "calm",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "훅", "material_slot": "any"},
        {"role": "cta", "duration_s": 8, "script_guide": "행동 유도", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


def _fake_retime(
    materials, narration, storage_local_dir, *, shots=None, headlines=None,
    font_path=None, retime_id=None,
):
    """ffmpeg 없이 리타이밍 산출물 모양만 흉내낸다 (실제 검증은 test_retime.py)."""
    return [
        RetimedClip(
            material=MaterialInfo(
                provider="local",
                url=f"{material.url}#retimed",
                duration=int(round(section.measured_s)),
            ),
            seconds=section.measured_s,
            section_index=index,
            shot_index=0,
        )
        for index, (material, section) in enumerate(zip(materials, narration.sections))
    ]


@pytest.fixture()
def conn(tmp_path):
    connection = promo_db.connect(tmp_path / "plans-test.db")
    yield connection
    connection.close()


@pytest.fixture()
def plan(tmp_path):
    brand_dir = tmp_path / "brand"
    local_dir = tmp_path / "local_videos"
    brand_dir.mkdir()
    local_dir.mkdir()
    clip = brand_dir / "b1.mp4"
    clip.write_bytes(b"dummy")
    template = validate_template(TEMPLATE_DATA)
    kit = BrandKit(business_name="가게", photos=[str(clip)])
    return plan_render(template, kit, [], "테스트 스크립트입니다.", str(local_dir))


def test_save_and_restore_roundtrip(conn, plan):
    plans.save_plan(conn, plan, TEMPLATE_DATA)

    row = plans.get_row(conn, plan.plan_id)
    assert row["status"] == plans.STATUS_PLANNED
    assert row["template_id"] == "plans-test-v1"

    restored = plans.restore_plan(row)
    assert restored == plan


def test_mark_rendering_blocks_reentry(conn, plan):
    plans.save_plan(conn, plan, TEMPLATE_DATA)

    assert plans.mark_rendering(conn, plan.plan_id, "task-1") is True
    assert plans.mark_rendering(conn, plan.plan_id, "task-2") is False
    assert plans.get_row(conn, plan.plan_id)["task_id"] == "task-1"


def test_finish_records_result_and_allows_rerender_after_failure(conn, plan):
    plans.save_plan(conn, plan, TEMPLATE_DATA)
    plans.mark_rendering(conn, plan.plan_id, "task-1")

    plans.finish(conn, plan.plan_id, plans.STATUS_FAILED, {"error": "boom"})
    row = plans.get_row(conn, plan.plan_id)
    assert row["status"] == plans.STATUS_FAILED
    assert "boom" in row["result_json"]

    # failed 상태에서는 재렌더 진입이 가능해야 한다.
    assert plans.mark_rendering(conn, plan.plan_id, "task-2") is True


def test_finish_rejects_non_terminal_status(conn, plan):
    plans.save_plan(conn, plan, TEMPLATE_DATA)
    with pytest.raises(ValueError, match="종료 상태"):
        plans.finish(conn, plan.plan_id, plans.STATUS_RENDERING, {})


def test_roundtrip_preserves_measured_narration(conn, tmp_path):
    """실측 타임라인/게이트 판정은 저장-복원 후에도 동일해야 한다.

    (승인 시점 값이 보존되지 않으면 재측정 편차로 승인 결과가 바뀐다.)
    """
    local_dir = tmp_path / "local_videos_v2"
    brand_dir = tmp_path / "brand_v2"
    local_dir.mkdir()
    brand_dir.mkdir()
    clip = brand_dir / "b1.mp4"
    clip.write_bytes(b"dummy")

    raw = dict(
        TEMPLATE_DATA,
        template_id="plans-test-v2",
        version=2,
        timing={"owner": "narration", "tolerance_s": 0.2},
    )
    template = validate_template(raw)
    kit = BrandKit(business_name="가게", photos=[str(clip)])
    measured = iter([4.0, 7.0])
    plan = plan_render(
        template,
        kit,
        [],
        "첫 문장입니다. 둘째 문장입니다.",
        str(local_dir),
        measure=lambda text: next(measured),
        retime=_fake_retime,
    )
    assert plan.narration.total_s == 11.0

    plans.save_plan(conn, plan, raw)
    restored = plans.restore_plan(plans.get_row(conn, plan.plan_id))

    assert restored == plan
    assert restored.narration.boundaries == ((0.0, 4.0), (4.0, 11.0))
    assert restored.timeline.passed is True


def test_legacy_payload_without_narration_restores_none(conn, plan):
    """v2 이전에 저장된 플랜(payload 에 narration 키 없음)도 그대로 복원된다."""
    plans.save_plan(conn, plan, TEMPLATE_DATA)
    row = plans.get_row(conn, plan.plan_id)
    payload = json.loads(row["payload_json"])
    del payload["narration"]
    del payload["timeline"]
    conn.execute(
        "UPDATE promo_plans SET payload_json = ? WHERE plan_id = ?",
        (json.dumps(payload, ensure_ascii=False), plan.plan_id),
    )
    conn.commit()

    restored = plans.restore_plan(plans.get_row(conn, plan.plan_id))

    assert restored.narration is None
    assert restored.timeline is None
    assert restored.approved_ready is True


def test_legacy_material_urls_payload_still_restores(conn, plan):
    """구버전 payload(material_urls, duration 없음)도 복원된다."""
    plans.save_plan(conn, plan, TEMPLATE_DATA)
    row = plans.get_row(conn, plan.plan_id)
    payload = json.loads(row["payload_json"])
    payload["material_urls"] = [entry["url"] for entry in payload.pop("materials")]
    conn.execute(
        "UPDATE promo_plans SET payload_json = ? WHERE plan_id = ?",
        (json.dumps(payload, ensure_ascii=False), plan.plan_id),
    )
    conn.commit()

    restored = plans.restore_plan(plans.get_row(conn, plan.plan_id))

    assert [m.url for m in restored.materials] == [m.url for m in plan.materials]
    assert all(m.duration == 0 for m in restored.materials)
