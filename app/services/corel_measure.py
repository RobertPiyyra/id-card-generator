"""
Text measurement and wrapping functions for CorelDRAW export.

Extracted from app/services/corel_export_service.py — handles PIL font
loading, text metrics measurement, text image building, and word wrapping.

USAGE: These functions are identical copies of those in corel_export_service.py.
The original definitions shadow these imports at runtime.
"""
import logging
import re

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Constants from corel_export_service.py
LAYOUT_DPI = 300
PRINT_DPI = 600
DEFAULT_EXPORT_MODE = "print"
SUPPORTED_EXPORT_MODES = {"editable", "print"}
SUPPORTED_COREL_PHOTO_MODES = {"embed", "frame_only"}
def _get_pil_font(font_path_or_name: str, font_size_px: int, language: str) -> ImageFont.ImageFont:
    """
    Load a Pillow font for text rasterization, with a small cache.

    Notes:
    - We intentionally go through `load_font_dynamic()` because it contains our Unicode fallbacks
      and avoids Arial for Arabic/Urdu/Hindi.
    """
    key = (str(font_path_or_name or ""), int(font_size_px), str(language or ""))
    cached = _PIL_FONT_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        lang = _normalize_language(language)
        sample_text = {
            "urdu": "نمونہ",
            "arabic": "عربي",
            "hindi": "परीक्षण",
        }.get(lang, "X")
        font = load_font_dynamic(
            font_path_or_name,
            sample_text,
            max_width=0,
            start_size=font_size_px,
            language=language,
        )
    except Exception:
        font = ImageFont.load_default()

    _PIL_FONT_CACHE[key] = font
    return font



def _pil_font_signature(pil_font: ImageFont.ImageFont) -> tuple[str, str, int, str]:
    try:
        font_name = "|".join(str(part) for part in pil_font.getname())
    except Exception:
        font_name = pil_font.__class__.__name__
    font_path = str(getattr(pil_font, "path", "") or "")
    font_size = int(getattr(pil_font, "size", 0) or 0)
    return (
        font_path,
        font_name,
        font_size,
        pil_font.__class__.__name__,
    )



def _measure_raster_text_metrics(
    text: str,
    pil_font: ImageFont.ImageFont,
    language: str,
) -> tuple[tuple[int, int, int, int], int, int, float, float]:
    text = "" if text is None else str(text)
    cache_key = (_pil_font_signature(pil_font), str(language or ""), text)
    cached = _RASTER_TEXT_METRICS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    draw_kwargs = get_draw_text_kwargs(text, language)
    dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    drawer = ImageDraw.Draw(dummy)

    try:
        bbox = drawer.textbbox((0, 0), text, font=pil_font, **draw_kwargs)
    except Exception:
        bbox = (0, 0, 0, 0)

    bbox_w = max(0, int(math.ceil((bbox[2] - bbox[0]) or 0)))
    bbox_h = max(0, int(math.ceil((bbox[3] - bbox[1]) or 0)))
    w = max(1, bbox_w)
    h = max(1, bbox_h)

    try:
        width_px = float(drawer.textlength(text, font=pil_font, **draw_kwargs))
    except Exception:
        try:
            width_px = float(pil_font.getlength(text))
        except Exception:
            width_px = float(bbox_w)
    width_px = float(max(width_px, float(bbox_w)))

    try:
        ascent, _descent = pil_font.getmetrics()
        baseline_y_px = float(ascent - bbox[1])
    except Exception:
        baseline_y_px = float(max(0, -bbox[1]))

    measured = (bbox, w, h, baseline_y_px, width_px)
    _RASTER_TEXT_METRICS_CACHE[cache_key] = measured
    return measured




def _build_text_image(
    text: str,
    pil_font: ImageFont.ImageFont,
    fill_rgba: tuple[int, int, int, int],
    language: str,
    enable_gradient: bool = False,
    gradient_color_bottom: tuple[int, int, int] = (51, 51, 51),
    char_spacing: int = 0,
    direction: str = "ltr"
) -> tuple[Image.Image, float, float]:
    """
    Render text into a transparent RGBA image.

    Returns:
    - image
    - baseline_y_px: y offset (in px) from top of image to text baseline
    - width_px: rendered width in pixels (used for RTL anchoring)

    Why:
    - ReportLab doesn't do complex script shaping (Urdu/Arabic/Hindi). Pillow+RAQM does, so we
      rasterize those runs to avoid font substitution and keep the same look as the preview.
    """
    text = "" if text is None else str(text)
    draw_kwargs = get_draw_text_kwargs(text, language)
    bbox, w, h, baseline_y_px, width_px = _measure_raster_text_metrics(text, pil_font, language)
    
    char_spacing_px = 0.0
    if char_spacing and direction != "rtl" and language not in ("urdu", "arabic") and not any(ord(c) >= 0x0600 and ord(c) <= 0x06FF for c in text):
        font_size = float(getattr(pil_font, "size", 24) or 24)
        char_spacing_px = font_size * (char_spacing / 1000.0)
        # Recompute width_px to include spacing
        spaced_width = 0.0
        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        dummy_draw = ImageDraw.Draw(dummy)
        for char in text:
            spaced_width += float(dummy_draw.textlength(char, font=pil_font, **draw_kwargs))
        spaced_width += char_spacing_px * (len(text) - 1)
        width_px = spaced_width
        w = int(math.ceil(width_px))

    pad_x = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.08)))
    pad_y = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.14)))
    img_w = max(1, w + (pad_x * 2))
    img_h = max(1, h + (pad_y * 2))
    baseline_y_px += pad_y

    # Render
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    
    if char_spacing_px > 0:
        cursor_x = pad_x - bbox[0]
        for char in text:
            if enable_gradient:
                from app.services.render_service import draw_text_gradient
                draw_text_gradient(
                    dr,
                    (cursor_x, pad_y - bbox[1]),
                    char,
                    font=pil_font,
                    top_color=fill_rgba[:3],
                    bottom_color=gradient_color_bottom,
                    enable_gradient=True,
                    lang=language,
                    target_image=img,
                    **draw_kwargs
                )
            else:
                dr.text((cursor_x, pad_y - bbox[1]), char, font=pil_font, fill=fill_rgba, **draw_kwargs)
            cursor_x += dr.textlength(char, font=pil_font, **draw_kwargs) + char_spacing_px
    else:
        if enable_gradient:
            from app.services.render_service import draw_text_gradient
            draw_text_gradient(
                dr,
                (pad_x - bbox[0], pad_y - bbox[1]),
                text,
                font=pil_font,
                top_color=fill_rgba[:3],
                bottom_color=gradient_color_bottom,
                enable_gradient=True,
                lang=language,
                target_image=img,
                **draw_kwargs
            )
        else:
            dr.text((pad_x - bbox[0], pad_y - bbox[1]), text, font=pil_font, fill=fill_rgba, **draw_kwargs)
            
    return img, baseline_y_px, float(max(width_px + (pad_x * 2), img_w))



def parse_pdf_export_mode(mode_raw: str | None) -> str | None:
    """Parse export mode from query/form input."""
    if mode_raw is None:
        return DEFAULT_EXPORT_MODE
    mode = str(mode_raw).strip().lower()
    if not mode:
        return DEFAULT_EXPORT_MODE
    if mode in SUPPORTED_EXPORT_MODES:
        return mode
    return None




def _render_profile(mode: str) -> dict:
    mode = (mode or DEFAULT_EXPORT_MODE).strip().lower()
    is_print = mode == "print"
    raster_multiplier = 2 if is_print else 1
    return {
        "mode": mode,
        "layout_dpi": LAYOUT_DPI,
        "asset_dpi": PRINT_DPI if is_print else LAYOUT_DPI,
        "raster_multiplier": raster_multiplier,
    }




def _corel_editable_photo_mode(photo_settings: dict | None) -> str:
    mode = str((photo_settings or {}).get("corel_editable_photo_mode", "frame_only") or "frame_only").strip().lower()
    if mode in SUPPORTED_COREL_PHOTO_MODES:
        return mode
    return "frame_only"



def _normalize_wrap_text(text: str) -> str:
    raw = "" if text is None else str(text)
    raw = unicodedata.normalize("NFC", raw)
    raw = raw.replace("\u00A0", " ").replace("\u202F", " ").replace("\t", " ")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = _clean_bidi_controls(raw)
    cleaned_lines: list[str] = []
    for line in raw.split("\n"):
        compact = " ".join(line.split()).strip()
        if compact:
            cleaned_lines.append(compact)
    return "\n".join(cleaned_lines).strip()



def _measure_vector_text_width(text: str, font_name: str, font_size_pt: float) -> float:
    cache_key = (str(font_name or ""), round(float(font_size_pt or 0.0), 4), str(text or ""))
    cached = _VECTOR_TEXT_WIDTH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        width = float(pdfmetrics.stringWidth(str(text or ""), font_name, float(font_size_pt)))
    except Exception:
        width = float(max(0, len(str(text or ""))) * max(1.0, float(font_size_pt)) * 0.55)
    _VECTOR_TEXT_WIDTH_CACHE[cache_key] = width
    return width



def _measure_raster_text_width(
    text: str,
    *,
    font_path_or_name: str,
    font_size_pt: float,
    language: str,
    scale: float,
    raster_multiplier: int,
) -> float:
    text = str(text or "")
    if not text:
        return 0.0

    scale = max(float(scale or 0.0), 0.001)
    raster_multiplier = max(1, int(raster_multiplier or 1))
    font_size_px = max(1, int(round((float(font_size_pt) / scale) * raster_multiplier)))
    pil_font = _get_pil_font(font_path_or_name, font_size_px, language)
    try:
        _bbox, _w, _h, _baseline, width_px = _measure_raster_text_metrics(text, pil_font, language)
    except Exception:
        width_px = float(max(0, len(text)) * font_size_px * 0.55)
    return width_px * (scale / raster_multiplier)



def _ellipsize_to_width(text: str, max_width_pt: float, measure_fn) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    ellipsis = "..."
    if measure_fn(value) <= max_width_pt:
        return value
    if measure_fn(ellipsis) > max_width_pt:
        return ""
    words = value.split()
    if len(words) > 1:
        for count in range(len(words), 0, -1):
            candidate = " ".join(words[:count]).rstrip()
            if not candidate:
                continue
            candidate = candidate + ellipsis
            if measure_fn(candidate) <= max_width_pt:
                return candidate

    low, high = 0, len(value)
    best = ellipsis
    while low <= high:
        mid = (low + high) // 2
        candidate = value[:mid].rstrip() + ellipsis
        if measure_fn(candidate) <= max_width_pt:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best



def _split_wrap_units(text: str) -> list[str]:
    text = str(text or "")
    if not text:
        return []

    parts = re.findall(r"\S+|\s+", text)
    units: list[str] = []
    break_after = {"/", "\\", "|", ",", ";", ":", "-", "_", ")"}
    break_before = {"(", "[", "{", "#"}

    for part in parts:
        if not part:
            continue
        if part.isspace():
            continue

        token = ""
        for ch in part:
            if ch in break_before and token:
                units.append(token)
                token = ch
                continue

            token += ch
            if ch in break_after:
                units.append(token)
                token = ""

        if token:
            units.append(token)

    return units



def _rebalance_wrapped_lines(lines: list[str], max_width_pt: float, measure_fn) -> list[str]:
    if len(lines) < 2:
        return lines

    updated = list(lines)
    prev_line = updated[-2].strip()
    last_line = updated[-1].strip()
    if not prev_line or not last_line:
        return updated

    prev_parts = prev_line.split()
    last_parts = last_line.split()
    if len(prev_parts) < 2 or len(last_parts) != 1:
        return updated

    moved = prev_parts[-1]
    new_prev = " ".join(prev_parts[:-1]).strip()
    new_last = f"{moved} {last_line}".strip()
    if not new_prev:
        return updated
    if measure_fn(new_prev) > max_width_pt or measure_fn(new_last) > max_width_pt:
        return updated

    updated[-2] = new_prev
    updated[-1] = new_last
    return updated



def _wrap_text_by_width(text: str, max_width_pt: float, measure_fn) -> list[str]:
    raw_text = str(text or "")
    paragraphs = [segment for segment in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if segment.strip()]
    if not paragraphs:
        paragraphs = [_normalize_wrap_text(raw_text)]
    wrapped_lines: list[str] = []

    for paragraph in paragraphs:
        lines = _wrap_text_by_width_single(_normalize_wrap_text(paragraph), max_width_pt, measure_fn)
        wrapped_lines.extend(lines)

    return wrapped_lines or [""]



def _wrap_text_by_width_single(text: str, max_width_pt: float, measure_fn) -> list[str]:
    text = _normalize_wrap_text(text)
    if not text:
        return [""]

    if max_width_pt <= 1:
        return [text]

    words = _split_wrap_units(text)
    lines: list[str] = []
    current = ""

    def flush_current():
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    for word in words:
        if not word:
            continue
        candidate = f"{current} {word}".strip() if current else word
        if measure_fn(candidate) <= max_width_pt:
            current = candidate
            continue

        if current:
            flush_current()

        if measure_fn(word) <= max_width_pt:
            current = word
            continue

        # Hard-break a single overlong token.
        chunk = ""
        for ch in word:
            test_chunk = chunk + ch
            if chunk and measure_fn(test_chunk) > max_width_pt:
                lines.append(chunk)
                chunk = ch
            else:
                chunk = test_chunk
        current = chunk

    flush_current()
    return _rebalance_wrapped_lines(lines or [text], max_width_pt, measure_fn)



def _fit_wrapped_text(
    text: str,
    *,
    font_name: str,
    start_size_pt: float,
    min_size_pt: float,
    max_width_pt: float,
    max_lines: int,
    max_height_pt: float | None = None,
    line_height_factor: float = 1.15,
    measure_builder=None,
) -> tuple[float, list[str]]:
    text = _normalize_wrap_text(text)
    if not text:
        return float(start_size_pt), [""]

    max_lines = max(1, int(max_lines or 1))
    min_size_pt = float(min_size_pt)
    start_size_pt = max(min_size_pt, float(start_size_pt))
    line_height_factor = max(1.0, float(line_height_factor or 1.15))
    max_height_pt = float(max_height_pt) if max_height_pt else None
    if measure_builder is None:
        measure_builder = lambda size_pt: (lambda s, _size=size_pt: _measure_vector_text_width(s, font_name, _size))

    def _effective_max_lines(size_pt: float) -> int:
        allowed = max_lines
        if max_height_pt:
            line_height_pt = max(size_pt * line_height_factor, 0.1)
            allowed = min(allowed, max(1, int(max_height_pt / line_height_pt)))
        return max(1, allowed)

    def _fits(size_pt: float) -> tuple[bool, list[str]]:
        measure_fn = measure_builder(size_pt)
        lines = _wrap_text_by_width(text, max_width_pt, measure_fn)
        allowed_lines = _effective_max_lines(size_pt)
        fits_width = all(measure_fn(line) <= max_width_pt for line in lines)
        fits_height = len(lines) <= allowed_lines
        return fits_width and fits_height, lines

    step = 0.25
    sizes: list[float] = []
    curr_size = min_size_pt
    while curr_size <= start_size_pt + 0.0001:
        sizes.append(round(curr_size, 4))
        curr_size += step

    low = 0
    high = len(sizes) - 1
    best_index = 0
    best_lines = [text]

    while low <= high:
        mid = (low + high) // 2
        size_pt = sizes[mid]
        fits, lines = _fits(size_pt)
        if fits:
            best_index = mid
            best_lines = lines
            low = mid + 1
        else:
            high = mid - 1

    best_size = sizes[best_index]
    best_measure = measure_builder(best_size)
    best_lines = _wrap_text_by_width(text, max_width_pt, best_measure)
    best_allowed_lines = _effective_max_lines(best_size)
    # Enforce max_lines limit even when binary search succeeds
    if len(best_lines) > best_allowed_lines:
        best_lines = best_lines[:best_allowed_lines]
        best_lines[-1] = _ellipsize_to_width(best_lines[-1], max_width_pt, best_measure)
    if len(best_lines) <= best_allowed_lines and all(best_measure(line) <= max_width_pt for line in best_lines):
        return best_size, best_lines

    final_measure = measure_builder(min_size_pt)
    final_lines = _wrap_text_by_width(text, max_width_pt, final_measure)
    final_allowed_lines = _effective_max_lines(min_size_pt)
    if len(final_lines) > final_allowed_lines:
        final_lines = final_lines[:final_allowed_lines]
        final_lines[-1] = _ellipsize_to_width(final_lines[-1], max_width_pt, final_measure)
    else:
        final_lines = [
            _ellipsize_to_width(line, max_width_pt, final_measure) if final_measure(line) > max_width_pt else line
            for line in final_lines
        ]
    return min_size_pt, final_lines




def _is_probably_pdf_source(src: str, content_type: str | None = None, content: bytes | None = None) -> bool:
    """Best-effort PDF detection for local paths and URLs."""
    src_l = (src or "").strip().lower()
    ct_l = (content_type or "").strip().lower()
    body = content or b""
    if ".pdf" in src_l:
        return True
    if "application/pdf" in ct_l:
        return True
    if body.startswith(b"%PDF-"):
        return True
    return False


