"""주제별 TOP3/TOP5를 네이버 블로그·클립 패키지로 재가공한다."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import config  # noqa: E402
from app.promo import commerce_metrics, commerce_policy, db, distribution  # noqa: E402


def _select(products: list[dict], product_ids: list[str], count: int) -> list[dict]:
    by_id = {product["id"]: product for product in products if product.get("active", True)}
    eligible = {key for key in product_ids if key in by_id}
    conn = db.connect()
    try:
        ranked = [row["product_key"] for row in commerce_metrics.rank_products(conn)]
    finally:
        conn.close()
    keys = [key for key in ranked if key in eligible]
    keys.extend(key for key in product_ids if key in eligible and key not in keys)
    return [by_id[key] for key in keys[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 TOP3/TOP5 블로그·클립 패키지")
    parser.add_argument("--top", type=int, choices=(3, 5), default=3)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--video", type=Path, help="QA가 끝난 TOP3/TOP5 원본 MP4")
    parser.add_argument("--qa-approved", action="store_true")
    parser.add_argument("--title", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    catalog = json.loads(
        (ROOT / "product-hub" / "products.json").read_text(encoding="utf-8")
    )
    collection = next(
        (
            item
            for item in catalog.get("collections", [])
            if item["id"] == args.collection and item.get("active", True)
        ),
        None,
    )
    if collection is None:
        parser.error(f"활성 컬렉션을 찾을 수 없습니다: {args.collection}")
    selected = _select(catalog["products"], collection["productIds"], args.top)
    if len(selected) != args.top:
        parser.error(
            f"컬렉션의 활성 상품이 {len(selected)}개뿐이라 "
            f"TOP{args.top} 패키지를 만들 수 없습니다"
        )

    policy = commerce_policy.validate_publication(
        selected, collection=collection, qa_passed=args.qa_approved
    )
    title = args.title.strip() or f"{collection['title']} TOP{args.top} 비교"
    hub_url = str(config.app.get("product_hub_url", "")).strip()
    output = (
        args.output
        or ROOT / "storage" / "distribution" / date.today().isoformat()
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)

    (output / "naver-blog.md").write_text(
        distribution.blog_markdown(selected, title=title, hub_url=hub_url),
        encoding="utf-8",
    )
    (output / "naver-clip.txt").write_text(
        distribution.clip_caption(selected, title=title, hub_url=hub_url),
        encoding="utf-8",
    )
    copied_video = ""
    if args.video:
        video = args.video.expanduser().resolve()
        if not video.is_file():
            parser.error(f"클립 원본 영상이 없습니다: {video}")
        destination = output / f"{args.collection}-top{args.top}{video.suffix.lower()}"
        shutil.copy2(video, destination)
        copied_video = str(destination)

    manifest = {
        "title": title,
        "collection": args.collection,
        "product_count": len(selected),
        "products": [product["id"] for product in selected],
        "hub_url": hub_url,
        "clip_video": copied_video,
        "qa_approved": args.qa_approved,
        "publication_ready": policy.passed and bool(copied_video),
        "policy_failures": list(policy.failures),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
