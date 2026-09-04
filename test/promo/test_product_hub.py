import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "product-hub"
sys.path.insert(0, str(ROOT))


def test_hub_products_have_direct_affiliate_links_and_identity():
    data = json.loads((HUB / "products.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert data["offerFreshnessDays"] > 0
    assert data["collections"]
    assert data["products"]
    product_ids = {product["id"] for product in data["products"]}
    for collection in data["collections"]:
        assert collection["productIds"]
        assert set(collection["productIds"]) <= product_ids

    for product in data["products"]:
        assert product["productId"].isdigit()
        assert product["itemId"].isdigit()
        assert product["vendorItemId"].isdigit()
        assert product["offers"]
        for offer in product["offers"]:
            assert offer["merchant"]
            assert isinstance(offer["price"], int) and offer["price"] > 0
            assert offer["affiliateUrl"].startswith("https://")
            assert offer["checkedAt"]
        assert product["videoUrl"].startswith("https://www.youtube.com/watch?v=")
        assert product["image"].startswith("https://")
        assert product["checkedAt"]


def test_hub_shows_affiliate_disclosure_and_sponsored_link_attributes():
    html = (HUB / "index.html").read_text(encoding="utf-8")
    script = (HUB / "app.js").read_text(encoding="utf-8")
    assert "[광고]" in html
    assert "쿠팡 파트너스 활동의 일환" in html
    assert "네이버 쇼핑 커넥트 활동의 일환" in html
    assert 'buy.rel = "sponsored nofollow noopener"' in script


def test_seo_builder_writes_product_pages_and_sitemap(tmp_path):
    from scripts.build_product_hub import build

    for name in ("products.json", "styles.css"):
        (tmp_path / name).write_bytes((HUB / name).read_bytes())
    pages = build(tmp_path)

    assert len(pages) == 3
    page = next(path for path in pages if path.parent.name == "p").read_text(
        encoding="utf-8"
    )
    assert '"@type": "Product"' in page
    assert "쿠팡 파트너스 활동의 일환" in page
    assert "youtube.com/embed/" in page
    collection = next(path for path in pages if path.parent.name == "c").read_text(
        encoding="utf-8"
    )
    assert '"@type": "ItemList"' in collection
    assert "sitemap.xml" in (tmp_path / "robots.txt").read_text(encoding="utf-8")


def test_stale_offer_is_not_advertised_as_current_structured_price():
    from scripts.build_product_hub import render_product

    product = deepcopy(
        json.loads((HUB / "products.json").read_text(encoding="utf-8"))["products"][1]
    )
    for offer in product["offers"]:
        offer["checkedAt"] = "2000-01-01"
    rendered = render_product(product, freshness_days=3)

    assert "최신 가격 다시 확인" in rendered
    assert "price-note stale" in rendered
    assert '"@type": "AggregateOffer"' not in rendered


def test_fresh_competing_offers_show_savings_and_aggregate_offer():
    from scripts.build_product_hub import render_product

    product = deepcopy(
        json.loads((HUB / "products.json").read_text(encoding="utf-8"))["products"][1]
    )
    for offer in product["offers"]:
        offer["checkedAt"] = date.today().isoformat()
    rendered = render_product(product, freshness_days=3)

    assert "1,280원 저렴" in rendered
    assert '"@type": "AggregateOffer"' in rendered
