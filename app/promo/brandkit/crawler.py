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

SSRF 방어 (사용자 입력 URL 을 서버가 대신 요청하므로 필수):
- http/https 스킴만 허용.
- 호스트를 resolve 한 뒤 사설/루프백/링크로컬 등 비공인 IP 대역 차단
  (ipaddress 모듈).
- 리다이렉트는 urllib 기본 처리를 따르되, 각 hop 의 새 URL 도 동일한
  검증을 통과해야 한다 (_SSRFGuardRedirectHandler).
- 응답 read 상한 MAX_RESPONSE_BYTES (2MB) — 초과분은 절단한다.
- 차단 시에도 예외를 던지지 않고 status="failed" + warnings 사유로 수렴한다.

네트워크는 stdlib urllib 만 사용하며 timeout 기본값은 10초다.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

DEFAULT_TIMEOUT_S = 10
# 응답 read 상한 (초과분 절단) — 메타 태그는 문서 앞부분에 있으므로 충분하다.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# SSRF 방어: 허용 스킴 화이트리스트
_ALLOWED_SCHEMES = ("http", "https")

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


class SSRFBlockedError(ValueError):
    """SSRF 방어 규칙 위반 URL. crawl 은 이를 status="failed" 로 수렴한다."""


def _validate_url(url: str) -> None:
    """스킴 화이트리스트 + resolve 후 IP 대역 검증. 위반 시 SSRFBlockedError."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(
            f"허용되지 않는 스킴: {parsed.scheme or '(없음)'} "
            f"(허용: {', '.join(_ALLOWED_SCHEMES)})"
        )
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("호스트가 없는 URL 입니다")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise SSRFBlockedError(f"호스트 resolve 실패: {host} ({exc})") from exc
    for info in infos:
        # sockaddr[0] 은 IP 문자열. IPv6 링크로컬은 '%scope' 가 붙을 수 있다.
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise SSRFBlockedError(
                f"resolve 결과 해석 불가: {host} -> {addr}"
            ) from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SSRFBlockedError(f"차단된 IP 대역: {host} -> {ip}")


class _SSRFGuardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """리다이렉트 각 hop 의 새 URL 에도 동일한 SSRF 검증을 적용한다.

    검증 실패 시 SSRFBlockedError(ValueError) 가 opener.open 밖으로 전파되고,
    crawl 의 _FETCH_ERRORS 처리에서 status="failed" 로 수렴한다.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(url: str, timeout: float):
    """SSRF 가드 리다이렉트 핸들러가 적용된 opener 로 URL 을 연다."""
    opener = urllib.request.build_opener(_SSRFGuardRedirectHandler())
    return opener.open(url, timeout=timeout)


def crawl(url: str, timeout: float = DEFAULT_TIMEOUT_S) -> CrawlResult:
    """URL 하나를 베스트에포트로 크롤해 CrawlResult 를 반환한다.

    예외를 밖으로 던지지 않는다 — 모든 실패는 status="failed" + warnings 로
    수렴한다 (모듈 docstring 의 실패 허용 설계 참조).
    """
    try:
        _validate_url(url)
    except SSRFBlockedError as exc:
        return _failed(f"차단된 URL: {exc}")

    try:
        with _urlopen(url, timeout=timeout) as resp:
            content_type = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            # 응답 read 상한 — 초과분은 절단한다 (메타 태그는 앞부분에 위치).
            body = resp.read(MAX_RESPONSE_BYTES)
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
