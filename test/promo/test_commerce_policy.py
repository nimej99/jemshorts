import pytest

from app.promo.commerce_policy import (
    PublicationPolicyError,
    require_publication,
    validate_publication,
)


def _products(count):
    return [{"id": f"p{index}"} for index in range(count)]


def test_rejects_single_and_two_product_publications():
    collection = {"active": True, "productIds": ["p0", "p1"]}
    for count in (1, 2):
        result = validate_publication(
            _products(count), collection=collection, qa_passed=True
        )
        assert result.passed is False
        assert "3개 또는 5개" in result.failures[0]


def test_rejects_missing_visual_qa_and_collection_outsider():
    products = _products(3)
    collection = {"active": True, "productIds": ["p0", "p1", "other"]}

    result = validate_publication(
        products, collection=collection, qa_passed=False
    )

    assert result.passed is False
    assert any("컬렉션 밖" in failure for failure in result.failures)
    assert any("QA" in failure for failure in result.failures)


def test_allows_only_qa_approved_top3_collection():
    products = _products(3)
    collection = {"active": True, "productIds": ["p0", "p1", "p2"]}

    require_publication(products, collection=collection, qa_passed=True)

    with pytest.raises(PublicationPolicyError):
        require_publication(products, collection=collection, qa_passed=False)
