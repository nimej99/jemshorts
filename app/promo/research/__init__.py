"""promo-shorts 레퍼런스 리서치 계층 (경쟁/참고 쇼츠 자막 수집·분석·프롬프트 힌트)."""

from app.promo.research.hints import (
    DEFAULT_CHARS_PER_SEC,
    build_script_prompt,
    char_budget,
)
from app.promo.research.ingest import (
    Cue,
    ReferenceStats,
    ResearchToolMissingError,
    analyze_reference,
    fetch_subtitles,
    parse_vtt,
)

__all__ = [
    "Cue",
    "DEFAULT_CHARS_PER_SEC",
    "ReferenceStats",
    "ResearchToolMissingError",
    "analyze_reference",
    "build_script_prompt",
    "char_budget",
    "fetch_subtitles",
    "parse_vtt",
]
