"""promo API 라우터 테스트 — 실 렌더/실 네트워크 없음.

플랜 생성(사전 게이트 포함) -> 렌더(백그라운드, 가짜 러너) -> 조회 플로우와
경계 조건(템플릿/플랜/브랜드킷 부재, 렌더 중복, yt-dlp 부재)을 검증한다.
"""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.promo import api as promo_api
from app.promo import db as promo_db
from app.promo import pipeline, plans
from app.promo.brandkit import store as brandkit_store
from app.promo.brandkit.models import BrandKit
from app.promo.quality import GateResult

TEMPLATE_DATA = {
    "template_id": "api-test-v1",
    "name": "API 테스트",
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

SCRIPT = "드디어 나왔다, 신메뉴! 지금 방문하세요."


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 격리: DB / 템플릿 디렉터리 / storage 디렉터리 전부 tmp 로
    db_path = tmp_path / "api-test.db"
    monkeypatch.setattr(promo_db, "default_db_path", lambda: str(db_path))

    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    (templates_dir / "api-test.json").write_text(
        json.dumps(TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(promo_api, "templates_data_dir", lambda: str(templates_dir))

    local_dir = tmp_path / "local_videos"
    local_dir.mkdir()
    monkeypatch.setattr(
        promo_api.utils, "storage_dir", lambda sub="", create=False: str(local_dir)
    )

    # 브랜드킷 시드
    brand_clip = tmp_path / "brand.mp4"
    brand_clip.write_bytes(b"dummy")
    conn = promo_db.connect()
    brandkit_store.save(
        conn, BrandKit(business_name="우리가게", photos=[str(brand_clip)])
    )
    conn.close()

    app = FastAPI()
    app.include_router(promo_api.router)
    return TestClient(app)


def _create_plan(client, script=SCRIPT):
    response = client.post(
        "/api/v1/promo/plans",
        json={"template_id": "api-test-v1", "script": script},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_status(client, plan_id, terminal, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/promo/plans/{plan_id}").json()
        if data["status"] in terminal:
            return data
        time.sleep(0.02)
    raise AssertionError(f"플랜이 {terminal} 상태에 도달하지 못했습니다: {data}")


def test_list_templates(client):
    response = client.get("/api/v1/promo/templates")
    assert response.status_code == 200
    templates = response.json()["templates"]
    assert [t["template_id"] for t in templates] == ["api-test-v1"]
    assert templates[0]["sections"] == ["hook", "cta"]


def test_create_plan_returns_gate_verdict(client):
    plan = _create_plan(client)
    assert plan["structural_gate"]["passed"] is True
    assert plan["approved_ready"] is True
    assert plan["used_brand_count"] == 1
    assert len(plan["materials"]) == 2


def test_create_plan_unknown_template_404(client):
    response = client.post(
        "/api/v1/promo/plans", json={"template_id": "nope", "script": SCRIPT}
    )
    assert response.status_code == 404


def test_create_plan_empty_script_422(client):
    response = client.post(
        "/api/v1/promo/plans", json={"template_id": "api-test-v1", "script": "  "}
    )
    assert response.status_code == 422


def test_render_flow_success(client, monkeypatch, tmp_path):
    fake_video = tmp_path / "final.mp4"
    fake_video.write_bytes(b"x")

    def fake_execute(plan, *, task_id=None, **kwargs):
        return pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=(str(fake_video),),
            render_seconds=1.0,
            technical=GateResult(passed=True, failures=[], warnings=[]),
        )

    monkeypatch.setattr(pipeline, "execute_render", fake_execute)

    plan = _create_plan(client)
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/render", json={})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert task_id.startswith(f"promo-{plan['plan_id']}")

    data = _wait_status(client, plan["plan_id"], {plans.STATUS_RENDERED})
    assert data["result"]["videos"] == [str(fake_video)]
    assert data["result"]["technical_gate"]["passed"] is True


def test_render_failure_recorded(client, monkeypatch):
    def broken_execute(plan, *, task_id=None, **kwargs):
        raise pipeline.PipelineError("렌더 폭발")

    monkeypatch.setattr(pipeline, "execute_render", broken_execute)

    plan = _create_plan(client)
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/render", json={})
    assert response.status_code == 202

    data = _wait_status(client, plan["plan_id"], {plans.STATUS_FAILED})
    assert "렌더 폭발" in data["result"]["error"]


def test_render_reentry_blocked_while_rendering(client, monkeypatch):
    import threading

    release = threading.Event()

    def slow_execute(plan, *, task_id=None, **kwargs):
        release.wait(timeout=5)
        return pipeline.RenderResult(
            task_id=task_id,
            plan_id=plan.plan_id,
            videos=("v.mp4",),
            render_seconds=0.1,
            technical=GateResult(passed=True, failures=[], warnings=[]),
        )

    monkeypatch.setattr(pipeline, "execute_render", slow_execute)

    plan = _create_plan(client)
    first = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/render", json={})
    assert first.status_code == 202
    second = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/render", json={})
    assert second.status_code == 409
    release.set()
    _wait_status(client, plan["plan_id"], {plans.STATUS_RENDERED})


def test_render_unknown_plan_404(client):
    response = client.post("/api/v1/promo/plans/nope/render", json={})
    assert response.status_code == 404


def test_research_reference_without_ytdlp_503(client, monkeypatch):
    from app.promo.research import ingest

    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    response = client.post(
        "/api/v1/promo/research/reference", json={"url": "https://example.com/v"}
    )
    assert response.status_code == 503


def test_script_prompt_without_reference(client):
    response = client.post(
        "/api/v1/promo/script-prompt", json={"template_id": "api-test-v1"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reference_used"] is False
    assert "우리가게" in data["prompt"]
    assert "hook (3초)" in data["prompt"]


def test_script_prompt_with_reference(client, monkeypatch):
    vtt = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n드디어 나왔다\n\n"
        "00:00:02.000 --> 00:00:10.000\n지금 방문하세요\n"
    )
    monkeypatch.setattr(promo_api, "fetch_subtitles", lambda url, **kw: vtt)

    response = client.post(
        "/api/v1/promo/script-prompt",
        json={"template_id": "api-test-v1", "reference_url": "https://example.com/v"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reference_used"] is True
    assert "드디어 나왔다" in data["prompt"]
