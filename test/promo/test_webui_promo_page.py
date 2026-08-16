"""webui promo 페이지(AppTest) 스모크 테스트 — 실 렌더/실 네트워크 없음.

페이지 스크립트가 예외 없이 실행되고, 브랜드킷 유무에 따라
게이트(경고 후 중단 vs 템플릿 단계 진입)가 동작하는지 검증한다.
페이지의 실제 동작 로직은 app/promo/api.py 핸들러를 그대로 호출하므로
test_promo_api.py 가 로직 검증을 담당한다.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest

from app.promo import api as promo_api
from app.promo import db as promo_db
from app.promo.brandkit import store as brandkit_store
from app.promo.brandkit.models import BrandKit

PAGE_PATH = "webui/pages/promo.py"

TEMPLATE_DATA = {
    "template_id": "ui-test-v1",
    "name": "UI 테스트",
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
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        promo_db, "default_db_path", lambda: str(tmp_path / "ui-test.db")
    )
    templates_dir = tmp_path / "templates-data"
    templates_dir.mkdir()
    (templates_dir / "ui-test.json").write_text(
        json.dumps(TEMPLATE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(promo_api, "templates_data_dir", lambda: str(templates_dir))
    return tmp_path


def test_page_gap_panel_runs_research_gap(isolated_env, monkeypatch):
    captured = {}

    def fake_gap(body):
        captured["keywords"] = body.keywords
        return {
            "results": [
                {
                    "keyword": "무선 선풍기",
                    "demand": 42.5,
                    "result_count": 3,
                    "top_view_sum": 1500,
                    "top_view_max": 900,
                    "score": 1.06,
                }
            ]
        }

    monkeypatch.setattr(promo_api, "research_gap", fake_gap)

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.run()
    assert not at.exception

    area = next(t for t in at.text_area if "후보 키워드" in t.label)
    area.set_value("무선 선풍기").run()
    button = next(b for b in at.button if b.label == "갭 실측")
    button.click().run()

    assert not at.exception
    assert captured["keywords"] == ["무선 선풍기"]
    assert at.dataframe  # 갭 결과 테이블이 그려졌다


def test_page_stops_with_warning_without_brandkit(isolated_env):
    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.run()

    assert not at.exception
    assert any("브랜드킷이 없습니다" in w.value for w in at.warning)
    # 브랜드킷이 없으면 템플릿 단계로 진입하지 않아야 한다 (st.stop)
    assert not at.selectbox


def test_page_reaches_template_step_with_brandkit(isolated_env):
    conn = promo_db.connect()
    try:
        brandkit_store.save(
            conn, BrandKit(business_name="우리가게", photos=["b.mp4"])
        )
    finally:
        conn.close()

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.run()

    assert not at.exception
    assert at.selectbox
    # AppTest 는 format_func 적용된 라벨을 options 로 보고한다
    assert at.selectbox[0].options == ["UI 테스트 (upbeat, hook/cta)"]


def test_page_renders_v2_narration_panel(isolated_env, monkeypatch):
    """v2 플랜 조회 시 실측 타임라인 패널(테이블+요약)이 예외 없이 그려진다."""
    conn = promo_db.connect()
    try:
        brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=["b.mp4"]))
    finally:
        conn.close()

    canned_plan = {
        "plan_id": "v2-plan",
        "template_id": "ui-test-v1",
        "subject": "우리가게",
        "materials": ["a.mp4", "b.mp4", "c.mp4"],
        "used_brand_count": 1,
        "photo_warning": False,
        "structural_gate": {"passed": True, "failures": [], "warnings": []},
        "approved_ready": True,
        "template_version": 2,
        "style_preset": "warm-food",
        "narration": {
            "total_s": 11.0,
            "sections": [
                {"role": "hook", "text": "훅", "target_s": 3.0, "measured_s": 3.0,
                 "drift_s": 0.0, "start_s": 0.0, "end_s": 3.0},
                {"role": "cta", "text": "행동 유도", "target_s": 8.0, "measured_s": 8.0,
                 "drift_s": 0.0, "start_s": 3.0, "end_s": 11.0},
            ],
        },
        "timeline_gate": {"passed": True, "failures": [], "warnings": []},
        "clip_seconds": [1.5, 1.5, 8.0],
        "headlines": ["우리가게 신메뉴", None],
        "status": "planned",
        "task_id": None,
    }
    monkeypatch.setattr(promo_api, "get_plan", lambda plan_id: canned_plan)

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.session_state["promo_plan_id"] = "v2-plan"
    at.run()

    assert not at.exception
    # 버전 배지가 서브헤더에 붙고, 실측 타임라인 요약/테이블이 그려진다.
    assert any("템플릿 v2" in s.value for s in at.subheader)
    assert any("실측 총 길이 11초" in c.value for c in at.caption)
    assert any("클립 3개" in c.value for c in at.caption)
    assert at.dataframe  # 섹션별 목표/실측/편차 테이블


UI_VARS_TEMPLATE = {
    "template_id": "ui-vars-v2",
    "name": "UI 변수 테스트",
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
    "caption_template": "{shop_name} {menu_name} 출시",
    "hashtags_base": ["테스트"],
}


def test_page_shows_variable_inputs_for_v2_template(isolated_env):
    """동적 변수가 있는 v2 템플릿을 고르면 변수 입력란이 나타난다."""
    conn = promo_db.connect()
    try:
        brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=["b.mp4"]))
    finally:
        conn.close()
    (isolated_env / "templates-data" / "ui-vars-v2.json").write_text(
        json.dumps(UI_VARS_TEMPLATE, ensure_ascii=False), encoding="utf-8"
    )

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.run()
    at.selectbox[0].set_value("ui-vars-v2").run()

    assert not at.exception
    # {menu_name} 변수 입력란이 렌더링된다 (라벨에 플레이스홀더명)
    assert any("menu_name" in (ti.label or "") for ti in at.text_input)


def test_page_shows_clip_preview_and_used_variables(isolated_env, monkeypatch):
    """플랜에 material_paths/variables 가 있으면 클립 미리보기 + 변수 캡션이 그려진다."""
    conn = promo_db.connect()
    try:
        brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=["b.mp4"]))
    finally:
        conn.close()

    canned_plan = {
        "plan_id": "v2-plan",
        "template_id": "ui-test-v1",
        "subject": "우리가게",
        "materials": ["a.mp4", "b.mp4"],
        "material_paths": ["/tmp/preview-a.mp4", "/tmp/preview-b.mp4"],
        "used_brand_count": 1,
        "photo_warning": False,
        "structural_gate": {"passed": True, "failures": [], "warnings": []},
        "approved_ready": True,
        "template_version": 2,
        "style_preset": "warm-food",
        "narration": None,
        "timeline_gate": None,
        "clip_seconds": [3.0, 8.0],
        "headlines": ["우리가게 신메뉴", None],
        "variables": {"menu_name": "매운떡볶이"},
        "required_variables": ["menu_name"],
        "status": "planned",
        "task_id": None,
    }
    monkeypatch.setattr(promo_api, "get_plan", lambda plan_id: canned_plan)

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.session_state["promo_plan_id"] = "v2-plan"
    at.run()

    assert not at.exception
    # 채워진 변수 캡션이 보인다
    assert any("menu_name=매운떡볶이" in c.value for c in at.caption)
    # 클립 미리보기 셀렉트박스 (템플릿 + 클립 = 2개)
    assert len(at.selectbox) == 2


def test_page_shows_caption_preview_and_missing_variables_warning(
    isolated_env, monkeypatch
):
    """승인 패널에 업로드 캡션 미리보기 + 미충전 변수 경고가 그려진다."""
    conn = promo_db.connect()
    try:
        brandkit_store.save(conn, BrandKit(business_name="우리가게", photos=["b.mp4"]))
    finally:
        conn.close()

    canned_plan = {
        "plan_id": "v2-plan",
        "template_id": "ui-test-v1",
        "subject": "우리가게",
        "materials": ["a.mp4"],
        "material_paths": [],
        "used_brand_count": 1,
        "photo_warning": False,
        "structural_gate": {"passed": True, "failures": [], "warnings": []},
        "approved_ready": True,
        "template_version": 2,
        "style_preset": "warm-food",
        "narration": None,
        "timeline_gate": None,
        "clip_seconds": [3.0],
        "headlines": [None],
        "variables": {},
        "required_variables": ["menu_name"],
        "missing_variables": ["menu_name"],
        "upload_caption": "우리가게 — UI 테스트",
        "status": "planned",
        "task_id": None,
    }
    monkeypatch.setattr(promo_api, "get_plan", lambda plan_id: canned_plan)

    at = AppTest.from_file(PAGE_PATH, default_timeout=15)
    at.session_state["promo_plan_id"] = "v2-plan"
    at.run()

    assert not at.exception
    # 미충전 변수 경고
    assert any("미충전 변수" in w.value and "menu_name" in w.value for w in at.warning)
    # 업로드 캡션 미리보기
    assert any("우리가게 — UI 테스트" in i.value for i in at.info)
