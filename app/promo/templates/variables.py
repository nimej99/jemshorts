"""템플릿 변수 엔진 — caption_template / headline 플레이스홀더의 출처를 통일한다.

플레이스홀더 값의 출처는 두 가지다.

- **브랜드킷 필드** (`shop_name`, `category`, `address`, ...) — 가게 단위의
  안정 값. `brandkit_context` 가 채운다.
- **동적 변수** (`menu_name`, `event_name`, `discount`, ...) — 영상마다 다른
  값. 스크립트를 쓰는 LLM 이 제공한다 (`research.hints` 가 프롬프트에 요구하고
  `api`/`scheduler` 가 응답에서 파싱). 호출자가 직접 줄 수도 있다.

`template_context` = 브랜드킷 컨텍스트 + 동적 변수. 이걸로 `caption_template`
과 `headline` 을 채운다. 못 채운 플레이스홀더는 `{menu_name}` 처럼 원문이
남는다 — 조용히 빈칸으로 지우면 운영자가 누락을 못 본다.
"""

from __future__ import annotations

import re

from app.promo.brandkit.models import BrandKit
from app.promo.templates.schema import Template

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# 브랜드킷에서 채우는 컨텍스트 키 (가게 단위 안정 값). required_variables 가
# "동적 변수"를 가려내는 기준이다 — 이 키들은 LLM 이 제공할 필요가 없다.
BRANDKIT_KEYS = frozenset(
    {
        "shop_name",
        "business_name",
        "category",
        "description",
        "address",
        "phone",
        "sns_url",
        "template_name",
    }
)


class _SafeDict(dict):
    """미지정 키는 원문(`{key}`)을 유지한다 (조용한 빈칸 금지)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def placeholder_names(text: str) -> list[str]:
    """텍스트 안 `{placeholder}` 이름을 출현 순서대로(중복 제거) 반환한다."""
    names: list[str] = []
    for name in _PLACEHOLDER_RE.findall(text or ""):
        if name not in names:
            names.append(name)
    return names


def fill_placeholders(text: str, context: dict) -> str:
    """플레이스홀더를 컨텍스트로 채운다. 미지정 키는 원문이 남는다."""
    try:
        return (text or "").format_map(_SafeDict(context))
    except (IndexError, ValueError) as exc:
        raise ValueError(f"플레이스홀더 형식이 잘못됐습니다: {text} ({exc})") from exc


def brandkit_context(template: Template, brandkit: BrandKit) -> dict:
    """브랜드킷에서 채우는 컨텍스트 (가게 단위 안정 값)."""
    return {
        "shop_name": brandkit.business_name,
        "business_name": brandkit.business_name,
        "category": brandkit.category or "",
        "description": brandkit.description or "",
        "address": brandkit.address or "",
        "phone": brandkit.phone or "",
        "sns_url": brandkit.sns_url or "",
        "template_name": template.name,
    }


def template_context(
    template: Template, brandkit: BrandKit, variables: dict | None = None
) -> dict:
    """브랜드킷 컨텍스트 + 동적 변수를 합친 전체 치환 컨텍스트."""
    context = brandkit_context(template, brandkit)
    context.update({key: str(value) for key, value in (variables or {}).items()})
    return context


def _all_placeholders(template: Template) -> list[str]:
    """caption_template + 모든 섹션 헤드라인이 참조하는 플레이스홀더 전체."""
    names = placeholder_names(template.caption_template)
    for section in template.structure:
        if section.headline is not None:
            for name in placeholder_names(section.headline.template):
                if name not in names:
                    names.append(name)
    return names


def required_variables(template: Template) -> list[str]:
    """템플릿이 참조하지만 브랜드킷으로 채워지지 않는 동적 변수 이름.

    LLM(또는 호출자)이 제공해야 한다. caption_template 과 헤드라인의
    플레이스홀더에서 브랜드킷 키를 뺀 나머지다.
    """
    return [name for name in _all_placeholders(template) if name not in BRANDKIT_KEYS]


def render_caption(
    template: Template, brandkit: BrandKit, variables: dict | None = None
) -> tuple[str, list[str]]:
    """`caption_template` 를 채운다.

    반환: (채워진 캡션, 비어 있는 플레이스홀더 이름 목록). unfilled 가 비어있지
    않으면 캡션에 `{menu_name}` 같은 원문이 남았다는 뜻 — 호출자는 이걸
    그대로 공개하지 말고 폴백해야 한다.
    """
    context = template_context(template, brandkit, variables)
    caption = fill_placeholders(template.caption_template, context)
    unfilled = [
        name
        for name in placeholder_names(template.caption_template)
        if not str(context.get(name, "")).strip()
    ]
    return caption, unfilled
