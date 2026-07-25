"""레퍼런스 실측 -> 스크립트 생성 프롬프트 힌트.

템플릿 섹션 가이드 + 브랜드킷 + (선택) 레퍼런스 페이싱 실측을 결합해
LLM 에 넘길 한국어 스크립트 생성 프롬프트를 만든다. 어떤 LLM 을 쓸지는
호출자 몫이다 (코어 llm 서비스 포함) — 이 모듈은 순수 문자열 조립만 한다.
"""

from __future__ import annotations

from typing import Sequence

from app.promo.brandkit.models import BrandKit
from app.promo.research.ingest import ReferenceStats
from app.promo.templates.schema import Template

# 레퍼런스가 없을 때의 기본 페이싱 (한국어 TTS 낭독 실측 통념치, 초당 글자수)
DEFAULT_CHARS_PER_SEC = 4.5


def char_budget(template: Template, chars_per_sec: float) -> int:
    """템플릿 총 길이와 페이싱으로 스크립트 글자수(공백 제외) 예산을 계산한다."""
    if chars_per_sec <= 0:
        raise ValueError(f"chars_per_sec 는 0보다 커야 합니다: {chars_per_sec}")
    return round(template.total_duration_s * chars_per_sec)


def build_script_prompt(
    template: Template,
    brandkit: BrandKit,
    reference: ReferenceStats | None = None,
    trend_keywords: Sequence[str] | None = None,
) -> str:
    """홍보 쇼츠 스크립트 생성용 한국어 프롬프트를 만든다.

    - 섹션(hook/body/cta)별 길이와 가이드를 명시한다.
    - reference 가 있으면 실측 페이싱(초당 글자수)과 훅 문구를 참고로 넣는다.
      없으면 기본 페이싱으로 글자수 예산만 제시한다.
    - trend_keywords 가 있으면 참고용으로 제시하되, 억지 반영은 금지 문구로
      막는다 (트렌드는 부가 신호 — 가게 맥락이 우선).
    """
    pacing = reference.chars_per_sec if reference else DEFAULT_CHARS_PER_SEC
    budget = char_budget(template, pacing)

    lines = [
        "당신은 소상공인 홍보 쇼츠 스크립트 작가입니다.",
        "아래 조건에 맞는 한국어 내레이션 스크립트를 작성하세요.",
        "",
        f"[가게 정보] {brandkit.business_name}"
        + (f" ({brandkit.category})" if brandkit.category else ""),
    ]
    if brandkit.description:
        lines.append(f"[소개] {brandkit.description}")
    lines += [
        "",
        f"[구성] 총 {template.total_duration_s:g}초, 섹션 순서대로:",
    ]
    for index, section in enumerate(template.structure, start=1):
        lines.append(
            f"{index}. {section.role} ({section.duration_s:g}초): {section.script_guide}"
        )
    lines += [
        "",
        f"[분량] 공백 제외 약 {budget}자 (초당 {pacing:g}자 낭독 기준). "
        "섹션 길이 비율대로 배분하세요.",
    ]
    if reference is not None:
        lines += [
            "",
            "[레퍼런스 실측] 참고 영상 분석:",
            f"- 길이 {reference.duration_s:g}초, 페이싱 초당 {reference.chars_per_sec:g}자",
            f"- 훅(첫 3초) 문구: \"{reference.hook_text}\"",
            "훅의 톤과 속도감만 참고하고 문구를 베끼지 마세요.",
        ]
    if trend_keywords:
        lines += [
            "",
            "[트렌드] 최근 검색 급상승 키워드 (참고): "
            + ", ".join(trend_keywords),
            "가게/업종과 자연스럽게 연결될 때만 활용하고, 관련 없으면 무시하세요.",
        ]
    lines += [
        "",
        "출력은 내레이션 문장만, 섹션 구분 표시 없이 이어서 작성하세요.",
    ]
    return "\n".join(lines)
