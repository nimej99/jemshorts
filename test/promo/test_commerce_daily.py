import json

from app.promo import db
from scripts import commerce_daily


def test_youtube_auth_failure_does_not_abort_market_snapshot(tmp_path, monkeypatch):
    products = tmp_path / "products.json"
    products.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "p1",
                        "productId": "1",
                        "itemId": "2",
                        "vendorItemId": "3",
                        "name": "상품",
                        "summary": "리뷰 10개",
                        "offers": [
                            {
                                "id": "naver",
                                "merchant": "네이버",
                                "price": 10000,
                                "affiliateUrl": "https://naver.me/x",
                                "checkedAt": "2026-09-04",
                                "active": True,
                            }
                        ],
                        "image": "https://example.com/x.jpg",
                        "videoUrl": "https://www.youtube.com/watch?v=video",
                        "badges": ["생활"],
                        "active": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    original_connect = db.connect
    monkeypatch.setattr(
        commerce_daily.db,
        "connect",
        lambda: original_connect(tmp_path / "promo.db"),
    )
    monkeypatch.setattr(commerce_daily.youtube, "configured", lambda: True)
    monkeypatch.setattr(
        commerce_daily.youtube,
        "_access_token",
        lambda: (_ for _ in ()).throw(RuntimeError("expired token")),
    )
    monkeypatch.setattr(commerce_daily.datalab, "configured", lambda: False)

    result = commerce_daily.sync(products_path=products)

    assert result["1"][0]["product_key"] == "p1"
    assert result["_errors"] == [
        {"source": "youtube", "error": "expired token"}
    ]
