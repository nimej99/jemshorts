"""스케줄러(주기 계산/tick/오토파일럿) 테스트 — 실 렌더/실 네트워크 없음."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.promo import db as promo_db
from app.promo import pipeline, plans, scheduler, uploads
from app.promo.brandkit import store as brandkit_store
from app.promo.brandkit.models import BrandKit
from app.promo.quality import GateResult

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)

TEMPLATE_DATA = {
    "template_id": "sched-test-v1",
    "name": "스케줄 테스트",
    "version": 1,
    "mood": "upbeat",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "훅", "material_slot": "any"},
        {"role": "cta", "duration_s": 8, "script_guide": "행동 유도", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


@pytest.fixture(autouse=True)
def _offline_trends(monkeypatch):
    """tick 의 트렌드 피기백이 네트워크로 나가지 않게 고정한다."""
    from app.promo import trends

    monkeypatch.setattr(
        trends,
        "fetch_google_trends",
        lambda timeout=0: [
            trends.TrendItem(keyword="테스트트렌드", traffic_value=100)
        ],
    )


@pytest.fixture()
def conn(tmp_path):
    connection = promo_db.connect(tmp_path / "sched-test.db")
    yield connection
    connection.close()


# ── 주기 계산 ────────────────────────────────────────────────────────


def test_set_frequency_computes_even_spacing(conn):
    schedule = scheduler.set_frequency(conn, 7, now=NOW)

    assert schedule.freq_per_week == 7
    assert len(schedule.next_runs) == 7
    runs = [datetime.fromisoformat(r) for r in schedule.next_runs]
    assert runs[0] == NOW + timedelta(days=1)
    deltas = {runs[i + 1] - runs[i] for i in range(len(runs) - 1)}
    assert deltas == {timedelta(days=1)}


def test_set_frequency_validates_range(conn):
    for bad in (0, -1, scheduler.MAX_FREQ_PER_WEEK + 1, "3"):
        with pytest.raises(ValueError):
            scheduler.set_frequency(conn, bad, now=NOW)


def test_set_frequency_preserves_missed_history(conn):
    scheduler.set_frequency(conn, 2, now=NOW)
    conn.execute(
        "UPDATE schedule SET missed_runs = ? WHERE id = 1",
        (json.dumps(["2026-07-01T00:00:00+00:00"]),),
    )
    conn.commit()

    schedule = scheduler.set_frequency(conn, 3, now=NOW)
    assert schedule.missed_runs == ["2026-07-01T00:00:00+00:00"]


# ── tick ─────────────────────────────────────────────────────────────


def _rewind_first_run(conn, hours=1):
    schedule = scheduler.get_schedule(conn)
    runs = list(schedule.next_runs)
    runs[0] = (NOW - timedelta(hours=hours)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE schedule SET next_runs = ? WHERE id = 1", (json.dumps(runs),)
    )
    conn.commit()
    return runs[0]


def test_tick_without_schedule(conn):
    report = scheduler.tick(conn, now=NOW)
    assert report["status"] == "no-schedule"


def test_tick_runs_due_and_refills(conn):
    scheduler.set_frequency(conn, 3, now=NOW)
    due = _rewind_first_run(conn)

    report = scheduler.tick(
        conn, now=NOW, runner=lambda c: {"plan_id": "p1"}
    )

    assert [entry["run"] for entry in report["ran"]] == [due]
    assert report["missed"] == [] and report["skipped"] == []
    schedule = scheduler.get_schedule(conn)
    assert len(schedule.next_runs) == 3  # 소비분 보충
    assert due not in schedule.next_runs


def test_tick_records_failure_as_missed_and_fallback_log(conn):
    scheduler.set_frequency(conn, 2, now=NOW)
    due = _rewind_first_run(conn)

    def broken(c):
        raise scheduler.SchedulerError("렌더 폭발")

    report = scheduler.tick(conn, now=NOW, runner=broken)

    assert [entry["run"] for entry in report["missed"]] == [due]
    schedule = scheduler.get_schedule(conn)
    assert due in schedule.missed_runs
    row = conn.execute(
        "SELECT component, cause FROM fallback_log"
    ).fetchone()
    assert row["component"] == "scheduler"
    assert "렌더 폭발" in row["cause"]


def test_tick_skiprun_not_missed(conn):
    scheduler.set_frequency(conn, 2, now=NOW)
    _rewind_first_run(conn)

    def skip(c):
        raise scheduler.SkipRun("일일 상한")

    report = scheduler.tick(conn, now=NOW, runner=skip)

    assert report["missed"] == []
    assert len(report["skipped"]) == 1
    assert scheduler.get_schedule(conn).missed_runs == []


# ── 오토파일럿 ───────────────────────────────────────────────────────


@pytest.fixture()
def autopilot_env(conn, tmp_path, monkeypatch):
    """브랜드킷/템플릿/스토리지/외부 의존을 전부 격리한 오토파일럿 환경."""
    clip = tmp_path / "brand.mp4"
    clip.write_bytes(b"dummy")
    brandkit_store.save(
        conn, BrandKit(business_name="우리가게", photos=[str(clip)])
    )

    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    (templates_dir / "sched-test.json").write_text(
        json.dumps(TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(scheduler, "templates_data_dir", lambda: str(templates_dir))

    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    monkeypatch.setattr(
        scheduler.utils, "storage_dir", lambda sub="", create=False: str(local_dir)
    )

    from app.services import llm

    monkeypatch.setattr(llm, "_generate_response", lambda prompt: "자동 생성 스크립트")

    video = tmp_path / "final-auto.mp4"
    video.write_bytes(b"x")

    def fake_execute(plan, *, task_id=None, **kwargs):
        return pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=(str(video),),
            render_seconds=1.0,
            technical=GateResult(passed=True, failures=[], warnings=[]),
        )

    monkeypatch.setattr(pipeline, "execute_render", fake_execute)

    from app.services import upload_post

    monkeypatch.setattr(
        upload_post,
        "cross_post_video",
        lambda *a, **k: {"success": True, "request_id": "req-auto"},
    )
    return conn


def test_autopilot_happy_path(autopilot_env):
    conn = autopilot_env
    outcome = scheduler.run_autopilot_once(conn)

    assert outcome["request_id"] == "req-auto"
    # 플랜 원장: rendered 로 종결
    row = plans.get_row(conn, outcome["plan_id"])
    assert row["status"] == plans.STATUS_RENDERED
    # 업로드 원장: delivered 1건
    assert uploads.count_delivered_today(conn) == 1


def test_autopilot_requires_brandkit(conn, monkeypatch, tmp_path):
    with pytest.raises(scheduler.SchedulerError, match="브랜드킷"):
        scheduler.run_autopilot_once(conn)


def test_autopilot_refuses_when_only_manual_templates(conn, tmp_path, monkeypatch):
    """autopilot=false 템플릿만 남으면 무인 실행은 거부한다 (지어내기 방지)."""
    clip = tmp_path / "brand.mp4"
    clip.write_bytes(b"dummy")
    brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=[str(clip)]))
    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    manual_only = dict(TEMPLATE_DATA, template_id="manual-only", autopilot=False)
    (templates_dir / "manual-only.json").write_text(
        json.dumps(manual_only, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(scheduler, "templates_data_dir", lambda: str(templates_dir))

    with pytest.raises(scheduler.SchedulerError, match="오토파일럿 대상"):
        scheduler.run_autopilot_once(conn)


def test_autopilot_rotation_skips_manual_only_templates(autopilot_env):
    """manual-only 템플릿이 섞여도 로테이션은 eligible 템플릿을 고른다."""
    conn = autopilot_env
    manual_only = dict(TEMPLATE_DATA, template_id="manual-only", autopilot=False)
    with open(
        os.path.join(scheduler.templates_data_dir(), "a-manual.json"),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(manual_only, fh, ensure_ascii=False)

    outcome = scheduler.run_autopilot_once(conn)

    row = plans.get_row(conn, outcome["plan_id"])
    restored = plans.restore_plan(row)
    assert restored.template.template_id == TEMPLATE_DATA["template_id"]
    assert restored.template.autopilot is True


def test_autopilot_skips_when_cap_reached(autopilot_env, monkeypatch):
    conn = autopilot_env
    from app.config import config

    monkeypatch.setitem(config.app, "promo_upload_daily_cap", 1)
    uploads.record_delivered(conn, "prev", "tpl", "", [])

    with pytest.raises(scheduler.SkipRun, match="상한"):
        scheduler.run_autopilot_once(conn)


def test_autopilot_technical_gate_failure_recorded(autopilot_env, monkeypatch):
    conn = autopilot_env

    def failing_execute(plan, *, task_id=None, **kwargs):
        return pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=("v.mp4",),
            render_seconds=1.0,
            technical=GateResult(
                passed=False, failures=["duration 벗어남"], warnings=[]
            ),
        )

    monkeypatch.setattr(pipeline, "execute_render", failing_execute)

    with pytest.raises(scheduler.SchedulerError, match="기술 게이트"):
        scheduler.run_autopilot_once(conn)

    # 플랜은 rendered(결과 보존) 로 남고, 업로드는 없어야 한다
    row = conn.execute("SELECT status FROM promo_plans").fetchone()
    assert row["status"] == plans.STATUS_RENDERED
    assert uploads.count_delivered_today(conn) == 0


# ── v2 템플릿 오토파일럿 (실측 타임라인이 영속화 경로까지 타는지) ──────

V2_TEMPLATE_DATA = {
    "template_id": "sched-test-v2",
    "name": "스케줄 v2 테스트",
    "version": 2,
    "mood": "upbeat",
    "style_preset": "warm-food",
    "timing": {"owner": "narration", "tolerance_s": 0.2},
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "훅",
            "material_slot": "any",
            "headline": {"template": "{shop_name} 신메뉴", "show": True},
            "shots": [{"kind": "wide"}, {"kind": "cutin"}],
        },
        {"role": "cta", "duration_s": 8, "script_guide": "행동 유도", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


def _fake_narrate(script, template, *, voice_name, voice_rate=1.0, audio_dir, synthesize=None):
    from app.promo.timing import NarrationAudio, NarrationTiming, SectionTiming

    narration = NarrationTiming(
        sections=(
            SectionTiming(role="hook", text="훅", target_s=3.0, measured_s=3.0, start_s=0.0),
            SectionTiming(role="cta", text="행동 유도", target_s=8.0, measured_s=8.0, start_s=3.0),
        )
    )
    audio = NarrationAudio(
        audio_file=str(audio_dir) + "/fake.mp3",
        duration_s=11.0,
        sub_maker=object(),
        voice_name=voice_name,
        voice_rate=voice_rate,
    )
    return narration, audio


def _fake_retime(materials, narration, storage_local_dir, *, shots=None, headlines=None, font_path=None, retime_id=None, tail_padding_s=1.0):
    from app.models.schema import MaterialInfo
    from app.promo.materials.retime import RetimedClip

    clips = []
    position = 0
    for index, section in enumerate(narration.sections):
        section_shots = list(shots[index]) if shots else []
        count = len(section_shots) or 1
        share = section.measured_s / count
        for shot_index in range(count):
            clips.append(
                RetimedClip(
                    material=MaterialInfo(
                        provider="local",
                        url=f"/tmp/sched-v2-{position}.mp4",
                        duration=int(round(share)),
                    ),
                    seconds=round(share, 3),
                    section_index=index,
                    shot_index=shot_index,
                )
            )
            position += 1
    return clips


@pytest.fixture()
def autopilot_v2_env(conn, tmp_path, monkeypatch):
    """v2 템플릿 오토파일럿 환경 — TTS/ffmpeg 만 주입, 나머지 흐름은 실제 코드."""
    clip = tmp_path / "brand.mp4"
    clip.write_bytes(b"dummy")
    brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=[str(clip)]))

    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    (templates_dir / "sched-test-v2.json").write_text(
        json.dumps(V2_TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(scheduler, "templates_data_dir", lambda: str(templates_dir))

    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    monkeypatch.setattr(
        scheduler.utils, "storage_dir", lambda sub="", create=False: str(local_dir)
    )

    from app.services import llm

    monkeypatch.setattr(llm, "_generate_response", lambda prompt: "훅 문장. 행동 유도 문장.")

    # v2 경로: 실측/리타이밍은 주입 (실제 TTS/ffmpeg 없이 흐름만 검증)
    monkeypatch.setattr(pipeline, "narrate_full_script", _fake_narrate)
    monkeypatch.setattr(pipeline, "retime_materials", _fake_retime)

    video = tmp_path / "final-v2.mp4"
    video.write_bytes(b"x")

    def fake_execute(plan, *, task_id=None, **kwargs):
        return pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=(str(video),),
            render_seconds=1.0,
            technical=GateResult(passed=True, failures=[], warnings=[]),
        )

    monkeypatch.setattr(pipeline, "execute_render", fake_execute)

    from app.services import upload_post

    monkeypatch.setattr(
        upload_post, "cross_post_video", lambda *a, **k: {"success": True, "request_id": "req-v2"}
    )
    return conn


def test_autopilot_v2_persists_measured_timeline(autopilot_v2_env):
    """v2 오토파일럿: 실측 타임라인/컷 분할이 플랜 영속화까지 살아 있다."""
    conn = autopilot_v2_env
    outcome = scheduler.run_autopilot_once(conn)

    assert outcome["request_id"] == "req-v2"
    row = plans.get_row(conn, outcome["plan_id"])
    assert row["status"] == plans.STATUS_RENDERED

    restored = plans.restore_plan(row)
    assert restored.template.version == 2
    assert restored.narration is not None
    assert restored.narration.total_s == 11.0
    assert restored.clip_seconds == (1.5, 1.5, 8.0)  # hook 2컷 분할 반영
    assert restored.timeline is not None and restored.timeline.passed


def test_tick_survives_v2_tts_failure_as_missed(autopilot_v2_env, monkeypatch):
    """v2 TTS 실패(TimingError)는 SchedulerError 가 아니지만 tick 이 missed 로 흡수한다.

    v2 는 무인 오토파일럿 경로에 네트워크 TTS 호출을 추가한다 — 그게 실패해도
    스케줄러가 죽지 않고 missed run 으로 기록해야 한다.
    """
    from app.promo.timing import TimingError

    conn = autopilot_v2_env

    def failing_narrate(*args, **kwargs):
        raise TimingError("TTS 합성에 실패했습니다 (음성/네트워크 확인)")

    monkeypatch.setattr(pipeline, "narrate_full_script", failing_narrate)

    scheduler.set_frequency(conn, 2, now=NOW)
    due = _rewind_first_run(conn)

    report = scheduler.tick(conn, now=NOW)  # 실제 run_autopilot_once 경유

    assert report["status"] == "ok"  # tick 자체는 예외로 죽지 않는다
    assert report["ran"] == []
    assert [entry["run"] for entry in report["missed"]] == [due]
    assert "TTS 합성" in report["missed"][0]["cause"]


# ── v2 동적 변수: LLM 응답의 [변수] 블록이 플랜까지 흐르는가 ──────────

SCHED_VARS_TEMPLATE = {
    "template_id": "sched-vars-v2",
    "name": "스케줄 변수 테스트",
    "version": 2,
    "mood": "upbeat",
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "훅",
            "material_slot": "any",
            "headline": {"template": "{shop_name} {menu_name}!", "show": True},
        },
        {"role": "cta", "duration_s": 8, "script_guide": "cta", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name} {menu_name} 출시!",
    "hashtags_base": ["테스트"],
}


@pytest.fixture()
def autopilot_vars_env(conn, tmp_path, monkeypatch):
    """동적 변수가 있는 v2 템플릿 오토파일럿 환경."""
    clip = tmp_path / "brand.mp4"
    clip.write_bytes(b"dummy")
    brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=[str(clip)]))

    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    (templates_dir / "sched-vars.json").write_text(
        json.dumps(SCHED_VARS_TEMPLATE, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(scheduler, "templates_data_dir", lambda: str(templates_dir))

    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    monkeypatch.setattr(
        scheduler.utils, "storage_dir", lambda sub="", create=False: str(local_dir)
    )

    from app.services import llm

    # LLM 이 내레이션 + [변수] 블록을 돌려준다.
    monkeypatch.setattr(
        llm,
        "_generate_response",
        lambda prompt: "자동 생성 내레이션입니다.\n[변수]\nmenu_name: 매운 떡볶이",
    )

    video = tmp_path / "final-vars.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        pipeline,
        "execute_render",
        lambda plan, *, task_id=None, **kw: pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=(str(video),),
            render_seconds=1.0,
            technical=GateResult(passed=True, failures=[], warnings=[]),
        ),
    )

    captured = {}
    from app.promo import publish

    monkeypatch.setattr(
        publish,
        "publish_video",
        lambda video, title, description, tags, **kw: captured.update(description=description)
        or {"success": True, "request_id": "req-vars"},
    )
    return conn, captured


def test_autopilot_v2_parses_variables_into_plan_and_caption(autopilot_vars_env):
    """LLM 응답의 [변수] 가 플랜에 실리고, 자동 업로드 캡션이 실제 값으로 채워진다."""
    conn, captured = autopilot_vars_env
    outcome = scheduler.run_autopilot_once(conn)

    assert outcome["request_id"] == "req-vars"
    restored = plans.restore_plan(plans.get_row(conn, outcome["plan_id"]))
    assert restored.variables == {"menu_name": "매운 떡볶이"}
    # 캡션이 subject 폴백이 아니라 채워진 caption_template 이어야 한다.
    assert "우리가게 매운 떡볶이 출시!" in captured["description"]
    assert "{menu_name}" not in captured["description"]


# ── 타임라인 게이트 실패 시 스크립트 재생성 재시도 ─────────────────────


def _fake_plan(template, timeline_passed, plan_id="plan-x", structural_passed=True):
    return pipeline.RenderPlan(
        plan_id=plan_id,
        template=template,
        script="스크립트",
        materials=(),
        used_brand_count=1,
        photo_warning=False,
        structural=GateResult(
            passed=structural_passed,
            failures=[] if structural_passed else ["브랜드 소재가 0개입니다"],
        ),
        timeline=GateResult(
            passed=timeline_passed,
            failures=[] if timeline_passed else ["TIMELINE_COVERAGE_MISMATCH: too long"],
        ),
        subject="테스트",
    )


def test_autopilot_retries_on_timeline_gate_failure(autopilot_v2_env, monkeypatch):
    """타임라인 게이트 실패 시 분량 힌트를 더해 재생성, 성공하면 완주한다."""
    from app.services import llm

    conn = autopilot_v2_env
    attempts = {"count": 0}
    prompts = []

    def fake_plan_render(template, kit, stock, script, local_dir, variables=None):
        attempts["count"] += 1
        return _fake_plan(template, timeline_passed=attempts["count"] >= 2,
                          plan_id=f"plan-{attempts['count']}")

    monkeypatch.setattr(pipeline, "plan_render", fake_plan_render)
    monkeypatch.setattr(
        llm, "_generate_response",
        lambda p: prompts.append(p) or "내레이션입니다.\n[변수]\nmenu_name: 매운 떡볶이",
    )

    outcome = scheduler.run_autopilot_once(conn)

    assert attempts["count"] == 2  # 첫 실패 -> 재시도 -> 성공
    assert outcome["plan_id"] == "plan-2"
    # 두 번째 시도 프롬프트에는 분량 재조절 힌트가 붙는다.
    assert "[재시도 안내]" not in prompts[0]
    assert "[재시도 안내]" in prompts[1]


def test_autopilot_no_retry_on_structural_failure(autopilot_v2_env, monkeypatch):
    """구조 게이트 실패는 스크립트와 무관 — 재시도 없이 즉시 실패한다."""
    conn = autopilot_v2_env
    attempts = {"count": 0}

    def fake_plan_render(template, kit, stock, script, local_dir, variables=None):
        attempts["count"] += 1
        return _fake_plan(template, timeline_passed=True, structural_passed=False)

    monkeypatch.setattr(pipeline, "plan_render", fake_plan_render)

    with pytest.raises(scheduler.SchedulerError, match="구조 게이트"):
        scheduler.run_autopilot_once(conn)
    assert attempts["count"] == 1  # 재시도하지 않는다


def test_autopilot_retry_exhaustion_raises(autopilot_v2_env, monkeypatch):
    """타임라인 게이트가 계속 실패하면 MAX_SCRIPT_ATTEMPTS 후 SchedulerError."""
    conn = autopilot_v2_env
    attempts = {"count": 0}

    def fake_plan_render(template, kit, stock, script, local_dir, variables=None):
        attempts["count"] += 1
        return _fake_plan(template, timeline_passed=False)

    monkeypatch.setattr(pipeline, "plan_render", fake_plan_render)

    with pytest.raises(scheduler.SchedulerError, match="통과하지 못함"):
        scheduler.run_autopilot_once(conn)
    assert attempts["count"] == scheduler.MAX_SCRIPT_ATTEMPTS


def test_autopilot_logs_missing_variables(autopilot_vars_env, monkeypatch):
    """LLM 이 동적 변수를 안 주면 경고 로그(헤드라인 생략/캡션 폴백 예고)."""
    from loguru import logger as loguru_logger

    from app.services import llm

    conn, _captured = autopilot_vars_env
    # 변수 블록 없는 응답 -> menu_name 미충전
    monkeypatch.setattr(llm, "_generate_response", lambda p: "내레이션만 있습니다.")

    messages = []
    sink_id = loguru_logger.add(lambda msg: messages.append(str(msg)), level="WARNING")
    try:
        scheduler.run_autopilot_once(conn)
    finally:
        loguru_logger.remove(sink_id)

    assert any("동적 변수를 다 채우지 못함" in m for m in messages)
