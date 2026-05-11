from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from selfietl.config import DateOverlayConfig


def format_overlay_date(captured_at: datetime) -> str:
    return f"{captured_at:%B} {captured_at.day}, {captured_at:%Y}"


def draw_date_overlay(image: Image.Image, captured_at: datetime, config: DateOverlayConfig) -> Image.Image:
    canvas = image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(config.font_size_px)
    text = format_overlay_date(captured_at)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = max(18, int(config.font_size_px * 0.45))
    margin = max(20, int(config.font_size_px * 0.7))
    if "right" in config.position:
        x = canvas.width - text_w - margin - pad
    else:
        x = margin + pad
    if "bottom" in config.position:
        y = canvas.height - text_h - margin - pad
    else:
        y = margin + pad

    alpha = int(255 * max(0.0, min(1.0, config.opacity)))
    box = (x - pad, y - pad, x + text_w + pad, y + text_h + pad)
    draw.rounded_rectangle(box, radius=max(6, pad // 3), fill=(10, 14, 16, int(alpha * 0.52)))
    draw.text((x, y), text, fill=(248, 244, 235, alpha), font=font)
    return Image.alpha_composite(canvas, overlay).convert("RGB")


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Avenir Next.ttc",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()
