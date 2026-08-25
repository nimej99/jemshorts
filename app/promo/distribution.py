"""동일 상품 원장에서 채널별로 다른 문법의 배포 콘텐츠를 생성한다."""

from __future__ import annotations

from datetime import date


def active_offers(product: dict) -> list[dict]:
    return sorted(
        (offer for offer in product.get("offers", []) if offer.get("active", True)),
        key=lambda offer: offer["price"],
    )


def _feature(summary: str) -> str:
    return summary.split("·")[0].strip()


def blog_markdown(products: list[dict], *, title: str, hub_url: str) -> str:
    if not products:
        raise ValueError("블로그 콘텐츠에 사용할 상품이 없습니다")
    lines = [
        f"# {title}",
        "",
        "**[광고] 이 글에는 제휴 링크가 포함되어 있으며, 링크를 통한 구매 발생 시 일정액의 수수료를 제공받습니다.**",
        "",
        f"> 가격·재고 확인일: {date.today().isoformat()}  ",
        "> 판매처 가격은 수시로 달라질 수 있으므로 구매 전 최종 가격을 확인하세요.",
        "",
        "## 한눈에 비교",
        "",
        "| 상품 | 현재 최저가 | 판매처 | 핵심 특징 |",
        "|---|---:|---|---|",
    ]
    for product in products:
        offers = active_offers(product)
        if not offers:
            continue
        best = offers[0]
        lines.append(
            f"| {product['name']} | {best['priceText']} | {best['merchant']} | "
            f"{_feature(product['summary'])} |"
        )
    lines.extend(["", "## 제품별로 살펴보기", ""])
    for index, product in enumerate(products, start=1):
        offers = active_offers(product)
        if not offers:
            continue
        lines.extend(
            [
                f"### {index}. {product['name']}",
                "",
                product["summary"],
                "",
                f"- 소개 영상: {product['videoUrl']}",
            ]
        )
        for offer in offers:
            shipping = f" · {offer.get('shippingText')}" if offer.get("shippingText") else ""
            lines.append(
                f"- [{offer['merchant']} {offer['priceText']}{shipping} 확인]"
                f"({offer['affiliateUrl']})"
            )
        lines.append("")
    lines.extend(
        [
            "## 고르는 기준",
            "",
            "1. 필요한 기능과 용량을 먼저 정합니다.",
            "2. 표시 가격뿐 아니라 배송비와 구성품을 함께 확인합니다.",
            "3. 리뷰 수는 참고하되 최근 리뷰의 반복 불만을 확인합니다.",
            "4. 같은 상품도 판매처별 최종 결제 금액을 비교합니다.",
            "",
            "## 자주 묻는 질문",
            "",
            "### 표시 가격과 실제 가격이 다른 이유는 무엇인가요?",
            "쿠폰, 회원 여부, 옵션과 배송 조건에 따라 최종 가격이 달라질 수 있습니다.",
            "",
            "### 어떤 판매처 링크를 눌러야 하나요?",
            "표의 최저가는 확인 시점 기준입니다. 각 버튼에서 현재 가격과 배송 조건을 다시 비교하세요.",
            "",
            f"전체 제품과 최신 가격 비교: {hub_url}",
        ]
    )
    return "\n".join(lines) + "\n"


def clip_caption(product: dict, *, hub_url: str) -> str:
    offers = active_offers(product)
    if not offers:
        raise ValueError(f"활성 판매 오퍼가 없습니다: {product['id']}")
    best = offers[0]
    tags = ["제품추천", "생활꿀템", *product.get("badges", [])]
    hashtags = " ".join(f"#{tag.replace(' ', '')}" for tag in dict.fromkeys(tags))
    return (
        f"[광고] {product['name']}\n"
        f"{product['summary']}\n"
        f"확인 시점 최저 {best['merchant']} {best['priceText']}\n\n"
        f"제품·판매처 비교: {hub_url}\n\n"
        "이 콘텐츠에는 제휴 링크가 포함되어 있으며, 링크를 통한 구매 발생 시 "
        f"일정액의 수수료를 제공받습니다.\n\n{hashtags}\n"
    )
