"""app.promo.db 마이그레이션 러너 테스트."""

import sqlite3

import pytest

from app.promo import db as promo_db
from app.promo.migrations import LATEST_VERSION


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "promo.db"


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_bootstrap_empty_db(db_path):
    """빈 DB 기동 시 최신 user_version 까지 부트스트랩되고 v1 테이블이 존재한다."""
    conn = promo_db.connect(db_path)
    try:
        assert _user_version(conn) == LATEST_VERSION
        assert {"videos", "schedule", "fallback_log"} <= _table_names(conn)
    finally:
        conn.close()


def test_reapply_is_idempotent(db_path):
    """이미 최신인 DB에 재기동/재적용해도 에러 없이 버전이 유지된다."""
    conn = promo_db.connect(db_path)
    conn.execute(
        "INSERT INTO videos (id, status) VALUES (?, ?)", ("v1", "generating")
    )
    conn.commit()
    conn.close()

    # 재기동 (connect 가 내부에서 apply_migrations 를 다시 수행)
    conn = promo_db.connect(db_path)
    try:
        assert _user_version(conn) == LATEST_VERSION
        # 명시적 재적용도 no-op
        assert promo_db.apply_migrations(conn) == LATEST_VERSION
        # 기존 데이터 보존 확인
        row = conn.execute("SELECT status FROM videos WHERE id='v1'").fetchone()
        assert row["status"] == "generating"
    finally:
        conn.close()


def test_wal_mode_enabled(db_path):
    """연결 팩토리가 WAL 저널 모드를 설정한다."""
    conn = promo_db.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_videos_status_check_constraint(db_path):
    """videos.status 는 허용된 상태값 외의 값을 거부한다."""
    conn = promo_db.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO videos (id, status) VALUES (?, ?)",
                ("bad", "not_a_status"),
            )
        # 허용된 상태값은 정상 삽입
        for i, status in enumerate(
            ["generating", "qa_failed", "pending_approval", "approved", "delivered"]
        ):
            conn.execute(
                "INSERT INTO videos (id, status) VALUES (?, ?)", (f"ok{i}", status)
            )
        conn.commit()
    finally:
        conn.close()


def test_newer_db_version_rejected(db_path):
    """코드보다 높은 user_version 의 DB는 다운그레이드 방지를 위해 거부한다."""
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1:d}")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError):
        promo_db.connect(db_path)
