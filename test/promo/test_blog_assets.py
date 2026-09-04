from PIL import Image

from app.promo.blog_assets import (
    HEIGHT,
    WIDTH,
    build_comparison_cover,
    qa_cover,
)


def test_comparison_cover_has_required_resolution_and_passes_qa(tmp_path):
    source = tmp_path / "product.jpg"
    Image.new("RGB", (1000, 1000), "white").save(source)
    output = build_comparison_cover(
        product_name="테스트 상품 2개",
        image_path=source,
        offers=[
            {
                "merchant": "네이버",
                "price": 7180,
                "priceText": "7,180원",
                "checkedAt": "2026-08-26",
            },
            {
                "merchant": "쿠팡",
                "price": 8460,
                "priceText": "8,460원",
                "checkedAt": "2026-08-25",
            },
        ],
        output_path=tmp_path / "cover.png",
    )

    result = qa_cover(output)

    assert result.passed is True
    assert (result.width, result.height) == (WIDTH, HEIGHT)


def test_qa_rejects_wrong_resolution(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (320, 320), "white").save(path)

    result = qa_cover(path)

    assert result.passed is False
    assert any("해상도" in failure for failure in result.failures)


def test_roundup_cover_requires_three_products_and_uses_checked_date(tmp_path):
    from app.promo.blog_assets import build_roundup_cover

    source = tmp_path / "product.jpg"
    Image.new("RGB", (1000, 1000), "white").save(source)
    products = [
        {
            "name": f"상품 {index}",
            "feature": "검증 특징",
            "priceText": f"{index * 1000:,}원",
            "image_path": source,
        }
        for index in range(1, 4)
    ]

    output = build_roundup_cover(
        title="검증 TOP3",
        products=products,
        checked_at="2026-09-04",
        output_path=tmp_path / "top3.png",
    )

    assert qa_cover(output).passed is True
