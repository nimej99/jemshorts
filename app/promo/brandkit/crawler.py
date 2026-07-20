"""브랜드킷 베스트에포트 URL 크롤러 (stdlib urllib + html.parser 전용).

정적 HTML 에서 OG 태그(og:title / og:description / og:image)와 <title>,
meta description 을 추출한다. og:image 는 URL 문자열만 기록하며 다운로드는
후속 단계에서 수행한다.

실패 허용 설계 (failure-tolerant by design):
- 네이버플레이스/인스타그램 등은 본문을 JS 렌더링으로 채우므로 정적 HTML
  크롤이 실패(failed)하거나 partial 에 그치는 것이 정상 동작이다.
  이 경우 검수 폼에서 수동 입력으로 보완한다 (store.merge_manual 참조).
- 네트워크 예외 / 비 HTML 응답 / 빈 페이지 → status="failed".
- 일부 필드만 추출 → status="partial". OG 3종(title/description/image)
  전부 추출 → status="ok".

네트워크는 stdlib urllib 만 사용하며 timeout 기본값은 10초다.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

DEFAULT_TIMEOUT_S = 10

# ok 판정에 필요한 OG 필드 3종
_OG_FIELDS = ("og_title", "og_description", "og_image")
# og:property -> fields 키 매핑
_OG_PROPERTY_MAP = {
    "og:title": "og_title",
    "og:description": "og_description",
    "og:image": "og_image",
}
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
# 크롤 실패로 취급하는 예외 (베스트에포트 — 전부 failed 로 수렴)
_FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


@dataclass
class CrawlResult:
    status: str  # "ok" | "partial" | "failed"
    fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class _MetaTitleParser(HTMLParser):
    """OG 메타 태그 + <title> + meta description 추출기."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        attr = dict(attrs)
        content = (attr.get("content") or "").strip()
        if not content:
            return
        prop = (attr.get("property") or "").strip().lower()
        if prop in _OG_PROPERTY_MAP:
            self.fields.setdefault(_OG_PROPERTY_MAP[prop], content)
            return
        name = (attr.get("name") or "").strip().lower()
        if name == "description":
            self.fields.setdefault("description", content)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            title = "".join(self._title_parts).strip()
            if title:
                self.fields.setdefault("title", title)
            self._title_parts = []

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)


def _failed(warning: str) -> CrawlResult:
    return CrawlResult(status="failed", fields={}, warnings=[warning])


def crawl(url: str, timeout: float = DEFAULT_TIMEOUT_S) -> CrawlResult:
    """URL 하나를 베스트에포트로 크롤해 CrawlResult 를 반환한다.

    예외를 밖으로 던지지 않는다 — 모든 실패는 status="failed" + warnings 로
    수렴한다 (모듈 docstring 의 실패 허용 설계 참조).
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            content_type = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read()
    except _FETCH_ERRORS as exc:
        return _failed(f"요청 실패: {exc}")

    if content_type not in _HTML_CONTENT_TYPES:
        return _failed(f"HTML 이 아닌 응답: {content_type}")

    html = body.decode(charset, errors="replace").strip()
    if not html:
        return _failed("빈 페이지")

    parser = _MetaTitleParser()
    parser.feed(html)
    parser.close()
    fields = parser.fields

    if not fields:
        return _failed("추출된 필드 없음 (JS 렌더링 페이지일 수 있음)")

    missing_og = [key for key in _OG_FIELDS if key not in fields]
    if not missing_og:
        return CrawlResult(status="ok", fields=fields, warnings=[])
    return CrawlResult(
        status="partial",
        fields=fields,
        warnings=[f"OG 필드 누락: {', '.join(missing_og)}"],
    )
