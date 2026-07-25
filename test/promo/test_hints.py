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
