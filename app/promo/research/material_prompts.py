"""소재 생성 프롬프트 (템플릿 v2 `style_preset` + 섹션/샷 지시).

docs/TEMPLATE_V2_DESIGN.md §6 (e). 브랜드 소재가 모자랄 때 사장님이
ComfyUI/외부 생성 도구에 그대로 붙여넣을 수 있는 컷별 프롬프트를 만든다.

구조는 vox-director `keyframe_prompt` 와 동형이다:
**고정 스타일 프리픽스(모든 컷 동일) + SCENE 슬롯(컷별 가변)**.
스타일은 템플릿 `style_preset` 이 정하고, 장면은 섹션 가이드/톤(`feel`)과
샷 지시(kind/crop/motion)에서 나온다.

문자열 조립만 한다 — 이미지 생성 자체는 호출자(외부 도구) 몫이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.promo.brandkit.models import BrandKit
from app.promo.templates.schema import Section, Shot, Template
from app.promo.templates.style_presets import StylePreset, get_style_preset

# 샷 지시 -> 영어 카메라 표현 (생성 도구가 알아듣는 어휘로 옮긴다)
_KIND_PHRASES = {
    "wide": "wide establishing shot",
    "cutin": "tight detail cut-in",
}
_CROP_PHRASES = {
    "center-zoom": "centered close crop",
    "top": "upper-frame crop",
    "bottom": "lower-frame crop",
    "left": "left-side crop",
    "right": "right-side crop",
}
_MOTION_PHRASES = {
    "static": "locked-off camera",
    "push-in": "slow push-in",
    "pull-out": "slow pull-out",
    "drift": "subtle drifting camera",
    "pan-left": "slow pan to the left",
    "pan-right": "slow pan to the right",
}


class MaterialPromptError(ValueError):
    """소재 프롬프트 생성 실패 (스타일 프리셋 미선언 등)."""


@dataclass(frozen=True)
class MaterialPrompt:
    """컷 하나에 대응하는 소재 생성 프롬프트."""

    section_index: int
    shot_index: int
    role: str
    kind: str
    prompt: str
    negative_prompt: str


def _scene_line(
    section: Section, shot: Shot | None, brandkit: BrandKit, index: int
) -> str:
    """SCENE 슬롯 문장 — 가게 맥락 + 섹션 의도 + 카메라 지시."""
    subject = brandkit.business_name
    if brandkit.category:
        subject += f" ({brandkit.category})"

    parts = [f"SCENE: {subject}", f"section {index + 1} ({section.role})"]
    parts.append(section.script_guide.strip())
    if section.feel:
        parts.append(f"mood: {section.feel}")
    if section.material_slot != "any":
        parts.append(f"asset type: {section.material_slot}")
    if shot is not None:
        parts.append(_KIND_PHRASES.get(shot.kind, shot.kind))
        crop = shot.crop or ("center-zoom" if shot.kind == "cutin" else None)
        if crop:
            parts.append(_CROP_PHRASES.get(crop, crop))
        if shot.motion:
            parts.append(_MOTION_PHRASES.get(shot.motion, shot.motion))
    else:
        parts.append(_KIND_PHRASES["wide"])
    return " | ".join(part for part in parts if part)


def build_material_prompts(
    template: Template, brandkit: BrandKit
) -> list[MaterialPrompt]:
    """템플릿의 컷 순서대로 소재 생성 프롬프트를 만든다.

    `style_preset` 이 없는 템플릿은 MaterialPromptError — 스타일이 정해지지
    않은 채 생성하면 컷마다 톤이 제각각이 된다 (기본값으로 얼버무리지 않는다).
    """
    if not template.style_preset:
        raise MaterialPromptError(
            "템플릿에 style_preset 이 없습니다 "
            "(version 2 + style_preset 선언이 필요합니다)"
        )
    preset: StylePreset = get_style_preset(template.style_preset)

    prompts: list[MaterialPrompt] = []
    for index, section in enumerate(template.structure):
        shots: tuple[Shot | None, ...] = section.shots or (None,)
        for shot_index, shot in enumerate(shots):
            prompts.append(
                MaterialPrompt(
                    section_index=index,
                    shot_index=shot_index,
                    role=section.role,
                    kind=shot.kind if shot else "wide",
                    prompt=(
                        f"{preset.prompt_prefix}. "
                        f"{_scene_line(section, shot, brandkit, index)}"
                    ),
                    negative_prompt=preset.negative_prompt,
                )
            )
    return prompts
