"""파이프라인 오케스트레이터(plan/render 2단계) 테스트 — 실 렌더 없음.

plan_render 의 합성·사전 게이트 결합, build_video_params 매핑,
execute_render 의 배선(주입된 러너/상태 사용, 미완료/무산출 실패 수렴)을
검증한다. 실 렌더 e2e 는 scripts/m1_render_check.py 가 담당한다.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.models import const
from app.models.schema import MaterialInfo, VideoAspect, VideoConcatMode
from app.promo.brandkit.models import BrandKit
from app.promo.materials import RetimedClip, probe_duration
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


def _fake_retime(
    materials, narration, storage_local_dir, *, shots=None, headlines=None,
    font_path=None, retime_id=None,
):
    """ffmpeg 없이 리타이밍 결과 모양만 흉내낸다 (실제 리타이밍은 test_retime.py)."""
    assert len(materials) == len(narration.sections)
    clips = []
    for index, (material, section) in enumerate(zip(materials, narration.sections)):
        section_shots = list(shots[index]) if shots else []
        count = len(section_shots) or 1
        share = section.measured_s / count
        for shot_index in range(count):
            clips.append(
                RetimedClip(
                    material=MaterialInfo(
                        provider="local",
                        url=f"{material.url}#retimed-{index}-{shot_index}",
                        duration=int(round(share)),
                    ),
                    seconds=round(share, 3),
                    section_index=index,
                    shot_index=shot_index,
                )
            )
    return clips


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
        retime=_fake_retime,
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
        retime=_fake_retime,
    )

    assert plan.timeline.passed is False
    assert plan.structural.passed is True
    assert plan.approved_ready is False
    assert any("TIMELINE_COVERAGE_MISMATCH" in f for f in plan.timeline.failures)


def test_clip_duration_follows_longest_measured_section(env):
    """코어 max_clip_duration 은 가장 긴 섹션 이상이어야 클립이 더 쪼개지지 않는다."""
    kit, stock_paths, local_dir = env
    template = validate_template(NARRATION_TEMPLATE_DATA)

    plan = plan_render(
        template,
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        measure=_measure_sequence(3.0, 8.4, 4.0),
        retime=_fake_retime,
    )

    # 가장 긴 클립 8.4초 -> 올림 9 (꼬리 여유는 마지막 클립에만 붙는다)
    assert plan.clip_duration_s == 9
    assert build_video_params(plan).video_clip_duration == 9
    assert [m.duration for m in plan.materials] == [3, 8, 4]


def test_clip_duration_defaults_without_narration(template, env):
    plan = _make_plan(template, env)

    assert plan.clip_duration_s == 4
    assert build_video_params(plan).video_clip_duration == 4


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="실 리타이밍 경로 검증에는 ffmpeg/ffprobe 가 필요합니다",
)
def test_plan_retimes_real_materials_to_measured_lengths(tmp_path):
    """주입 없이 실제 경로로: 플랜 소재가 실측 섹션 길이 클립이 된다."""
    brand_dir = tmp_path / "brand"
    local_dir = tmp_path / "local_videos"
    brand_dir.mkdir()
    local_dir.mkdir()
    sources = []
    for index, name in enumerate(("b1.mp4", "b2.mp4", "b3.mp4")):
        target = brand_dir / name
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=red:s=320x568:d={index + 2}",
                "-r", "30", "-pix_fmt", "yuv420p", str(target),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        sources.append(str(target))

    kit = BrandKit(business_name="우리가게", photos=sources)
    template = validate_template(NARRATION_TEMPLATE_DATA)

    plan = plan_render(
        template,
        kit,
        [],
        NARRATION_SCRIPT,
        str(local_dir),
        plan_id="retime-plan",
        measure=_measure_sequence(3.0, 8.0, 4.0),
    )

    assert plan.approved_ready is True
    for material, section in zip(plan.materials, plan.narration.sections):
        assert "retime-plan" in material.url
        expected = section.measured_s + (1.0 if section.role == "cta" else 0.0)
        assert abs(probe_duration(material.url) - expected) <= 0.15
    # body 8초가 가장 긴 클립 (cta 는 4+1=5초)
    assert plan.clip_duration_s == 8


def test_section_shots_expand_into_multiple_clips(env):
    """섹션이 shots 를 선언하면 소재 한 장에서 컷 수만큼 클립이 나온다."""
    kit, stock_paths, local_dir = env
    data = dict(NARRATION_TEMPLATE_DATA, template_id="pipeline-shots-v2")
    data["structure"] = [dict(section) for section in NARRATION_TEMPLATE_DATA["structure"]]
    data["structure"][0]["shots"] = [
        {"kind": "wide", "motion": "push-in"},
        {"kind": "cutin", "crop": "center-zoom"},
    ]

    plan = plan_render(
        validate_template(data),
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        measure=_measure_sequence(3.0, 8.0, 4.0),
        retime=_fake_retime,
    )

    # hook 3초가 2컷으로 쪼개지고 나머지 섹션은 1컷씩 = 총 4클립
    assert len(plan.materials) == 4
    assert plan.clip_seconds == (1.5, 1.5, 8.0, 4.0)
    assert plan.narration.total_s == 15.0  # 타임라인 총 길이는 그대로
    assert plan.clip_duration_s == 8


def test_headline_texts_fill_brand_context_and_respect_show_flag():
    """헤드라인은 브랜드 정보 + 변수로 채우고, show=false 는 배너를 만들지 않는다."""
    from app.promo.pipeline import headline_texts

    data = dict(NARRATION_TEMPLATE_DATA, template_id="pipeline-headline-v2")
    data["structure"] = [dict(s) for s in NARRATION_TEMPLATE_DATA["structure"]]
    data["structure"][0]["headline"] = {"template": "{shop_name} {menu_name} 출시!"}
    data["structure"][1]["headline"] = {"template": "숨김", "show": False}
    kit = BrandKit(business_name="우리분식", category="음식점")
    template = validate_template(data)

    # 변수가 있으면 브랜드 정보와 함께 채워진다.
    assert headline_texts(template, kit, {"menu_name": "매운떡볶이"}) == [
        "우리분식 매운떡볶이 출시!",
        None,
        None,
    ]
    # 변수가 없어 플레이스홀더가 남으면 원문 배너 대신 생략(None) — 자동 공개 안전장치.
    assert headline_texts(template, kit) == [None, None, None]


# --- 실측 오디오 재사용 (b-3) -------------------------------------------------


class _FakeCue:
    def __init__(self, start, end, text):
        from datetime import timedelta

        self.start = timedelta(seconds=start)
        self.end = timedelta(seconds=end)
        self.content = text


class _FakeSubMaker:
    def __init__(self, cues):
        self.cues = cues


def _narration_env(tmp_path, monkeypatch):
    """plan_render 기본 경로(전체 스크립트 1회 TTS)를 가짜 합성으로 돌린다."""
    from app.promo import pipeline as pipeline_module

    narration_dir = tmp_path / "narration"
    monkeypatch.setattr(
        pipeline_module, "narration_audio_dir", lambda: str(narration_dir)
    )

    calls = []

    def fake_synthesize(*, text, voice_name, voice_rate, voice_file):
        from app.promo.timing import split_sentences

        calls.append(voice_file)
        Path(voice_file).write_bytes(b"fake-audio")
        sentences = split_sentences(text)
        spans = [(0.0, 3.0), (3.0, 11.0), (11.0, 15.0)][: len(sentences)]
        return _FakeSubMaker(
            [_FakeCue(s, e, t) for (s, e), t in zip(spans, sentences)]
        )

    monkeypatch.setattr(
        pipeline_module, "narrate_full_script", _wrap_narrate(fake_synthesize)
    )
    return calls


def _wrap_narrate(fake_synthesize):
    from app.promo import timing as timing_module

    def _narrate(script, template, *, voice_name, voice_rate=1.0, audio_dir):
        return timing_module.narrate_full_script(
            script,
            template,
            voice_name=voice_name,
            voice_rate=voice_rate,
            audio_dir=audio_dir,
            synthesize=fake_synthesize,
        )

    return _narrate


def test_plan_synthesizes_full_script_once_and_keeps_audio(tmp_path, monkeypatch, env):
    """기본 경로는 전체 스크립트를 1회 합성하고 그 오디오를 플랜에 들고 있는다."""
    kit, stock_paths, local_dir = env
    calls = _narration_env(tmp_path, monkeypatch)

    plan = plan_render(
        validate_template(NARRATION_TEMPLATE_DATA),
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        retime=_fake_retime,
    )

    assert len(calls) == 1  # 섹션 수만큼 TTS 하지 않는다
    assert plan.narration.total_s == 15.0
    assert plan.narration_audio is not None
    assert Path(plan.narration_audio.audio_file).is_file()


def test_execute_render_reuses_measured_audio(tmp_path, monkeypatch, env):
    """렌더는 실측 오디오를 voice_preview 로 넘겨 TTS 재합성을 건너뛴다."""
    kit, stock_paths, local_dir = env
    _narration_env(tmp_path, monkeypatch)
    task_root = tmp_path / "tasks"
    monkeypatch.setattr(
        "app.utils.utils.task_dir",
        lambda sub="": str((task_root / sub) if sub else task_root),
    )
    (task_root / "promo-test").mkdir(parents=True)

    plan = plan_render(
        validate_template(NARRATION_TEMPLATE_DATA),
        kit,
        stock_paths,
        NARRATION_SCRIPT,
        local_dir,
        retime=_fake_retime,
    )
    fake_video = tmp_path / "final-1.mp4"
    fake_video.write_bytes(b"not-a-real-video")
    seen = {}

    def runner(task_id, params, stop_at, voice_preview=None):
        seen["preview"] = voice_preview
        seen["params"] = params
        return {"videos": [str(fake_video)]}

    execute_render(
        plan,
        task_id="promo-test",
        task_runner=runner,
        state_getter=lambda tid: {"state": const.TASK_STATE_COMPLETE},
    )

    preview = seen["preview"]
    assert preview is not None
    # 코어 재사용 조건: 문안/음성 파라미터 일치 + 오디오가 task_dir 안 + sub_maker 동반
    assert preview["script"] == seen["params"].video_script.strip()
    assert preview["voice_name"] == seen["params"].voice_name
    assert preview["voice_rate"] == seen["params"].voice_rate
    assert preview["voice_volume"] == seen["params"].voice_volume
    assert preview["duration"] == plan.narration.total_s
    assert preview["sub_maker"] is plan.narration_audio.sub_maker
    assert Path(preview["audio_file"]).parent == task_root / "promo-test"
    assert Path(preview["audio_file"]).is_file()


def test_execute_render_without_measured_audio_keeps_core_tts(template, env, tmp_path):
    """실측 오디오가 없는 플랜은 예전처럼 코어가 TTS 한다 (voice_preview 없음)."""
    plan = _make_plan(template, env)
    fake_video = tmp_path / "final-1.mp4"
    fake_video.write_bytes(b"x")
    seen = {}

    def runner(task_id, params, stop_at):
        seen["called"] = True
        return {"videos": [str(fake_video)]}

    execute_render(
        plan,
        task_id="promo-plain",
        task_runner=runner,
        state_getter=lambda tid: {"state": const.TASK_STATE_COMPLETE},
    )

    assert seen["called"] is True  # voice_preview 키워드 없이 호출된다


# --- BGM 무드 정렬 -------------------------------------------------------------


def _songs_dir(tmp_path, names):
    songs = tmp_path / "songs"
    songs.mkdir()
    for name in names:
        (songs / name).write_bytes(b"x")
    return songs


def test_list_bgm_for_mood_filters_by_prefix(tmp_path, monkeypatch):
    """{mood}- 프리픽스로 거르고, 비-mp3/다른 무드는 제외한다."""
    from app.promo.pipeline import list_bgm_for_mood
    from app.utils import utils

    songs = _songs_dir(
        tmp_path,
        ["upbeat-a.mp3", "upbeat-b.mp3", "calm-c.mp3", "energetic-d.mp3", "notes.txt"],
    )
    monkeypatch.setattr(utils, "song_dir", lambda sub="": str(songs))

    assert list_bgm_for_mood("upbeat") == ["upbeat-a.mp3", "upbeat-b.mp3"]
    assert list_bgm_for_mood("calm") == ["calm-c.mp3"]
    assert list_bgm_for_mood("nonexistent") == []
    assert list_bgm_for_mood("") == []


def test_pick_bgm_file_empty_when_no_mood_match(tmp_path, monkeypatch):
    """무드에 맞는 곡이 없으면 빈 문자열 → 코어 전체 랜덤 폴백."""
    from app.promo.pipeline import pick_bgm_file
    from app.utils import utils

    songs = _songs_dir(tmp_path, ["calm-only.mp3"])
    monkeypatch.setattr(utils, "song_dir", lambda sub="": str(songs))

    assert pick_bgm_file("energetic") == ""


def test_build_video_params_picks_mood_matched_bgm(template, env, tmp_path, monkeypatch):
    """build_video_params 가 템플릿 무드(upbeat)의 트랙을 BGM 으로 고른다."""
    from app.utils import utils

    songs = _songs_dir(tmp_path, ["upbeat-x.mp3", "upbeat-y.mp3", "calm-z.mp3"])
    monkeypatch.setattr(utils, "song_dir", lambda sub="": str(songs))

    plan = _make_plan(template, env)  # template mood == "upbeat"
    params = build_video_params(plan)

    assert params.bgm_file in ("upbeat-x.mp3", "upbeat-y.mp3")
    assert params.bgm_type == "random"


def test_build_video_params_bgm_empty_without_mood_songs(template, env, tmp_path, monkeypatch):
    """무드 곡이 없으면 bgm_file 은 비고(랜덤 폴백) 랜덤 타입은 유지."""
    from app.utils import utils

    songs = _songs_dir(tmp_path, ["calm-only.mp3"])  # upbeat 곡 없음
    monkeypatch.setattr(utils, "song_dir", lambda sub="": str(songs))

    plan = _make_plan(template, env)
    params = build_video_params(plan)

    assert params.bgm_file == ""
    assert params.bgm_type == "random"
