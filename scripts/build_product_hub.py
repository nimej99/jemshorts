"""products.json에서 검색엔진용 정적 제품 페이지·사이트맵을 생성한다."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "product-hub"
BASE_URL = "https://nimej99.github.io/jemshorts"


def _price(text: str) -> str:
    numbers = re.findall(r"[\d,]+", text)
    return numbers[-1].replace(",", "") if numbers else "0"


def _video_id(url: str) -> str:
    return parse_qs(urlparse(url).query).get("v", [""])[0]


def render_product(product: dict) -> str:
    name = html.escape(product["name"])
    summary = html.escape(product["summary"])
    affiliate = html.escape(product["affiliateUrl"], quote=True)
    image = html.escape(product["image"], quote=True)
    video_id = html.escape(_video_id(product["videoUrl"]), quote=True)
    canonical = f"{BASE_URL}/p/{product['id']}.html"
    structured = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product["name"],
        "description": product["summary"],
        "image": [product["image"]],
        "sku": product["itemId"],
        "offers": {
            "@type": "Offer",
            "url": product["affiliateUrl"],
            "priceCurrency": "KRW",
            "price": _price(product["priceText"]),
            "availability": "https://schema.org/InStock",
        },
    }
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} 가격·리뷰·영상 | 젬쇼츠</title>
<meta name="description" content="{summary} 현재 가격과 젬쇼츠 소개 영상을 확인하세요.">
<link rel="canonical" href="{canonical}"><link rel="stylesheet" href="../styles.css">
<meta property="og:type" content="product"><meta property="og:title" content="{name}">
<meta property="og:description" content="{summary}"><meta property="og:image" content="{image}">
<script type="application/ld+json">{json.dumps(structured, ensure_ascii=False)}</script></head>
<body><main><p><a class="watch" href="../">← 전체 추천 제품</a></p>
<header class="hero"><div class="logo">J</div><div><h1>{name}</h1><p>{summary}</p></div></header>
<aside class="disclosure"><strong>[광고]</strong> 이 페이지는 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</aside>
<article class="card" style="margin-top:22px"><img class="product-image" src="{image}" alt="{name} 실제 상품 이미지"><div class="content">
<h2>{name}</h2><p class="summary">{summary}</p><p class="price">{html.escape(product['priceText'])}</p>
<div class="actions"><a class="buy" href="{affiliate}" rel="sponsored nofollow noopener" target="_blank">쿠팡에서 최신 가격 확인</a><a class="watch" href="{html.escape(product['videoUrl'], quote=True)}">YouTube 쇼츠 보기</a></div>
<p class="updated">정보 확인: {html.escape(product['checkedAt'])}</p></div></article>
<section style="margin-top:22px"><h2>소개 영상</h2><iframe width="100%" height="420" src="https://www.youtube.com/embed/{video_id}" title="{name} 소개 영상" frameborder="0" allowfullscreen></iframe></section>
<footer>가격과 재고는 판매처에서 변경될 수 있습니다. 구매 전 쿠팡의 최종 정보를 확인하세요.</footer></main></body></html>"""


def build(hub_dir: Path = HUB) -> list[Path]:
    data = json.loads((hub_dir / "products.json").read_text(encoding="utf-8"))
    products = [product for product in data["products"] if product.get("active", True)]
    pages = hub_dir / "p"
    pages.mkdir(exist_ok=True)
    written = []
    for old in pages.glob("*.html"):
        old.unlink()
    for product in products:
        path = pages / f"{product['id']}.html"
        path.write_text(render_product(product), encoding="utf-8")
        written.append(path)
    urls = [f"{BASE_URL}/", *(f"{BASE_URL}/p/{p['id']}.html" for p in products)]
    sitemap = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join(f"<url><loc>{html.escape(url)}</loc></url>" for url in urls) + "</urlset>\n"
    (hub_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (hub_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    return written


if __name__ == "__main__":
    for output in build():
        print(output)
