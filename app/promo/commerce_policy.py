"""모든 커머스 발행 경로가 공유하는 강제 정책 게이트."""

from __future__ import annotations

from dataclasses import dataclass

MIN_PRODUCTS = 3
ALLOWED_PRODUCT_COUNTS = frozenset({3, 5})
MAX_PRICE_AGE_DAYS = 3


@dataclass(frozen=True)
class PublicationPolicyResult:
    passed: bool
    failures: tuple[str, ...]


class PublicationPolicyError(ValueError):
    pass


def validate_publication(
    products: list[dict],
    *,
    collection: dict | None,
    qa_passed: bool,
) -> PublicationPolicyResult:
    failures: list[str] = []
    count = len(products)
    if count not in ALLOWED_PRODUCT_COUNTS:
        failures.append(
            f"공개 커머스 콘텐츠는 상품 3개 또는 5개가 필요합니다 (현재 {count}개)"
        )
    if collection is None or not collection.get("active", True):
        failures.append("활성 주제 컬렉션이 필요합니다")
    else:
        allowed = set(collection.get("productIds") or [])
        outside = [product.get("id", "") for product in products if product.get("id") not in allowed]
        if outside:
            failures.append(f"컬렉션 밖 상품이 포함됐습니다: {outside}")
    duplicate_ids = [
        key
        for key in {product.get("id", "") for product in products}
        if sum(product.get("id", "") == key for product in products) > 1
    ]
    if duplicate_ids:
        failures.append(f"중복 상품이 포함됐습니다: {sorted(duplicate_ids)}")
    if not qa_passed:
        failures.append("대표 이미지/렌더 QA가 통과되지 않았습니다")
    return PublicationPolicyResult(passed=not failures, failures=tuple(failures))


def require_publication(
    products: list[dict],
    *,
    collection: dict | None,
    qa_passed: bool,
) -> None:
    result = validate_publication(
        products, collection=collection, qa_passed=qa_passed
    )
    if not result.passed:
        raise PublicationPolicyError("; ".join(result.failures))
