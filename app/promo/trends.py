"""트렌드 폴링 (Google Trends 급상승 RSS -> trends 테이블 캐시, v4).

- 소스: Google Trends 일일 급상승 검색어 RSS. 무료·무키.
  geo 는 config `promo_trends_geo`(기본 "KR").
- 폴링은 스케줄러 tick 에 피기백된다: stale(기본 6시간, config
  `promo_trends_stale_hours`) 이면 갱신 시도.
- 실패 허용 경계:
  - `maybe_refresh()` 는 수집 실패를 삼키고 기존 캐시를 유지한다
    (트렌드는 부가 정보 — 오토파일럿/tick 을 막으면 안 된다).
  - `refresh()` 는 TrendsFetchError 를 던진다 (API 가 502 로 승격).
- 배치 = 같은 fetched_at 값의 행 집합. 7일 지난 배치는 저장 시 정리.
"""

from __future__ import annotations

import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from loguru import logger

from app.config import config

SOURCE_GOOGLE_TRENDS = "google-trends"
RSS_URL_TEMPLATE = "https://trends.google.com/trending/rss?geo={geo}"
DEFAULT_GEO = "KR"
DEFAULT_STALE_HOURS = 6
FETCH_TIMEOUT_S = 15
# 배치 보존 기간 (진단용 이력 — 무한 적재 방지)
KEEP_DAYS = 7

_HT_NS = "{https://trends.google.com/trending/rss}"
_TRAFFIC_DIGITS = re.compile(r"\d+")


class TrendsFetchError(RuntimeError):
    """트렌드 수집 실패 (네트워크/파싱)."""


@dataclass(frozen=True)
class TrendItem:
    keyword: str
    traffic: str = ""
    traffic_value: int = 0
    news_title: str | None = None


def geo() -> str:
    value = str(config.app.get("promo_trends_geo", DEFAULT_GEO)).strip()
    return value or DEFAULT_GEO


def stale_hours() -> float:
    try:
        value = float(config.app.get("promo_trends_stale_hours", DEFAULT_STALE_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_STALE_HOURS
    return value if value > 0 else DEFAULT_STALE_HOURS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _traffic_value(traffic: str) -> int:
    match = _TRAFFIC_DIGITS.search(traffic or "")
    return int(match.group()) if match else 0


def parse_trends_rss(xml_text: str) -> list[TrendItem]:
    """Google Trends RSS XML 을 TrendItem 목록으로 파싱한다 (트래픽 내림차순).

    형식 위반이면 TrendsFetchError.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise TrendsFetchError(f"RSS 파싱 실패: {exc}") from exc

    items: list[TrendItem] = []
    for item in root.iter("item"):
        keyword = (item.findtext("title") or "").strip()
        if not keyword:
            continue
        traffic = (item.findtext(f"{_HT_NS}approx_traffic") or "").strip()
        news_title = None
        news = item.find(f"{_HT_NS}news_item")
        if news is not None:
            news_title = (
                news.findtext(f"{_HT_NS}news_item_title") or ""
            ).strip() or None
        items.append(
            TrendItem(
                keyword=keyword,
                traffic=traffic,
                traffic_value=_traffic_value(traffic),
                news_title=news_title,
            )
        )
    if not items:
        raise TrendsFetchError("RSS 에 트렌드 항목이 없습니다")
    return sorted(items, key=lambda t: t.traffic_value, reverse=True)


def fetch_google_trends(timeout: float = FETCH_TIMEOUT_S) -> list[TrendItem]:
    """Google Trends RSS 를 받아 파싱한다. 실패 시 TrendsFetchError."""
    url = RSS_URL_TEMPLATE.format(geo=geo())
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (promo-shorts trends poller)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read()
    except OSError as exc:
        raise TrendsFetchError(f"요청 실패: {exc}") from exc
    return parse_trends_rss(body.decode("utf-8", errors="replace"))


def save_batch(
    conn: sqlite3.Connection,
    items: Sequence[TrendItem],
    now: datetime | None = None,
) -> str:
    """배치를 저장하고 fetched_at(배치 식별자)을 반환한다. 오래된 배치는 정리."""
    now = now or _now()
    fetched_at = now.isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO trends (source, keyword, traffic, traffic_value, "
        "news_title, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                SOURCE_GOOGLE_TRENDS,
                item.keyword,
                item.traffic,
                item.traffic_value,
                item.news_title,
                fetched_at,
            )
            for item in items
        ],
    )
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM trends WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    return fetched_at


def latest(
    conn: sqlite3.Connection, limit: int = 10
) -> tuple[str | None, list[TrendItem]]:
    """최신 배치의 (fetched_at, 항목들) 을 트래픽 내림차순으로 반환한다."""
    row = conn.execute("SELECT MAX(fetched_at) FROM trends").fetchone()
    fetched_at = row[0]
    if fetched_at is None:
        return None, []
    rows = conn.execute(
        "SELECT keyword, traffic, traffic_value, news_title FROM trends "
        "WHERE fetched_at = ? ORDER BY traffic_value DESC, id ASC LIMIT ?",
        (fetched_at, limit),
    ).fetchall()
    return fetched_at, [
        TrendItem(
            keyword=r["keyword"],
            traffic=r["traffic"] or "",
            traffic_value=int(r["traffic_value"]),
            news_title=r["news_title"],
        )
        for r in rows
    ]


def is_stale(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    now = now or _now()
    fetched_at, _ = latest(conn, limit=1)
    if fetched_at is None:
        return True
    age = now - datetime.fromisoformat(fetched_at)
    return age > timedelta(hours=stale_hours())


def refresh(
    conn: sqlite3.Connection,
    fetcher: Callable[[], Sequence[TrendItem]] | None = None,
    now: datetime | None = None,
) -> dict:
    """강제 갱신. 실패 시 TrendsFetchError 전파."""
    items = list((fetcher or fetch_google_trends)())
    fetched_at = save_batch(conn, items, now=now)
    return {"fetched_at": fetched_at, "count": len(items)}


def maybe_refresh(
    conn: sqlite3.Connection,
    fetcher: Callable[[], Sequence[TrendItem]] | None = None,
    now: datetime | None = None,
) -> dict:
    """stale 이면 갱신을 시도한다. 실패는 삼키고 기존 캐시 유지 (부가 정보)."""
    if not is_stale(conn, now=now):
        return {"status": "fresh"}
    try:
        outcome = refresh(conn, fetcher=fetcher, now=now)
    except TrendsFetchError as exc:
        logger.warning(f"트렌드 갱신 실패 (기존 캐시 유지): {exc}")
        return {"status": "failed", "error": str(exc)}
    return {"status": "refreshed", **outcome}


def latest_keywords(conn: sqlite3.Connection, limit: int = 5) -> list[str]:
    """스크립트 프롬프트용 최신 키워드 목록 (없으면 빈 리스트)."""
    _, items = latest(conn, limit=limit)
    return [item.keyword for item in items]
