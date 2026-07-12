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
    """Find the largest font size that fits text on one line using binary search.

    Replaces the previous linear descent (O(n) font loads) with O(log n).
    """
    display_text = str(display_text or "")
    try:
        safe_width = max(1, int(float(max_width)))
    except Exception:
        safe_width = 1
    try:
        hi = max(int(float(start_size or min_size)), int(min_size))
    except Exception:
        hi = int(min_size)
    lo = max(1, int(min_size))

    best_font = font_loader(lo)
    best_size = lo

    while lo <= hi:
        mid = (lo + hi) // 2
        font = font_loader(mid)
        try:
            text_len = draw.textlength(
                display_text, font=font,
                **get_draw_text_kwargs(display_text, language),
            )
            if text_len <= safe_width:
                # Text fits — try larger
                best_font = font
                best_size = mid
                lo = mid + 1
            else:
                # Text overflows — try smaller
                hi = mid - 1
        except Exception:
            best_font = font
            best_size = mid
            break

    return best_font, int(getattr(best_font, "size", best_size) or best_size)


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
