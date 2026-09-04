"""블로그 대표 이미지 생성과 게시 전 기술 QA."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from app.promo.commerce_policy import ALLOWED_PRODUCT_COUNTS

WIDTH = 1080
HEIGHT = 1350
FONT = str(Path(__file__).resolve().parents[2] / "resource/fonts/NotoSansKR-Bold.otf")


@dataclass(frozen=True)
class CoverQA:
    passed: bool
    width: int
    height: int
    sharpness: float
    failures: tuple[str, ...]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size=size)


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 24:
        font = _font(size, True)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(24, True)


def build_comparison_cover(
    *,
    product_name: str,
    image_path: str | Path,
    offers: list[dict],
    output_path: str | Path,
) -> Path:
    active = sorted(
        (offer for offer in offers if offer.get("active", True)),
        key=lambda offer: offer["price"],
    )
    if len(active) < 2:
        raise ValueError("가격 비교 대표 이미지는 활성 오퍼가 2개 이상 필요합니다")
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#09101f")
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = (
            int(9 + 10 * ratio),
            int(16 + 16 * ratio),
            int(31 + 29 * ratio),
        )
        draw.line((0, y, WIDTH, y), fill=color)

    draw.rounded_rectangle((64, 48, 1016, 224), radius=34, fill="#14213d")
    draw.text((104, 78), "같은 제품, 가격은 다릅니다", font=_font(58, True), fill="#ffffff")
    name_font = _fit(draw, product_name, 870, 36)
    draw.text((104, 158), product_name, font=name_font, fill="#b8c8e8")

    product = Image.open(image_path).convert("RGB")
    product.thumbnail((820, 640), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (920, 650), "white")
    panel.paste(product, ((920 - product.width) // 2, (650 - product.height) // 2))
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel.width, panel.height), radius=38, fill=255)
    canvas.paste(panel, (80, 254), mask)

    best, other = active[0], active[1]
    card_y = 932
    def checked_label(offer: dict) -> str:
        parts = offer["checkedAt"].split("-")
        return f"{int(parts[1])}/{int(parts[2])} 확인가"

    cards = [
        (best, (64, card_y, 524, 1176), "#03c75a", checked_label(best)),
        (other, (556, card_y, 1016, 1176), "#ff4e50", checked_label(other)),
    ]
    for offer, box, color, label in cards:
        draw.rounded_rectangle(box, radius=30, fill="#17243f", outline=color, width=5)
        draw.text((box[0] + 32, box[1] + 28), label, font=_font(25, True), fill=color)
        draw.text((box[0] + 32, box[1] + 74), offer["merchant"], font=_font(36, True), fill="white")
        draw.text((box[0] + 32, box[1] + 137), offer["priceText"], font=_font(52, True), fill="white")

    savings = other["price"] - best["price"]
    badge = f"확인 당시 {savings:,}원 차이"
    draw.rounded_rectangle((172, 1204, 908, 1302), radius=49, fill="#ffd45c")
    badge_font = _fit(draw, badge, 650, 42)
    bbox = draw.textbbox((0, 0), badge, font=badge_font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) / 2, 1227), badge, font=badge_font, fill="#111827")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return output


def build_roundup_cover(
    *,
    title: str,
    products: list[dict],
    checked_at: str,
    output_path: str | Path,
) -> Path:
    if len(products) not in ALLOWED_PRODUCT_COUNTS:
        raise ValueError(
            f"TOP 대표 이미지는 상품 3개 또는 5개가 필요합니다 (현재 {len(products)}개)"
        )
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#09101f")
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        draw.line(
            (0, y, WIDTH, y),
            fill=(int(9 + 12 * ratio), int(16 + 18 * ratio), int(31 + 35 * ratio)),
        )
    draw.text((68, 55), "실측 수요 1위 주제", font=_font(28, True), fill="#70e6ab")
    title_font = _fit(draw, title, 940, 62)
    draw.text((68, 105), title, font=title_font, fill="white")
    draw.text(
        (68, 184),
        "가격 · 리뷰 · 기능 차이로 비교했습니다",
        font=_font(30),
        fill="#aebbd2",
    )

    count = len(products)
    top = 255
    available = 950
    gap = 18
    card_height = (available - gap * (count - 1)) // count
    for index, product in enumerate(products, start=1):
        y0 = top + (index - 1) * (card_height + gap)
        y1 = y0 + card_height
        draw.rounded_rectangle((55, y0, 1025, y1), radius=30, fill="#16233d")
        rank_color = "#ffd45c" if index == 1 else "#7f91b5"
        draw.ellipse((78, y0 + 28, 158, y0 + 108), fill=rank_color)
        rank = str(index)
        rank_font = _font(38, True)
        rank_box = draw.textbbox((0, 0), rank, font=rank_font)
        draw.text(
            (
                118 - (rank_box[2] - rank_box[0]) / 2,
                y0 + 43,
            ),
            rank,
            font=rank_font,
            fill="#101827",
        )

        source = Image.open(product["image_path"]).convert("RGB")
        thumb_size = min(card_height - 30, 250)
        source.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
        thumb = Image.new("RGB", (thumb_size, thumb_size), "white")
        thumb.paste(
            source,
            ((thumb_size - source.width) // 2, (thumb_size - source.height) // 2),
        )
        mask = Image.new("L", thumb.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, thumb.width, thumb.height), radius=22, fill=255
        )
        canvas.paste(thumb, (180, y0 + (card_height - thumb_size) // 2), mask)

        text_x = 465
        name = product["name"]
        name_font = _fit(draw, name, 515, 31 if count == 3 else 27)
        draw.text((text_x, y0 + 34), name, font=name_font, fill="white")
        draw.text(
            (text_x, y0 + 92),
            product["feature"],
            font=_font(24 if count == 3 else 21),
            fill="#aebbd2",
        )
        draw.text(
            (text_x, y1 - 78),
            product["priceText"],
            font=_font(42 if count == 3 else 34, True),
            fill="#70e6ab",
        )

    draw.rounded_rectangle((110, 1240, 970, 1315), radius=38, fill="#253659")
    footer = (
        f"광고 · 가격 확인일 {checked_at.replace('-', '.')} · 구매 전 최신가 확인"
    )
    footer_font = _fit(draw, footer, 800, 27)
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(
        ((WIDTH - (footer_box[2] - footer_box[0])) / 2, 1261),
        footer,
        font=footer_font,
        fill="#e7edfa",
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return output


def qa_cover(path: str | Path) -> CoverQA:
    image = Image.open(path).convert("RGB")
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    sharpness = round(ImageStat.Stat(edges).var[0], 2)
    failures = []
    if image.size != (WIDTH, HEIGHT):
        failures.append(f"해상도 {image.width}x{image.height}, 기대 {WIDTH}x{HEIGHT}")
    if sharpness < 100:
        failures.append(f"선명도 부족: {sharpness}")
    return CoverQA(
        passed=not failures,
        width=image.width,
        height=image.height,
        sharpness=sharpness,
        failures=tuple(failures),
    )
