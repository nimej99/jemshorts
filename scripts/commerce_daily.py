"""커머스 일일 스냅샷 수집 + 1/3/7일 성과 랭킹.

자동 수집: 제품 허브 원장, YouTube 통계, 네이버 검색 수요.
쿠팡 클릭/주문/수수료는 승인 전 공식 API가 없으므로 --coupang-json 으로
브라우저 리포트 추출 결과를 받는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from app.promo import commerce_metrics, db, youtube  # noqa: E402
from app.promo.research import datalab  # noqa: E402
from scripts.build_product_hub import build  # noqa: E402


def _video_id(url: str) -> str:
    return parse_qs(urlparse(url).query).get("v", [""])[0]


def _review_count(summary: str) -> int:
    match = re.search(r"리뷰\s*([\d,]+)", summary)
    return int(match.group(1).replace(",", "")) if match else 0


def _load_products(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["products"]


def sync(*, products_path: Path, coupang_json: Path | None = None) -> dict:
    products = _load_products(products_path)
    conn = db.connect()
    errors: list[dict[str, str]] = []
    try:
        for item in products:
            offers = sorted(
                (
                    offer
                    for offer in item.get("offers", [])
                    if offer.get("active", True)
                ),
                key=lambda offer: offer["price"],
            )
            if not offers:
                raise ValueError(f"활성 판매 오퍼가 없습니다: {item['id']}")
            primary_offer = offers[0]
            identity = item.get("identity") or {}
            product_id = item.get("productId") or identity.get("affiliateProductId")
            item_id = item.get("itemId") or identity.get("channelProductNo")
            vendor_item_id = item.get("vendorItemId") or item_id
            product = commerce_metrics.CommerceProduct(
                product_key=item["id"],
                product_id=str(product_id),
                item_id=str(item_id),
                vendor_item_id=str(vendor_item_id),
                name=item["name"],
                category=(item.get("badges") or ["기타"])[0],
                affiliate_url=primary_offer["affiliateUrl"],
                image_url=item["image"],
                video_id=_video_id(item.get("videoUrl", "")),
                active=item.get("active", True),
            )
            commerce_metrics.upsert_product(conn, product)
            for offer in offers:
                commerce_metrics.upsert_offer(
                    conn,
                    commerce_metrics.CommerceOffer(
                        offer_key=f"{item['id']}:{offer['id']}",
                        product_key=item["id"],
                        merchant=offer["merchant"],
                        price=int(offer["price"]),
                        affiliate_url=offer["affiliateUrl"],
                        checked_at=offer["checkedAt"],
                        shipping_text=offer.get("shippingText", ""),
                        commission_rate=offer.get("commissionRate"),
                        active=offer.get("active", True),
                    ),
                )
            commerce_metrics.deactivate_missing_offers(
                conn,
                item["id"],
                {f"{item['id']}:{offer['id']}" for offer in offers},
            )
            commerce_metrics.record_snapshot(
                conn,
                product.product_key,
                "market",
                price=int(primary_offer["price"]),
                available=product.active,
                review_count=_review_count(item.get("summary", "")),
                payload={"checkedAt": item.get("checkedAt")},
            )

        video_map: dict[str, list[str]] = {}
        for item in products:
            video_id = _video_id(item.get("videoUrl", ""))
            if video_id:
                video_map.setdefault(video_id, []).append(item["id"])
        if video_map and youtube.configured():
            try:
                token = youtube._access_token()
                response = requests.get(
                    f"{youtube.API_URL}/videos",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"part": "statistics,status", "id": ",".join(video_map)},
                    timeout=30,
                )
                response.raise_for_status()
                for video in response.json().get("items", []):
                    stats = video.get("statistics", {})
                    for product_key in video_map[video["id"]]:
                        commerce_metrics.record_snapshot(
                            conn,
                            product_key,
                            "youtube",
                            video_views=int(stats.get("viewCount", 0)),
                            likes=int(stats.get("likeCount", 0)),
                            comments=int(stats.get("commentCount", 0)),
                            payload={
                                "privacy": video.get("status", {}).get(
                                    "privacyStatus"
                                )
                            },
                        )
            except Exception as exc:
                errors.append({"source": "youtube", "error": str(exc)})

        if datalab.configured():
            try:
                keywords = [
                    item["name"] for item in products if item.get("active", True)
                ]
                demand = datalab.fetch_demand(keywords)
                for item in products:
                    if item["name"] in demand:
                        commerce_metrics.record_snapshot(
                            conn,
                            item["id"],
                            "naver",
                            demand_index=demand[item["name"]],
                        )
            except Exception as exc:
                errors.append({"source": "naver", "error": str(exc)})

        if coupang_json:
            rows = json.loads(coupang_json.read_text(encoding="utf-8"))
            for row in rows:
                commerce_metrics.record_snapshot(
                    conn,
                    row["product_key"],
                    "coupang",
                    clicks=int(row.get("clicks", 0)),
                    orders_count=int(row.get("orders_count", 0)),
                    sales_amount=int(row.get("sales_amount", 0)),
                    commission=int(row.get("commission", 0)),
                    payload=row,
                )

        result = {
            str(days): commerce_metrics.rank_products(conn, days=days)
            for days in (1, 3, 7)
        }
        result["_errors"] = errors
        return result
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="커머스 일일 성과 수집")
    parser.add_argument(
        "--products", type=Path, default=ROOT / "product-hub" / "products.json"
    )
    parser.add_argument("--coupang-json", type=Path)
    parser.add_argument("--build-hub", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.build_hub:
        build(args.products.parent)
    rendered = json.dumps(
        sync(products_path=args.products, coupang_json=args.coupang_json),
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
