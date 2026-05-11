from datetime import datetime

from PIL import Image, ImageFont

from selfietl.config import DateOverlayConfig
from selfietl.pipeline import overlay


def test_format_overlay_date_uses_full_month_day_year():
    assert overlay.format_overlay_date(datetime(2025, 5, 5, 8, 30)) == "May 5, 2025"
    assert overlay.format_overlay_date(datetime(2025, 11, 12, 8, 30)) == "November 12, 2025"


def test_draw_date_overlay_ignores_disabled_and_format_config(monkeypatch):
    drawn_text: list[str] = []

    class FakeDraw:
        def textbbox(self, _position, _text, font=None):
            return (0, 0, 120, 30)

        def rounded_rectangle(self, *_args, **_kwargs):
            return None

        def text(self, _position, text, **_kwargs):
            drawn_text.append(text)

    monkeypatch.setattr(overlay.ImageDraw, "Draw", lambda _image: FakeDraw())
    monkeypatch.setattr(overlay, "_load_font", lambda _size: ImageFont.load_default())

    result = overlay.draw_date_overlay(
        Image.new("RGB", (400, 300)),
        datetime(2025, 5, 5, 8, 30),
        DateOverlayConfig(enabled=False, format="%b %Y"),
    )

    assert drawn_text == ["May 5, 2025"]
    assert result.mode == "RGB"
