"""템플릿 변수 엔진(app.promo.templates.variables) 테스트.

caption_template / headline 플레이스홀더의 출처 통일 — 브랜드킷 필드 vs
LLM/호출자가 제공하는 동적 변수.
"""

import pytest

from app.promo.brandkit.models import BrandKit
from app.promo.pipeline import RenderPlan, headline_texts, upload_caption
from app.promo.quality import GateResult
from app.promo.templates.schema import validate_template
from app.promo.templates.variables import (
    brandkit_context,
    fill_placeholders,
    placeholder_names,
    render_caption,
    required_variables,
    template_context,
)

TEMPLATE_DATA = {
    "template_id": "vars-test-v2",
    "name": "변수 테스트",
    "version": 2,
    "mood": "upbeat",
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "훅",
            "material_slot": "any",
            "headline": {"template": "{shop_name} {menu_name} 출시!", "show": True},
        },
        {"role": "cta", "duration_s": 8, "script_guide": "cta", "material_slot": "any"},
    ],
    "total_duration_range": [10, 15],
    "caption_template": "{shop_name} 신메뉴 '{menu_name}' 출시! {highlight}",
    "hashtags_base": ["테스트"],
}


@pytest.fixture()
def template():
    return validate_template(TEMPLATE_DATA)


@pytest.fixture()
def kit():
    return BrandKit(business_name="우리분식", category="음식점", address="서울시 마포구")


def _plan(template, variables):
    return RenderPlan(
        plan_id="p1",
        template=template,
        script="스크립트",
        materials=(),
        used_brand_count=1,
        photo_warning=False,
        structural=GateResult(passed=True),
        subject="우리분식 — 변수 테스트",
        variables=variables,
    )


# --- 플레이스홀더 추출 ---------------------------------------------------------


def test_placeholder_names_dedupes_in_order():
    assert placeholder_names("{a} {b} {a} 텍스트 {c}") == ["a", "b", "c"]
    assert placeholder_names("플레이스홀더 없음") == []
    assert placeholder_names("") == []


def test_required_variables_excludes_brandkit_keys(template):
    """shop_name 은 브랜드킷 값, menu_name/highlight 는 동적 변수로 가려낸다."""
    assert required_variables(template) == ["menu_name", "highlight"]


def test_required_variables_includes_headline_placeholders(template):
    """caption_template 뿐 아니라 헤드라인의 플레이스홀더도 합친다."""
    assert "menu_name" in required_variables(template)  # 헤드라인에도 등장


def test_required_variables_empty_when_only_brandkit_keys():
    data = {**TEMPLATE_DATA, "caption_template": "{shop_name} ({category})"}
    data["structure"] = [dict(s) for s in TEMPLATE_DATA["structure"]]
    data["structure"][0]["headline"] = {"template": "{shop_name}", "show": True}

    assert required_variables(validate_template(data)) == []


# --- 컨텍스트 조립 -------------------------------------------------------------


def test_brandkit_context_maps_fields(template, kit):
    context = brandkit_context(template, kit)

    assert context["shop_name"] == "우리분식"
    assert context["category"] == "음식점"
    assert context["address"] == "서울시 마포구"
    assert context["template_name"] == "변수 테스트"


def test_template_context_merges_variables_over_brandkit(template, kit):
    context = template_context(template, kit, {"menu_name": "매운떡볶이"})

    assert context["shop_name"] == "우리분식"  # 브랜드킷
    assert context["menu_name"] == "매운떡볶이"  # 동적 변수


def test_fill_placeholders_keeps_unknown_literal():
    assert fill_placeholders("{a}와 {b}", {"a": "갑"}) == "갑와 {b}"


def test_fill_placeholders_rejects_malformed():
    with pytest.raises(ValueError, match="형식"):
        fill_placeholders("{unclosed", {"unclosed": "x"})


# --- 캡션 렌더링 ---------------------------------------------------------------


def test_render_caption_full(template, kit):
    caption, unfilled = render_caption(
        template, kit, {"menu_name": "매운떡볶이", "highlight": "20% 할인"}
    )

    assert caption == "우리분식 신메뉴 '매운떡볶이' 출시! 20% 할인"
    assert unfilled == []


def test_render_caption_reports_unfilled(template, kit):
    """변수가 없으면 unfilled 에 남아 호출자가 폴백할 수 있게 한다."""
    caption, unfilled = render_caption(template, kit)

    assert "{menu_name}" in caption  # 원문 유지 (조용한 빈칸 금지)
    assert unfilled == ["menu_name", "highlight"]


def test_render_caption_partial_variables(template, kit):
    caption, unfilled = render_caption(template, kit, {"menu_name": "매운떡볶이"})

    assert "매운떡볶이" in caption
    assert unfilled == ["highlight"]


# --- 업로드 캡션 폴백 ----------------------------------------------------------


def test_upload_caption_uses_filled_caption(template, kit):
    plan = _plan(template, {"menu_name": "매운떡볶이", "highlight": "20% 할인"})

    assert upload_caption(plan, kit) == "우리분식 신메뉴 '매운떡볶이' 출시! 20% 할인"


def test_upload_caption_falls_back_to_subject_when_unfilled(template, kit):
    """자동 공개 캡션에 `{menu_name}` 이 찍히면 안 되므로 subject 로 폴백."""
    plan = _plan(template, {})

    assert upload_caption(plan, kit) == "우리분식 — 변수 테스트"


# --- 헤드라인 ------------------------------------------------------------------


def test_headline_texts_fill_variables(template, kit):
    texts = headline_texts(template, kit, {"menu_name": "매운떡볶이"})

    assert texts[0] == "우리분식 매운떡볶이 출시!"
    assert texts[1] is None  # cta 는 헤드라인 없음


def test_headline_texts_skip_unfilled_banner(template, kit):
    """동적 변수가 미충전이면 원문 배너 대신 배너를 생략한다 (None).

    영상에 `{menu_name}` 같은 원문이 박히면 안 되므로 — 자동 공개 안전장치.
    """
    texts = headline_texts(template, kit)

    assert texts[0] is None  # {menu_name} 미충전 → 배너 생략
