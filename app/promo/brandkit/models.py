"""브랜드킷 데이터 모델.

BrandKit 은 가게(사장님) 브랜드 정보 한 세트다. 크롤 결과(crawl),
수동 입력(manual), 크롤+수동 병합(mixed) 세 가지 출처(source)를 가진다.

photo_warning 은 저장 필드가 아니라 photos 로부터 파생되는 UI 플래그다:
photos 가 빈 리스트면 True — 렌더링 단계에서 스톡 이미지로 대체되며
품질 경고를 노출해야 함을 의미한다. from_dict 는 직렬화된 값이 있어도
무시하고 photos 기준으로 재계산한다 (불일치 방지).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

VALID_SOURCES = ("crawl", "manual", "mixed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class BrandKit:
    business_name: str = ""
    category: str = ""
    description: str = ""
    address: str = ""
    phone: str = ""
    sns_url: str = ""
    primary_color: str | None = None  # hex 문자열 (예: "#ff6600"), 선택
    logo_path: str | None = None  # 선택
    photos: list[str] = field(default_factory=list)
    source: str = "manual"  # "crawl" | "manual" | "mixed"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self):
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"source '{self.source}' 은 유효하지 않습니다 "
                f"(허용: {', '.join(VALID_SOURCES)})"
            )

    @property
    def photo_warning(self) -> bool:
        """photos 가 비어 있으면 True (스톡 대체 + 품질 경고 UI 플래그)."""
        return not self.photos

    def to_dict(self) -> dict:
        return {
            "business_name": self.business_name,
            "category": self.category,
            "description": self.description,
            "address": self.address,
            "phone": self.phone,
            "sns_url": self.sns_url,
            "primary_color": self.primary_color,
            "logo_path": self.logo_path,
            "photos": list(self.photos),
            "source": self.source,
            "photo_warning": self.photo_warning,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BrandKit":
        """dict 로부터 복원한다. photo_warning 은 파생값이므로 무시(재계산)."""
        kwargs = {
            key: data[key]
            for key in (
                "business_name",
                "category",
                "description",
                "address",
                "phone",
                "sns_url",
                "primary_color",
                "logo_path",
                "source",
                "created_at",
                "updated_at",
            )
            if key in data
        }
        kwargs["photos"] = list(data.get("photos") or [])
        return cls(**kwargs)

    def touched(self, **changes) -> "BrandKit":
        """updated_at 을 현재 시각으로 갱신한 사본을 반환한다."""
        return replace(self, updated_at=_now_iso(), **changes)
