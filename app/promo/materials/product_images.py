"""상품 이미지 소재 검증.

첫 이미지는 상품 선별 결과에서 받은 대표 이미지여야 한다. 나머지 후보는
대표 이미지와 색상 분포가 충분히 가까운 경우에만 같은 상품의 변형 이미지로
인정한다. 상세 페이지 전체 ``img`` 수집은 추천상품까지 섞이므로 금지한다.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MIN_SIDE_PX = 500
MIN_HISTOGRAM_SIMILARITY = 0.75


class ProductImageError(ValueError):
    pass


@dataclass(frozen=True)
class ProductImageCheck:
    path: str
    similarity: float
    accepted: bool
    reason: str = ""


def _normalized_histogram(path: str) -> tuple[float, ...]:
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            if min(image.size) < MIN_SIDE_PX:
                raise ProductImageError(
                    f"상품 이미지 최소 변이 {MIN_SIDE_PX}px 미만입니다: "
                    f"{path} ({image.width}x{image.height})"
                )
            values = image.resize((64, 64)).histogram()
    except (OSError, UnidentifiedImageError) as exc:
        raise ProductImageError(f"상품 이미지를 열 수 없습니다: {path}") from exc

    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ProductImageError(f"빈 상품 이미지입니다: {path}")
    return tuple(value / norm for value in values)


def validate_product_images(
    primary_image: str,
    candidates: list[str],
    *,
    min_similarity: float = MIN_HISTOGRAM_SIMILARITY,
) -> tuple[list[str], list[ProductImageCheck]]:
    """대표 이미지와 일치하는 소재만 반환한다.

    후보가 모두 탈락해도 대표 이미지는 유지한다. 템플릿의 크롭/줌 샷으로
    대표 이미지 하나를 재사용하는 편이 타 상품을 노출하는 것보다 안전하다.
    """
    primary = os.path.abspath(primary_image)
    if not os.path.isfile(primary):
        raise ProductImageError(f"대표 상품 이미지가 없습니다: {primary}")

    baseline = _normalized_histogram(primary)
    accepted = [primary]
    checks: list[ProductImageCheck] = [
        ProductImageCheck(path=primary, similarity=1.0, accepted=True)
    ]
    seen = {primary}

    for candidate in candidates:
        path = os.path.abspath(candidate)
        if path in seen:
            continue
        seen.add(path)
        if not os.path.isfile(path):
            checks.append(
                ProductImageCheck(path=path, similarity=0.0, accepted=False, reason="파일 없음")
            )
            continue
        try:
            histogram = _normalized_histogram(path)
        except ProductImageError as exc:
            checks.append(
                ProductImageCheck(path=path, similarity=0.0, accepted=False, reason=str(exc))
            )
            continue
        similarity = sum(left * right for left, right in zip(baseline, histogram))
        ok = similarity >= min_similarity
        checks.append(
            ProductImageCheck(
                path=path,
                similarity=round(similarity, 4),
                accepted=ok,
                reason="" if ok else "대표 상품 이미지와 불일치",
            )
        )
        if ok:
            accepted.append(path)

    return accepted, checks
