"""소재 합성: 템플릿 structure + BrandKit -> MPT 로컬 렌더 입력 구성.

MPT 코어는 수정하지 않는다. `video_source="local"` + `params.video_materials`
주입 인터페이스만 사용한다 (scripts/e2e_render_ko.py 의 M0 패턴과 동일).

보안 경로 규칙: MPT 의 로컬 소재 전처리
(app/services/video.py preprocess_video ->
app/utils/file_security.resolve_path_within_directory)는 소재 경로를
storage/local_videos 화이트리스트 디렉터리 내부로 제한한다. 따라서 이
모듈은 브랜드/스톡 소재 중 해당 디렉터리 밖에 있는 파일을 전부
`storage_local_dir` 로 복사한 뒤, 복사본 경로만 MaterialInfo url 로
반환한다. 이미지(jpg/png 등)는 별도 변환 없이 url 로 그대로 전달해도
MPT 가 이미지 클립으로 처리한다 (app/services/task.py
get_video_materials 의 local 분기 -> preprocess_video 이미지 분기 확인).

스톡 소재 선다운로드(Pexels 등 API 호출)는 이 슬라이스에서 구현하지
않는다. 호출자가 이미 로컬에 확보한 파일 경로를 `stock_paths` 로
주입한다 — Pexels API 키 의존을 합성 계층에서 제거하기 위한 인터페이스
설계다.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Sequence

from loguru import logger

from app.models import const
from app.models.schema import MaterialInfo
from app.promo.brandkit.models import BrandKit
from app.promo.templates.schema import Template
from app.utils import file_security

# 소재 종류 판별용 확장자 (MPT 코어와 동일 기준 — app/models/const.py)
_PHOTO_EXTS = frozenset(const.FILE_TYPE_IMAGES)
_VIDEO_EXTS = frozenset(const.FILE_TYPE_VIDEOS)


class _PoolItem:
    """합성 후보 소재 1개 (로컬 확보 완료 상태)."""

    __slots__ = ("path", "kind", "is_brand", "used")

    def __init__(self, path: str, kind: str, is_brand: bool):
        self.path = path
        self.kind = kind  # "photo" | "video" | "other"
        self.is_brand = is_brand
        self.used = False


def _material_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext in _PHOTO_EXTS:
        return "photo"
    if ext in _VIDEO_EXTS:
        return "video"
    return "other"


def _slot_accepts(slot: str, kind: str) -> bool:
    if slot == "any":
        return True
    return slot == kind


def _ensure_local(
    source_path: str, local_dir_real: str, prefix: str, index: int
) -> str | None:
    """소재 파일을 storage/local_videos 내부 경로로 확보한다.

    이미 화이트리스트 내부면 그대로 사용, 외부면 복사한다.
    파일이 없거나 읽을 수 없으면 None (호출자가 스킵).
    """
    if not source_path or not os.path.isfile(source_path):
        logger.warning(f"skip missing material file: {source_path}")
        return None
    try:
        # 이미 local_videos 내부라면 복사 없이 그대로 사용한다.
        return file_security.resolve_path_within_directory(
            local_dir_real, source_path
        )
    except ValueError:
        pass
    dest_name = f"{prefix}-{index:02d}-{os.path.basename(source_path)}"
    dest_path = os.path.join(local_dir_real, dest_name)
    try:
        shutil.copy2(source_path, dest_path)
    except OSError as exc:
        logger.warning(f"skip uncopyable material file: {source_path} ({exc})")
        return None
    return dest_path


def _build_pool(
    paths: Sequence[str], local_dir_real: str, prefix: str, is_brand: bool
) -> list[_PoolItem]:
    pool = []
    for index, source_path in enumerate(paths):
        local_path = _ensure_local(str(source_path), local_dir_real, prefix, index)
        if local_path is None:
            continue
        pool.append(_PoolItem(local_path, _material_kind(local_path), is_brand))
    return pool


def _pick_for_slot(slot: str, pools: Sequence[list[_PoolItem]]) -> _PoolItem | None:
    """슬롯에 배정할 미사용 소재를 고른다.

    우선순위: 브랜드 슬롯일치 > 스톡 슬롯일치 > 브랜드 불일치 > 스톡 불일치.
    (pools 는 [브랜드, 스톡] 순서로 전달된다.)
    """
    for require_match in (True, False):
        for pool in pools:
            for item in pool:
                if item.used:
                    continue
                if require_match and not _slot_accepts(slot, item.kind):
                    continue
                if not require_match and _slot_accepts(slot, item.kind):
                    continue  # 일치 항목은 이미 앞 라운드에서 소진됨
                if not require_match:
                    logger.warning(
                        f"material_slot '{slot}' 일치 소재가 없어 "
                        f"{item.kind} 소재로 대체: {item.path}"
                    )
                item.used = True
                return item
    return None


def compose_materials(
    template: Template,
    brandkit: BrandKit,
    stock_paths: Sequence[str],
    storage_local_dir: str | Path,
    compose_id: str | None = None,
) -> tuple[list[MaterialInfo], int, bool]:
    """템플릿 섹션 순서대로 브랜드/스톡 소재를 배치해 MPT 입력을 만든다.

    - brandkit.photos (고객 사진/영상)와 stock_paths 를 storage_local_dir
      (= storage/local_videos) 내부로 복사/확보한다 (보안 경로 규칙).
    - 섹션별 material_slot("photo"|"video"|"any")에 맞는 소재를
      브랜드 우선으로 1개씩 배정한다. 소재가 섹션 수보다 적으면
      확보된 소재를 순환 재사용한다.
    - 브랜드 소재가 0개면 스톡만으로 구성하고 photo_warning=True 를
      반환한다 (조용한 강등 금지 — 경고 플래그 전파).
    - compose_id: 복사본 파일명에 포함되는 호출별 식별자. 미지정 시
      uuid4 앞 8자를 사용한다 — 호출 간 복사본 파일명 충돌/덮어쓰기 방지.

    반환: (video_materials, used_brand_count, photo_warning)
    - video_materials: MaterialInfo(provider="local") 리스트 (섹션 순서)
    - used_brand_count: 최종 배치에 실제 사용된 브랜드 소재 개수 (중복 제외)
    - photo_warning: brandkit.photo_warning 전파 + 브랜드 소재 미사용 시 True

    브랜드/스톡 모두 0개면 ValueError.
    """
    local_dir_real = os.path.realpath(str(storage_local_dir))
    os.makedirs(local_dir_real, exist_ok=True)

    if compose_id is None:
        compose_id = uuid.uuid4().hex[:8]

    brand_pool = _build_pool(
        brandkit.photos, local_dir_real, f"brand-{compose_id}", is_brand=True
    )
    stock_pool = _build_pool(
        stock_paths, local_dir_real, f"stock-{compose_id}", is_brand=False
    )

    combined = brand_pool + stock_pool
    if not combined:
        raise ValueError(
            "합성할 소재가 없습니다: 브랜드 소재와 스톡 소재가 모두 비어 있습니다"
        )

    ordered: list[_PoolItem] = []
    for section in template.structure:
        picked = _pick_for_slot(section.material_slot, (brand_pool, stock_pool))
        if picked is None:
            # 모든 소재를 소진했으면 확보된 소재를 순환 재사용한다.
            picked = combined[len(ordered) % len(combined)]
            logger.warning(
                f"소재({len(combined)}개)가 섹션 수보다 적어 순환 재사용: "
                f"section[{len(ordered)}] '{section.material_slot}' <- {picked.path}"
            )
        ordered.append(picked)

    used_brand_count = len({item.path for item in ordered if item.is_brand})
    photo_warning = brandkit.photo_warning or used_brand_count == 0
    if used_brand_count == 0:
        logger.warning(
            "브랜드 소재 0개 — 스톡 소재만으로 구성합니다 (photo_warning=True)"
        )

    materials = [
        MaterialInfo(provider="local", url=item.path, duration=0) for item in ordered
    ]
    return materials, used_brand_count, photo_warning
