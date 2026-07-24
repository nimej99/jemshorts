"""브랜드킷 계층 테스트 (모델 / 크롤러 / 영속화).

네트워크 호출은 전부 mock 처리한다 (실제 urlopen 호출 금지).
"""

import email
import json
import sqlite3
import urllib.error

import pytest

from app.promo import db as promo_db
from app.promo.brandkit import crawler, store
from app.promo.brandkit.models import BrandKit
from app.promo.migrations import LATEST_VERSION, MIGRATIONS


# ---------------------------------------------------------------------------
# 크롤러 (전부 mock — 실 네트워크 0)
# ---------------------------------------------------------------------------

FULL_OG_HTML = """<!doctype html>
<html><head>
<title>우리동네 카페</title>
<meta property="og:title" content="우리동네 카페" />
<meta property="og:description" content="핸드드립 전문 동네 카페" />
<meta property="og:image" content="https://example.com/cover.jpg" />
<meta name="description" content="핸드드립 전문" />
</head><body>본문</body></html>
"""

TITLE_ONLY_HTML = """<!doctype html>
<html><head><title>우리동네 카페</title></head><body>본문</body></html>
"""


class _FakeResponse:
    """urlopen 컨텍스트 매니저를 흉내내는 최소 응답 객체."""

    def __init__(self, body: bytes, content_type="text/html; charset=utf-8"):
        self._body = body
        self.headers = email.message_from_string(f"Content-Type: {content_type}\n\n")

    def read(self, n=None):
        if n is None:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PUBLIC_IP = "93.184.216.34"


def _mock_getaddrinfo(monkeypatch, resolved_ip):
    """호스트 resolve 를 mock 한다 (실 DNS 조회 0)."""
    monkeypatch.setattr(
        crawler.socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (crawler.socket.AF_INET, crawler.socket.SOCK_STREAM, 6, "", (resolved_ip, 0))
        ],
    )


def _mock_urlopen(monkeypatch, response=None, error=None, resolved_ip=PUBLIC_IP):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        if error is not None:
            raise error
        return response

    _mock_getaddrinfo(monkeypatch, resolved_ip)
    monkeypatch.setattr(crawler, "_urlopen", fake_urlopen)
    return calls


def test_crawl_full_og_html_is_ok(monkeypatch):
    """OG 3종이 모두 있는 정적 HTML 은 status=ok."""
    calls = _mock_urlopen(
        monkeypatch, response=_FakeResponse(FULL_OG_HTML.encode("utf-8"))
    )
    result = crawler.crawl("https://example.com/shop")
    assert result.status == "ok"
    assert result.fields["og_title"] == "우리동네 카페"
    assert result.fields["og_description"] == "핸드드립 전문 동네 카페"
    # og:image 는 URL 문자열만 기록 (다운로드는 후속 단계)
    assert result.fields["og_image"] == "https://example.com/cover.jpg"
    assert result.fields["title"] == "우리동네 카페"
    assert result.fields["description"] == "핸드드립 전문"
    assert result.warnings == []
    assert calls == [{"url": "https://example.com/shop", "timeout": 10}]


def test_crawl_title_only_is_partial(monkeypatch):
    """title 만 있는 HTML 은 status=partial + OG 누락 warning."""
    _mock_urlopen(monkeypatch, response=_FakeResponse(TITLE_ONLY_HTML.encode("utf-8")))
    result = crawler.crawl("https://example.com/shop")
    assert result.status == "partial"
    assert result.fields == {"title": "우리동네 카페"}
    assert any("og_title" in warning for warning in result.warnings)


def test_crawl_network_error_is_failed(monkeypatch):
    """urlopen 예외(네트워크 오류)는 status=failed 로 수렴 (예외 전파 없음)."""
    _mock_urlopen(monkeypatch, error=urllib.error.URLError("connection refused"))
    result = crawler.crawl("https://place.naver.com/restaurant/12345")
    assert result.status == "failed"
    assert result.fields == {}
    assert result.warnings


def test_crawl_non_html_is_failed(monkeypatch):
    """비 HTML 응답(JSON 등)은 status=failed."""
    _mock_urlopen(
        monkeypatch,
        response=_FakeResponse(b'{"a": 1}', content_type="application/json"),
    )
    result = crawler.crawl("https://example.com/api")
    assert result.status == "failed"


def test_crawl_empty_page_is_failed(monkeypatch):
    """빈 페이지는 status=failed."""
    _mock_urlopen(monkeypatch, response=_FakeResponse(b""))
    result = crawler.crawl("https://example.com/empty")
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# SSRF 방어 (전부 mock — 실 네트워크/실 DNS 0)
# ---------------------------------------------------------------------------

def test_crawl_rejects_file_scheme(monkeypatch):
    """file:// 등 비 http/https 스킴은 요청 없이 status=failed."""
    calls = _mock_urlopen(
        monkeypatch, response=_FakeResponse(FULL_OG_HTML.encode("utf-8"))
    )
    result = crawler.crawl("file:///etc/passwd")
    assert result.status == "failed"
    assert any("스킴" in warning for warning in result.warnings)
    assert calls == []  # 요청 자체가 나가지 않는다


def test_crawl_rejects_loopback_ip(monkeypatch):
    """127.0.0.1 (루프백) 은 요청 없이 status=failed."""
    calls = _mock_urlopen(
        monkeypatch,
        response=_FakeResponse(FULL_OG_HTML.encode("utf-8")),
        resolved_ip="127.0.0.1",
    )
    result = crawler.crawl("http://127.0.0.1:8080/admin")
    assert result.status == "failed"
    assert any("차단된 IP" in warning for warning in result.warnings)
    assert calls == []


def test_crawl_rejects_private_ip_after_resolve(monkeypatch):
    """호스트가 사설 IP(10.x)로 resolve 되면 요청 없이 status=failed."""
    calls = _mock_urlopen(
        monkeypatch,
        response=_FakeResponse(FULL_OG_HTML.encode("utf-8")),
        resolved_ip="10.20.30.40",
    )
    result = crawler.crawl("https://internal.example.com/shop")
    assert result.status == "failed"
    assert any("차단된 IP" in warning for warning in result.warnings)
    assert calls == []


def test_redirect_hop_to_private_ip_is_blocked(monkeypatch):
    """리다이렉트 hop 의 새 URL 도 동일한 SSRF 검증을 통과해야 한다."""
    _mock_getaddrinfo(monkeypatch, "10.0.0.5")
    handler = crawler._SSRFGuardRedirectHandler()
    with pytest.raises(crawler.SSRFBlockedError, match="차단된 IP"):
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://internal.example.com/next"
        )


def test_crawl_truncates_body_at_2mb(monkeypatch):
    """응답 read 는 2MB 상한으로 절단된다 (앞부분 메타 태그 파싱은 유지)."""
    read_sizes = []

    class _RecordingResponse(_FakeResponse):
        def read(self, n=None):
            read_sizes.append(n)
            return super().read(n)

    big_body = FULL_OG_HTML.encode("utf-8") + b"<!--" + b"x" * (3 * 1024 * 1024)
    _mock_urlopen(monkeypatch, response=_RecordingResponse(big_body))

    result = crawler.crawl("https://example.com/huge")

    assert read_sizes == [crawler.MAX_RESPONSE_BYTES]
    assert result.status == "ok"
    assert result.fields["og_title"] == "우리동네 카페"


# ---------------------------------------------------------------------------
# 모델
# ---------------------------------------------------------------------------

def test_photo_warning_true_when_photos_empty():
    kit = BrandKit(business_name="우리동네 카페", photos=[])
    assert kit.photo_warning is True


def test_photo_warning_false_when_photos_present():
    kit = BrandKit(business_name="우리동네 카페", photos=["a.jpg"])
    assert kit.photo_warning is False


def test_to_dict_from_dict_round_trip():
    kit = BrandKit(
        business_name="우리동네 카페",
        category="카페",
        description="핸드드립 전문",
        address="서울시 어딘가 1-2",
        phone="02-000-0000",
        sns_url="https://instagram.com/cafe",
        primary_color="#ff6600",
        logo_path="storage/logo.png",
        photos=["a.jpg", "b.jpg"],
        source="crawl",
    )
    data = kit.to_dict()
    assert data["photo_warning"] is False
    restored = BrandKit.from_dict(data)
    assert restored == kit


def test_invalid_source_rejected():
    with pytest.raises(ValueError):
        BrandKit(source="scraped")


# ---------------------------------------------------------------------------
# 영속화 (v2 마이그레이션 + round-trip)
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "promo.db"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_store_round_trip_and_migration(db_path):
    """save/load round-trip + 최신 user_version + brandkit 테이블 생성 확인."""
    conn = promo_db.connect(db_path)
    try:
        assert LATEST_VERSION >= 2
        assert (
            int(conn.execute("PRAGMA user_version").fetchone()[0]) == LATEST_VERSION
        )
        assert "brandkit" in _table_names(conn)

        assert store.exists(conn) is False
        assert store.load(conn) is None

        kit = BrandKit(
            business_name="우리동네 카페",
            category="카페",
            photos=["a.jpg"],
            source="crawl",
        )
        store.save(conn, kit)
        assert store.exists(conn) is True
        assert store.load(conn) == kit

        # upsert: 재저장 시에도 단일행 유지
        store.save(conn, kit.touched(description="수정된 소개"))
        assert conn.execute("SELECT COUNT(*) FROM brandkit").fetchone()[0] == 1
        assert store.load(conn).description == "수정된 소개"
    finally:
        conn.close()


def test_migration_preserves_v1_tables_and_data(db_path):
    """기존 v1 DB 를 최신으로 올려도 v1 테이블/데이터가 보존된다."""
    # v1 상태의 DB 를 수동으로 구성
    conn = sqlite3.connect(db_path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO videos (id, status) VALUES (?, ?)", ("v1", "generating")
    )
    conn.commit()
    conn.close()

    # 기동 시 v2 마이그레이션 적용
    conn = promo_db.connect(db_path)
    try:
        assert (
            int(conn.execute("PRAGMA user_version").fetchone()[0]) == LATEST_VERSION
        )
        assert {"videos", "schedule", "fallback_log", "brandkit"} <= _table_names(conn)
        row = conn.execute("SELECT status FROM videos WHERE id='v1'").fetchone()
        assert row["status"] == "generating"
    finally:
        conn.close()


def test_stored_payload_is_json(db_path):
    """payload_json 컬럼에는 파싱 가능한 JSON 이 저장된다."""
    conn = promo_db.connect(db_path)
    try:
        store.save(conn, BrandKit(business_name="가게"))
        raw = conn.execute("SELECT payload_json FROM brandkit").fetchone()[0]
        payload = json.loads(raw)
        assert payload["business_name"] == "가게"
        assert payload["photo_warning"] is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# merge_manual (검수 폼 워크플로)
# ---------------------------------------------------------------------------

def test_merge_manual_merges_and_switches_source_to_mixed():
    kit = BrandKit(business_name="크롤된 이름", category="카페", source="crawl")
    merged = store.merge_manual(
        kit, {"business_name": "수정된 이름", "phone": "02-000-0000"}
    )
    assert merged.business_name == "수정된 이름"
    assert merged.phone == "02-000-0000"
    assert merged.category == "카페"  # 미입력 필드는 크롤 값 유지
    assert merged.source == "mixed"
    assert merged.created_at == kit.created_at
    # 원본은 불변
    assert kit.business_name == "크롤된 이름"
    assert kit.source == "crawl"


def test_merge_manual_no_changes_keeps_source():
    kit = BrandKit(business_name="크롤된 이름", source="crawl")
    assert store.merge_manual(kit, {}) is kit
    assert store.merge_manual(kit, {"business_name": "크롤된 이름"}) is kit
    # None 값과 병합 불가 키는 무시
    assert store.merge_manual(
        kit, {"primary_color": None, "source": "manual", "unknown": "x"}
    ) is kit


def test_merge_manual_manual_source_stays_manual():
    kit = BrandKit(business_name="수동 입력", source="manual")
    merged = store.merge_manual(kit, {"category": "식당"})
    assert merged.category == "식당"
    assert merged.source == "manual"
