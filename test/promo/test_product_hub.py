import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "product-hub"


def test_hub_products_have_direct_affiliate_links_and_identity():
    data = json.loads((HUB / "products.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert data["products"]

    for product in data["products"]:
        assert product["productId"].isdigit()
        assert product["itemId"].isdigit()
        assert product["vendorItemId"].isdigit()
        assert product["affiliateUrl"].startswith("https://link.coupang.com/")
        assert product["videoUrl"].startswith("https://www.youtube.com/watch?v=")
        assert product["image"].startswith("https://")
        assert product["checkedAt"]


def test_hub_shows_affiliate_disclosure_and_sponsored_link_attributes():
    html = (HUB / "index.html").read_text(encoding="utf-8")
    assert "[광고]" in html
    assert "쿠팡 파트너스 활동의 일환" in html
    assert 'rel="sponsored nofollow noopener"' in html
