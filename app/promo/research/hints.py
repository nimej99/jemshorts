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
from app.promo.templates.variables import required_variables

# 레퍼런스가 없을 때의 기본 페이싱 (한국어 TTS 낭독 실측 통념치, 초당 글자수)
DEFAULT_CHARS_PER_SEC = 4.5

# LLM 응답에서 내레이션과 동적 변수 블록을 가르는 표지. build_script_prompt 가
# 요구하고 parse_script_response 가 파싱한다 (템플릿 변수 엔진 — PR "variables").
VARIABLES_MARKER = "[변수]"


def parse_script_response(
    text: str, required_keys: Sequence[str]
) -> tuple[str, dict[str, str]]:
    """LLM 응답을 (내레이션, 변수 dict) 로 분리한다.

    `[변수]` 블록이 있으면 그 뒤의 "키: 값" 줄을 파싱한다. 블록이 없으면
    (구형 응답/변수 불필요 템플릿) 전체를 내레이션으로 보고 변수는 비운다 —
    하위호환. `required_keys` 에 없는 키는 무시한다(프롬프트 주입 방어).
    """
    text = (text or "").strip()
    if VARIABLES_MARKER not in text:
        return text, {}

    narration_part, _, vars_part = text.partition(VARIABLES_MARKER)
    narration = narration_part.strip()
    required = set(required_keys)
    variables: dict[str, str] = {}
    for raw_line in vars_part.splitlines():
        line = raw_line.strip().lstrip("-•*·").strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip()
        if key in required and value:
            variables[key] = value
    return narration, variables

def char_budget(template: Template, chars_per_sec: float) -> int:
    """템플릿 총 길이와 페이싱으로 스크립트 글자수(공백 제외) 예산을 계산한다.

    템플릿 v2 `voice.speed` 가 있으면 낭독 속도만큼 예산을 비례 조정한다
    (같은 초 안에 더 빠르게 읽으면 더 많은 글자가 들어간다).
    """
    if chars_per_sec <= 0:
        raise ValueError(f"chars_per_sec 는 0보다 커야 합니다: {chars_per_sec}")
    speed = template.voice.speed if template.voice else 1.0
    return round(template.total_duration_s * chars_per_sec * speed)


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
        line = (
            f"{index}. {section.role} ({section.duration_s:g}초): {section.script_guide}"
        )
        if section.feel:
            line += f" [톤: {section.feel}]"
        lines.append(line)
        headline = section.headline
        if headline is not None and headline.show:
            lines.append(
                f'   화면 헤드라인: "{headline.template}" '
                "— 같은 문구를 내레이션에서 그대로 반복하지 마세요."
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
    required = required_variables(template)
    if not required:
        lines += [
            "",
            "출력은 내레이션 문장만, 섹션 구분 표시 없이 이어서 작성하세요.",
        ]
    else:
        lines += [
            "",
            "[출력 형식]",
            "1. 먼저 내레이션 문장을 섹션 구분 없이 이어서 작성하세요.",
            f'2. 그 다음 줄에 "{VARIABLES_MARKER}" 를 쓰고, 아래 항목을 '
            '"키: 값" 형식으로 한 줄씩 작성하세요.',
            "",
            "[변수 항목] 캡션/화면 헤드라인에 들어갈 값입니다:",
        ]
        lines += [f"- {name}" for name in required]
        # 캡션/헤드라인 템플릿을 맥락으로 제시해 각 변수의 의미를 유추하게 한다.
        lines.append(f'캡션 템플릿 참고: "{template.caption_template}"')
        headline_templates = [
            section.headline.template
            for section in template.structure
            if section.headline is not None and section.headline.show
        ]
        if headline_templates:
            lines.append(
                "헤드라인 참고: "
                + " / ".join(f'"{h}"' for h in headline_templates)
            )
        lines += ["", "출력 예시:", "내레이션 본문입니다. 문장을 이어갑니다.", VARIABLES_MARKER]
        lines += [f"{name}: (값)" for name in required]
    return "\n".join(lines)
