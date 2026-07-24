"""스케줄러(주기 계산/tick/오토파일럿) 테스트 — 실 렌더/실 네트워크 없음."""

import json
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
