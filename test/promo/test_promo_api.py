"""promo API 라우터 테스트 — 실 렌더/실 네트워크 없음.

플랜 생성(사전 게이트 포함) -> 렌더(백그라운드, 가짜 러너) -> 조회 플로우와
경계 조건(템플릿/플랜/브랜드킷 부재, 렌더 중복, yt-dlp 부재)을 검증한다.
"""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.schema import MaterialInfo
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

    # 트렌드 폴링 오프라인 고정 (tick 피기백/갱신 엔드포인트용)
    from app.promo import trends

    monkeypatch.setattr(
        trends,
        "fetch_google_trends",
        lambda timeout=0: [
            trends.TrendItem(keyword="급상승키워드", traffic="500+", traffic_value=500)
        ],
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


def test_generate_script_via_llm(client, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(
        llm, "_generate_response", lambda prompt: "생성된 스크립트입니다."
    )
    response = client.post(
        "/api/v1/promo/scripts", json={"template_id": "api-test-v1"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["script"] == "생성된 스크립트입니다."
    assert data["reference_used"] is False


def test_generate_script_llm_error_502(client, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(
        llm, "_generate_response", lambda prompt: "Error: api_key is not set"
    )
    response = client.post(
        "/api/v1/promo/scripts", json={"template_id": "api-test-v1"}
    )
    assert response.status_code == 502
    assert "api_key" in response.json()["detail"]


def test_generate_script_parses_variables_block(client, monkeypatch, tmp_path):
    """v2 템플릿: LLM 응답의 [변수] 블록을 파싱해 내레이션과 분리한다."""
    _write_caption_template(tmp_path)
    from app.services import llm

    monkeypatch.setattr(
        llm,
        "_generate_response",
        lambda prompt: "드디어 나왔다, 신메뉴!\n[변수]\nmenu_name: 매운 떡볶이",
    )
    response = client.post(
        "/api/v1/promo/scripts", json={"template_id": "api-caption-v2"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["script"] == "드디어 나왔다, 신메뉴!"  # 내레이션만
    assert data["variables"] == {"menu_name": "매운 떡볶이"}


def test_generate_script_v1_has_empty_variables(client, monkeypatch):
    """v1 템플릿은 변수 블록 없이 내레이션만 — variables 는 빈 dict."""
    from app.services import llm

    monkeypatch.setattr(llm, "_generate_response", lambda prompt: "그냥 내레이션.")
    response = client.post(
        "/api/v1/promo/scripts", json={"template_id": "api-test-v1"}
    )
    data = response.json()
    assert data["script"] == "그냥 내레이션."
    assert data["variables"] == {}


def _make_rendered_plan(client, tmp_path):
    """플랜을 생성하고 DB 직접 조작으로 rendered 상태 + 실존 산출물을 만든다."""
    plan = _create_plan(client)
    video = tmp_path / f"final-{plan['plan_id']}.mp4"
    video.write_bytes(b"video-bytes")
    conn = promo_db.connect()
    try:
        plans.mark_rendering(conn, plan["plan_id"], f"task-{plan['plan_id']}")
        plans.finish(
            conn,
            plan["plan_id"],
            plans.STATUS_RENDERED,
            {
                "task_id": f"task-{plan['plan_id']}",
                "videos": [str(video)],
                "render_seconds": 1.0,
                "technical_gate": {"passed": True, "failures": [], "warnings": []},
                "warnings": [],
            },
        )
    finally:
        conn.close()
    return plan


def test_upload_success_records_delivery(client, monkeypatch, tmp_path):
    from app.services import upload_post

    calls = {}

    def fake_cross_post(video_path, title, platforms=None, youtube_extra=None):
        calls.update(
            video_path=video_path,
            title=title,
            platforms=platforms,
            youtube_extra=youtube_extra,
        )
        return {"success": True, "request_id": "req-1"}

    monkeypatch.setattr(upload_post, "cross_post_video", fake_cross_post)

    plan = _make_rendered_plan(client, tmp_path)
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/upload", json={})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["request_id"] == "req-1"
    assert data["uploads_today"] == 1
    assert calls["platforms"] == ["youtube"]
    assert "우리가게" in calls["title"]
    assert calls["youtube_extra"]["tags"] == ["테스트"]


def test_upload_requires_rendered_status(client, tmp_path):
    plan = _create_plan(client)
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/upload", json={})
    assert response.status_code == 409


def test_upload_daily_cap_429(client, monkeypatch, tmp_path):
    from app.config import config
    from app.services import upload_post

    monkeypatch.setitem(config.app, "promo_upload_daily_cap", 1)
    monkeypatch.setattr(
        upload_post,
        "cross_post_video",
        lambda *a, **k: {"success": True, "request_id": "req"},
    )

    first = _make_rendered_plan(client, tmp_path)
    assert (
        client.post(f"/api/v1/promo/plans/{first['plan_id']}/upload", json={})
        .status_code
        == 200
    )

    second = _make_rendered_plan(client, tmp_path)
    response = client.post(f"/api/v1/promo/plans/{second['plan_id']}/upload", json={})
    assert response.status_code == 429
    assert response.json()["detail"]["daily_cap"] == 1


def test_upload_service_failure_502_not_recorded(client, monkeypatch, tmp_path):
    from app.services import upload_post

    monkeypatch.setattr(
        upload_post,
        "cross_post_video",
        lambda *a, **k: {"success": False, "error": "Upload-Post not configured"},
    )

    plan = _make_rendered_plan(client, tmp_path)
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/upload", json={})
    assert response.status_code == 502

    conn = promo_db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


# ── 스케줄 ───────────────────────────────────────────────────────────


def test_schedule_get_404_before_setup(client):
    assert client.get("/api/v1/promo/schedule").status_code == 404


def test_schedule_put_and_get(client):
    response = client.put("/api/v1/promo/schedule", json={"freq_per_week": 3})
    assert response.status_code == 200
    assert len(response.json()["next_runs"]) == 3

    data = client.get("/api/v1/promo/schedule").json()
    assert data["freq_per_week"] == 3


def test_schedule_put_invalid_422(client):
    assert (
        client.put("/api/v1/promo/schedule", json={"freq_per_week": 0}).status_code
        == 422
    )


def test_schedule_tick_runs_due(client, monkeypatch):
    import json as jsonlib

    from app.promo import scheduler

    client.put("/api/v1/promo/schedule", json={"freq_per_week": 2})
    # 첫 런을 과거로 되감아 만기 상태로 만든다
    conn = promo_db.connect()
    try:
        schedule = scheduler.get_schedule(conn)
        runs = list(schedule.next_runs)
        runs[0] = "2000-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE schedule SET next_runs = ? WHERE id = 1", (jsonlib.dumps(runs),)
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        scheduler, "run_autopilot_once", lambda conn: {"plan_id": "auto-1"}
    )
    report = client.post("/api/v1/promo/schedule/tick").json()
    assert report["status"] == "ok"
    assert len(report["ran"]) == 1
    assert report["ran"][0]["plan_id"] == "auto-1"


# ── 브랜드킷 ─────────────────────────────────────────────────────────


def test_brandkit_get_returns_seeded_kit(client):
    data = client.get("/api/v1/promo/brandkit").json()
    assert data["business_name"] == "우리가게"


def test_brandkit_put_merges_fields(client):
    response = client.put(
        "/api/v1/promo/brandkit", json={"category": "카페", "description": "새 소개"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "카페"
    assert data["business_name"] == "우리가게"  # 기존 필드 보존


def test_brandkit_put_promotion_links_round_trip(client):
    response = client.put(
        "/api/v1/promo/brandkit",
        json={"promotion_links": [{"label": "예약", "url": "https://booking.kr"}]},
    )
    assert response.status_code == 200
    assert response.json()["promotion_links"] == [
        {"label": "예약", "url": "https://booking.kr"}
    ]
    data = client.get("/api/v1/promo/brandkit").json()
    assert data["promotion_links"] == [
        {"label": "예약", "url": "https://booking.kr"}
    ]


def test_brandkit_put_promotion_link_empty_url_rejected(client):
    response = client.put(
        "/api/v1/promo/brandkit",
        json={"promotion_links": [{"label": "예약", "url": ""}]},
    )
    assert response.status_code == 422


def test_brandkit_get_without_links_returns_empty_list(client):
    data = client.get("/api/v1/promo/brandkit").json()
    assert data["promotion_links"] == []


# ── 키워드 갭 리서치 ──────────────────────────────────────────────


def test_research_gap_endpoint_uses_demand_map(client, monkeypatch):
    captured = {}

    def fake_rank(keywords, demand_map=None, *, limit=10):
        captured["keywords"] = keywords
        captured["demand_map"] = demand_map
        return []

    monkeypatch.setattr(promo_api, "rank_keywords", fake_rank)
    response = client.post(
        "/api/v1/promo/research/gap",
        json={"keywords": ["무선 선풍기"], "demand_map": {"무선 선풍기": 500}},
    )
    assert response.status_code == 200
    assert response.json() == {"results": []}
    assert captured["keywords"] == ["무선 선풍기"]
    assert captured["demand_map"]["무선 선풍기"] == 500.0


def test_research_gap_endpoint_trends_demand_fallback(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        promo_api.trends,
        "latest",
        lambda conn, limit=10: (
            "2026-08-12T00:00:00Z",
            [SimpleNamespace(keyword="무선 선풍기", traffic_value=700)],
        ),
    )
    captured = {}

    def fake_rank(keywords, demand_map=None, *, limit=10):
        captured["demand_map"] = demand_map
        return []

    monkeypatch.setattr(promo_api, "rank_keywords", fake_rank)
    response = client.post(
        "/api/v1/promo/research/gap", json={"keywords": ["무선 선풍기"]}
    )
    assert response.status_code == 200
    assert captured["demand_map"]["무선 선풍기"] == 700.0


def test_research_gap_endpoint_tool_missing_503(client, monkeypatch):
    from app.promo.research.ingest import ResearchToolMissingError

    def raise_missing(keywords, demand_map=None, *, limit=10):
        raise ResearchToolMissingError("yt-dlp 를 찾을 수 없습니다")

    monkeypatch.setattr(promo_api, "rank_keywords", raise_missing)
    response = client.post(
        "/api/v1/promo/research/gap", json={"keywords": ["무선 선풍기"]}
    )
    assert response.status_code == 503


def test_research_gap_endpoint_blank_keywords_422(client):
    response = client.post(
        "/api/v1/promo/research/gap", json={"keywords": ["  "]}
    )
    assert response.status_code == 422


def test_research_gap_endpoint_datalab_demand(client, monkeypatch):
    monkeypatch.setattr(promo_api.datalab, "configured", lambda: True)
    monkeypatch.setattr(
        promo_api.datalab,
        "fetch_demand",
        lambda keywords, **kwargs: {"무선 선풍기": 42.5},
    )
    captured = {}

    def fake_rank(keywords, demand_map=None, *, limit=10):
        captured["demand_map"] = demand_map
        return []

    monkeypatch.setattr(promo_api, "rank_keywords", fake_rank)
    response = client.post(
        "/api/v1/promo/research/gap", json={"keywords": ["무선 선풍기"]}
    )
    assert response.status_code == 200
    assert captured["demand_map"]["무선 선풍기"] == 42.5


def test_research_gap_endpoint_datalab_failure_falls_back_to_trends(
    client, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(promo_api.datalab, "configured", lambda: True)

    def raise_fetch(keywords, **kwargs):
        raise promo_api.datalab.DataLabFetchError("boom")

    monkeypatch.setattr(promo_api.datalab, "fetch_demand", raise_fetch)
    monkeypatch.setattr(
        promo_api.trends,
        "latest",
        lambda conn, limit=10: (
            "2026-08-12T00:00:00Z",
            [SimpleNamespace(keyword="무선 선풍기", traffic_value=700)],
        ),
    )
    captured = {}

    def fake_rank(keywords, demand_map=None, *, limit=10):
        captured["demand_map"] = demand_map
        return []

    monkeypatch.setattr(promo_api, "rank_keywords", fake_rank)
    response = client.post(
        "/api/v1/promo/research/gap", json={"keywords": ["무선 선풍기"]}
    )
    assert response.status_code == 200
    assert captured["demand_map"]["무선 선풍기"] == 700.0


def test_brandkit_crawl_fills_empty_fields_only(client, monkeypatch):
    from app.promo.brandkit.enrich import EnrichResult

    monkeypatch.setattr(
        promo_api.brandkit_enrich,
        "enrich",
        lambda url: EnrichResult(
            site="generic",
            status="ok",
            fields={
                "business_name": "크롤된 상호",
                "description": "크롤된 소개",
                "sns_url": url,
            },
            warnings=[],
        ),
    )
    response = client.post(
        "/api/v1/promo/brandkit/crawl", json={"url": "https://example.com"}
    )
    assert response.status_code == 200
    data = response.json()
    # business_name 은 이미 채워져 있으므로 미반영, description/sns_url 만 반영
    assert "business_name" not in data["applied_fields"]
    assert "description" in data["applied_fields"]
    assert data["site"] == "generic"
    assert data["brandkit"]["business_name"] == "우리가게"
    assert data["brandkit"]["description"] == "크롤된 소개"
    assert data["brandkit"]["sns_url"] == "https://example.com"


def test_brandkit_crawl_failure_502(client, monkeypatch):
    from app.promo.brandkit.enrich import EnrichResult

    monkeypatch.setattr(
        promo_api.brandkit_enrich,
        "enrich",
        lambda url: EnrichResult(
            site="generic", status="failed", warnings=["요청 실패: timeout"]
        ),
    )
    response = client.post(
        "/api/v1/promo/brandkit/crawl", json={"url": "https://example.com"}
    )
    assert response.status_code == 502
    assert "timeout" in response.json()["detail"]


def test_brandkit_naver_place_url_guides_to_local_api(client):
    response = client.post(
        "/api/v1/promo/brandkit/crawl",
        json={"url": "https://m.place.naver.com/restaurant/123/home"},
    )
    assert response.status_code == 502
    assert "naver-local" in response.json()["detail"]


def test_brandkit_naver_local_applies_top_result(client, monkeypatch):
    monkeypatch.setattr(
        promo_api.brandkit_enrich,
        "naver_local_search",
        lambda query, display=5: [
            {
                "business_name": "우리가게",
                "category": "음식점>분식",
                "address": "서울 마포구 1-1",
                "road_address": "서울 마포구 도로명로 1",
                "phone": "02-123-4567",
                "link": "https://example.com/shop",
            }
        ],
    )
    # query 미지정 -> 저장된 상호명("우리가게")으로 검색
    response = client.post("/api/v1/promo/brandkit/naver-local", json={})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["query"] == "우리가게"
    assert "category" in data["applied_fields"]
    assert data["brandkit"]["address"] == "서울 마포구 도로명로 1"  # 도로명 우선
    assert data["brandkit"]["phone"] == "02-123-4567"


def test_brandkit_naver_local_not_configured_503(client, monkeypatch):
    from app.promo.brandkit.enrich import NaverApiNotConfiguredError

    def not_configured(query, display=5):
        raise NaverApiNotConfiguredError("naver_client_id 미설정")

    monkeypatch.setattr(
        promo_api.brandkit_enrich, "naver_local_search", not_configured
    )
    response = client.post("/api/v1/promo/brandkit/naver-local", json={})
    assert response.status_code == 503


# ── 트렌드 ───────────────────────────────────────────────────────────


def test_trends_get_empty_cache(client):
    data = client.get("/api/v1/promo/research/trends").json()
    assert data["fetched_at"] is None
    assert data["stale"] is True
    assert data["items"] == []


def test_trends_refresh_and_get(client):
    refreshed = client.post("/api/v1/promo/research/trends/refresh").json()
    assert refreshed["count"] == 1
    assert refreshed["items"][0]["keyword"] == "급상승키워드"

    data = client.get("/api/v1/promo/research/trends").json()
    assert data["stale"] is False
    assert data["items"][0]["traffic"] == "500+"


def test_trends_refresh_failure_502(client, monkeypatch):
    from app.promo import trends

    def broken():
        raise trends.TrendsFetchError("수집 불가")

    monkeypatch.setattr(trends, "fetch_google_trends", broken)
    response = client.post("/api/v1/promo/research/trends/refresh")
    assert response.status_code == 502


def test_script_prompt_use_trends(client):
    client.post("/api/v1/promo/research/trends/refresh")

    data = client.post(
        "/api/v1/promo/script-prompt",
        json={"template_id": "api-test-v1", "use_trends": True},
    ).json()
    assert data["trend_keywords"] == ["급상승키워드"]
    assert "급상승키워드" in data["prompt"]
    assert "[트렌드]" in data["prompt"]

    # use_trends 미지정이면 프롬프트에 트렌드 블록 없음
    plain = client.post(
        "/api/v1/promo/script-prompt", json={"template_id": "api-test-v1"}
    ).json()
    assert plain["trend_keywords"] == []
    assert "[트렌드]" not in plain["prompt"]


# ---------------------------------------------------------------------------
# 소재 생성 프롬프트 (템플릿 v2 style_preset)
# ---------------------------------------------------------------------------


V2_TEMPLATE_DATA = {
    **TEMPLATE_DATA,
    "template_id": "api-test-v2",
    "version": 2,
    "style_preset": "warm-food",
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "훅",
            "material_slot": "any",
            "feel": "따뜻한",
            "shots": [{"kind": "wide"}, {"kind": "cutin"}],
        },
        {
            "role": "cta",
            "duration_s": 8,
            "script_guide": "행동 유도",
            "material_slot": "photo",
        },
    ],
}


def _write_v2_template(tmp_path):
    (tmp_path / "templates-data" / "api-test-v2.json").write_text(
        json.dumps(V2_TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )


def test_material_prompts_returns_prompt_per_shot(client, tmp_path):
    _write_v2_template(tmp_path)

    response = client.post(
        "/api/v1/promo/material-prompts", json={"template_id": "api-test-v2"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["style_preset"] == "warm-food"
    # hook 2컷 + cta 1컷
    assert [(p["section_index"], p["shot_index"]) for p in data["prompts"]] == [
        (0, 0),
        (0, 1),
        (1, 0),
    ]
    assert all("9:16" in p["prompt"] for p in data["prompts"])
    assert all(p["negative_prompt"] for p in data["prompts"])
    assert "tight detail cut-in" in data["prompts"][1]["prompt"]


def test_material_prompts_without_style_preset_400(client):
    """v1 템플릿에는 스타일 선언이 없다 — 기본값으로 얼버무리지 않고 400."""
    response = client.post(
        "/api/v1/promo/material-prompts", json={"template_id": "api-test-v1"}
    )

    assert response.status_code == 400
    assert "style_preset" in response.json()["detail"]


def test_material_prompts_unknown_template_404(client):
    response = client.post(
        "/api/v1/promo/material-prompts", json={"template_id": "nope"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 템플릿 v2: 플랜 요약이 실측 타임라인/컷/헤드라인을 노출하는가
# ---------------------------------------------------------------------------

V2_TEMPLATE_DATA = {
    "template_id": "api-test-v2",
    "name": "API v2 테스트",
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
        {
            "role": "cta",
            "duration_s": 8,
            "script_guide": "행동 유도",
            "material_slot": "photo",
        },
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


def _fake_narrate(script, template, *, voice_name, voice_rate=1.0, audio_dir, synthesize=None):
    from app.promo.timing import NarrationAudio, NarrationTiming, SectionTiming

    narration = NarrationTiming(
        sections=(
            SectionTiming(role="hook", text="훅 문장", target_s=3.0, measured_s=3.0, start_s=0.0),
            SectionTiming(role="cta", text="행동 유도 문장", target_s=8.0, measured_s=8.0, start_s=3.0),
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
                        url=f"/tmp/fake-retimed-{position}.mp4",
                        duration=int(round(share)),
                    ),
                    seconds=round(share, 3),
                    section_index=index,
                    shot_index=shot_index,
                )
            )
            position += 1
    return clips


def test_v2_plan_summary_exposes_timeline_clips_headlines(client, monkeypatch, tmp_path):
    (tmp_path / "templates-data" / "api-test-v2.json").write_text(
        json.dumps(V2_TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(pipeline, "narrate_full_script", _fake_narrate)
    monkeypatch.setattr(pipeline, "retime_materials", _fake_retime)

    response = client.post(
        "/api/v1/promo/plans",
        json={"template_id": "api-test-v2", "script": "훅 문장. 행동 유도 문장."},
    )
    assert response.status_code == 200, response.text
    plan = response.json()

    assert plan["template_version"] == 2
    assert plan["style_preset"] == "warm-food"
    # 실측 타임라인: 총 11초, 섹션 2개
    assert plan["narration"]["total_s"] == 11.0
    assert [s["role"] for s in plan["narration"]["sections"]] == ["hook", "cta"]
    assert plan["narration"]["sections"][0]["end_s"] == 3.0
    # 타임라인 게이트 통과 + 컷 분할(hook 2컷) 반영
    assert plan["timeline_gate"]["passed"] is True
    assert plan["clip_seconds"] == [1.5, 1.5, 8.0]
    assert len(plan["materials"]) == 3
    # 헤드라인은 브랜드킷으로 채워진다 (cta 는 선언 없음 -> None)
    assert plan["headlines"] == ["우리가게 신메뉴", None]
    assert plan["approved_ready"] is True

    # 조회 시에도 복원된 플랜에서 같은 v2 필드가 나온다 (영속화 왕복).
    fetched = client.get(f"/api/v1/promo/plans/{plan['plan_id']}").json()
    assert fetched["narration"]["total_s"] == 11.0
    assert fetched["clip_seconds"] == [1.5, 1.5, 8.0]
    assert fetched["timeline_gate"]["passed"] is True
    assert fetched["headlines"] == ["우리가게 신메뉴", None]


def test_v1_plan_summary_has_no_narration(client):
    """v1 플랜은 narration/timeline 이 None — 기존 계약 불변."""
    plan = _create_plan(client)

    assert plan["template_version"] == 1
    assert plan["narration"] is None
    assert plan["timeline_gate"] is None
    assert plan["clip_seconds"] == []


# ---------------------------------------------------------------------------
# 템플릿 변수 (caption_template 소비 + required_variables 노출)
# ---------------------------------------------------------------------------

CAPTION_TEMPLATE_DATA = {
    "template_id": "api-caption-v2",
    "name": "캡션 테스트",
    "version": 2,
    "mood": "upbeat",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "훅", "material_slot": "any"},
        {"role": "cta", "duration_s": 8, "script_guide": "cta", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name} 신메뉴 '{menu_name}' 출시!",
    "hashtags_base": ["테스트"],
}


def _write_caption_template(tmp_path):
    (tmp_path / "templates-data" / "api-caption-v2.json").write_text(
        json.dumps(CAPTION_TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )


def _make_rendered_v2_plan(client, tmp_path, variables):
    _write_caption_template(tmp_path)
    response = client.post(
        "/api/v1/promo/plans",
        json={"template_id": "api-caption-v2", "script": "훅. 행동 유도.", "variables": variables},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    video = tmp_path / f"final-{plan['plan_id']}.mp4"
    video.write_bytes(b"x")
    conn = promo_db.connect()
    try:
        plans.mark_rendering(conn, plan["plan_id"], f"task-{plan['plan_id']}")
        plans.finish(
            conn,
            plan["plan_id"],
            plans.STATUS_RENDERED,
            {
                "task_id": f"task-{plan['plan_id']}",
                "videos": [str(video)],
                "render_seconds": 1.0,
                "technical_gate": {"passed": True, "failures": [], "warnings": []},
                "warnings": [],
            },
        )
    finally:
        conn.close()
    return plan


def test_list_templates_includes_version_and_required_variables(client, tmp_path):
    _write_caption_template(tmp_path)

    templates = client.get("/api/v1/promo/templates").json()["templates"]
    by_id = {t["template_id"]: t for t in templates}

    assert by_id["api-test-v1"]["version"] == 1
    assert by_id["api-test-v1"]["required_variables"] == []
    assert by_id["api-test-v1"]["autopilot"] is True  # 미선언 기본값
    assert by_id["api-caption-v2"]["version"] == 2
    # caption_template 의 {shop_name} 은 브랜드킷 키라 빠지고 {menu_name} 만 동적 변수
    assert by_id["api-caption-v2"]["required_variables"] == ["menu_name"]


def test_create_plan_summary_includes_variables(client, tmp_path):
    _write_caption_template(tmp_path)

    response = client.post(
        "/api/v1/promo/plans",
        json={
            "template_id": "api-caption-v2",
            "script": "훅. 행동 유도.",
            "variables": {"menu_name": "매운떡볶이"},
        },
    )
    plan = response.json()

    assert plan["variables"] == {"menu_name": "매운떡볶이"}
    assert plan["required_variables"] == ["menu_name"]


def test_upload_description_uses_filled_caption(client, monkeypatch, tmp_path):
    """변수가 채워지면 caption_template 가 업로드 설명으로 쓰인다."""
    from app.promo import publish

    captured = {}
    monkeypatch.setattr(
        publish,
        "publish_video",
        lambda video, title, description, tags, **kw: captured.update(description=description)
        or {"success": True, "request_id": "req-cap"},
    )

    plan = _make_rendered_v2_plan(client, tmp_path, {"menu_name": "매운떡볶이"})
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/upload", json={})

    assert response.status_code == 200, response.text
    assert "우리가게 신메뉴 '매운떡볶이' 출시!" in captured["description"]
    assert "{menu_name}" not in captured["description"]


def test_upload_description_falls_back_to_subject_without_variables(
    client, monkeypatch, tmp_path
):
    """변수가 없어 캡션에 원문이 남으면 공개 캡션 대신 안전한 subject 로 폴백."""
    from app.promo import publish

    captured = {}
    monkeypatch.setattr(
        publish,
        "publish_video",
        lambda video, title, description, tags, **kw: captured.update(description=description)
        or {"success": True, "request_id": "req-cap"},
    )

    plan = _make_rendered_v2_plan(client, tmp_path, {})
    response = client.post(f"/api/v1/promo/plans/{plan['plan_id']}/upload", json={})

    assert response.status_code == 200, response.text
    assert "{menu_name}" not in captured["description"]  # 원문이 공개되면 안 된다
    assert "캡션 테스트" in captured["description"]  # subject(가게 — 템플릿명) 폴백


def test_create_plan_summary_includes_caption_and_missing_variables(client, tmp_path):
    """플랜 요약에 업로드 캡션 미리보기 + 미충전 변수가 노출된다."""
    _write_caption_template(tmp_path)

    # 변수 충전 → 캡션이 caption_template 으로 채워지고 미충전 없음
    filled = client.post(
        "/api/v1/promo/plans",
        json={
            "template_id": "api-caption-v2",
            "script": "훅. 행동 유도.",
            "variables": {"menu_name": "매운떡볶이"},
        },
    ).json()
    assert filled["upload_caption"] == "우리가게 신메뉴 '매운떡볶이' 출시!"
    assert filled["missing_variables"] == []

    # 변수 없음 → 캡션은 subject 폴백(원문 금지), 미충전 변수 노출
    missing = client.post(
        "/api/v1/promo/plans",
        json={"template_id": "api-caption-v2", "script": "훅. 행동 유도."},
    ).json()
    assert missing["missing_variables"] == ["menu_name"]
    assert "{menu_name}" not in missing["upload_caption"]
