"""사이트 인지 브랜드킷 보강 (인스타그램 / 네이버 지역검색 / 일반 OG).

고객사 정보 관리용 수집 경로 3종. M1 원칙(무로그인·읽기 전용) 유지:

- **인스타그램**: Instaloader(MIT 오픈소스) 익명 컨텍스트로 공개 프로필
  메타(상호/소개)를 가져온다. 로그인월·레이트리밋·미설치 시 OG 메타
  폴백 (인스타는 비로그인에도 OG 를 서빙한다 — 실측 확인).
- **네이버**: 플레이스 페이지는 클라이언트 렌더 + 봇차단(실측 429)이라
  스크래핑하지 않는다. 대신 **공식 네이버 오픈API 지역검색**(무료 키)을
  쓴다 — config `naver_client_id` / `naver_client_secret` 필요.
- **일반 URL**: 기존 OG 크롤러(crawler.crawl, SSRF 가드) 그대로.

모든 수집 결과는 "비어 있는 필드만 채움" 정책으로 병합된다 (호출자 몫).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from loguru import logger

from app.config import config
from app.promo.brandkit import crawler

SITE_INSTAGRAM = "instagram"
SITE_NAVER_PLACE = "naver-place"
SITE_GENERIC = "generic"

NAVER_LOCAL_API = "https://openapi.naver.com/v1/search/local.json"
_NAVER_TIMEOUT_S = 10

# "Name (@handle) • Instagram photos and videos" / "이름(@handle) • Instagram ..."
_IG_TITLE_RE = re.compile(r"^(?P<name>.*?)\s*\(@(?P<handle>[A-Za-z0-9._]+)\)")
# og:description 선두의 "686M Followers, 264 Following, 8,534 Posts - bio..."
_IG_STATS_PREFIX_RE = re.compile(
    r"^[\d.,KMB만천억\s]+ ?(Followers|팔로워)[^-–]*[-–]\s*", re.IGNORECASE
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_NAVER_PLACE_HOSTS = (
    "place.naver.com",
    "m.place.naver.com",
    "pcmap.place.naver.com",
    "map.naver.com",
    "naver.me",
)
_INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com", "m.instagram.com")


class NaverApiNotConfiguredError(RuntimeError):
    """네이버 오픈API 키 미설정."""


class NaverApiError(RuntimeError):
    """네이버 지역검색 호출 실패."""


@dataclass(frozen=True)
class EnrichResult:
    """수집 결과 (crawler.CrawlResult 와 동일한 status 계약 + site)."""

    site: str
    status: str  # "ok" | "partial" | "failed"
    fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def detect_site(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in _INSTAGRAM_HOSTS:
        return SITE_INSTAGRAM
    if any(host == h or host.endswith("." + h) for h in _NAVER_PLACE_HOSTS):
        return SITE_NAVER_PLACE
    return SITE_GENERIC


_IG_RESERVED_PATHS = frozenset(
    {"p", "reel", "reels", "explore", "accounts", "stories", "direct", "about"}
)


def instagram_handle(url: str) -> str | None:
    """프로필 URL 에서 핸들을 추출한다 (프로필이 아니면 None)."""
    path = urllib.parse.urlparse(url).path.strip("/")
    if not path or "/" in path or path.lower() in _IG_RESERVED_PATHS:
        # /p/<post>, /reel/<id> 등은 프로필이 아니다
        return None
    return path


def map_instagram_og(fields: dict) -> dict:
    """인스타그램 OG 메타를 브랜드킷 필드 후보로 변환한다 (순수 함수)."""
    mapped: dict = {}
    title = fields.get("og_title") or fields.get("title") or ""
    match = _IG_TITLE_RE.match(title)
    if match and match.group("name").strip():
        mapped["business_name"] = match.group("name").strip()
    description = fields.get("og_description") or fields.get("description") or ""
    if description:
        mapped["description"] = _IG_STATS_PREFIX_RE.sub("", description).strip()
    return mapped


def _fetch_instagram_profile(handle: str) -> dict:
    """Instaloader 익명 컨텍스트로 공개 프로필 필드를 가져온다.

    실패(미설치/로그인월/레이트리밋)는 예외로 전파 — 호출자가 OG 폴백.
    """
    import instaloader  # 지연 임포트 (선택 의존, 무거움)

    loader = instaloader.Instaloader(
        quiet=True, download_pictures=False, download_videos=False
    )
    profile = instaloader.Profile.from_username(loader.context, handle)
    fields = {"sns_url": f"https://www.instagram.com/{handle}/"}
    if profile.full_name:
        fields["business_name"] = profile.full_name
    if profile.biography:
        fields["description"] = profile.biography
    return fields


def enrich_instagram(url: str) -> EnrichResult:
    handle = instagram_handle(url)
    if not handle:
        return EnrichResult(
            site=SITE_INSTAGRAM,
            status="failed",
            warnings=["프로필 URL 이 아닙니다 (예: https://instagram.com/<계정>)"],
        )
    warnings: list = []
    try:
        fields = _fetch_instagram_profile(handle)
        return EnrichResult(site=SITE_INSTAGRAM, status="ok", fields=fields)
    except Exception as exc:  # instaloader 계열 예외 다양 — 전부 OG 폴백
        warnings.append(f"instaloader 실패, OG 폴백: {exc}")
        logger.warning(f"instagram enrich 폴백 ({handle}): {exc}")

    crawled = crawler.crawl(url)
    if crawled.status == "failed":
        return EnrichResult(
            site=SITE_INSTAGRAM,
            status="failed",
            warnings=warnings + crawled.warnings,
        )
    fields = map_instagram_og(crawled.fields)
    fields["sns_url"] = f"https://www.instagram.com/{handle}/"
    return EnrichResult(
        site=SITE_INSTAGRAM,
        status="partial",
        fields=fields,
        warnings=warnings + crawled.warnings,
    )


def enrich_generic(url: str) -> EnrichResult:
    crawled = crawler.crawl(url)
    fields: dict = {}
    if crawled.status != "failed":
        raw = crawled.fields
        name = raw.get("og_title") or raw.get("title")
        if name:
            fields["business_name"] = name
        description = raw.get("og_description") or raw.get("description")
        if description:
            fields["description"] = description
        fields["sns_url"] = url
    return EnrichResult(
        site=SITE_GENERIC,
        status=crawled.status,
        fields=fields,
        warnings=list(crawled.warnings),
    )


# 네이버 플레이스 URL 에서 place id 추출 (/restaurant/{id}/, /place/{id}, /p/entry/place/{id})
_NAVER_PLACE_ID_RE = re.compile(r"/(?:place|restaurant|cafe|hairshop|hospital|entry/place)/(\d+)")


def scrapling_enabled() -> bool:
    """Scrapling 보조 수집 활성 여부 (기본 비활성 — 약관 리스크는 운영 결정)."""
    return bool(config.app.get("promo_scrapling_enabled", False))


def naver_place_id(url: str) -> str | None:
    match = _NAVER_PLACE_ID_RE.search(urllib.parse.urlparse(url).path)
    return match.group(1) if match else None


def parse_naver_place_html(html: str) -> dict:
    """pcmap 페이지의 __APOLLO_STATE__ 에서 브랜드킷 필드 후보를 추출한다.

    순수 함수 (오프라인 테스트 대상). PlaceDetailBase 엔트리가 없으면 빈 dict.
    """
    match = re.search(r"window\.__APOLLO_STATE__\s*=\s*", html)
    if not match:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except ValueError:
        return {}
    for key, entry in data.items():
        if not key.startswith("PlaceDetailBase") or not isinstance(entry, dict):
            continue
        fields: dict = {}
        if entry.get("name"):
            fields["business_name"] = str(entry["name"]).strip()
        if entry.get("category"):
            fields["category"] = str(entry["category"]).strip()
        address = entry.get("roadAddress") or entry.get("address")
        if address:
            fields["address"] = str(address).strip()
        phone = entry.get("phone") or entry.get("virtualPhone")
        if phone:
            fields["phone"] = str(phone).strip()
        if entry.get("microReview"):
            review = entry["microReview"]
            if isinstance(review, list):
                review = " ".join(str(item) for item in review if item)
            if str(review).strip():
                fields["description"] = str(review).strip()
        if fields:
            return fields
    return {}


def _fetch_rendered_html(url: str) -> str:
    """Scrapling StealthyFetcher 로 JS/봇월 통과 후 HTML 을 가져온다.

    선택 의존 — 미설치면 RuntimeError 로 안내한다 (조용한 빈 결과 금지).
    설치: `uv pip install scrapling && scrapling install`
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError as exc:
        raise RuntimeError(
            "Scrapling 미설치: `uv pip install scrapling && scrapling install` "
            "후 다시 시도하세요"
        ) from exc

    page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    status = getattr(page, "status", None)
    if status != 200:
        raise RuntimeError(f"응답 상태 {status}")
    return page.html_content


def enrich_naver_place(url: str) -> EnrichResult:
    """Scrapling 경유 네이버 플레이스 수집 (config 로 명시 활성화한 경우만).

    실측 근거: 모바일 페이지는 클라이언트 렌더(빈 Apollo), pcmap 은 일반
    HTTP 클라이언트에 429 — 실브라우저(StealthyFetcher)로 pcmap 을 연다.
    """
    place_id = naver_place_id(url)
    if place_id is None:
        return EnrichResult(
            site=SITE_NAVER_PLACE,
            status="failed",
            warnings=[
                "URL 에서 place id 를 찾지 못했습니다 "
                "(지원: m.place.naver.com/*/{id}, map.naver.com/p/entry/place/{id}). "
                "naver.me 단축링크는 원본 URL 로 바꿔 주세요."
            ],
        )
    pcmap_url = f"https://pcmap.place.naver.com/place/{place_id}/home"
    try:
        html = _fetch_rendered_html(pcmap_url)
    except RuntimeError as exc:
        return EnrichResult(
            site=SITE_NAVER_PLACE, status="failed", warnings=[f"수집 실패: {exc}"]
        )
    fields = parse_naver_place_html(html)
    if not fields:
        return EnrichResult(
            site=SITE_NAVER_PLACE,
            status="failed",
            warnings=["렌더된 페이지에서 플레이스 정보를 찾지 못했습니다"],
        )
    return EnrichResult(site=SITE_NAVER_PLACE, status="ok", fields=fields)


def enrich(url: str) -> EnrichResult:
    """URL 사이트를 감지해 맞는 수집기로 라우팅한다."""
    site = detect_site(url)
    if site == SITE_INSTAGRAM:
        return enrich_instagram(url)
    if site == SITE_NAVER_PLACE:
        if scrapling_enabled():
            return enrich_naver_place(url)
        return EnrichResult(
            site=SITE_NAVER_PLACE,
            status="failed",
            warnings=[
                "네이버 플레이스 페이지는 봇 차단으로 기본 수집하지 않습니다. "
                "네이버 지역검색(공식 API, POST /brandkit/naver-local)을 쓰거나, "
                "보조 수집을 켜려면 config `promo_scrapling_enabled = true` "
                "(Scrapling 설치 필요, 약관 리스크는 운영 판단)"
            ],
        )
    return enrich_generic(url)


def _naver_credentials() -> tuple[str, str]:
    client_id = str(config.app.get("naver_client_id", "")).strip()
    client_secret = str(config.app.get("naver_client_secret", "")).strip()
    if not client_id or not client_secret:
        raise NaverApiNotConfiguredError(
            "네이버 오픈API 키가 없습니다: config 에 naver_client_id / "
            "naver_client_secret 을 설정하세요 (developers.naver.com 무료 발급)"
        )
    return client_id, client_secret


def naver_local_search(query: str, display: int = 5) -> list[dict]:
    """네이버 지역검색(공식 오픈API)으로 상호를 검색한다.

    반환 항목 키: business_name, category, address, road_address, phone, link.
    키 미설정 -> NaverApiNotConfiguredError, 호출 실패 -> NaverApiError.
    """
    if not query or not query.strip():
        raise NaverApiError("검색어가 비어 있습니다")
    client_id, client_secret = _naver_credentials()

    params = urllib.parse.urlencode({"query": query.strip(), "display": display})
    request = urllib.request.Request(
        f"{NAVER_LOCAL_API}?{params}",
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_NAVER_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NaverApiError(f"지역검색 호출 실패: {exc}") from exc

    results = []
    for item in payload.get("items", []):
        results.append(
            {
                "business_name": _HTML_TAG_RE.sub("", item.get("title", "")).strip(),
                "category": (item.get("category") or "").strip(),
                "address": (item.get("address") or "").strip(),
                "road_address": (item.get("roadAddress") or "").strip(),
                "phone": (item.get("telephone") or "").strip(),
                "link": (item.get("link") or "").strip(),
            }
        )
    return results
