"""소재 생성 프롬프트(app.promo.research.material_prompts) 테스트.

문자열 조립만 검증한다 — 외부 생성 도구 호출 없음.
"""

import pytest

from app.promo.brandkit.models import BrandKit
from app.promo.research import (
    MaterialPromptError,
    build_material_prompts,
)
from app.promo.templates.schema import TemplateValidationError, validate_template
from app.promo.templates.style_presets import (
    STYLE_PRESETS,
    VALID_STYLE_PRESETS,
    get_style_preset,
)

TEMPLATE_DATA = {
    "template_id": "material-prompt-v2",
    "name": "소재 프롬프트 테스트",
    "version": 2,
    "mood": "upbeat",
    "style_preset": "warm-food",
    "structure": [
        {
            "role": "hook",
            "duration_s": 3,
            "script_guide": "신메뉴 클로즈업으로 시선을 붙잡으세요",
            "material_slot": "video",
            "feel": "따뜻하고 식욕을 돋우는",
            "shots": [
                {"kind": "wide", "motion": "push-in"},
                {"kind": "cutin", "crop": "center-zoom", "motion": "drift"},
            ],
        },
        {
            "role": "body",
            "duration_s": 12,
            "script_guide": "조리 과정을 보여주세요",
            "material_slot": "any",
        },
        {
            "role": "cta",
            "duration_s": 5,
            "script_guide": "방문을 유도하세요",
            "material_slot": "photo",
        },
    ],
    "total_duration_range": [15, 30],
    "caption_template": "{shop_name}",
    "hashtags_base": ["테스트"],
}


@pytest.fixture()
def kit():
    return BrandKit(business_name="우리분식", category="음식점")


def _template(**overrides):
    data = {**TEMPLATE_DATA, **overrides}
    return validate_template(data)


# --- 프리셋 레지스트리 ---------------------------------------------------------


def test_every_preset_has_prefix_and_negative():
    """프리셋은 전부 스타일 프리픽스와 네거티브를 갖는다 (빈 프리셋 금지)."""
    assert VALID_STYLE_PRESETS
    for key in VALID_STYLE_PRESETS:
        preset = STYLE_PRESETS[key]
        assert preset.key == key
        assert preset.name.strip()
        assert "9:16" in preset.prompt_prefix  # 세로 숏폼 규격이 프리픽스에 박혀 있다
        assert preset.negative_prompt.strip()


def test_unknown_preset_lists_available_keys():
    with pytest.raises(KeyError, match="사용 가능"):
        get_style_preset("no-such-preset")


def test_schema_rejects_unknown_style_preset():
    """오타가 소재 생성 단계까지 조용히 흘러가면 톤이 통째로 어긋난다."""
    with pytest.raises(TemplateValidationError, match="style_preset"):
        validate_template({**TEMPLATE_DATA, "style_preset": "warm-foods"})


def test_schema_accepts_every_registered_preset():
    for key in VALID_STYLE_PRESETS:
        assert validate_template({**TEMPLATE_DATA, "style_preset": key}).style_preset == key


# --- 프롬프트 조립 -------------------------------------------------------------


def test_prompt_per_shot_with_style_prefix_and_scene(kit):
    prompts = build_material_prompts(_template(), kit)

    # hook 2컷 + body 1컷 + cta 1컷
    assert [(p.section_index, p.shot_index) for p in prompts] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (2, 0),
    ]
    prefix = STYLE_PRESETS["warm-food"].prompt_prefix
    assert all(p.prompt.startswith(prefix) for p in prompts)
    assert all(p.negative_prompt == STYLE_PRESETS["warm-food"].negative_prompt for p in prompts)

    hook_wide, hook_cutin = prompts[0], prompts[1]
    assert "우리분식 (음식점)" in hook_wide.prompt
    assert "신메뉴 클로즈업으로 시선을 붙잡으세요" in hook_wide.prompt
    assert "mood: 따뜻하고 식욕을 돋우는" in hook_wide.prompt
    assert "wide establishing shot" in hook_wide.prompt
    assert "slow push-in" in hook_wide.prompt
    # 컷인은 프레이밍/모션 지시가 달라야 의미가 있다.
    assert "tight detail cut-in" in hook_cutin.prompt
    assert "centered close crop" in hook_cutin.prompt
    assert "subtle drifting camera" in hook_cutin.prompt


def test_section_without_shots_gets_single_wide_prompt(kit):
    prompts = build_material_prompts(_template(), kit)
    body = prompts[2]

    assert body.kind == "wide"
    assert "wide establishing shot" in body.prompt
    # material_slot 이 any 가 아니면 자산 종류를 명시한다.
    assert "asset type" not in body.prompt
    assert "asset type: photo" in prompts[3].prompt


def test_missing_style_preset_is_rejected(kit):
    """스타일 없이 생성하면 컷마다 톤이 제각각 — 기본값으로 얼버무리지 않는다."""
    data = {key: value for key, value in TEMPLATE_DATA.items() if key != "style_preset"}

    with pytest.raises(MaterialPromptError, match="style_preset"):
        build_material_prompts(validate_template(data), kit)
