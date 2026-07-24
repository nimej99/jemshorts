"""렌더 플랜 영속화(promo_plans) 테스트 — 실 렌더 없음.

저장/복원 왕복 동등성, rendering 중복 진입 차단, 종료 상태 기록,
failed 재렌더 허용을 검증한다.
"""

import pytest

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
