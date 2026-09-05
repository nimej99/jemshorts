from datetime import datetime, timezone

from app.promo import commerce_metrics, db


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _product(key="p1"):
    return commerce_metrics.CommerceProduct(
        product_key=key,
        product_id="1",
        item_id="2",
        vendor_item_id="3",
        name=f"상품 {key}",
        category="생활",
        affiliate_url="https://link.coupang.com/a/x",
        image_url="https://example.com/x.jpg",
        video_id="video",
    )


def test_records_snapshots_and_computes_7_day_performance(tmp_path):
    conn = db.connect(tmp_path / "metrics.db")
    commerce_metrics.upsert_product(conn, _product())
    commerce_metrics.record_snapshot(
        conn, "p1", "youtube", captured_at="2026-08-18T12:00:00+00:00", video_views=10
    )
    commerce_metrics.record_snapshot(
        conn, "p1", "youtube", captured_at="2026-08-25T12:00:00+00:00", video_views=210
    )
    commerce_metrics.record_snapshot(
        conn, "p1", "coupang", captured_at="2026-08-18T12:00:00+00:00",
        clicks=2, orders_count=0, commission=0
    )
    commerce_metrics.record_snapshot(
        conn, "p1", "coupang", captured_at="2026-08-25T12:00:00+00:00",
        clicks=12, orders_count=2, commission=1600
    )

    result = commerce_metrics.performance_window(conn, "p1", days=7, now=NOW)

    assert result["youtube"]["video_views"]["delta"] == 200
    assert result["coupang"]["clicks"]["delta"] == 10
    assert result["conversion_rate"] == 0.2
    assert result["earnings_per_click"] == 160


def test_ranks_actual_commission_before_views(tmp_path):
    conn = db.connect(tmp_path / "rank.db")
    commerce_metrics.upsert_product(conn, _product("money"))
    commerce_metrics.upsert_product(conn, _product("views"))
    commerce_metrics.record_snapshot(
        conn, "money", "coupang", captured_at=NOW.isoformat(),
        clicks=5, orders_count=1, commission=500
    )
    commerce_metrics.record_snapshot(
        conn, "views", "youtube", captured_at=NOW.isoformat(), video_views=100000
    )

    ranked = commerce_metrics.rank_products(conn, days=7, now=NOW)

    assert [item["product_key"] for item in ranked] == ["money", "views"]


def test_monotonic_counter_corrections_do_not_create_negative_growth(tmp_path):
    conn = db.connect(tmp_path / "corrections.db")
    commerce_metrics.upsert_product(conn, _product())
    commerce_metrics.record_snapshot(
        conn,
        "p1",
        "youtube",
        captured_at="2026-08-18T12:00:00+00:00",
        video_views=20,
    )
    commerce_metrics.record_snapshot(
        conn,
        "p1",
        "youtube",
        captured_at="2026-08-25T12:00:00+00:00",
        video_views=14,
    )

    result = commerce_metrics.performance_window(conn, "p1", days=7, now=NOW)

    assert result["youtube"]["video_views"]["current"] == 14
    assert result["youtube"]["video_views"]["delta"] == 0


def test_offers_are_sorted_by_viewer_price(tmp_path):
    conn = db.connect(tmp_path / "offers.db")
    commerce_metrics.upsert_product(conn, _product())
    commerce_metrics.upsert_offer(
        conn,
        commerce_metrics.CommerceOffer(
            offer_key="p1:naver",
            product_key="p1",
            merchant="네이버",
            price=12000,
            affiliate_url="https://shopping.naver.com/x",
            checked_at="2026-08-25",
        ),
    )
    commerce_metrics.upsert_offer(
        conn,
        commerce_metrics.CommerceOffer(
            offer_key="p1:coupang",
            product_key="p1",
            merchant="쿠팡",
            price=9900,
            affiliate_url="https://link.coupang.com/a/x",
            checked_at="2026-08-25",
        ),
    )

    offers = commerce_metrics.offers_for_product(conn, "p1")

    assert [offer["merchant"] for offer in offers] == ["쿠팡", "네이버"]

    commerce_metrics.deactivate_missing_offers(conn, "p1", {"p1:coupang"})

    offers = commerce_metrics.offers_for_product(conn, "p1")
    assert [offer["merchant"] for offer in offers] == ["쿠팡"]
