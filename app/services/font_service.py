"""
Font fitting and loading service.

Handles dynamic font sizing, single-line fitting, and font caching.
Extracted from legacy_app.py and utils.py.
"""

import logging
from functools import lru_cache

from PIL import ImageFont

from utils import load_font_dynamic, get_draw_text_kwargs

logger = logging.getLogger(__name__)


def fit_loaded_font_to_single_line(
    draw, font_loader, display_text, max_width, start_size,
    language="english", min_size=6,
):
    """Shrink a font until the text fits on one line."""
    display_text = str(display_text or "")
    try:
        safe_width = max(1, int(float(max_width)))
    except Exception:
        safe_width = 1
    try:
        size = max(int(float(start_size or min_size)), int(min_size))
    except Exception:
        size = int(min_size)
    min_size = max(1, int(min_size))

    last_font = None
    while size >= min_size:
        font = font_loader(size)
        last_font = font
        try:
            text_len = draw.textlength(
                display_text, font=font,
                **get_draw_text_kwargs(display_text, language),
            )
            if text_len <= safe_width:
                return font, int(getattr(font, "size", size) or size)
        except Exception:
            return font, int(getattr(font, "size", size) or size)
        size -= 1

    if last_font is None:
        last_font = font_loader(min_size)
    return last_font, int(getattr(last_font, "size", min_size) or min_size)


def fit_dynamic_font_to_single_line(
    draw, font_path, display_text, max_width, start_size,
    language="english", min_size=6,
):
    """Fit text using a font file path with dynamic loading."""
    return fit_loaded_font_to_single_line(
        draw,
        lambda size: load_font_dynamic(
            font_path, display_text or "X", 10**9, size, language=language,
        ),
        display_text,
        max_width,
        start_size,
        language=language,
        min_size=min_size,
    )
