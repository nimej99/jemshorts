"""사이트 인지 브랜드킷 보강(enrich) 테스트 — 실 네트워크 없음."""

import io
import json
import urllib.request

import pytest

from app.config import config
from app.promo.brandkit import crawler, enrich
from app.promo.brandkit.crawler import CrawlResult

# ── 사이트 감지 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://www.instagram.com/cafe.abc/", enrich.SITE_INSTAGRAM),
        ("https://instagram.com/cafe.abc", enrich.SITE_INSTAGRAM),
        ("https://m.place.naver.com/restaurant/123/home", enrich.SITE_NAVER_PLACE),
        ("https://map.naver.com/p/entry/place/123", enrich.SITE_NAVER_PLACE),
        ("https://naver.me/abc123", enrich.SITE_NAVER_PLACE),
        ("https://example.com/about", enrich.SITE_GENERIC),
    ],
)
def test_detect_site(url, site):
    assert enrich.detect_site(url) == site


@pytest.mark.parametrize(
    ("url", "handle"),
    [
        ("https://www.instagram.com/cafe.abc/", "cafe.abc"),
        ("https://instagram.com/cafe_abc", "cafe_abc"),
        ("https://www.instagram.com/p/XyZ123/", None),  # 게시물
        ("https://www.instagram.com/reel/XyZ123/", None),
        ("https://www.instagram.com/explore/", None),  # 예약 경로
        ("https://www.instagram.com/", None),
    ],
)
def test_instagram_handle(url, handle):
    assert enrich.instagram_handle(url) == handle


# ── 인스타그램 OG 매핑 ───────────────────────────────────────────────


def test_map_instagram_og_extracts_name_and_bio():
    fields = {
        "og_title": "우리동네 카페 (@cafe.abc) • Instagram photos and videos",
        "og_description": (
            "1,234 Followers, 56 Following, 78 Posts - 매일 아침 직접 로스팅합니다"
        ),
    }
    mapped = enrich.map_instagram_og(fields)
    assert mapped["business_name"] == "우리동네 카페"
    assert mapped["description"] == "매일 아침 직접 로스팅합니다"


def test_map_instagram_og_keeps_description_without_stats_prefix():
    mapped = enrich.map_instagram_og({"og_description": "소개문만 있는 경우"})
    assert mapped["description"] == "소개문만 있는 경우"
    assert "business_name" not in mapped


# ── enrich 라우팅 ────────────────────────────────────────────────────


def test_enrich_instagram_uses_instaloader(monkeypatch):
    monkeypatch.setattr(
        enrich,
        "_fetch_instagram_profile",
        lambda handle: {
            "business_name": "우리동네 카페",
            "description": "로스팅 전문",
            "sns_url": f"https://www.instagram.com/{handle}/",
        },
    )
    result = enrich.enrich("https://www.instagram.com/cafe.abc/")

    assert result.site == enrich.SITE_INSTAGRAM
    assert result.status == "ok"
    assert result.fields["business_name"] == "우리동네 카페"


def test_enrich_instagram_falls_back_to_og(monkeypatch):
    def broken(handle):
        raise RuntimeError("login wall")

    monkeypatch.setattr(enrich, "_fetch_instagram_profile", broken)
    monkeypatch.setattr(
        crawler,
        "crawl",
        lambda url, **kw: CrawlResult(
            status="ok",
            fields={
                "og_title": "우리동네 카페 (@cafe.abc) • Instagram photos and videos",
                "og_description": "10 Followers, 2 Following, 3 Posts - 소개",
                "og_image": "https://example.com/x.jpg",
            },
            warnings=[],
        ),
    )
    result = enrich.enrich("https://www.instagram.com/cafe.abc/")

    assert result.status == "partial"
    assert result.fields["business_name"] == "우리동네 카페"
    assert result.fields["sns_url"] == "https://www.instagram.com/cafe.abc/"
    assert any("폴백" in w for w in result.warnings)


def test_enrich_instagram_non_profile_fails():
    result = enrich.enrich("https://www.instagram.com/p/XyZ123/")
    assert result.status == "failed"


def test_enrich_naver_place_refuses_with_guidance():
    result = enrich.enrich("https://m.place.naver.com/restaurant/123/home")

    assert result.site == enrich.SITE_NAVER_PLACE
    assert result.status == "failed"
    assert any("naver-local" in w for w in result.warnings)


def test_enrich_generic_maps_og(monkeypatch):
    monkeypatch.setattr(
        crawler,
        "crawl",
        lambda url, **kw: CrawlResult(
            status="ok",
            fields={"og_title": "우리가게", "og_description": "소개"},
            warnings=[],
        ),
    )
    result = enrich.enrich("https://example.com")

    assert result.site == enrich.SITE_GENERIC
    assert result.fields == {
        "business_name": "우리가게",
        "description": "소개",
        "sns_url": "https://example.com",
    }


# ── 네이버 지역검색 (공식 API) ───────────────────────────────────────

NAVER_PAYLOAD = {
    "items": [
        {
            "title": "우리동네 <b>분식</b>",
            "category": "음식점>분식",
            "address": "서울 마포구 1-1",
            "roadAddress": "서울 마포구 도로명로 1",
            "telephone": "02-123-4567",
            "link": "https://example.com/shop",
        }
    ]
}


def _fake_urlopen(payload):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=0):
        # urllib 가 헤더 키를 capitalize 함 — API HUB 인증 헤더 + 호출 주소 고정
        assert request.get_header("X-ncp-apigw-api-key-id") == "cid"
        assert request.get_header("X-ncp-apigw-api-key") == "sec"
        assert request.full_url.startswith(enrich.NAVER_LOCAL_API)
        return _Resp(json.dumps(payload).encode("utf-8"))

    return opener


def test_naver_local_search(monkeypatch):
    monkeypatch.setitem(config.app, "naver_client_id", "cid")
    monkeypatch.setitem(config.app, "naver_client_secret", "sec")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(NAVER_PAYLOAD))

    results = enrich.naver_local_search("우리동네 분식")

    assert results[0]["business_name"] == "우리동네 분식"  # <b> 태그 제거
    assert results[0]["category"] == "음식점>분식"
    assert results[0]["road_address"] == "서울 마포구 도로명로 1"


def test_naver_local_search_requires_credentials(monkeypatch):
    monkeypatch.delitem(config.app, "naver_client_id", raising=False)
    monkeypatch.delitem(config.app, "naver_client_secret", raising=False)

    with pytest.raises(enrich.NaverApiNotConfiguredError, match="naver_client_id"):
        enrich.naver_local_search("우리동네 분식")


def test_naver_local_search_rejects_empty_query(monkeypatch):
    monkeypatch.setitem(config.app, "naver_client_id", "cid")
    monkeypatch.setitem(config.app, "naver_client_secret", "sec")

    with pytest.raises(enrich.NaverApiError, match="비어"):
        enrich.naver_local_search("  ")


# ── Scrapling 보조 수집 (네이버 플레이스, 기본 비활성) ───────────────

APOLLO_HTML = """<html><head><script>
window.__APOLLO_STATE__ = {"ROOT_QUERY":{},"PlaceDetailBase:123":{
"name":"우리동네 분식","category":"분식","roadAddress":"서울 마포구 도로명로 1",
"virtualPhone":"050-1234-5678","microReview":["떡볶이 맛집"]}};
</script></head><body></body></html>"""


@pytest.mark.parametrize(
    ("url", "place_id"),
    [
        ("https://m.place.naver.com/restaurant/13153842/home", "13153842"),
        ("https://m.place.naver.com/place/123/home", "123"),
        ("https://map.naver.com/p/entry/place/456", "456"),
        ("https://naver.me/abc123", None),  # 단축링크는 id 없음
        ("https://m.place.naver.com/", None),
    ],
)
def test_naver_place_id(url, place_id):
    assert enrich.naver_place_id(url) == place_id


def test_parse_naver_place_html_extracts_fields():
    fields = enrich.parse_naver_place_html(APOLLO_HTML)

    assert fields == {
        "business_name": "우리동네 분식",
        "category": "분식",
        "address": "서울 마포구 도로명로 1",
        "phone": "050-1234-5678",
        "description": "떡볶이 맛집",
    }


def test_parse_naver_place_html_empty_cases():
    assert enrich.parse_naver_place_html("<html></html>") == {}
    assert (
        enrich.parse_naver_place_html(
            "<script>window.__APOLLO_STATE__ = {broken</script>"
        )
        == {}
    )
    # PlaceDetailBase 없는 Apollo (모바일 페이지 실측 케이스)
    assert (
        enrich.parse_naver_place_html(
            '<script>window.__APOLLO_STATE__ = {"ROOT_QUERY":{}};</script>'
        )
        == {}
    )


def test_naver_place_disabled_by_default(monkeypatch):
    monkeypatch.delitem(config.app, "promo_scrapling_enabled", raising=False)
    result = enrich.enrich("https://m.place.naver.com/restaurant/123/home")

    assert result.status == "failed"
    assert any("promo_scrapling_enabled" in w for w in result.warnings)


def test_naver_place_enabled_fetches_pcmap(monkeypatch):
    monkeypatch.setitem(config.app, "promo_scrapling_enabled", True)
    fetched = {}

    def fake_fetch(url):
        fetched["url"] = url
        return APOLLO_HTML

    monkeypatch.setattr(enrich, "_fetch_rendered_html", fake_fetch)
    result = enrich.enrich("https://m.place.naver.com/restaurant/13153842/home")

    assert fetched["url"] == "https://pcmap.place.naver.com/place/13153842/home"
    assert result.status == "ok"
    assert result.fields["business_name"] == "우리동네 분식"


def test_naver_place_enabled_without_scrapling_fails_clearly(monkeypatch):
    monkeypatch.setitem(config.app, "promo_scrapling_enabled", True)

    def fake_fetch(url):
        raise RuntimeError("Scrapling 미설치: `uv pip install scrapling ...`")

    monkeypatch.setattr(enrich, "_fetch_rendered_html", fake_fetch)
    result = enrich.enrich("https://m.place.naver.com/restaurant/123/home")

    assert result.status == "failed"
    assert any("Scrapling 미설치" in w for w in result.warnings)
