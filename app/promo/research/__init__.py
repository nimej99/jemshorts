"""promo-shorts 레퍼런스 리서치 계층 (경쟁/참고 쇼츠 자막 수집·분석)."""

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
    "ReferenceStats",
    "ResearchToolMissingError",
    "analyze_reference",
    "fetch_subtitles",
    "parse_vtt",
]
