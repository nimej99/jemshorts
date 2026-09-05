from scripts.commerce_roundup import _evidence_hook, _product_label, _roundup_title


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

    assert hook == "보조배터리 TOP3, 가격과 구성으로 골랐습니다."


def test_roundup_title_does_not_duplicate_existing_top_marker():
    assert _roundup_title("환절기 온습도계 TOP3", 3) == "환절기 온습도계 TOP3"
    assert _roundup_title("환절기 온습도계", 3) == "환절기 온습도계 TOP3"


def test_product_label_prefers_locked_short_name():
    assert (
        _product_label(
            {
                "name": "카스 백라이트 초정밀 디지털 온습도계 T035",
                "shortName": "카스 T035 백라이트",
            }
        )
        == "카스 T035 백라이트"
    )
