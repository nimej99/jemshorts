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


def _number(text: str) -> int:
    values = re.findall(r"[\d,]+", text)
    return int(values[-1].replace(",", "")) if values else 0


def _review_count(summary: str) -> int:
    match = re.search(r"리뷰\s*([\d,]+)", summary)
    return int(match.group(1).replace(",", "")) if match else 0


def _load_products(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["products"]


def sync(*, products_path: Path, coupang_json: Path | None = None) -> dict:
    products = _load_products(products_path)
    conn = db.connect()
    try:
        for item in products:
            product = commerce_metrics.CommerceProduct(
                product_key=item["id"],
                product_id=item["productId"],
                item_id=item["itemId"],
                vendor_item_id=item["vendorItemId"],
                name=item["name"],
                category=(item.get("badges") or ["기타"])[0],
                affiliate_url=item["affiliateUrl"],
                image_url=item["image"],
                video_id=_video_id(item.get("videoUrl", "")),
                active=item.get("active", True),
            )
            commerce_metrics.upsert_product(conn, product)
            commerce_metrics.record_snapshot(
                conn,
                product.product_key,
                "market",
                price=_number(item.get("priceText", "")),
                available=product.active,
                review_count=_review_count(item.get("summary", "")),
                payload={"checkedAt": item.get("checkedAt")},
            )

        video_map = {
            _video_id(item.get("videoUrl", "")): item["id"]
            for item in products
            if _video_id(item.get("videoUrl", ""))
        }
        if video_map and youtube.configured():
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
                commerce_metrics.record_snapshot(
                    conn,
                    video_map[video["id"]],
                    "youtube",
                    video_views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(stats.get("commentCount", 0)),
                    payload={"privacy": video.get("status", {}).get("privacyStatus")},
                )

        if datalab.configured():
            keywords = [item["name"] for item in products if item.get("active", True)]
            demand = datalab.fetch_demand(keywords)
            for item in products:
                if item["name"] in demand:
                    commerce_metrics.record_snapshot(
                        conn, item["id"], "naver", demand_index=demand[item["name"]]
                    )

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

        return {
            str(days): commerce_metrics.rank_products(conn, days=days)
            for days in (1, 3, 7)
        }
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
