"""헤드라인 배너 렌더 (템플릿 v2 `section.headline`).

docs/TEMPLATE_V2_DESIGN.md §6 (d). 코어 자막과 **독립된** 레이어다: 자막은
내레이션 받아쓰기(코어가 sub_maker 로 생성), 헤드라인은 화면 상단에 고정되는
짧은 카피다. 그래서 코어를 건드리지 않고 소재 클립 위에 직접 얹는다.

이 ffmpeg 빌드에는 `drawtext` 가 없을 수 있어(실측 확인) 텍스트는 Pillow 로
PNG 배너를 그린 뒤 `overlay` 필터로 합성한다 — 폰트 지원이 ffmpeg 빌드에
의존하지 않는다는 이점도 있다.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 배너 배치/모양 (세로 1920 기준 비율)
BANNER_TOP_RATIO = 0.08  # 상단 여백
BANNER_SIDE_RATIO = 0.06  # 좌우 여백
BANNER_RADIUS_RATIO = 0.25  # 모서리 둥글기 (배너 높이 대비)
BANNER_FILL = (0, 0, 0, 165)  # 반투명 검정 — 사진 위에서도 글자가 읽힌다
TEXT_FILL = (255, 255, 255, 255)
MAX_LINES = 2


class HeadlineError(RuntimeError):
    """헤드라인 배너 렌더 실패 (폰트 없음 등)."""


class _SafeDict(dict):
    """미지정 플레이스홀더는 원문 그대로 남긴다 (조용한 빈칸 금지)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_headline(text: str, context: dict) -> str:
    """헤드라인 템플릿 문자열의 플레이스홀더를 채운다.

    값이 없는 키는 `{menu_name}` 처럼 원문을 남긴다 — 빈칸으로 지워버리면
    운영자가 무엇이 안 채워졌는지 화면에서 알 수 없다.
    """
    try:
        return text.format_map(_SafeDict(context))
    except (IndexError, ValueError) as exc:
        raise HeadlineError(f"헤드라인 템플릿 형식이 잘못됐습니다: {text} ({exc})") from exc


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(font_path, size)
    except OSError as exc:
        raise HeadlineError(f"폰트를 열 수 없습니다: {font_path} ({exc})") from exc


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """글자 단위로 감싼다 (한국어는 공백이 적어 단어 단위로는 안 맞는다)."""
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
            if len(lines) == MAX_LINES:
                break
    if current and len(lines) < MAX_LINES:
        lines.append(current)
    if not lines:
        return [text]
    # 잘린 경우 마지막 줄에 말줄임을 붙인다 (조용한 잘림 금지).
    consumed = sum(len(line) for line in lines)
    if consumed < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def render_headline_png(
    text: str,
    width: int,
    height: int,
    output_path: str | Path,
    *,
    font_path: str,
    font_size: int | None = None,
) -> str:
    """소재 해상도에 맞춘 투명 배경 배너 PNG 를 만든다."""
    if not text or not text.strip():
        raise HeadlineError("헤드라인 텍스트가 비어 있습니다")
    if width <= 0 or height <= 0:
        raise HeadlineError(f"해상도가 잘못됐습니다: {width}x{height}")

    size = font_size or max(int(width * 0.062), 12)
    font = _load_font(font_path, size)

    side = int(width * BANNER_SIDE_RATIO)
    max_text_width = width - 2 * side - int(width * 0.06)
    lines = _wrap(text.strip(), font, max_text_width)

    line_height = int(size * 1.35)
    pad_y = int(size * 0.45)
    pad_x = int(size * 0.6)
    text_width = max(int(font.getlength(line)) for line in lines)
    banner_w = min(text_width + 2 * pad_x, width - 2 * side)
    banner_h = line_height * len(lines) + 2 * pad_y
    banner_x = (width - banner_w) // 2
    banner_y = int(height * BANNER_TOP_RATIO)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (banner_x, banner_y, banner_x + banner_w, banner_y + banner_h),
        radius=int(banner_h * BANNER_RADIUS_RATIO),
        fill=BANNER_FILL,
    )
    for index, line in enumerate(lines):
        line_width = int(font.getlength(line))
        draw.text(
            (
                banner_x + (banner_w - line_width) // 2,
                banner_y + pad_y + index * line_height,
            ),
            line,
            font=font,
            fill=TEXT_FILL,
        )

    output_path = str(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    canvas.save(output_path)
    return output_path
