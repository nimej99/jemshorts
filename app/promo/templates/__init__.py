"""promo-shorts 트렌드 템플릿 계층 (스키마 + 큐레이션 fetch)."""

from app.promo.templates.schema import (
    Section,
    Template,
    TemplateValidationError,
    load_all,
    load_template,
    validate_template,
)

__all__ = [
    "Section",
    "Template",
    "TemplateValidationError",
    "load_all",
    "load_template",
    "validate_template",
]
