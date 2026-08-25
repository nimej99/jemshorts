from PIL import Image

from app.promo.materials.product_images import validate_product_images


def _image(path, color, *, accent=None):
    image = Image.new("RGB", (600, 600), color)
    if accent:
        for x in range(200, 400):
            for y in range(200, 400):
                image.putpixel((x, y), accent)
    image.save(path)


def test_rejects_unrelated_product_and_keeps_matching_variant(tmp_path):
    primary = tmp_path / "primary.jpg"
    variant = tmp_path / "variant.jpg"
    unrelated = tmp_path / "ham.jpg"
    _image(primary, (210, 240, 255), accent=(0, 120, 220))
    # 같은 상품을 다른 압축 품질로 저장한 상세 이미지
    with Image.open(primary) as image:
        image.save(variant, quality=70)
    _image(unrelated, (90, 25, 20), accent=(230, 140, 120))

    accepted, checks = validate_product_images(
        str(primary), [str(variant), str(unrelated)]
    )

    assert accepted == [str(primary), str(variant)]
    assert checks[-1].accepted is False
    assert checks[-1].reason == "대표 상품 이미지와 불일치"


def test_primary_is_safe_fallback_when_every_candidate_is_rejected(tmp_path):
    primary = tmp_path / "primary.jpg"
    unrelated = tmp_path / "vegetables.jpg"
    _image(primary, (210, 240, 255))
    _image(unrelated, (20, 100, 25))

    accepted, _ = validate_product_images(str(primary), [str(unrelated)])

    assert accepted == [str(primary)]
