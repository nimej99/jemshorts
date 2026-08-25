"""제품 원장에서 네이버 블로그 글·클립 업로드 패키지를 만든다."""

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
from app.promo import commerce_metrics, db, distribution  # noqa: E402


def _parse_video(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--video는 product_key=/path/video.mp4 형식")
    key, path = value.split("=", 1)
    return key, Path(path).expanduser().resolve()


def _select(products: list[dict], count: int, ids: list[str]) -> list[dict]:
    by_id = {product["id"]: product for product in products if product.get("active", True)}
    if ids:
        missing = [key for key in ids if key not in by_id]
        if missing:
            raise ValueError(f"제품 허브에 없는 상품: {missing}")
        return [by_id[key] for key in ids]
    conn = db.connect()
    try:
        keys = [row["product_key"] for row in commerce_metrics.rank_products(conn)]
    finally:
        conn.close()
    keys.extend(key for key in by_id if key not in keys)
    return [by_id[key] for key in keys[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 블로그/클립 재가공 패키지")
    parser.add_argument("--top", type=int, choices=(2, 3, 5), default=3)
    parser.add_argument("--product-id", action="append", default=[])
    parser.add_argument("--video", action="append", type=_parse_video, default=[])
    parser.add_argument("--title", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    data = json.loads(
        (ROOT / "product-hub" / "products.json").read_text(encoding="utf-8")
    )
    selected = _select(data["products"], args.top, args.product_id)
    if len(selected) < args.top and not args.product_id:
        parser.error(f"활성 상품이 {len(selected)}개뿐이라 TOP{args.top} 패키지를 만들 수 없습니다")
    title = args.title.strip() or f"가성비 생활 꿀템 TOP{len(selected)} 비교"
    hub_url = str(config.app.get("product_hub_url", "")).strip()
    output = (args.output or ROOT / "storage" / "distribution" / date.today().isoformat()).resolve()
    output.mkdir(parents=True, exist_ok=True)

    (output / "naver-blog.md").write_text(
        distribution.blog_markdown(selected, title=title, hub_url=hub_url),
        encoding="utf-8",
    )
    clip_dir = output / "naver-clips"
    clip_dir.mkdir(exist_ok=True)
    videos = dict(args.video)
    manifest = {"title": title, "products": [], "hub_url": hub_url}
    for product in selected:
        (clip_dir / f"{product['id']}.txt").write_text(
            distribution.clip_caption(product, hub_url=hub_url), encoding="utf-8"
        )
        video = videos.get(product["id"])
        copied = ""
        if video:
            if not video.is_file():
                parser.error(f"클립 원본 영상이 없습니다: {video}")
            destination = clip_dir / f"{product['id']}{video.suffix.lower()}"
            shutil.copy2(video, destination)
            copied = str(destination)
        manifest["products"].append(
            {
                "product_key": product["id"],
                "youtube_url": product["videoUrl"],
                "clip_video": copied,
                "caption": str(clip_dir / f"{product['id']}.txt"),
            }
        )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
