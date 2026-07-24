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
