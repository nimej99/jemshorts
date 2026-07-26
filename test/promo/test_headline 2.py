"""헤드라인 배너 렌더(app.promo.materials.headline) 테스트 — ffmpeg 불필요.

Pillow 로 그린 PNG 를 픽셀 단위로 확인한다 (파일 존재만 보면 빈 배너도 통과한다).
"""

from pathlib import Path

import pytest
from PIL import Image

from app.promo.materials.headline import (
    BANNER_TOP_RATIO,
    MAX_LINES,
    HeadlineError,
    format_headline,
    render_headline_png,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FONT_PATH = str(REPO_ROOT / "resource" / "fonts" / "NotoSansKR-Bold.otf")


def _render(tmp_path, text, width=540, height=960):
    output = tmp_path / "banner.png"
    render_headline_png(text, width, height, output, font_path=FONT_PATH)
    return Image.open(output).convert("RGBA")


# --- 플레이스홀더 치환 ---------------------------------------------------------


def test_format_headline_fills_known_keys():
    result = format_headline(
        "{shop_name} {menu_name} 출시!", {"shop_name": "우리분식", "menu_name": "매운떡볶이"}
    )

    assert result == "우리분식 매운떡볶이 출시!"


def test_format_headline_keeps_unknown_placeholder_visible():
    """못 채운 키를 빈칸으로 지우면 운영자가 누락을 못 본다 — 원문을 남긴다."""
    result = format_headline("{shop_name} {menu_name} 출시!", {"shop_name": "우리분식"})

    assert result == "우리분식 {menu_name} 출시!"


def test_format_headline_rejects_broken_template():
    with pytest.raises(HeadlineError, match="형식"):
        format_headline("{shop_name", {"shop_name": "가게"})


# --- 배너 렌더 -----------------------------------------------------------------


def test_banner_is_drawn_in_top_band_only(tmp_path):
    """배너는 상단에만 그려지고 나머지는 완전히 투명해야 한다 (소재를 가리면 안 된다)."""
    image = _render(tmp_path, "신메뉴 출시!")
    width, height = image.size
    alpha = image.getchannel("A")

    top_band = alpha.crop((0, int(height * BANNER_TOP_RATIO), width, int(height * 0.3)))
    bottom_band = alpha.crop((0, int(height * 0.4), width, height))

    assert top_band.getextrema()[1] > 0  # 상단에는 그려진 픽셀이 있다
    assert bottom_band.getextrema() == (0, 0)  # 하단은 완전 투명


def test_banner_matches_material_resolution(tmp_path):
    image = _render(tmp_path, "신메뉴 출시!", width=1080, height=1920)

    assert image.size == (1080, 1920)


def test_long_text_wraps_and_truncates(tmp_path):
    """긴 문구는 최대 줄 수까지만 쓰고 말줄임을 남긴다 (조용한 잘림 금지)."""
    long_text = "우리 동네에서 가장 맛있다고 소문난 신메뉴가 드디어 출시되었습니다 지금 바로 방문하세요"
    image = _render(tmp_path, long_text)
    alpha = image.getchannel("A")
    banner_box = alpha.getbbox()

    assert banner_box is not None
    banner_height = banner_box[3] - banner_box[1]
    # 최대 줄 수를 넘겨 배너가 화면 절반을 잡아먹으면 안 된다.
    assert banner_height < image.size[1] * 0.25
    assert MAX_LINES == 2


def test_empty_text_rejected(tmp_path):
    with pytest.raises(HeadlineError, match="비어"):
        render_headline_png("   ", 540, 960, tmp_path / "x.png", font_path=FONT_PATH)


def test_missing_font_rejected(tmp_path):
    with pytest.raises(HeadlineError, match="폰트"):
        render_headline_png(
            "제목", 540, 960, tmp_path / "x.png", font_path=str(tmp_path / "none.otf")
        )


def test_invalid_size_rejected(tmp_path):
    with pytest.raises(HeadlineError, match="해상도"):
        render_headline_png("제목", 0, 960, tmp_path / "x.png", font_path=FONT_PATH)
