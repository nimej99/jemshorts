from scripts.commerce_roundup import _evidence_hook


def _product(*prices):
    return {
        "id": "p",
        "offers": [
            {
                "merchant": f"판매처{index}",
                "price": price,
                "priceText": f"{price:,}원",
                "affiliateUrl": f"https://example.com/{index}",
                "active": True,
            }
            for index, price in enumerate(prices)
        ],
    }


def test_evidence_hook_uses_verified_same_product_price_gap():
    hook = _evidence_hook([_product(7180, 8460)], "차량 방향제 TOP3")

    assert hook == "같은 상품인데 판매처만 바꿔도 최대 1,280원 차이 납니다."


def test_evidence_hook_falls_back_to_comparison_not_fake_anecdote():
    hook = _evidence_hook([_product(9900)], "보조배터리 TOP3")

    assert hook == "리뷰 수만 보고 고르기 전에 보조배터리 TOP3의 가격과 구성을 비교했습니다."
