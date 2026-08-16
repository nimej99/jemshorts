"""promo-shorts 레퍼런스 리서치 계층 (경쟁/참고 쇼츠 자막 수집·분석·프롬프트 힌트)."""

from app.promo.research.datalab import (
    DataLabFetchError,
    DataLabNotConfiguredError,
    fetch_demand,
)
from app.promo.research.gap import (
    GapFetchError,
    GapResult,
    gap_score,
    rank_keywords,
    youtube_supply,
)
from app.promo.research.hints import (
    DEFAULT_CHARS_PER_SEC,
    VARIABLES_MARKER,
    build_script_prompt,
    char_budget,
    parse_script_response,
)
from app.promo.research.material_prompts import (
    MaterialPrompt,
    MaterialPromptError,
    build_material_prompts,
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
    "DataLabFetchError",
    "DataLabNotConfiguredError",
    "GapFetchError",
    "GapResult",
    "MaterialPrompt",
    "MaterialPromptError",
    "DEFAULT_CHARS_PER_SEC",
    "ReferenceStats",
    "ResearchToolMissingError",
    "analyze_reference",
    "build_material_prompts",
    "build_script_prompt",
    "char_budget",
    "parse_script_response",
    "VARIABLES_MARKER",
    "fetch_demand",
    "fetch_subtitles",
    "gap_score",
    "parse_vtt",
    "rank_keywords",
    "youtube_supply",
]
