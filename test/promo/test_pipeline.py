"""파이프라인 오케스트레이터(plan/render 2단계) 테스트 — 실 렌더 없음.

plan_render 의 합성·사전 게이트 결합, build_video_params 매핑,
execute_render 의 배선(주입된 러너/상태 사용, 미완료/무산출 실패 수렴)을
검증한다. 실 렌더 e2e 는 scripts/m1_render_check.py 가 담당한다.
"""

import pytest

from app.models import const
from app.models.schema import VideoAspect, VideoConcatMode
from app.promo.brandkit.models import BrandKit
from app.promo.pipeline import (
    PipelineError,
    build_video_params,
    execute_render,
    plan_render,
)
from app.promo.templates.schema import validate_template

TEMPLATE_DATA = {
    "template_id": "pipeline-test-v1",
    "name": "파이프라인 테스트",
    "version": 1,
    "mood": "upbeat",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "훅", "material_slot": "video"},
        {"role": "body", "duration_s": 8, "script_guide": "본문", "material_slot": "any"},
        {"role": "cta", "duration_s": 4, "script_guide": "행동 유도", "material_slot": "photo"},
    ],
    "total_duration_range": [10, 20],
    "caption_template": "{shop_name} 캡션",
    "hashtags_base": ["테스트"],
}

SCRIPT = "드디어 나왔다, 신메뉴! 매콤하고 담백한 맛. 이번 주말까지 이벤트."


@pytest.fixture()
def template():
    return validate_template(TEMPLATE_DATA)


@pytest.fixture()
def env(tmp_path):
    brand_dir = tmp_path / "brand"
    local_dir = tmp_path / "local_videos"
    brand_dir.mkdir()
    local_dir.mkdir()
    photo = brand_dir / "b1.jpg"
    clip = brand_dir / "b2.mp4"
    stock = brand_dir / "s1.mp4"
    for f in (photo, clip, stock):
        f.write_bytes(b"dummy")
    kit = BrandKit(business_name="우리가게", photos=[str(photo), str(clip)])
    return kit, [str(stock)], str(local_dir)


def _make_plan(template, env, **kwargs):
    kit, stock_paths, local_dir = env
    return plan_render(template, kit, stock_paths, SCRIPT, local_dir, **kwargs)


def test_plan_composes_and_passes_structural_gate(template, env):
    plan = _make_plan(template, env, plan_id="t1")

    assert plan.plan_id == "t1"
    assert len(plan.materials) == 3
    assert plan.used_brand_count == 2
    assert plan.photo_warning is False
    assert plan.structural.passed is True
    assert plan.approved_ready is True
    assert "우리가게" in plan.subject


def test_plan_rejects_empty_script(template, env):
    kit, stock_paths, local_dir = env
    with pytest.raises(ValueError, match="script"):
        plan_render(template, kit, stock_paths, "   ", local_dir)


def test_plan_propagates_photo_warning_without_brand(template, env):
    _, stock_paths, local_dir = env
    kit = BrandKit(business_name="우리가게", photos=[])

    plan = plan_render(template, kit, stock_paths, SCRIPT, local_dir)

    assert plan.photo_warning is True
    assert plan.used_brand_count == 0


def test_build_video_params_maps_plan(template, env):
    plan = _make_plan(template, env)

    params = build_video_params(plan, n_threads=1)

    assert params.video_script == SCRIPT
    assert params.video_source == "local"
    assert params.video_aspect == VideoAspect.portrait
    assert params.video_concat_mode == VideoConcatMode.sequential
    assert [m.url for m in params.video_materials] == [m.url for m in plan.materials]
    assert params.voice_name == plan.voice_name
    assert params.subtitle_enabled is True
    assert params.n_threads == 1


def test_execute_render_wires_runner_and_runs_technical_gate(template, env, tmp_path):
    plan = _make_plan(template, env)
    fake_video = tmp_path / "final-1.mp4"
    fake_video.write_bytes(b"not-a-real-video")
    calls = {}

    def runner(task_id, params, stop_at):
        calls["task_id"] = task_id
        calls["stop_at"] = stop_at
        calls["script"] = params.video_script
        return {"videos": [str(fake_video)]}

    result = execute_render(
        plan,
        task_id="promo-test",
        task_runner=runner,
        state_getter=lambda tid: {"state": const.TASK_STATE_COMPLETE},
    )

    assert calls == {"task_id": "promo-test", "stop_at": "video", "script": SCRIPT}
    assert result.videos == (str(fake_video),)
    assert result.plan_id == plan.plan_id
    # 더미 바이트는 실제 영상이 아니므로 기술 게이트는 실패로 수렴해야 한다
    # (예외 아님 — 판정은 결과로 반환).
    assert result.technical.passed is False
    assert result.passed is False


def test_execute_render_raises_on_incomplete_task(template, env):
    plan = _make_plan(template, env)

    with pytest.raises(PipelineError, match="미완료"):
        execute_render(
            plan,
            task_runner=lambda *a, **k: {"videos": ["x.mp4"]},
            state_getter=lambda tid: {"state": const.TASK_STATE_FAILED, "error": "boom"},
        )


def test_execute_render_raises_on_missing_videos(template, env):
    plan = _make_plan(template, env)

    with pytest.raises(PipelineError, match="산출물"):
        execute_render(
            plan,
            task_runner=lambda *a, **k: {"videos": []},
            state_getter=lambda tid: {"state": const.TASK_STATE_COMPLETE},
        )


def test_voice_rate_defaults_to_core_default_for_v1_template(template, env):
    """v1 템플릿에는 voice 선언이 없으므로 코어 기본 속도(1.0)를 쓴다."""
    plan = _make_plan(template, env)

    assert plan.voice_rate == 1.0
    assert build_video_params(plan).voice_rate == 1.0


def test_voice_rate_follows_template_v2_voice_speed(env):
    """템플릿 v2 voice.speed 가 렌더 파라미터 voice_rate 로 전달된다."""
    data = dict(TEMPLATE_DATA, version=2, voice={"speed": 1.15})
    plan = _make_plan(validate_template(data), env)

    assert plan.voice_rate == 1.15
    assert build_video_params(plan).voice_rate == 1.15


# --- 템플릿 v2 timing.owner = narration (실측 타임라인) ------------------------

NARRATION_TEMPLATE_DATA = dict(
    TEMPLATE_DATA,
    template_id="pipeline-narration-v2",
    version=2,
    timing={"owner": "narration", "tolerance_s": 0.2},
)

NARRATION_SCRIPT = (
    "드디어 나왔다, 신메뉴! 매콤하고 담백한 맛입니다. 이번 주말까지 이벤트."
)


def _measure_sequence(*seconds):
    values = iter(seconds)

    def _measure(text: str) -> float:
        return next(values)

    return _measure


def _explode(text: str) -> float:
    raise AssertionError("timing 선언이 없는데 내레이션 실측이 호출되었습니다")


def test_v1_template_never_measures_narration(template, env):
    plan = _make_plan(template, env, measure=_explode)

    assert plan.narration is None
    assert plan.timeline is None
    assert plan.approved_ready is True


def test_narration_owner_measures_and_passes_timeline_gate(env):
    kit, stock_paths, local_dir = env
    template = validate_template(NARRATION_TEMPLATE_DATA)

    plan = plan_render(
        template,
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        measure=_measure_sequence(3.0, 8.0, 4.0),
    )

    assert plan.narration is not None
    assert plan.narration.total_s == 15.0
    assert plan.narration.boundaries[0] == (0.0, 3.0)
    assert plan.timeline.passed is True
    assert plan.approved_ready is True


def test_narration_out_of_range_blocks_approval(env):
    """실측 총 길이가 템플릿 범위를 벗어나면 렌더 전에 승인이 막힌다."""
    kit, stock_paths, local_dir = env
    template = validate_template(NARRATION_TEMPLATE_DATA)

    plan = plan_render(
        template,
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        measure=_measure_sequence(9.0, 9.0, 9.0),
    )

    assert plan.timeline.passed is False
    assert plan.structural.passed is True
    assert plan.approved_ready is False
    assert any("TIMELINE_COVERAGE_MISMATCH" in f for f in plan.timeline.failures)
