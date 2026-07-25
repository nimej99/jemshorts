"""트렌드 폴링(trends) 테스트 — 실 네트워크 없음."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import config
from app.promo import db as promo_db
from app.promo import trends

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
  <channel>
    <item>
      <title>동네 맛집</title>
      <ht:approx_traffic>200+</ht:approx_traffic>
      <ht:news_item>
        <ht:news_item_title>동네 맛집 열풍</ht:news_item_title>
      </ht:news_item>
    </item>
    <item>
      <title>투자</title>
      <ht:approx_traffic>10000+</ht:approx_traffic>
    </item>
    <item>
      <title/>
    </item>
  </channel>
</rss>
"""


@pytest.fixture()
def conn(tmp_path):
    connection = promo_db.connect(tmp_path / "trends-test.db")
    yield connection
    connection.close()


def _items():
    return trends.parse_trends_rss(SAMPLE_RSS)


def test_parse_rss_sorts_by_traffic_and_skips_empty():
    items = _items()

    assert [i.keyword for i in items] == ["투자", "동네 맛집"]  # 트래픽 내림차순
    assert items[0].traffic_value == 10000
    assert items[1].news_title == "동네 맛집 열풍"


def test_parse_rss_invalid_raises():
    with pytest.raises(trends.TrendsFetchError):
        trends.parse_trends_rss("not-xml")
    with pytest.raises(trends.TrendsFetchError, match="항목이 없습니다"):
        trends.parse_trends_rss("<rss><channel></channel></rss>")


def test_refresh_and_latest_roundtrip(conn):
    trends.refresh(conn, fetcher=_items, now=NOW)

    fetched_at, items = trends.latest(conn)
    assert fetched_at == NOW.isoformat(timespec="seconds")
    assert [i.keyword for i in items] == ["투자", "동네 맛집"]
    assert trends.latest_keywords(conn, limit=1) == ["투자"]


def test_latest_returns_newest_batch_only(conn):
    trends.refresh(conn, fetcher=_items, now=NOW - timedelta(hours=2))
    trends.refresh(
        conn,
        fetcher=lambda: [trends.TrendItem(keyword="신규", traffic_value=1)],
        now=NOW,
    )

    _, items = trends.latest(conn)
    assert [i.keyword for i in items] == ["신규"]


def test_old_batches_pruned(conn):
    trends.refresh(
        conn, fetcher=_items, now=NOW - timedelta(days=trends.KEEP_DAYS + 1)
    )
    trends.refresh(conn, fetcher=_items, now=NOW)

    count = conn.execute("SELECT COUNT(DISTINCT fetched_at) FROM trends").fetchone()[0]
    assert count == 1  # 7일 지난 배치는 정리


def test_is_stale(conn, monkeypatch):
    assert trends.is_stale(conn, now=NOW) is True  # 캐시 없음

    trends.refresh(conn, fetcher=_items, now=NOW - timedelta(hours=1))
    assert trends.is_stale(conn, now=NOW) is False

    monkeypatch.setitem(config.app, "promo_trends_stale_hours", 0.5)
    assert trends.is_stale(conn, now=NOW) is True


def test_maybe_refresh_swallows_fetch_failure(conn):
    def broken():
        raise trends.TrendsFetchError("네트워크 죽음")

    outcome = trends.maybe_refresh(conn, fetcher=broken, now=NOW)
    assert outcome["status"] == "failed"
    assert "네트워크" in outcome["error"]

    # 기존 캐시가 있으면 유지된다
    trends.refresh(conn, fetcher=_items, now=NOW - timedelta(days=1))
    trends.maybe_refresh(conn, fetcher=broken, now=NOW)
    _, items = trends.latest(conn)
    assert len(items) == 2


def test_maybe_refresh_fresh_skips_fetch(conn):
    trends.refresh(conn, fetcher=_items, now=NOW)

    def must_not_call():
        raise AssertionError("fresh 인데 fetch 호출됨")

    outcome = trends.maybe_refresh(conn, fetcher=must_not_call, now=NOW)
    assert outcome["status"] == "fresh"
