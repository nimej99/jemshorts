"""소재 합성(compose_materials) 테스트 — 실 렌더/실 네트워크 없음.

복사 동작, 섹션 순서 배치, photo_warning 전파, storage/local_videos
보안 경로 규칙(파일이 화이트리스트 디렉터리 내부로 수렴)을 검증한다.
소재 파일은 파싱하지 않으므로 더미 바이트 파일로 충분하다.
"""

import os
import re

import pytest
from loguru import logger

from app.promo.brandkit.models import BrandKit
from app.promo.materials import compose_materials
from app.promo.templates.schema import validate_template
from app.utils import file_security

TEMPLATE_DATA = {
    "template_id": "compose-test-v1",
    "name": "합성 테스트",
    "version": 1,
    "mood": "upbeat",
    "structure": [
        {"role": "hook", "duration_s": 3, "script_guide": "훅", "material_slot": "video"},
        {"role": "body", "duration_s": 8, "script_guide": "본문", "material_slot": "any"},
        {"role": "cta", "duration_s": 4, "script_guide": "행동 유도", "material_slot": "photo"},
    ],
    "total_duration_range": [10, 20],
    "caption_template": "{shop_name} 캡션",
    "hashtags_base": ["테스트"],
}


@pytest.fixture()
def template():
    return validate_template(TEMPLATE_DATA)


@pytest.fixture()
def dirs(tmp_path):
    brand_dir = tmp_path / "brand"
    stock_dir = tmp_path / "stock"
    local_dir = tmp_path / "local_videos"
    brand_dir.mkdir()
    stock_dir.mkdir()
    local_dir.mkdir()
    return brand_dir, stock_dir, local_dir


def _touch(path, payload=b"dummy"):
    path.write_bytes(payload)
    return str(path)


def test_brand_files_copied_into_local_dir_and_pass_path_rule(dirs, template):
    brand_dir, stock_dir, local_dir = dirs
    photo = _touch(brand_dir / "b1.jpg")
    clip = _touch(brand_dir / "b2.mp4")
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[photo, clip])

    materials, used_brand_count, photo_warning = compose_materials(
        template, kit, [stock], local_dir
    )

    assert len(materials) == len(template.structure)
    for material in materials:
        assert material.provider == "local"
        # MPT preprocess_video 와 동일한 보안 경로 규칙을 통과해야 한다.
        resolved = file_security.resolve_path_within_directory(
            str(local_dir), material.url
        )
        assert resolved == os.path.realpath(material.url)
    # 원본은 그대로, 복사본만 사용
    assert os.path.isfile(photo) and os.path.isfile(clip)
    assert used_brand_count == 2
    assert photo_warning is False


def test_section_order_prefers_brand_and_matches_slots(dirs, template):
    brand_dir, stock_dir, local_dir = dirs
    photo = _touch(brand_dir / "b1.jpg")
    clip = _touch(brand_dir / "b2.mp4")
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[photo, clip])

    materials, _, _ = compose_materials(
        template, kit, [stock], local_dir, compose_id="cid1"
    )

    names = [os.path.basename(m.url) for m in materials]
    # hook(video) -> 브랜드 영상, body(any) -> 남은 브랜드 사진,
    # cta(photo) -> photo 소재 소진으로 스톡 영상 대체 (순서 결정적)
    assert names == [
        "brand-cid1-01-b2.mp4",
        "brand-cid1-00-b1.jpg",
        "stock-cid1-00-s1.mp4",
    ]


def test_photo_warning_propagates_from_empty_brandkit(dirs, template):
    _, stock_dir, local_dir = dirs
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[])
    assert kit.photo_warning is True

    materials, used_brand_count, photo_warning = compose_materials(
        template, kit, [stock], local_dir, compose_id="cid1"
    )

    assert photo_warning is True
    assert used_brand_count == 0
    # 스톡만으로 섹션 수만큼 구성 (순환 재사용)
    assert len(materials) == len(template.structure)
    assert all("stock-cid1-00-s1.mp4" in m.url for m in materials)


def test_missing_brand_files_are_skipped_and_flagged(dirs, template):
    brand_dir, stock_dir, local_dir = dirs
    stock = _touch(stock_dir / "s1.mp4")
    # photos 는 비어있지 않지만 실제 파일이 전부 없음 -> 브랜드 0개 + warning
    kit = BrandKit(business_name="가게", photos=[str(brand_dir / "ghost.jpg")])
    assert kit.photo_warning is False

    materials, used_brand_count, photo_warning = compose_materials(
        template, kit, [stock], local_dir
    )

    assert used_brand_count == 0
    assert photo_warning is True
    assert len(materials) == len(template.structure)


def test_file_already_inside_local_dir_is_not_duplicated(dirs, template):
    _, stock_dir, local_dir = dirs
    inside = _touch(local_dir / "already.mp4")
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[inside])

    materials, used_brand_count, _ = compose_materials(
        template, kit, [stock], local_dir, compose_id="cid1"
    )

    assert used_brand_count == 1
    brand_urls = {m.url for m in materials if os.path.basename(m.url) == "already.mp4"}
    assert brand_urls == {os.path.realpath(inside)}
    # 복사본이 새로 생기지 않는다
    assert sorted(p.name for p in local_dir.iterdir()) == [
        "already.mp4",
        "stock-cid1-00-s1.mp4",
    ]


def test_no_materials_at_all_raises(dirs, template):
    _, _, local_dir = dirs
    kit = BrandKit(business_name="가게", photos=[])
    with pytest.raises(ValueError):
        compose_materials(template, kit, [], local_dir)


def test_distinct_compose_ids_avoid_copy_collisions(dirs, template):
    """호출별 compose_id 가 복사본 파일명에 포함되어 호출 간 충돌이 없다."""
    _, stock_dir, local_dir = dirs
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[])

    compose_materials(template, kit, [stock], local_dir, compose_id="run1")
    compose_materials(template, kit, [stock], local_dir, compose_id="run2")

    assert sorted(p.name for p in local_dir.iterdir()) == [
        "stock-run1-00-s1.mp4",
        "stock-run2-00-s1.mp4",
    ]


def test_default_compose_id_is_uuid4_hex8(dirs, template):
    """compose_id 미지정 시 uuid4 앞 8자(hex)가 파일명에 들어간다."""
    _, stock_dir, local_dir = dirs
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[])

    compose_materials(template, kit, [stock], local_dir)
    compose_materials(template, kit, [stock], local_dir)

    names = sorted(p.name for p in local_dir.iterdir())
    assert len(names) == 2  # 호출 간 파일명이 겹치지 않는다
    for name in names:
        assert re.fullmatch(r"stock-[0-9a-f]{8}-00-s1\.mp4", name)


def test_cyclic_reuse_emits_warning_log(dirs, template):
    """소재가 섹션 수보다 적어 순환 재사용될 때 warning 로그를 남긴다."""
    _, stock_dir, local_dir = dirs
    stock = _touch(stock_dir / "s1.mp4")
    kit = BrandKit(business_name="가게", photos=[])

    messages = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        compose_materials(template, kit, [stock], local_dir, compose_id="cid1")
    finally:
        logger.remove(sink_id)

    assert any("순환 재사용" in message for message in messages)
