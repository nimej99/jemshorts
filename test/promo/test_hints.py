"""스크립트 프롬프트 힌트(build_script_prompt/char_budget) 테스트."""

import pytest

from app.promo.brandkit.models import BrandKit
from app.promo.research import (
    DEFAULT_CHARS_PER_SEC,
    ReferenceStats,
    build_script_prompt,
    char_budget,
)
from app.promo.templates.schema import validate_template

TEMPLATE_DATA = {
    "template_id": "hints-test-v1",
    "name": "힌트 테스트",
    "version": 1,
    "mood": "upbeat",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "시선을 붙잡으세요", "material_slot": "video"},
        {"role": "body", "duration_s": 12, "script_guide": "맛 포인트 소개", "material_slot": "any"},
        {"role": "cta", "duration_s": 5, "script_guide": "방문 유도", "material_slot": "photo"},
    ],
    "total_duration_range": [15, 30],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


@pytest.fixture()
def template():
    return validate_template(TEMPLATE_DATA)


@pytest.fixture()
def kit():
    return BrandKit(business_name="우리동네 분식", category="음식점", description="30년 전통")


def test_char_budget_uses_total_duration(template):
    # 20초 * 4.5자/초 = 90자
    assert char_budget(template, DEFAULT_CHARS_PER_SEC) == 90
    with pytest.raises(ValueError):
        char_budget(template, 0)


def test_prompt_without_reference(template, kit):
    prompt = build_script_prompt(template, kit)

    assert "우리동네 분식" in prompt
    assert "(음식점)" in prompt
    assert "30년 전통" in prompt
    assert "총 20초" in prompt
    assert "1. hook (3초): 시선을 붙잡으세요" in prompt
    assert "3. cta (5초): 방문 유도" in prompt
    assert "약 90자" in prompt
    assert "레퍼런스 실측" not in prompt


def test_prompt_with_reference_uses_measured_pacing(template, kit):
    reference = ReferenceStats(
        duration_s=18.0,
        hook_text="드디어 나왔다",
        total_chars=108,
        chars_per_sec=6.0,
        cue_count=5,
    )

    prompt = build_script_prompt(template, kit, reference)

    # 20초 * 6자/초 = 120자 (실측 페이싱 반영)
    assert "약 120자" in prompt
    assert "드디어 나왔다" in prompt
    assert "레퍼런스 실측" in prompt
    assert "베끼지 마세요" in prompt


def test_prompt_with_trend_keywords(template, kit):
    prompt = build_script_prompt(template, kit, trend_keywords=["동네 맛집", "폭염"])

    assert "[트렌드]" in prompt
    assert "동네 맛집, 폭염" in prompt
    assert "관련 없으면 무시하세요" in prompt


def test_prompt_without_trend_keywords_has_no_trend_block(template, kit):
    assert "[트렌드]" not in build_script_prompt(template, kit)


# --- 템플릿 v2 선택 필드 반영 -------------------------------------------------


def _v2_template(*, hook_fields: dict | None = None, **overrides):
    """version 2 템플릿 — hook 섹션에만 v2 필드를 얹을 수 있다."""
    data = {**TEMPLATE_DATA, "version": 2}
    data["structure"] = [dict(section) for section in TEMPLATE_DATA["structure"]]
    if hook_fields:
        data["structure"][0].update(hook_fields)
    data.update(overrides)
    return validate_template(data)


def test_char_budget_scales_with_template_voice_speed():
    """낭독이 빠르면 같은 길이에 더 많은 글자가 들어간다 (20초 * 4.5 * 1.2 = 108)."""
    template = _v2_template(voice={"speed": 1.2})

    assert char_budget(template, DEFAULT_CHARS_PER_SEC) == 108


def test_prompt_includes_section_feel_and_headline(kit):
    template = _v2_template(
        hook_fields={
            "feel": "설레는",
            "headline": {"template": "신메뉴 출시!", "show": True},
        }
    )

    prompt = build_script_prompt(template, kit)

    assert "1. hook (3초): 시선을 붙잡으세요 [톤: 설레는]" in prompt
    assert '화면 헤드라인: "신메뉴 출시!"' in prompt
    assert "그대로 반복하지 마세요" in prompt


def test_hidden_headline_is_not_prompted(kit):
    template = _v2_template(
        hook_fields={"headline": {"template": "숨김 배너", "show": False}}
    )

    prompt = build_script_prompt(template, kit)

    assert "숨김 배너" not in prompt
    assert "화면 헤드라인" not in prompt


# --- 템플릿 변수: 프롬프트가 [변수] 블록을 요구하고 응답을 파싱하는가 ---------

from app.promo.research import VARIABLES_MARKER, parse_script_response  # noqa: E402

V2_VARS_TEMPLATE = {
    "template_id": "hint-vars-v2",
    "name": "변수 힌트 테스트",
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


def test_prompt_requests_variables_block_when_required(kit):
    """동적 변수가 필요한 템플릿은 프롬프트가 [변수] 블록과 항목을 요구한다."""
    prompt = build_script_prompt(validate_template(V2_VARS_TEMPLATE), kit)

    assert VARIABLES_MARKER in prompt
    assert "- menu_name" in prompt
    assert "- highlight" in prompt
    # 변수 의미를 유추할 맥락(캡션/헤드라인 템플릿)을 함께 제시한다
    assert "캡션 템플릿 참고" in prompt
    assert "{shop_name} {menu_name} 출시!" in prompt  # 헤드라인 참고
    # shop_name 은 브랜드킷 키라 요구 항목이 아니다
    assert "- shop_name" not in prompt


def test_prompt_omits_variables_block_when_not_required(template, kit):
    """변수 불필요 템플릿은 기존처럼 '내레이션만' 지시 (하위호환)."""
    prompt = build_script_prompt(template, kit)

    assert VARIABLES_MARKER not in prompt
    assert "내레이션 문장만" in prompt


def test_parse_script_response_splits_narration_and_variables():
    text = (
        "드디어 나왔다, 신메뉴! 매콤합니다.\n"
        "[변수]\n"
        "menu_name: 매운 떡볶이\n"
        "highlight: 20% 할인"
    )

    narration, variables = parse_script_response(text, ["menu_name", "highlight"])

    assert narration == "드디어 나왔다, 신메뉴! 매콤합니다."
    assert variables == {"menu_name": "매운 떡볶이", "highlight": "20% 할인"}


def test_parse_script_response_no_marker_returns_empty_variables():
    """[변수] 블록이 없으면(구형 응답) 전체가 내레이션, 변수는 비운다."""
    narration, variables = parse_script_response("그냥 내레이션입니다.", ["menu_name"])

    assert narration == "그냥 내레이션입니다."
    assert variables == {}


def test_parse_script_response_ignores_unknown_and_empty():
    """required 에 없는 키(주입 방어)와 빈 값은 무시한다."""
    text = "[변수]\nmenu_name: 매운 떡볶이\nevil: 주입\nhighlight:   "

    narration, variables = parse_script_response(text, ["menu_name", "highlight"])

    assert narration == ""
    assert variables == {"menu_name": "매운 떡볶이"}  # evil/빈 highlight 제외


def test_parse_script_response_tolerates_bullets_and_quotes():
    text = "[변수]\n- menu_name: \"매운 떡볶이\"\n* highlight: 20% 할인"

    _, variables = parse_script_response(text, ["menu_name", "highlight"])

    assert variables == {"menu_name": "매운 떡볶이", "highlight": "20% 할인"}
