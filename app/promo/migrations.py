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

# v3 스키마: 렌더 플랜 (plan/render 2단계 승인 게이트의 영속화)
# payload_json: RenderPlan 복원에 필요한 전체 페이로드 (템플릿 원본 dict 포함
# — plan 과 render 사이에 템플릿 파일이 바뀌어도 플랜은 불변).
_V3 = """
CREATE TABLE IF NOT EXISTS promo_plans (
    plan_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'planned'
                 CHECK (status IN ('planned', 'rendering', 'rendered', 'failed')),
    template_id  TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    task_id      TEXT,
    result_json  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# v4 스키마: 트렌드 캐시 (Google Trends RSS 폴링 결과, 배치 = fetched_at 동일값)
_V4 = """
CREATE TABLE IF NOT EXISTS trends (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    keyword       TEXT NOT NULL,
    traffic       TEXT,
    traffic_value INTEGER NOT NULL DEFAULT 0,
    news_title    TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trends_fetched_at ON trends(fetched_at);
"""

# v5 스키마: 커머스 상품 원장 + 원천별 시계열 성과 스냅샷.
_V5 = """
CREATE TABLE IF NOT EXISTS commerce_products (
    product_key    TEXT PRIMARY KEY,
    product_id     TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    vendor_item_id TEXT NOT NULL,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    affiliate_url  TEXT NOT NULL,
    image_url      TEXT NOT NULL,
    video_id       TEXT,
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS commerce_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key     TEXT NOT NULL REFERENCES commerce_products(product_key)
                    ON DELETE CASCADE,
    source          TEXT NOT NULL CHECK (
                        source IN ('youtube', 'coupang', 'market', 'naver')
                    ),
    captured_at     TEXT NOT NULL,
    price           INTEGER,
    list_price      INTEGER,
    available       INTEGER CHECK (available IN (0, 1)),
    review_count    INTEGER,
    demand_index    REAL,
    supply_views    INTEGER,
    video_views     INTEGER,
    likes           INTEGER,
    comments        INTEGER,
    clicks          INTEGER,
    orders_count    INTEGER,
    sales_amount    INTEGER,
    commission      INTEGER,
    payload_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_commerce_snapshots_product_time
ON commerce_snapshots(product_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_commerce_snapshots_source_time
ON commerce_snapshots(source, captured_at DESC);
"""

# v6 스키마: 상품별 판매처/제휴 프로그램 오퍼. 가격은 시청자에게 전부 공개하고
# 실제 수수료/EPC는 commerce_snapshots(source='coupang' 등)와 함께 판단한다.
_V6 = """
CREATE TABLE IF NOT EXISTS commerce_offers (
    offer_key       TEXT PRIMARY KEY,
    product_key     TEXT NOT NULL REFERENCES commerce_products(product_key)
                    ON DELETE CASCADE,
    merchant        TEXT NOT NULL,
    price           INTEGER NOT NULL,
    shipping_text   TEXT,
    affiliate_url   TEXT NOT NULL,
    commission_rate REAL,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    checked_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_commerce_offers_product_price
ON commerce_offers(product_key, active, price);
"""

MIGRATIONS: list[str] = [
    _V1,
    _V2,
    _V3,
    _V4,
    _V5,
    _V6,
]

# 최신 스키마 버전 == 마이그레이션 개수 (user_version 목표값)
LATEST_VERSION: int = len(MIGRATIONS)
