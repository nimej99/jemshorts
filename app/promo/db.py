"""promo-shorts SQLite 연결 팩토리 + 마이그레이션 러너.

동시성 전제 (단일 writer):
- 이 DB는 promo-shorts 프로세스 하나만 쓰기(write)한다는 전제로 운용한다.
  스케줄러/파이프라인/승인 API가 같은 프로세스 안에서 순차적으로 접근하며,
  다중 프로세스 동시 쓰기는 지원 범위 밖이다.
- WAL 모드는 "읽기(다른 연결/도구)와 쓰기의 병행"을 위한 것이지 다중 writer
  허용을 의미하지 않는다. 만약 별도 프로세스에서 열어야 한다면 읽기 전용으로만 열 것.

마이그레이션:
- PRAGMA user_version 을 스키마 버전으로 사용한다 (0 = 미초기화).
- app.promo.migrations.MIGRATIONS 의 SQL 스크립트를 현재 버전 이후부터
  순서대로 적용한다. 각 마이그레이션은 스크립트 적용 + user_version 범프를
  하나의 트랜잭션으로 커밋하므로, 실패 시 중간 상태가 DB에 남지 않는다.
- connect() 호출(기동) 시 자동 적용되며, 이미 최신이면 아무것도 하지 않는다(멱등).
"""

from __future__ import annotations

import os
import sqlite3

from app.promo.migrations import LATEST_VERSION, MIGRATIONS
from app.utils import utils

DB_FILENAME = "promo.db"


def default_db_path() -> str:
    """기본 DB 경로: <repo>/storage/promo.db (storage 디렉터리는 자동 생성)."""
    return os.path.join(utils.storage_dir(create=True), DB_FILENAME)


def _get_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _iter_statements(script: str):
    """SQL 스크립트를 개별 문장으로 분할한다 (트랜잭션 내 실행용).

    executescript 는 실행 전 암묵 COMMIT 을 수행해 마이그레이션 원자성을
    깨뜨리므로 사용하지 않는다. sqlite3.complete_statement 는 토크나이저
    수준에서 문자열/주석/트리거(BEGIN...END) 내부의 세미콜론을 구분하므로
    단순 split(";") 과 달리 안전하다.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            stripped = statement.strip()
            if stripped:
                yield stripped
            statement = ""
    tail = statement.strip()
    if tail:
        yield tail


def apply_migrations(conn: sqlite3.Connection) -> int:
    """현재 user_version 이후의 마이그레이션을 순서대로 적용한다.

    반환값: 적용 완료 후의 user_version (== LATEST_VERSION).
    이미 최신이면 no-op. DB 버전이 코드보다 앞서 있으면 에러(다운그레이드 방지).
    """
    version = _get_user_version(conn)
    if version > LATEST_VERSION:
        raise RuntimeError(
            f"DB user_version={version} 이 코드가 아는 최신 버전"
            f"({LATEST_VERSION})보다 높음 — 구버전 코드로 신버전 DB를 열었는지 확인 필요"
        )
    for target in range(version + 1, LATEST_VERSION + 1):
        script = MIGRATIONS[target - 1]
        # 스크립트 적용 + user_version 범프를 같은 트랜잭션에서 커밋한다.
        # (sqlite3 는 DDL 앞에서 암묵 BEGIN 을 하지 않으므로 명시적 BEGIN 필요.
        #  PRAGMA user_version 쓰기도 트랜잭션에 포함되어 함께 롤백된다.)
        conn.execute("BEGIN")
        try:
            for statement in _iter_statements(script):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {target:d}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return _get_user_version(conn)


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """SQLite 연결을 열고 WAL 모드 설정 + 마이그레이션 자동 적용 후 반환한다.

    db_path 미지정 시 storage/promo.db 를 사용한다.
    """
    path = os.fspath(db_path) if db_path is not None else default_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL 은 DB 파일에 영속되지만, 연결마다 설정해도 무해(멱등)하다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    apply_migrations(conn)
    return conn
