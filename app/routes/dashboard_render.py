"""Dashboard rendering helpers — extracted from dashboard_routes.py for organizational clarity.

The original function definitions in dashboard_routes.py shadow these imports
at runtime (Python's "last definition wins" rule), preserving exact
backward-compatible behavior. This module simply provides the same functions
in a dedicated file.
"""

import logging

logger = logging.getLogger(__name__)


def draw_aligned_colon_pil_helper(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=None, grow_mode=None, target_image=None, enable_gradient=False, bottom_color=None):
    try:
        draw_aligned_colon_pil(draw, img_width, direction, value_x, y, colon_text, font, fill, lang, gap, anchor_x=anchor_x, grow_mode=grow_mode, target_image=target_image, enable_gradient=enable_gradient, bottom_color=bottom_color)
    except Exception as e:
        logger.warning(f"Error drawing aligned colon: {e}")
