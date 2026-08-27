from app.promo import distribution


PRODUCT = {
    "id": "p1",
    "name": "테스트 보조배터리",
    "summary": "10000mAh · 잔량 표시 · 리뷰 1,000개",
    "videoUrl": "https://www.youtube.com/watch?v=video",
    "badges": ["보조배터리"],
    "offers": [
        {
            "id": "naver",
            "merchant": "네이버",
            "price": 11000,
            "priceText": "11,000원",
            "affiliateUrl": "https://shopping.naver.com/x",
            "shippingText": "무료배송",
            "active": True,
        },
        {
            "id": "coupang",
            "merchant": "쿠팡",
            "price": 9900,
            "priceText": "9,900원",
            "affiliateUrl": "https://link.coupang.com/a/x",
            "shippingText": "",
            "active": True,
        },
    ],
}


def test_blog_contains_comparison_disclosure_and_both_merchants():
    rendered = distribution.blog_markdown(
        [PRODUCT], title="보조배터리 비교", hub_url="https://hub.example"
    )

    assert rendered.startswith("# 보조배터리 비교")
    assert "[광고]" in rendered
    assert "네이버 쇼핑 커넥트 활동의 일환" in rendered
    assert "쿠팡 9,900원" in rendered
    assert "네이버 11,000원" in rendered
    assert "고르는 기준" in rendered
    assert "자주 묻는 질문" in rendered
    assert "https://hub.example" in rendered


def test_clip_caption_uses_lowest_offer_and_affiliate_disclosure():
    rendered = distribution.clip_caption(PRODUCT, hub_url="https://hub.example")

    assert "최저 쿠팡 9,900원" in rendered
    assert "일정액의 수수료" in rendered
    assert "네이버 쇼핑 커넥트 활동의 일환" in rendered
    assert "#보조배터리" in rendered
