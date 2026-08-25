"""성과 원장에서 TOP3/TOP5 비교 쇼츠를 렌더·게시한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import config  # noqa: E402
from app.promo import commerce_metrics, db, pipeline, plans, publish, uploads, youtube  # noqa: E402
from app.promo.brandkit.models import BrandKit, PromotionLink  # noqa: E402
from app.promo.templates.schema import load_raw, load_template  # noqa: E402
from app.utils import utils  # noqa: E402


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value).strip("-")


def _download(product: dict, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_name(product['id'])}.jpg"
    response = requests.get(product["image"], timeout=30)
    response.raise_for_status()
    path.write_bytes(response.content)
    return str(path.resolve())


def _short_name(name: str, limit: int = 18) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _build_script(ranked: list[dict], title: str) -> str:
    count = len(ranked)
    sentences = [f"검색 수요와 실제 반응으로 고른 {title}, 바로 확인해 볼게요."]
    for position, product in zip(range(count, 0, -1), reversed(ranked)):
        feature = product["summary"].split("·")[0].strip()
        sentences.append(
            f"{position}위는 {_short_name(product['name'], 24)}. "
            f"{feature}, 현재 {product['priceText']}입니다."
        )
    sentences.append("제품별 최신 정보는 채널 프로필의 추천 제품 링크에서 확인하세요.")
    return " ".join(sentences)


def _select_products(conn, products: list[dict], count: int) -> list[dict]:
    by_key = {product["id"]: product for product in products if product.get("active", True)}
    ranked = commerce_metrics.rank_products(conn, days=7)
    keys = [row["product_key"] for row in ranked if row["product_key"] in by_key]
    keys.extend(key for key in by_key if key not in keys)
    if len(keys) < count:
        raise ValueError(f"활성 상품이 {len(keys)}개뿐이라 TOP{count}를 만들 수 없습니다")
    return [by_key[key] for key in keys[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description="커머스 TOP3/TOP5 쇼츠")
    parser.add_argument("--top", type=int, choices=(3, 5), required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    products_path = ROOT / "product-hub" / "products.json"
    products = json.loads(products_path.read_text(encoding="utf-8"))["products"]
    conn = db.connect()
    try:
        try:
            selected = _select_products(conn, products, args.top)
        except ValueError as exc:
            parser.error(str(exc))
        title = args.title.strip() or f"가성비 생활 꿀템 TOP{args.top}"
        template_path = ROOT / "templates-data" / f"commerce-top{args.top}-v2.json"
        template_raw = load_raw(str(template_path))
        template = load_template(str(template_path))
        asset_dir = ROOT / "storage" / "promo_photos" / f"roundup-top{args.top}"
        downloaded = {item["id"]: _download(item, asset_dir) for item in selected}

        # 훅 → 최하위부터 1위 → CTA 화면 순서에 맞춘다.
        descending = list(reversed(selected))
        photos = [downloaded[descending[0]["id"]]]
        photos.extend(downloaded[item["id"]] for item in descending)
        photos.append(downloaded[selected[0]["id"]])
        kit = BrandKit(
            business_name=title,
            description=" / ".join(item["summary"] for item in selected),
            photos=photos,
            promotion_links=[
                PromotionLink(label=_short_name(item["name"]), url=item["affiliateUrl"])
                for item in selected
            ],
            source="commerce-roundup",
        )
        variables = {
            "roundup_title": title,
            "cta_text": "채널 프로필 추천 제품",
        }
        for rank, item in enumerate(selected, start=1):
            variables[f"product_{rank}"] = _short_name(item["name"])
        script = _build_script(selected, title)
        local_dir = utils.storage_dir("local_videos", create=True)
        plan = pipeline.plan_render(
            template, kit, [], script, local_dir, variables=variables
        )
        if not plan.structural.passed:
            raise RuntimeError(f"구조 게이트 실패: {plan.structural.failures}")
        if plan.timeline is not None and not plan.timeline.passed:
            raise RuntimeError(f"타임라인 게이트 실패: {plan.timeline.failures}")
        plans.save_plan(conn, plan, template_raw)
        task_id = f"promo-roundup-{plan.plan_id}"
        plans.mark_rendering(conn, plan.plan_id, task_id)
        result = pipeline.execute_render(plan, task_id=task_id)
        if not result.technical.passed:
            raise RuntimeError(f"렌더 게이트 실패: {result.technical.failures}")
        video_path = result.videos[0]
        hub_url = str(config.app.get("product_hub_url", "")).strip()
        direct = "\n".join(
            f"▶ {item['name']}: {item['affiliateUrl']}" for item in selected
        )
        description = (
            f"[광고] {title}\n\n{direct}\n\n▶ 전체 제품 모아보기: {hub_url}\n\n"
            "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 "
            "수수료를 제공받습니다.\n\n#제품비교 #생활꿀템 #가성비 #TOP추천"
        )
        print(json.dumps({"video": video_path, "script": script, "products": [p["id"] for p in selected]}, ensure_ascii=False, indent=2))
        if args.dry_run:
            return 0
        if uploads.cap_reached(conn) and not args.force:
            raise RuntimeError("일일 업로드 상한 도달 — --force 없이 게시하지 않습니다")
        upload = publish.publish_video(
            video_path,
            f"[광고] {title}",
            description,
            [f"TOP{args.top}", "제품비교", "생활꿀템", "가성비"],
            privacy_status="public",
            platforms=["youtube"],
        )
        if not upload.get("success"):
            raise RuntimeError(f"업로드 실패: {upload}")
        uploads.record_delivered(conn, task_id, template.template_id, description, [])
        comment = (
            f"[광고] TOP{args.top} 제품은 채널 프로필의 추천 제품 링크에서 확인하세요.\n"
            f"{hub_url}\n\n이 댓글은 쿠팡 파트너스 활동의 일환으로, 이에 따른 "
            "일정액의 수수료를 제공받습니다."
        )
        try:
            youtube.wait_and_comment(f"[광고] {title}", comment)
        except youtube.YoutubeEngagementError as exc:
            print(f"댓글 자동화 경고: {exc}", file=sys.stderr)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
