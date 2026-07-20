"""promo-shorts SQLite 스키마 마이그레이션 정의.

- MIGRATIONS 리스트의 각 항목은 하나의 스키마 버전에 해당하는 SQL 스크립트다.
- 인덱스 0 = user_version 1 로 올리는 마이그레이션. 순서대로만 적용되며,
  기존 항목을 수정하지 말고 새 버전은 리스트 끝에 append 할 것.
- 적용/버전 관리는 app.promo.db 의 러너(PRAGMA user_version 기반)가 담당한다.
"""

# v1 스키마: videos / schedule / fallback_log
_V1 = """
CREATE TABLE IF NOT EXISTS videos (
    id          TEXT PRIMARY KEY,
    template_id TEXT,
    status      TEXT NOT NULL CHECK (
        status IN ('generating', 'qa_failed', 'pending_approval', 'approved', 'delivered')
    ),
    caption     TEXT,
    hashtags    TEXT,
    fail_reason TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedule (
    id            INTEGER PRIMARY KEY,
    freq_per_week INTEGER,
    next_runs     TEXT,  -- JSON 배열 (ISO8601 문자열 목록)
    missed_runs   TEXT   -- JSON 배열 (ISO8601 문자열 목록)
);

CREATE TABLE IF NOT EXISTS fallback_log (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL DEFAULT (datetime('now')),
    component  TEXT NOT NULL,
    from_value TEXT,
    to_value   TEXT,
    cause      TEXT
);
"""

# v2 스키마: brandkit 단일행 테이블 (단순 JSON 저장 전략 — 스키마 변화에 유연)
_V2 = """
CREATE TABLE IF NOT EXISTS brandkit (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MIGRATIONS: list[str] = [
    _V1,
    _V2,
]

# 최신 스키마 버전 == 마이그레이션 개수 (user_version 목표값)
LATEST_VERSION: int = len(MIGRATIONS)
