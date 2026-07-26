"""promo-shorts 트렌드 템플릿 계층 (스키마 + 큐레이션 fetch)."""

from app.promo.templates.schema import (
    Headline,
    Section,
    Shot,
    Template,
    TemplateValidationError,
    TimingSpec,
    VoiceSpec,
    load_all,
    load_template,
    validate_template,
)

__all__ = [
    "Headline",
    "Section",
    "Shot",
    "Template",
    "TemplateValidationError",
    "TimingSpec",
    "VoiceSpec",
    "load_all",
    "load_template",
    "validate_template",
]
