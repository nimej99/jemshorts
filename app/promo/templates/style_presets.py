"""소재 생성 스타일 프리셋 (템플릿 v2 `style_preset`).

docs/TEMPLATE_V2_DESIGN.md §6 (e). vox-director 의 `keyframe_prompt` 구조를
그대로 가져왔다: **고정 스타일 프리픽스(모든 샷 동일) + SCENE 슬롯(샷별 가변)**.
프리셋은 프리픽스만 정의하고, 장면 묘사는 섹션 가이드/톤에서 만들어진다.

닫힌 어휘다 — 템플릿이 모르는 프리셋 키를 쓰면 스키마 검증에서 막힌다
(오타가 소재 생성 단계에서 조용히 무시되면 결과물 톤이 통째로 어긋난다).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StylePreset:
    """소재 생성 프롬프트의 고정 스타일 문단."""

    key: str
    name: str  # 한국어 표시 이름 (승인 UI/문서용)
    prompt_prefix: str
    negative_prompt: str


_NEGATIVE_COMMON = (
    "watermark, logo overlay, text artifacts, distorted hands, extra fingers, "
    "lowres, jpeg artifacts, oversaturated skin"
)

STYLE_PRESETS: dict[str, StylePreset] = {
    "clean-product": StylePreset(
        key="clean-product",
        name="깔끔한 제품 컷",
        prompt_prefix=(
            "clean commercial product photography, seamless neutral backdrop, "
            "soft diffused key light with gentle falloff, shallow depth of field, "
            "crisp focus on the product, vertical 9:16 framing"
        ),
        negative_prompt=f"cluttered background, harsh shadows, {_NEGATIVE_COMMON}",
    ),
    "warm-food": StylePreset(
        key="warm-food",
        name="따뜻한 음식 컷",
        prompt_prefix=(
            "appetizing food photography, warm tungsten-toned lighting, "
            "steam and fresh texture detail, wooden table surface, "
            "shallow depth of field, vertical 9:16 framing"
        ),
        negative_prompt=f"cold blue tint, plastic looking food, {_NEGATIVE_COMMON}",
    ),
    "bright-lifestyle": StylePreset(
        key="bright-lifestyle",
        name="밝은 일상 컷",
        prompt_prefix=(
            "bright natural daylight lifestyle photography, airy interior, "
            "candid moment, soft window light, pastel color palette, "
            "vertical 9:16 framing"
        ),
        negative_prompt=f"dark underexposed scene, heavy vignette, {_NEGATIVE_COMMON}",
    ),
    "night-neon": StylePreset(
        key="night-neon",
        name="밤/네온 무드",
        prompt_prefix=(
            "night city mood, neon sign reflections, moody low-key lighting, "
            "wet asphalt highlights, cinematic teal and magenta grade, "
            "vertical 9:16 framing"
        ),
        negative_prompt=f"flat daylight, washed out colors, {_NEGATIVE_COMMON}",
    ),
    "calm-space": StylePreset(
        key="calm-space",
        name="차분한 공간 컷",
        prompt_prefix=(
            "calm interior space photography, minimal composition, "
            "muted earth tones, soft ambient light, slow quiet atmosphere, "
            "vertical 9:16 framing"
        ),
        negative_prompt=f"busy crowd, cluttered props, {_NEGATIVE_COMMON}",
    ),
}

VALID_STYLE_PRESETS = tuple(STYLE_PRESETS)


def get_style_preset(key: str) -> StylePreset:
    """프리셋을 가져온다. 모르는 키면 사용 가능한 키를 알려주며 실패한다."""
    try:
        return STYLE_PRESETS[key]
    except KeyError as exc:
        raise KeyError(
            f"알 수 없는 style_preset '{key}' (사용 가능: {', '.join(VALID_STYLE_PRESETS)})"
        ) from exc
