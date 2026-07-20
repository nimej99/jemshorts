"""브랜드킷 SQLite 영속화 (brandkit 단일행 테이블, v2 마이그레이션).

저장 전략: BrandKit 전체를 payload_json 하나에 JSON 으로 직렬화해
id=1 단일행으로 저장한다. 필드 추가/변경 시 스키마 마이그레이션 없이
모델 계층(to_dict/from_dict)만 바꾸면 되는 단순화 전략이다.

검수 폼 워크플로: 크롤 결과(source="crawl")를 초안으로 보여주고,
사장님이 수정/보완한 값을 merge_manual 로 병합해 source="mixed" 로
전환한 뒤 save 한다.
"""

from __future__ import annotations

import json
import sqlite3

from app.promo.brandkit.models import BrandKit

# merge_manual 이 병합을 허용하는 폼 필드 (타임스탬프/출처는 병합 대상 아님)
_MERGEABLE_FIELDS = (
    "business_name",
    "category",
    "description",
    "address",
    "phone",
    "sns_url",
    "primary_color",
    "logo_path",
    "photos",
)


def save(conn: sqlite3.Connection, kit: BrandKit) -> None:
    """브랜드킷을 id=1 단일행으로 upsert 한다."""
    conn.execute(
        """
        INSERT INTO brandkit (id, payload_json, updated_at)
        VALUES (1, :payload, :updated_at)
        ON CONFLICT (id) DO UPDATE SET
            payload_json = :payload,
            updated_at = :updated_at
        """,
        {
            "payload": json.dumps(kit.to_dict(), ensure_ascii=False),
            "updated_at": kit.updated_at,
        },
    )
    conn.commit()


def load(conn: sqlite3.Connection) -> BrandKit | None:
    """저장된 브랜드킷을 복원한다. 없으면 None."""
    row = conn.execute("SELECT payload_json FROM brandkit WHERE id = 1").fetchone()
    if row is None:
        return None
    return BrandKit.from_dict(json.loads(row["payload_json"]))


def exists(conn: sqlite3.Connection) -> bool:
    """저장된 브랜드킷이 있는지 여부."""
    row = conn.execute("SELECT 1 FROM brandkit WHERE id = 1").fetchone()
    return row is not None


def merge_manual(kit: BrandKit, form_fields: dict) -> BrandKit:
    """검수 폼의 수동 입력을 kit 에 병합한 새 BrandKit 을 반환한다.

    - form_fields 중 _MERGEABLE_FIELDS 에 속하고 값이 None 이 아닌 항목만 반영.
    - 실제 변경이 있고 원본이 크롤 결과(source="crawl")면 source="mixed" 로 전환.
      (manual/mixed 는 그대로 유지 — 이미 수동 입력이 반영된 상태다.)
    - updated_at 은 현재 시각으로 갱신된다.
    """
    changes = {}
    for key in _MERGEABLE_FIELDS:
        if key not in form_fields or form_fields[key] is None:
            continue
        value = form_fields[key]
        if key == "photos":
            value = list(value)
        if value != getattr(kit, key):
            changes[key] = value

    if not changes:
        return kit

    if kit.source == "crawl":
        changes["source"] = "mixed"
    return kit.touched(**changes)
