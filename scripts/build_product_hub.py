"""products.json에서 제품·컬렉션 SEO 페이지와 사이트맵을 생성한다."""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "product-hub"
BASE_URL = "https://nimej99.github.io/jemshorts"


def _video_id(url: str) -> str:
    return parse_qs(urlparse(url).query).get("v", [""])[0]


def _all_offers(product: dict) -> list[dict]:
    return sorted(
        (offer for offer in product.get("offers", []) if offer.get("active", True)),
        key=lambda offer: offer["price"],
    )


def _fresh_offers(
    product: dict, freshness_days: int, today: date | None = None
) -> list[dict]:
    current = today or date.today()
    offers = []
    for offer in _all_offers(product):
        try:
            age = (current - date.fromisoformat(offer["checkedAt"])).days
        except (KeyError, ValueError):
            continue
        if -1 <= age <= freshness_days:
            offers.append(offer)
    return offers


def render_product(product: dict, freshness_days: int = 3) -> str:
    name = html.escape(product["name"])
    summary = html.escape(product["summary"])
    image = html.escape(product["image"], quote=True)
    video_url = product.get("videoUrl", "")
    video_id = html.escape(_video_id(video_url), quote=True)
    identity = product.get("identity") or {}
    sku = product.get("itemId") or identity.get("channelProductNo") or product["id"]
    canonical = f"{BASE_URL}/p/{product['id']}.html"
    offers = _all_offers(product)
    if not offers:
        raise ValueError(f"활성 판매 오퍼가 없습니다: {product['id']}")
    fresh = _fresh_offers(product, freshness_days)
    displayed = fresh or offers
    structured = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product["name"],
        "description": product["summary"],
        "image": [product["image"]],
        "sku": sku,
    }
    if fresh:
        structured_offers = [
            {
                "@type": "Offer",
                "seller": {"@type": "Organization", "name": offer["merchant"]},
                "url": offer["affiliateUrl"],
                "priceCurrency": "KRW",
                "price": str(offer["price"]),
                "availability": "https://schema.org/InStock",
            }
            for offer in fresh
        ]
        structured["offers"] = {
            "@type": "AggregateOffer",
            "priceCurrency": "KRW",
            "lowPrice": str(fresh[0]["price"]),
            "highPrice": str(fresh[-1]["price"]),
            "offerCount": len(fresh),
            "offers": structured_offers,
        }
    offer_buttons = "".join(
        f'<a class="buy" href="{html.escape(offer["affiliateUrl"], quote=True)}" '
        'rel="sponsored nofollow noopener" target="_blank">'
        f'{html.escape(offer["merchant"])} '
        f'{html.escape(offer["priceText"] + " 확인" if fresh else "최신 가격 다시 확인")}'
        "</a>"
        for offer in displayed
    )
    if fresh:
        price_label = f"현재 최저 {html.escape(fresh[0]['priceText'])}"
        if len(fresh) >= 2:
            savings = fresh[1]["price"] - fresh[0]["price"]
            price_note = (
                f'<p class="price-note savings">{html.escape(fresh[0]["merchant"])}가 '
                f'다음 판매처보다 {savings:,}원 저렴</p>'
            )
        else:
            price_note = '<p class="price-note"></p>'
    else:
        price_label = f"최근 확인가 {html.escape(offers[0]['priceText'])}"
        price_note = (
            '<p class="price-note stale">가격 확인일이 지나 판매처에서 '
            "최신 가격을 다시 확인하세요.</p>"
        )
    video_action = (
        f'<a class="watch" href="{html.escape(video_url, quote=True)}">'
        "YouTube 쇼츠 보기</a>"
        if video_url
        else ""
    )
    video_section = (
        f'<section style="margin-top:22px"><h2>소개 영상</h2><iframe width="100%" '
        f'height="420" src="https://www.youtube.com/embed/{video_id}" '
        f'title="{name} 소개 영상" frameborder="0" allowfullscreen></iframe></section>'
        if video_id
        else ""
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} 가격·리뷰·영상 | 젬쇼츠</title>
<meta name="description" content="{summary} 가격과 젬쇼츠 소개 영상을 확인하세요.">
<link rel="canonical" href="{canonical}"><link rel="stylesheet" href="../styles.css">
<meta property="og:type" content="product"><meta property="og:title" content="{name}">
<meta property="og:description" content="{summary}"><meta property="og:image" content="{image}">
<script type="application/ld+json">{json.dumps(structured, ensure_ascii=False)}</script></head>
<body><main><p><a class="watch" href="../">← 전체 추천 제품</a></p>
<header class="hero"><div class="logo">J</div><div><h1>{name}</h1><p>{summary}</p></div></header>
<aside class="disclosure"><strong>[광고]</strong><br>이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.<br>이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다.</aside>
<article class="card" style="margin-top:22px"><img class="product-image" src="{image}" alt="{name} 실제 상품 이미지"><div class="content">
<h2>{name}</h2><p class="summary">{summary}</p><p class="price">{price_label}</p>{price_note}
<div class="actions"><div class="offer-actions">{offer_buttons}</div>{video_action}</div>
<p class="updated">가격 확인: {html.escape(displayed[0]['checkedAt'])}</p></div></article>
{video_section}
<footer>가격과 재고는 판매처에서 변경될 수 있습니다. 구매 전 판매처의 최종 정보를 확인하세요.</footer></main></body></html>"""


def render_collection(collection: dict, products: list[dict]) -> str:
    title = html.escape(collection["title"])
    description = html.escape(collection["description"])
    canonical = f"{BASE_URL}/c/{collection['id']}.html"
    structured = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": collection["title"],
        "description": collection["description"],
        "numberOfItems": len(products),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "url": f"{BASE_URL}/p/{product['id']}.html",
                "name": product["name"],
            }
            for index, product in enumerate(products, start=1)
        ],
    }
    cards = "".join(
        f'<article class="card"><img class="product-image" src="{html.escape(product["image"], quote=True)}" '
        f'alt="{html.escape(product["name"])} 상품 이미지"><div class="content">'
        f'<h2>{index}위 {html.escape(product["name"])}</h2>'
        f'<p class="summary">{html.escape(product["summary"])}</p>'
        f'<a class="watch" href="../p/{product["id"]}.html">가격·판매처 비교</a></div></article>'
        for index, product in enumerate(products, start=1)
    )
    blog_link = (
        f'<p style="margin-top:18px"><a class="buy" '
        f'href="{html.escape(collection["blogUrl"], quote=True)}" '
        'rel="noopener" target="_blank">네이버 TOP3 비교 글 보기</a></p>'
        if collection.get("blogUrl")
        else ""
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} 비교·추천 | 젬쇼츠</title><meta name="description" content="{description}">
<link rel="canonical" href="{canonical}"><link rel="stylesheet" href="../styles.css">
<meta property="og:title" content="{title}"><meta property="og:description" content="{description}">
<script type="application/ld+json">{json.dumps(structured, ensure_ascii=False)}</script></head>
<body><main><p><a class="watch" href="../">← 전체 추천 제품</a></p>
<header class="hero"><div class="logo">J</div><div><h1>{title}</h1><p>{description}</p></div></header>
<aside class="disclosure"><strong>[광고]</strong> 이 페이지에는 제휴 링크가 포함되어 판매 발생 시 수수료를 제공받습니다.</aside>
<section class="products">{cards}</section>
{blog_link}
<footer>같은 검색 의도와 가격 조건으로 묶은 제품입니다. 구매 전 최신 가격을 확인하세요.</footer>
</main></body></html>"""


def build(hub_dir: Path = HUB) -> list[Path]:
    data = json.loads((hub_dir / "products.json").read_text(encoding="utf-8"))
    products = [product for product in data["products"] if product.get("active", True)]
    freshness_days = int(data.get("offerFreshnessDays", 3))
    by_id = {product["id"]: product for product in products}
    written: list[Path] = []

    pages = hub_dir / "p"
    pages.mkdir(exist_ok=True)
    for old in pages.glob("*.html"):
        old.unlink()
    for product in products:
        path = pages / f"{product['id']}.html"
        path.write_text(render_product(product, freshness_days), encoding="utf-8")
        written.append(path)

    collection_dir = hub_dir / "c"
    collection_dir.mkdir(exist_ok=True)
    for old in collection_dir.glob("*.html"):
        old.unlink()
    collections = [
        collection
        for collection in data.get("collections", [])
        if collection.get("active", True)
    ]
    for collection in collections:
        members = [by_id[key] for key in collection["productIds"] if key in by_id]
        if not members:
            continue
        path = collection_dir / f"{collection['id']}.html"
        path.write_text(render_collection(collection, members), encoding="utf-8")
        written.append(path)

    urls = [
        f"{BASE_URL}/",
        *(f"{BASE_URL}/p/{product['id']}.html" for product in products),
        *(f"{BASE_URL}/c/{collection['id']}.html" for collection in collections),
    ]
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{html.escape(url)}</loc></url>" for url in urls)
        + "</urlset>\n"
    )
    (hub_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (hub_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )
    return written


if __name__ == "__main__":
    for output in build():
        print(output)
