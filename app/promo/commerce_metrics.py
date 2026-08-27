"""커머스 상품/성과 시계열 원장과 비교 점수."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class CommerceProduct:
    product_key: str
    product_id: str
    item_id: str
    vendor_item_id: str
    name: str
    category: str
    affiliate_url: str
    image_url: str
    video_id: str = ""
    active: bool = True


@dataclass(frozen=True)
class CommerceOffer:
    offer_key: str
    product_key: str
    merchant: str
    price: int
    affiliate_url: str
    checked_at: str
    shipping_text: str = ""
    commission_rate: float | None = None
    active: bool = True


def upsert_product(conn: sqlite3.Connection, product: CommerceProduct) -> None:
    conn.execute(
        """
        INSERT INTO commerce_products (
            product_key, product_id, item_id, vendor_item_id, name, category,
            affiliate_url, image_url, video_id, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
            product_id=excluded.product_id,
            item_id=excluded.item_id,
            vendor_item_id=excluded.vendor_item_id,
            name=excluded.name,
            category=excluded.category,
            affiliate_url=excluded.affiliate_url,
            image_url=excluded.image_url,
            video_id=excluded.video_id,
            active=excluded.active,
            updated_at=datetime('now')
        """,
        (
            product.product_key,
            product.product_id,
            product.item_id,
            product.vendor_item_id,
            product.name,
            product.category,
            product.affiliate_url,
            product.image_url,
            product.video_id or None,
            int(product.active),
        ),
    )
    conn.commit()


def upsert_offer(conn: sqlite3.Connection, offer: CommerceOffer) -> None:
    conn.execute(
        """
        INSERT INTO commerce_offers (
            offer_key, product_key, merchant, price, shipping_text,
            affiliate_url, commission_rate, active, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(offer_key) DO UPDATE SET
            product_key=excluded.product_key,
            merchant=excluded.merchant,
            price=excluded.price,
            shipping_text=excluded.shipping_text,
            affiliate_url=excluded.affiliate_url,
            commission_rate=excluded.commission_rate,
            active=excluded.active,
            checked_at=excluded.checked_at,
            updated_at=datetime('now')
        """,
        (
            offer.offer_key,
            offer.product_key,
            offer.merchant,
            offer.price,
            offer.shipping_text or None,
            offer.affiliate_url,
            offer.commission_rate,
            int(offer.active),
            offer.checked_at,
        ),
    )
    conn.commit()


def offers_for_product(conn: sqlite3.Connection, product_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM commerce_offers
        WHERE product_key=? AND active=1
        ORDER BY price ASC, merchant ASC
        """,
        (product_key,),
    ).fetchall()


def deactivate_missing_offers(
    conn: sqlite3.Connection, product_key: str, retained_offer_keys: set[str]
) -> None:
    rows = conn.execute(
        "SELECT offer_key FROM commerce_offers WHERE product_key=?", (product_key,)
    ).fetchall()
    stale = [row["offer_key"] for row in rows if row["offer_key"] not in retained_offer_keys]
    if stale:
        conn.executemany(
            "UPDATE commerce_offers SET active=0, updated_at=datetime('now') "
            "WHERE offer_key=?",
            ((offer_key,) for offer_key in stale),
        )
        conn.commit()


def record_snapshot(
    conn: sqlite3.Connection,
    product_key: str,
    source: str,
    *,
    captured_at: str | None = None,
    payload: dict | None = None,
    **metrics,
) -> int:
    allowed = {
        "price",
        "list_price",
        "available",
        "review_count",
        "demand_index",
        "supply_views",
        "video_views",
        "likes",
        "comments",
        "clicks",
        "orders_count",
        "sales_amount",
        "commission",
    }
    unknown = set(metrics) - allowed
    if unknown:
        raise ValueError(f"지원하지 않는 커머스 지표: {sorted(unknown)}")
    columns = ["product_key", "source", "captured_at", *metrics, "payload_json"]
    timestamp = captured_at or datetime.now(timezone.utc).isoformat()
    values = [
        product_key,
        source,
        timestamp,
        *(int(value) if isinstance(value, bool) else value for value in metrics.values()),
        json.dumps(payload or {}, ensure_ascii=False),
    ]
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT INTO commerce_snapshots ({', '.join(columns)}) "
        f"VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return int(cursor.lastrowid)


def latest_snapshot(
    conn: sqlite3.Connection, product_key: str, source: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM commerce_snapshots
        WHERE product_key=? AND source=?
        ORDER BY captured_at DESC, id DESC LIMIT 1
        """,
        (product_key, source),
    ).fetchone()


def _at_or_before(
    conn: sqlite3.Connection, product_key: str, source: str, before: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM commerce_snapshots
        WHERE product_key=? AND source=? AND captured_at<=?
        ORDER BY captured_at DESC, id DESC LIMIT 1
        """,
        (product_key, source, before),
    ).fetchone()


def performance_window(
    conn: sqlite3.Connection,
    product_key: str,
    *,
    days: int,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(timezone.utc)
    cutoff = (current_time - timedelta(days=days)).isoformat()
    result: dict[str, dict] = {}
    fields = {
        "youtube": ("video_views", "likes", "comments"),
        "coupang": ("clicks", "orders_count", "sales_amount", "commission"),
    }
    for source, names in fields.items():
        latest = latest_snapshot(conn, product_key, source)
        prior = _at_or_before(conn, product_key, source, cutoff)
        result[source] = {}
        for name in names:
            latest_value = int((latest[name] if latest else 0) or 0)
            prior_value = int((prior[name] if prior else 0) or 0)
            result[source][name] = {
                "current": latest_value,
                "delta": latest_value - prior_value,
            }
    clicks = result["coupang"]["clicks"]["delta"]
    orders = result["coupang"]["orders_count"]["delta"]
    commission = result["coupang"]["commission"]["delta"]
    result["conversion_rate"] = round(orders / clicks, 4) if clicks else 0.0
    result["earnings_per_click"] = round(commission / clicks, 2) if clicks else 0.0
    return result


def rank_products(
    conn: sqlite3.Connection, *, days: int = 7, now: datetime | None = None
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM commerce_products WHERE active=1 ORDER BY created_at"
    ).fetchall()
    ranked = []
    for row in rows:
        perf = performance_window(conn, row["product_key"], days=days, now=now)
        coupang = perf["coupang"]
        youtube = perf["youtube"]
        ranked.append(
            {
                "product_key": row["product_key"],
                "name": row["name"],
                "commission": coupang["commission"]["delta"],
                "orders": coupang["orders_count"]["delta"],
                "clicks": coupang["clicks"]["delta"],
                "video_views": youtube["video_views"]["delta"],
                "conversion_rate": perf["conversion_rate"],
                "earnings_per_click": perf["earnings_per_click"],
            }
        )
    return sorted(
        ranked,
        key=lambda item: (
            item["commission"],
            item["earnings_per_click"],
            item["orders"],
            item["video_views"],
        ),
        reverse=True,
    )
