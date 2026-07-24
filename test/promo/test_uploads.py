"""업로드 기록/일일 상한(uploads) 테스트 — 실 업로드 없음."""

import json

import pytest

from app.config import config
from app.promo import db as promo_db
from app.promo import uploads


@pytest.fixture()
def conn(tmp_path):
    connection = promo_db.connect(tmp_path / "uploads-test.db")
    yield connection
    connection.close()


def test_daily_cap_default_and_override(monkeypatch):
    monkeypatch.delitem(config.app, "promo_upload_daily_cap", raising=False)
    assert uploads.daily_cap() == uploads.DEFAULT_DAILY_CAP

    monkeypatch.setitem(config.app, "promo_upload_daily_cap", 5)
    assert uploads.daily_cap() == 5

    # 1 미만/비정상 값은 기본값으로 수렴 (상한 무력화 방지)
    monkeypatch.setitem(config.app, "promo_upload_daily_cap", 0)
    assert uploads.daily_cap() == uploads.DEFAULT_DAILY_CAP
    monkeypatch.setitem(config.app, "promo_upload_daily_cap", "abc")
    assert uploads.daily_cap() == uploads.DEFAULT_DAILY_CAP


def test_record_and_count_today(conn):
    assert uploads.count_delivered_today(conn) == 0

    uploads.record_delivered(conn, "v1", "tpl-1", "캡션", ["#태그"])
    uploads.record_delivered(conn, "v2", "tpl-1", "캡션2", ["#태그"])
    assert uploads.count_delivered_today(conn) == 2

    row = conn.execute("SELECT * FROM videos WHERE id='v1'").fetchone()
    assert row["status"] == uploads.STATUS_DELIVERED
    assert json.loads(row["hashtags"]) == ["#태그"]


def test_yesterday_uploads_do_not_count(conn):
    uploads.record_delivered(conn, "old", "tpl-1", "캡션", [])
    conn.execute(
        "UPDATE videos SET updated_at = datetime('now', '-1 day') WHERE id='old'"
    )
    conn.commit()
    assert uploads.count_delivered_today(conn) == 0


def test_cap_reached(conn, monkeypatch):
    monkeypatch.setitem(config.app, "promo_upload_daily_cap", 2)
    assert uploads.cap_reached(conn) is False
    uploads.record_delivered(conn, "v1", "tpl-1", "", [])
    assert uploads.cap_reached(conn) is False
    uploads.record_delivered(conn, "v2", "tpl-1", "", [])
    assert uploads.cap_reached(conn) is True
