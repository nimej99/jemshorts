"""promo-shorts 소재 합성 계층 (브랜드 + 스톡 -> MPT 로컬 렌더 입력)."""

from app.promo.materials.compose import compose_materials
from app.promo.materials.retime import (
    RetimeError,
    RetimedClip,
    build_shot_filter,
    probe_dimensions,
    probe_duration,
    retime_material,
    retime_materials,
)

__all__ = [
    "RetimeError",
    "RetimedClip",
    "build_shot_filter",
    "compose_materials",
    "probe_dimensions",
    "probe_duration",
    "retime_material",
    "retime_materials",
]
