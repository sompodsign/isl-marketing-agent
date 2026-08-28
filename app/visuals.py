"""Compose branded Facebook social cards from raw product screenshots.

Plain UI screenshots perform poorly in the Facebook feed: they are small, get
cropped, and carry no message. This module renders the selected screenshot into
a designed 1200x675 card that uses the post's own generated headline, the
product's brand colours, and the product logo. Layout is fully deterministic —
no generative AI touches the pixels of the product UI, so the published visual
always depicts the real product.

Fonts are vendored under ``app/assets/fonts`` (Noto Sans / Noto Sans Bengali,
SIL Open Font License) and brand icons under ``app/assets/brand`` so the Docker
image ships everything it needs. Bengali headlines render correctly because
Pillow's bundled Raqm performs complex-text shaping.
"""

from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import database

logger = logging.getLogger(__name__)

CARD_WIDTH = 1200
CARD_HEIGHT = 675
THEME_VERSION = 4

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BRAND_DIR = Path(__file__).resolve().parent / "assets" / "brand"

BENGALI_PATTERN = re.compile(r"[\u0980-\u09FF]")
LATIN_FONT = FONT_DIR / "NotoSans-VF.ttf"
BENGALI_FONT = FONT_DIR / "NotoSansBengali-VF.ttf"

# Diagonal-feel header gradients (top colour, bottom colour) and an accent used
# for the divider line and the fallback brand badge. Values follow each
# product's reviewed logo description: blue-to-teal for LabLink/KarbarPro,
# warm gold-to-coral for Shikha.
PRODUCT_THEMES: dict[str, dict] = {
    "lablink": {"top": (9, 36, 66), "bottom": (13, 115, 143), "accent": (34, 211, 238)},
    "karbarpro": {"top": (11, 30, 63), "bottom": (12, 122, 138), "accent": (45, 212, 191)},
    "shikha": {"top": (110, 38, 20), "bottom": (222, 88, 28), "accent": (250, 190, 90)},
}
DEFAULT_THEME = {"top": (12, 32, 60), "bottom": (13, 110, 138), "accent": (34, 211, 238)}

# Two layouts. Wide dashboard screenshots (aspect >= threshold) get a compact
# one-line header so the product UI itself stays the hero of the card;
# squarish or portrait screenshots keep the taller header that fits a
# multi-line headline.
HEADER_HEIGHT = 260
COMPACT_HEADER_HEIGHT = 148
WIDE_ASPECT_THRESHOLD = 1.45
ACCENT_HEIGHT = 6
BODY_BACKGROUND = (238, 242, 247)
HEADER_MARGIN = 56
HEADLINE_AREA_TOP = 116
HEADLINE_AREA_BOTTOM = HEADER_HEIGHT - 14


@lru_cache(maxsize=32)
def _variable_font(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size)
    try:
        font.set_variation_by_axes([weight, 100])  # fvar order: wght, wdth
    except OSError:  # static fallback font without variation axes
        pass
    return font


def _is_bengali(text: str) -> bool:
    return bool(BENGALI_PATTERN.search(text))


def _headline_font(size: int, text: str, weight: int = 700) -> ImageFont.FreeTypeFont:
    path = BENGALI_FONT if _is_bengali(text) else LATIN_FONT
    return _variable_font(path, size, weight)


def _theme_for(product: str) -> dict:
    key = re.sub(r"[^a-z0-9]", "", product.lower())
    for name, theme in PRODUCT_THEMES.items():
        if key == re.sub(r"[^a-z0-9]", "", name.lower()):
            return theme
    return DEFAULT_THEME


def _wrap_headline(text: str, font: ImageFont.FreeTypeFont, max_width: int, drawer: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if drawer.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _fit_headline(
    text: str,
    drawer: ImageDraw.ImageDraw,
    max_width: int,
    max_height: int,
    attempts: tuple[tuple[int, int], ...],
    line_factor: float = 1.34,
) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Pick the largest (size, max_lines) attempt whose wrapped block fits.

    ``attempts`` is an ordered sequence of ``(font_size, max_lines)`` pairs.
    When nothing fits, the smallest size truncates the wrapped text with an
    ellipsis so the headline never overflows its band.
    """
    for size, max_lines in attempts:
        font = _headline_font(size, text, weight=700)
        lines = _wrap_headline(text, font, max_width, drawer)
        if len(lines) > max_lines:
            continue
        ascent, descent = font.getmetrics()
        line_height = int((ascent + descent) * line_factor)
        if line_height * len(lines) <= max_height:
            return lines, font, line_height
    size, max_lines = attempts[-1]
    font = _headline_font(size, text, weight=700)
    lines = _wrap_headline(text, font, max_width, drawer)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip()
        while lines[-1] and drawer.textlength(lines[-1] + "…", font=font) > max_width:
            lines[-1] = lines[-1][:-1].rstrip()
        lines[-1] += "…"
    ascent, descent = font.getmetrics()
    return lines, font, int((ascent + descent) * line_factor)


def _lerp_colour(start: tuple, end: tuple, ratio: float) -> tuple:
    return tuple(round(a + (b - a) * ratio) for a, b in zip(start, end))


def _header_gradient(width: int, height: int, theme: dict) -> Image.Image:
    strip = Image.new("RGB", (width, 1))
    for x in range(width):
        # Blend top colour into bottom colour diagonally for a soft brand sweep.
        ratio = 0.55 * (x / max(width - 1, 1)) + 0.45 * (0.5)
        strip.putpixel((x, 0), _lerp_colour(theme["top"], theme["bottom"], min(ratio, 1.0)))
    return strip.resize((width, height))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _brand_icon(product: str, theme: dict, height: int) -> Image.Image:
    """Return a square brand icon, falling back to an initial badge."""
    icon_path = BRAND_DIR / f"{product}.png"
    icon: Image.Image | None = None
    if icon_path.exists():
        try:
            source = Image.open(icon_path).convert("RGBA")
            source.thumbnail((height * 3, height * 3), Image.LANCZOS)
            square = Image.new("RGBA", (max(source.size),) * 2, (0, 0, 0, 0))
            square.paste(source, ((square.width - source.width) // 2, (square.height - source.height) // 2))
            icon = square.resize((height, height), Image.LANCZOS)
        except OSError:
            icon = None
    if icon is None:
        badge = Image.new("RGBA", (height, height), theme["accent"] + (255,))
        draw = ImageDraw.Draw(badge)
        initial = (product or "I")[:1].upper()
        font = _variable_font(LATIN_FONT, int(height * 0.58), 700)
        box = draw.textbbox((0, 0), initial, font=font)
        draw.text(
            ((height - (box[2] - box[0])) / 2 - box[0], (height - (box[3] - box[1])) / 2 - box[1]),
            initial,
            font=font,
            fill=(12, 32, 60, 255),
        )
        icon = badge
    radius = max(height // 5, 8)
    icon.putalpha(_rounded_mask(icon.size, radius))
    return icon


def _draw_tracked_text(drawer: ImageDraw.ImageDraw, position: tuple[int, int], text: str, font: ImageFont.FreeTypeFont, fill: tuple, tracking: int = 3) -> None:
    x, y = position
    for char in text:
        drawer.text((x, y), char, font=font, fill=fill)
        x += drawer.textlength(char, font=font) + tracking


def compose_card(screenshot_path: str | Path, headline: str, product: str, output_path: str | Path) -> Path:
    """Render one social card and return the written file path."""
    theme = _theme_for(product)
    screenshot = Image.open(screenshot_path).convert("RGB")

    # Wide dashboard screenshots get a compact one-line header so the product
    # UI fills the card; squarish shots keep the taller multi-line header.
    wide = screenshot.width / max(screenshot.height, 1) >= WIDE_ASPECT_THRESHOLD
    if wide:
        header_height = COMPACT_HEADER_HEIGHT
        icon_size, icon_y = 36, 20
        brand_size, wordmark_size = 20, 14
        headline_top, headline_bottom = 60, 134
        headline_attempts = ((36, 1), (30, 1), (26, 1))
        headline_factor = 1.2
        box = (36, header_height + 34, CARD_WIDTH - 36, CARD_HEIGHT - 14)
    else:
        header_height = HEADER_HEIGHT
        icon_size, icon_y = 46, 44
        brand_size, wordmark_size = 25, 17
        headline_top, headline_bottom = HEADLINE_AREA_TOP, HEADLINE_AREA_BOTTOM
        headline_attempts = ((52, 2), (46, 2), (40, 2), (34, 2), (34, 3), (30, 3))
        headline_factor = 1.34
        box = (44, header_height + 40, CARD_WIDTH - 44, CARD_HEIGHT - 22)

    card = _header_gradient(CARD_WIDTH, CARD_HEIGHT, theme).crop((0, 0, CARD_WIDTH, CARD_HEIGHT))

    # Body: quiet light surface so light-coloured screenshots blend naturally.
    body = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT - header_height - ACCENT_HEIGHT), BODY_BACKGROUND)
    card.paste(body, (0, header_height + ACCENT_HEIGHT))

    header = card.crop((0, 0, CARD_WIDTH, header_height)).convert("RGBA")
    glow = Image.new("RGBA", header.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((CARD_WIDTH - 460, -260, CARD_WIDTH + 140, 300), fill=theme["accent"] + (42,))
    glow_draw.ellipse((-200, header_height - 190, 260, header_height + 160), fill=(255, 255, 255, 16))
    header = Image.alpha_composite(header, glow.filter(ImageFilter.GaussianBlur(48)))
    card.paste(header, (0, 0))

    # Divider in the product accent colour.
    divider = Image.new("RGB", (CARD_WIDTH, ACCENT_HEIGHT), theme["accent"])
    card.paste(divider, (0, header_height))

    draw = ImageDraw.Draw(card)

    # Brand row: product icon + name on the left, company wordmark on the right.
    icon = _brand_icon(product, theme, icon_size)
    card.paste(icon, (HEADER_MARGIN, icon_y), icon)
    row_center = icon_y + icon_size // 2
    brand_font = _variable_font(LATIN_FONT, brand_size, 650)
    draw.text((HEADER_MARGIN + icon_size + 16, row_center), product, font=brand_font, fill=(255, 255, 255), anchor="lm")
    wordmark_font = _variable_font(LATIN_FONT, wordmark_size, 600)
    wordmark = "INARISOFTLABS"
    wordmark_width = sum(draw.textlength(char, font=wordmark_font) + 3 for char in wordmark) - 3
    wordmark_y = row_center - int(wordmark_size * 0.72)
    _draw_tracked_text(draw, (CARD_WIDTH - HEADER_MARGIN - int(wordmark_width), wordmark_y), wordmark, wordmark_font, (255, 255, 255, 230))

    # Headline: the model-generated overlay copy, fitted to the header band.
    headline_text = re.sub(r"\s+", " ", str(headline or "").strip())
    if not headline_text:
        headline_text = product
    max_width = CARD_WIDTH - 2 * HEADER_MARGIN
    lines, headline_font, line_height = _fit_headline(
        headline_text, draw, max_width, headline_bottom - headline_top, headline_attempts, headline_factor
    )
    y = headline_top + (headline_bottom - headline_top - line_height * len(lines)) // 2
    for line in lines:
        draw.text((HEADER_MARGIN, y), line, font=headline_font, fill=(255, 255, 255))
        y += line_height

    # Screenshot: fitted inside a white mat with rounded corners and a soft shadow.
    box_left, box_top, box_right, box_bottom = box
    box_width, box_height = box_right - box_left, box_bottom - box_top
    mat_padding = 10
    inner_width, inner_height = box_width - 2 * mat_padding, box_height - 2 * mat_padding
    scale = min(inner_width / screenshot.width, inner_height / screenshot.height)
    fitted = screenshot.resize((max(round(screenshot.width * scale), 1), max(round(screenshot.height * scale), 1)), Image.LANCZOS)
    mat_size = (fitted.width + 2 * mat_padding, fitted.height + 2 * mat_padding)
    mat_left = box_left + (box_width - mat_size[0]) // 2
    mat_top = box_top + (box_height - mat_size[1]) // 2

    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (mat_left, mat_top + 14, mat_left + mat_size[0], mat_top + 14 + mat_size[1]),
        radius=18,
        fill=(15, 23, 42, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    card_rgba = card.convert("RGBA")
    card_rgba = Image.alpha_composite(card_rgba, shadow)

    mat = Image.new("RGBA", mat_size, (255, 255, 255, 255))
    rounded = Image.new("RGBA", fitted.size, (0, 0, 0, 0))
    rounded.paste(fitted, (0, 0), _rounded_mask(fitted.size, 12))
    mat.paste(rounded, (mat_padding, mat_padding), _rounded_mask(fitted.size, 12))
    card_rgba.paste(mat, (mat_left, mat_top), _rounded_mask(mat_size, 18))
    draw = ImageDraw.Draw(card_rgba)
    draw.rounded_rectangle(
        (mat_left, mat_top, mat_left + mat_size[0] - 1, mat_top + mat_size[1] - 1),
        radius=18,
        outline=(15, 23, 42, 36),
        width=1,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    card_rgba.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def _screenshot_stamp(path: str | Path) -> str:
    try:
        stat = Path(path).stat()
        return f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        return "missing"


def compose_post_card(post: dict, image: dict, product: str | None = None) -> Path:
    """Compose (or reuse) the social card for one post and return its path.

    The cache key covers every input the renderer depends on — headline, the
    post's selected assets, and the screenshot file itself — so editing a draft
    yields a fresh card while unchanged drafts reuse the rendered file. Stale
    renders of the same post are pruned so ``data/visuals`` stays tidy.
    """
    if product is None:
        from app.services import asset_product

        product = asset_product(image) or "InariSoftLabs"
    headline = str(post.get("headline") or image.get("label") or product)
    asset_ids = ",".join(post.get("assetIds") or [])
    stamp = _screenshot_stamp(image["path"])
    digest = hashlib.sha1(
        "\x00".join([headline, asset_ids, str(image["path"]), stamp, product, str(THEME_VERSION)]).encode("utf-8")
    ).hexdigest()[:12]
    post_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(post.get("id") or "post"))[:40]
    # Resolve the data directory at call time so tests can redirect it.
    output = database.DATA_DIR / "visuals" / f"{post_key}-{digest}.png"
    if output.exists():
        return output
    result = compose_card(image["path"], headline, product, output)
    for stale in output.parent.glob(f"{post_key}-*.png"):
        if stale != result:
            stale.unlink(missing_ok=True)
    return result