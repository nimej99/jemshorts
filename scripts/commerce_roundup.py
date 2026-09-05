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
from app.promo import (  # noqa: E402
    blog_assets,
    commerce_policy,
    commerce_metrics,
    db,
    distribution,
    pipeline,
    plans,
    publish,
    uploads,
    youtube,
)
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


def _product_label(product: dict, limit: int = 18) -> str:
    return _short_name(product.get("shortName") or product["name"], limit)


def _roundup_title(collection_title: str, count: int) -> str:
    return (
        collection_title
        if f"TOP{count}" in collection_title.upper()
        else f"{collection_title} TOP{count}"
    )


def _offers(product: dict) -> list[dict]:
    return sorted(
        (
            offer
            for offer in product.get("offers", [])
            if offer.get("active", True)
        ),
        key=lambda offer: offer["price"],
    )


def _best_offer(product: dict) -> dict:
    offers = _offers(product)
    if not offers:
        raise ValueError(f"활성 판매 오퍼가 없습니다: {product['id']}")
    return offers[0]


def _evidence_hook(products: list[dict], title: str) -> str:
    savings = [
        offers[1]["price"] - offers[0]["price"]
        for product in products
        if len(offers := _offers(product)) >= 2
        and offers[1]["price"] > offers[0]["price"]
    ]
    if savings:
        return (
            f"같은 상품인데 판매처만 바꿔도 최대 "
            f"{max(savings):,}원 차이 납니다."
        )
    return f"{title}, 가격과 구성으로 골랐습니다."


def _build_script(ranked: list[dict], title: str, evidence_hook: str) -> str:
    count = len(ranked)
    sentences = [evidence_hook]
    for position, product in zip(range(count, 0, -1), reversed(ranked)):
        feature = product["summary"].split("·")[0].strip()
        offer = _best_offer(product)
        sentences.append(
            f"{position}위 {_product_label(product)}. "
            f"{feature}, {offer['priceText']}."
        )
    sentences.append("최신 가격은 프로필 추천 제품에서 확인하세요.")
    return " ".join(sentences)


def _select_products(
    conn, products: list[dict], count: int, eligible_ids: list[str]
) -> list[dict]:
    by_key = {product["id"]: product for product in products if product.get("active", True)}
    eligible = {key for key in eligible_ids if key in by_key}
    ranked = commerce_metrics.rank_products(conn, days=7)
    keys = [row["product_key"] for row in ranked if row["product_key"] in eligible]
    keys.extend(key for key in eligible_ids if key in eligible and key not in keys)
    if len(keys) < count:
        raise ValueError(
            f"선택한 컬렉션의 활성 상품이 {len(keys)}개뿐이라 "
            f"TOP{count}를 만들 수 없습니다"
        )
    return [by_key[key] for key in keys[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description="커머스 TOP3/TOP5 쇼츠")
    parser.add_argument(
        "--top",
        type=int,
        choices=(3, 5),
        default=3,
        help="기본 3(일일 TOP3), 주간 성과 결산은 5",
    )
    parser.add_argument("--title", default="")
    parser.add_argument(
        "--collection",
        required=True,
        help="products.json collections의 주제 ID. 무관한 상품 혼합 방지를 위해 필수",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--qa-approved",
        action="store_true",
        help="기술 QA 후 훅·본문·CTA 시각 검수를 통과한 경우에만 지정",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    products_path = ROOT / "product-hub" / "products.json"
    catalog = json.loads(products_path.read_text(encoding="utf-8"))
    products = catalog["products"]
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
    conn = db.connect()
    try:
        try:
            selected = _select_products(
                conn, products, args.top, collection["productIds"]
            )
        except ValueError as exc:
            parser.error(str(exc))
        default_title = _roundup_title(collection["title"], args.top)
        title = args.title.strip() or default_title
        template_path = ROOT / "templates-data" / f"commerce-top{args.top}-v2.json"
        template_raw = load_raw(str(template_path))
        template = load_template(str(template_path))
        asset_dir = ROOT / "storage" / "promo_photos" / f"roundup-top{args.top}"
        downloaded = {item["id"]: _download(item, asset_dir) for item in selected}
        vertical_card = blog_assets.build_vertical_roundup_card(
            title=title,
            products=[
                {
                    "name": _product_label(item, 19),
                    "feature": item["summary"].split("·")[0].strip(),
                    "priceText": _best_offer(item)["priceText"],
                    "image_path": downloaded[item["id"]],
                }
                for item in selected
            ],
            checked_at=max(
                _best_offer(item)["checkedAt"] for item in selected
            ),
            output_path=asset_dir / "vertical-top-card.png",
        )
        card_qa = blog_assets.qa_vertical_card(vertical_card)
        if not card_qa.passed:
            raise RuntimeError(f"TOP 비교 카드 QA 실패: {card_qa.failures}")

        # 훅 → 최하위부터 1위 → CTA 화면 순서에 맞춘다.
        descending = list(reversed(selected))
        photos = [str(vertical_card)]
        photos.extend(downloaded[item["id"]] for item in descending)
        photos.append(str(vertical_card))
        kit = BrandKit(
            business_name=title,
            description=" / ".join(item["summary"] for item in selected),
            photos=photos,
            promotion_links=[
                PromotionLink(
                    label=f"{_product_label(item)} 최저가",
                    url=_best_offer(item)["affiliateUrl"],
                )
                for item in selected
            ],
            source="mixed",
        )
        variables = {
            "roundup_title": title,
            "evidence_hook": _evidence_hook(selected, title),
            "hook_headline": f"가격·기능 비교 TOP{args.top}",
            "cta_text": "채널 프로필 추천 제품",
        }
        for rank, item in enumerate(selected, start=1):
            variables[f"product_{rank}"] = _product_label(item)
        script = _build_script(selected, title, variables["evidence_hook"])
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
            f"▶ {item['name']} ({offer['merchant']} {offer['priceText']}): "
            f"{offer['affiliateUrl']}"
            for item in selected
            for offer in _offers(item)
        )
        disclosures = "\n".join(distribution.affiliate_disclosures(selected))
        description = (
            f"[광고] {title}\n\n{direct}\n\n▶ 전체 제품 모아보기: {hub_url}\n\n"
            f"{disclosures}\n\n#제품비교 #생활꿀템 #가성비 #TOP추천"
        )
        print(json.dumps({"video": video_path, "script": script, "products": [p["id"] for p in selected]}, ensure_ascii=False, indent=2))
        if args.dry_run:
            return 0
        commerce_policy.require_publication(
            selected,
            collection=collection,
            qa_passed=args.qa_approved,
        )
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
            f"{hub_url}\n\n{disclosures}"
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
