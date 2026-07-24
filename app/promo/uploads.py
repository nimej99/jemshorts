"""업로드 기록 + 일일 상한 (v1 `videos` 테이블 활용).

채널 스팸 정책 방어를 위해 업로드는 일일 상한을 강제한다
(config `promo_upload_daily_cap`, 기본 3). 성공한 업로드는 videos
테이블에 status='delivered' 로 기록하고, 상한 판정은 이 기록의
UTC 날짜 기준 카운트로 한다 (sqlite datetime('now') == UTC).
"""

from __future__ import annotations

import json
import sqlite3

from app.config import config

DEFAULT_DAILY_CAP = 3
STATUS_DELIVERED = "delivered"


def daily_cap() -> int:
    """설정된 일일 업로드 상한 (1 미만 설정은 기본값으로 수렴)."""
    try:
        value = int(config.app.get("promo_upload_daily_cap", DEFAULT_DAILY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP
    return value if value >= 1 else DEFAULT_DAILY_CAP


def count_delivered_today(conn: sqlite3.Connection) -> int:
    """오늘(UTC) delivered 로 기록된 업로드 수."""
    row = conn.execute(
        "SELECT COUNT(*) FROM videos "
        "WHERE status = ? AND date(updated_at) = date('now')",
        (STATUS_DELIVERED,),
    ).fetchone()
    return int(row[0])


def cap_reached(conn: sqlite3.Connection) -> bool:
    return count_delivered_today(conn) >= daily_cap()


def record_delivered(
    conn: sqlite3.Connection,
    video_id: str,
    template_id: str,
    caption: str,
    hashtags: list[str],
) -> None:
    """성공한 업로드를 delivered 로 기록한다. video_id 중복이면 IntegrityError."""
    conn.execute(
        "INSERT INTO videos (id, template_id, status, caption, hashtags) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            video_id,
            template_id,
            STATUS_DELIVERED,
            caption,
            json.dumps(hashtags, ensure_ascii=False),
        ),
    )
    conn.commit()
