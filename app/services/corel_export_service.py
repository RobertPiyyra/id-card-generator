"""CorelDRAW export utility functions. Extracted from app/routes/corel_routes.py."""

import io, json, logging, math, os, re, sys, unicodedata, base64, html, requests
from types import SimpleNamespace
from functools import lru_cache

import arabic_reshaper
from bidi.algorithm import get_display
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from reportlab.pdfgen import canvas as reportlab_canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
import reportlab.pdfbase.pdfdoc as reportlab_pdfdoc
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing, qr as rl_qr
from reportlab.graphics.shapes import Drawing

from models import db, Student, Template, TemplateField
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, PLACEHOLDER_PATH, STATIC_DIR,
    get_template_settings, get_template_path, get_card_size,
    get_template_orientation, generate_qr_code, generate_barcode_code128, generate_data_hash,
    load_template, _language_font_fallbacks, _presentation_forms_font_fallbacks,
    process_text_for_drawing, get_draw_text_kwargs,
    split_label_and_colon, colon_anchor_for_value,
    load_font_dynamic, get_field_layout_item, PIL_RAQM_AVAILABLE, _font_covers_text,
    get_cloudinary_face_crop_url, round_photo, parse_layout_config, get_anchor_max_text_width,
    get_layout_flow_start_y, get_localized_standard_labels, normalize_photo_shape,
)
from utils import load_template_smart

logger = logging.getLogger(__name__)
GOOGLE_TRANSLATE_API_KEY=(os.environ.get("GOOGLE_TRANSLATE_API_KEY") or "").strip()
LAYOUT_DPI = 300

try:
    _ARABIC_RESHAPER = arabic_reshaper.ArabicReshaper(
        configuration={"use_unshaped_instead_of_isolated": True}
    )
except Exception:
    _ARABIC_RESHAPER = None

try:
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
except Exception:
    PdfReader = PdfWriter = Transformation = ArrayObject = DecodedStreamObject = DictionaryObject = NameObject = None

try:
    import pikepdf
except Exception:
    pikepdf = None

# Monkeypatch PDFPage check_format and Canvas _setShadingUsed
# Guard against duplicate patching in case of reloads/multiple imports
if not hasattr(reportlab_pdfdoc.PDFPage, "_patched_by_corel"):
    orig_check_format = reportlab_pdfdoc.PDFPage.check_format
    
    def _my_check_format(self, document):
        orig_check_format(self, document)
        if hasattr(self, "_patternsUsed") and self._patternsUsed:
            for name, ref in self._patternsUsed.items():
                self.Resources.Pattern[name] = ref
                
    reportlab_pdfdoc.PDFPage.check_format = _my_check_format
    reportlab_pdfdoc.PDFPage._patched_by_corel = True

if not hasattr(reportlab_canvas.Canvas, "_patched_by_corel"):
    orig_setShadingUsed = reportlab_canvas.Canvas._setShadingUsed
    
    def _my_setShadingUsed(self, page):
        orig_setShadingUsed(self, page)
        page._patternsUsed = getattr(self, "_patternsUsed", {})
        
    reportlab_canvas.Canvas._setShadingUsed = _my_setShadingUsed
    reportlab_canvas.Canvas._patched_by_corel = True



def _parse_hex_to_rgb_normalized(hex_color_str, default=(0.55, 0.14, 0.24)):
    val = str(hex_color_str or "").strip()
    if len(val) == 7 and val.startswith("#"):
        try:
            return (
                int(val[1:3], 16) / 255.0,
                int(val[3:5], 16) / 255.0,
                int(val[5:7], 16) / 255.0
            )
        except ValueError:
            return default
    return default




def local_apply_text_case(text, case_type):
    if not text: return ""
    text = str(text)
    if case_type == "uppercase": return text.upper()
    elif case_type == "lowercase": return text.lower()
    elif case_type == "capitalize": return text.title()
    return text




def _corel_safe_pdf_bytes(doc, *, garbage=4, clean=False):
    """
    Serialize a PyMuPDF document with the simplest possible storage layout.

    This is intentionally minimal and uncompressed. Higher-level cleanup runs later via
    `_make_corel_friendly`, but every intermediate PDF should already avoid object
    streams and modern compression features that CorelDRAW often rejects.
    """
    return doc.tobytes(
        garbage=garbage,
        clean=clean,
        deflate=False,
        deflate_images=False,
        deflate_fonts=False,
        expand=255,
        linear=False,
        no_new_id=True,
        pretty=False,
        use_objstms=0,
    )




def _is_valid_pdf_bytes(pdf_bytes: bytes) -> bool:
    if not pdf_bytes:
        return False
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        valid = len(doc) > 0
        doc.close()
        return valid
    except Exception:
        return False




def _strip_marked_content_operators(content_bytes: bytes, *, ext_gstate_names: list[bytes] | None = None) -> bytes:
    if not content_bytes:
        return content_bytes
    updated = bytes(content_bytes)
    updated = re.sub(rb"/OC\s+/[A-Za-z0-9_.-]+\s+BDC\s*", b"", updated)
    updated = re.sub(rb"/[A-Za-z0-9_.-]+\s*<<[\s\S]*?>>\s*BDC\s*", b"", updated)
    updated = re.sub(rb"/[A-Za-z0-9_.-]+\s+BMC\s*", b"", updated)
    updated = re.sub(rb"\s+EMC(?=[\s\n\r]|$)", b"\n", updated)
    if ext_gstate_names:
        for name in ext_gstate_names:
            updated = re.sub(re.escape(name) + rb"\s+gs(?=[\s\n\r]|$)", b"", updated)
    updated = re.sub(rb"\n{3,}", b"\n\n", updated)
    return updated




def _strip_page_level_pdf_keys(page_obj) -> None:
    if page_obj is None:
        return
    for key in ("/Group", "/Metadata", "/PieceInfo", "/StructParents", "/Tabs", "/SeparationInfo"):
        try:
            if key in page_obj:
                del page_obj[key]
        except Exception:
            continue




def _strip_optional_content_pypdf_page(page, *, strip_transparency: bool = True) -> None:
    try:
        _strip_page_level_pdf_keys(page)
        resources = (page.get("/Resources") or {}).get_object()
        if "/Properties" in resources:
            del resources[NameObject("/Properties")]

        removed_gs_names: list[bytes] = []
        if strip_transparency and "/ExtGState" in resources:
            try:
                ext_state = resources.get("/ExtGState")
                if ext_state:
                    for name in list(ext_state.get_object().keys()):
                        removed_gs_names.append(str(name).encode("latin1"))
                del resources[NameObject("/ExtGState")]
            except Exception:
                pass

        xobjects = resources.get("/XObject")
        if xobjects:
            for _, xo_ref in xobjects.get_object().items():
                try:
                    xo = xo_ref.get_object()
                    for key in ("/Group", "/SMask", "/Mask", "/Metadata"):
                        if key in xo:
                            del xo[key]
                except Exception:
                    continue

        contents = page.get_contents()
        if contents:
            if isinstance(contents, list):
                for content in contents:
                    try:
                        data = _strip_marked_content_operators(content.get_data(), ext_gstate_names=removed_gs_names)
                        content.set_data(data)
                    except Exception:
                        continue
            else:
                try:
                    contents.set_data(
                        _strip_marked_content_operators(contents.get_data(), ext_gstate_names=removed_gs_names)
                    )
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Corel page sanitize failed (pypdf): %s", exc)




def _save_pikepdf_corel(pdf, out_stream) -> None:
    pdf.save(
        out_stream,
        force_version="1.4",
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
        compress_streams=False,
        recompress_flate=False,
        normalize_content=False,
        linearize=False,
    )




def _normalize_pdf_for_corel(pdf_bytes: bytes) -> bytes:
    """
    Re-write a PDF page-by-page through pypdf in a stripped, low-feature form.

    This pass removes page-level optional-content references and risky metadata, then
    writes a simple uncompressed PDF. It intentionally does not try to preserve layers.
    """
    if not pdf_bytes or PdfReader is None or PdfWriter is None:
        return pdf_bytes
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        _rebuild_optional_content_catalog(writer)
        out = io.BytesIO()
        writer.write(out)
        normalized_bytes = out.getvalue()
        if pikepdf is not None:
            try:
                src = io.BytesIO(normalized_bytes)
                dst = io.BytesIO()
                with pikepdf.open(src) as pdf:
                    _save_pikepdf_corel(pdf, dst)
                return dst.getvalue()
            except Exception as exc:
                logger.warning("pikepdf Corel normalization failed: %s", exc)
        return normalized_bytes
    except Exception as exc:
        logger.warning("pypdf Corel normalization failed: %s", exc)
        return pdf_bytes




def _rebuild_optional_content_catalog(writer) -> None:
    """
    Historically this rebuilt OCG metadata.

    For CorelDRAW compatibility we now do the opposite: strip optional-content and
    page-level metadata from the pypdf writer before it serializes.
    """
    if writer is None or ArrayObject is None or DictionaryObject is None or NameObject is None:
        return

    try:
        if hasattr(writer, "_root_object") and writer._root_object is not None:
            for key in ("/OCProperties", "/Metadata", "/StructTreeRoot", "/MarkInfo"):
                try:
                    if NameObject(key) in writer._root_object:
                        del writer._root_object[NameObject(key)]
                except Exception:
                    continue

        for page in writer.pages:
            _strip_optional_content_pypdf_page(page, strip_transparency=True)
    except Exception as exc:
        logger.warning("Failed to sanitize optional-content catalog: %s", exc)




def _flatten_optional_content_pdf_bytes(pdf_bytes: bytes) -> bytes:
    """
    Strip optional-content and marked-content operators from raw PDF bytes.

    This is intentionally aggressive for CorelDRAW. Layers and marked content are not
    preserved because they are a common import failure source.
    """
    if not pdf_bytes or pikepdf is None:
        return pdf_bytes

    try:
        src = io.BytesIO(pdf_bytes)
        dst = io.BytesIO()
        with pikepdf.open(src) as pdf:
            if "/OCProperties" in pdf.Root:
                del pdf.Root["/OCProperties"]
            for key in ("/Metadata", "/StructTreeRoot", "/MarkInfo"):
                if key in pdf.Root:
                    del pdf.Root[key]
            for page in pdf.pages:
                _strip_page_level_pdf_keys(page.obj)
                resources = page.obj.get("/Resources", None)
                removed_gs_names: list[bytes] = []
                if resources is not None and "/Properties" in resources:
                    del resources["/Properties"]
                if resources is not None and "/ExtGState" in resources:
                    try:
                        removed_gs_names = [str(name).encode("latin1") for name in list(resources["/ExtGState"].keys())]
                        del resources["/ExtGState"]
                    except Exception:
                        removed_gs_names = []
                if resources is not None and "/XObject" in resources:
                    try:
                        for _, xo in resources["/XObject"].items():
                            xo_obj = xo.get_object()
                            for key in ("/Group", "/SMask", "/Mask", "/Metadata"):
                                if key in xo_obj:
                                    del xo_obj[key]
                    except Exception:
                        pass
                contents = page.obj.get("/Contents", None)
                if contents is None:
                    continue
                streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
                for stream in streams:
                    data = _strip_marked_content_operators(
                        bytes(stream.read_bytes()),
                        ext_gstate_names=removed_gs_names,
                    )
                    stream.write(data)
            _save_pikepdf_corel(pdf, dst)
        return dst.getvalue()
    except Exception as exc:
        logger.warning("Failed to flatten optional-content PDF bytes: %s", exc)
        return pdf_bytes




def _aggressive_corel_flatten(pdf_bytes: bytes, mode: str = "editable") -> bytes:
    """
    Remove the PDF features CorelDRAW most often rejects.

    This pass is intentionally destructive for PDF metadata, transparency state, layers,
    and marked content. It keeps text, images, and basic vector content whenever possible.
    """
    if not pdf_bytes:
        return pdf_bytes

    mode = (mode or "editable").strip().lower()
    if pikepdf is None:
        logger.info("Corel flatten skipped: pikepdf unavailable")
        return _normalize_pdf_for_corel(pdf_bytes)

    try:
        src = io.BytesIO(pdf_bytes)
        dst = io.BytesIO()
        with pikepdf.open(src) as pdf:
            for key in ("/OCProperties", "/Metadata", "/StructTreeRoot", "/MarkInfo"):
                if key in pdf.Root:
                    del pdf.Root[key]

            for page_index, page in enumerate(pdf.pages):
                _strip_page_level_pdf_keys(page.obj)
                resources = page.obj.get("/Resources", None)
                removed_gs_names: list[bytes] = []

                if resources is not None and "/Properties" in resources:
                    del resources["/Properties"]

                if resources is not None and "/ExtGState" in resources:
                    try:
                        removed_gs_names = [str(name).encode("latin1") for name in list(resources["/ExtGState"].keys())]
                        del resources["/ExtGState"]
                        logger.info("Corel flatten: removed ExtGState page=%s entries=%s", page_index, len(removed_gs_names))
                    except Exception:
                        removed_gs_names = []

                if resources is not None and "/XObject" in resources:
                    try:
                        for _, xo in resources["/XObject"].items():
                            xo_obj = xo.get_object()
                            for key in ("/Group", "/SMask", "/Mask", "/Metadata"):
                                if key in xo_obj:
                                    del xo_obj[key]
                    except Exception:
                        pass

                contents = page.obj.get("/Contents", None)
                if contents is None:
                    continue
                streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
                for stream in streams:
                    stream.write(
                        _strip_marked_content_operators(
                            bytes(stream.read_bytes()),
                            ext_gstate_names=removed_gs_names,
                        )
                    )

            _save_pikepdf_corel(pdf, dst)
        return dst.getvalue()
    except Exception as exc:
        logger.warning("Aggressive Corel flatten failed mode=%s: %s", mode, exc)
        return pdf_bytes




def _make_corel_friendly(pdf_bytes: bytes, mode: str = "editable") -> bytes:
    """Final nuclear cleaning specifically for CorelDRAW compatibility."""
    current = bytes(pdf_bytes or b"")
    if not current:
        return current

    mode = (mode or "editable").strip().lower()
    logger.info("Corel clean start mode=%s size=%s", mode, len(current))

    try:
        current = _flatten_optional_content_pdf_bytes(current)
        logger.info("Corel clean step=flatten_optional_content size=%s", len(current))
    except Exception as exc:
        logger.warning("Corel clean flatten_optional_content failed: %s", exc)

    try:
        current = _normalize_pdf_for_corel(current)
        logger.info("Corel clean step=normalize size=%s", len(current))
    except Exception as exc:
        logger.warning("Corel clean normalize failed: %s", exc)

    if mode == "editable":
        try:
            previous = current
            current = _aggressive_corel_flatten(current, mode=mode)
            if not _is_valid_pdf_bytes(current):
                logger.warning("Corel clean aggressive_flatten produced invalid PDF; reverting to previous state")
                current = previous
            else:
                logger.info("Corel clean step=aggressive_flatten size=%s", len(current))
        except Exception as exc:
            logger.warning("Corel clean aggressive_flatten failed: %s", exc)

        try:
            previous = current
            current = _normalize_pdf_for_corel(current)
            if not _is_valid_pdf_bytes(current):
                logger.warning("Corel clean final_normalize produced invalid PDF; reverting to previous state")
                current = previous
            else:
                logger.info("Corel clean step=final_normalize size=%s", len(current))
        except Exception as exc:
            logger.warning("Corel clean final_normalize failed: %s", exc)

    if not _is_valid_pdf_bytes(current):
        logger.warning("Corel clean final bytes invalid; returning original unclean PDF bytes")
        return bytes(pdf_bytes or b"")

    logger.info("Corel clean end mode=%s size=%s", mode, len(current))
    return current




def _template_pdf_has_corel_hostile_features(pdf_bytes: bytes) -> bool:
    if not pdf_bytes:
        return False
    tokens = (
        b"/Shading",
        b"/Group",
        b"/SMask",
        b"/Mask",
        b"/FontFile",
        b"/FontFile2",
        b"/FontFile3",
        b"/FontDescriptor",
        b"/ExtGState",
        b"/OCProperties",
        b"/Properties",
    )
    return any(token in pdf_bytes for token in tokens)




def _rasterize_template_pdf_for_editable_overlay(pdf_bytes: bytes, *, dpi: int = 300) -> bytes:
    """
    Convert a template PDF page into a simple image-backed PDF page.

    This is the safety valve for uploaded PDFs that remain Corel-hostile even after
    structural cleanup. The generated user text/photo/QR stay editable, but the template
    background becomes a flat page image so Corel can open the exported file reliably.
    """
    if not pdf_bytes:
        return pdf_bytes

    template_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    try:
        if len(template_doc) < 1:
            return pdf_bytes
        src_page = template_doc[0]
        render_dpi = max(600, int(dpi or 300) * 2)
        pix = src_page.get_pixmap(
            dpi=render_dpi,
            alpha=False,
            colorspace=fitz.csRGB,
        )
        image_bytes = pix.tobytes("png")
        page = out_doc.new_page(width=float(src_page.rect.width), height=float(src_page.rect.height))
        page.insert_image(page.rect, stream=image_bytes, overlay=True, keep_proportion=False)
        raster_pdf = _corel_safe_pdf_bytes(out_doc, garbage=4, clean=False)
        logger.info(
            "Corel editable template fallback: rasterized uploaded PDF page size=%sx%s dpi=%s",
            pix.width,
            pix.height,
            render_dpi,
        )
        return _make_corel_friendly(raster_pdf, mode="editable")
    except Exception as exc:
        logger.warning("Editable template rasterization failed: %s", exc)
        return pdf_bytes
    finally:
        try:
            out_doc.close()
        except Exception:
            pass
        try:
            template_doc.close()
        except Exception:
            pass




def _build_template_card_placements(
    *,
    student_count: int,
    cols: int,
    rows: int,
    start_x_pt: float,
    start_y_pt: float,
    gap_pt: float,
    card_w_pt: float,
    card_h_pt: float,
    sheet_h_pt: float,
) -> list[dict]:
    placements: list[dict] = []
    cards_per_sheet = max(1, int(cols) * int(rows))
    for card_index in range(max(0, int(student_count))):
        idx_on_sheet = card_index % cards_per_sheet
        col_idx = idx_on_sheet % cols
        row_idx = idx_on_sheet // cols
        card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
        card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
        card_bottom_y = card_top_y - card_h_pt
        placements.append(
            {
                "page_index": int(card_index // cards_per_sheet),
                "x0": float(card_x),
                "y0": float(sheet_h_pt - card_top_y),
                "x1": float(card_x + card_w_pt),
                "y1": float(sheet_h_pt - card_bottom_y),
            }
        )
    return placements


@lru_cache(maxsize=1)


def _get_app_card_render_helpers():
    """
    Resolve the canonical card render helpers from the already-loaded Flask app module.

    We intentionally avoid `from app import ...` here because this blueprint is imported
    by `app.py`, and importing `app` again at request time can create a second module copy
    when the server was started via `python app.py`.
    """
    candidate_names: list[str] = []
    try:
        import_name = getattr(current_app, "import_name", None)
        if import_name:
            candidate_names.append(import_name)
    except Exception:
        pass
    candidate_names.extend(["app", "__main__"])

    for module_name in candidate_names:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        build_runs = getattr(module, "build_student_card_text_runs", None)
        render_side = getattr(module, "render_student_card_side", None)
        render_background = getattr(module, "render_student_card_side_background", None)
        load_photo = getattr(module, "load_student_photo_rgba", None)
        process_photo = getattr(module, "_process_photo_pil", None)
        if build_runs and render_side and render_background and load_photo:
            return {
                "build_student_card_text_runs": build_runs,
                "render_student_card_side": render_side,
                "render_student_card_side_background": render_background,
                "load_student_photo_rgba": load_photo,
                "process_photo_pil": process_photo,
            }

    raise RuntimeError("App card render helpers are unavailable in the loaded application module")




def _safe_canvas_font_name(font_path: str | None, language: str, role: str) -> str:
    role = str(role or "regular").strip().lower()
    builtin_fallback = "Helvetica-Bold" if role == "bold" else "Helvetica"
    source_path = str(font_path or "").strip()
    lang = _normalize_language(language)

    candidate_paths: list[str] = []
    if source_path and os.path.exists(source_path):
        candidate_paths.append(source_path)

    fallback_names = (
        _presentation_forms_font_fallbacks()
        if lang in {"urdu", "arabic"}
        else _language_font_fallbacks(lang)
    )
    for fallback_name in fallback_names:
        fallback_path = os.path.join(FONTS_FOLDER, fallback_name)
        if os.path.exists(fallback_path) and fallback_path not in candidate_paths:
            candidate_paths.append(fallback_path)

    sample_text = {
        "urdu": "محمد علی",
        "arabic": "محمد علي",
        "hindi": "परीक्षण",
    }.get(lang, "Sample")

    for candidate_path in candidate_paths:
        try:
            if lang in {"urdu", "arabic", "hindi"} and not _font_covers_text(candidate_path, sample_text):
                continue
        except Exception:
            continue

        ext = os.path.splitext(candidate_path)[1].lower()
        if ext not in {".ttf", ".ttc", ".otf"}:
            continue

        font_name = f"CorelRun_{abs(hash((candidate_path, role)))}"
        try:
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, candidate_path))
            return font_name
        except Exception:
            logger.warning("Canvas font registration failed: %s", candidate_path)

    return builtin_fallback




def _run_baseline_px(run: dict) -> float:
    font_size_px = max(1, int(run.get("font_size") or 1))
    language = run.get("language") or "english"
    font_path = run.get("font_path") or ""
    try:
        pil_font = _get_pil_font(font_path, font_size_px, language)
        ascent, _descent = pil_font.getmetrics()
        return float(run.get("y", 0)) + float(ascent)
    except Exception:
        return float(run.get("y", 0)) + float(font_size_px * 0.8)




def _draw_raster_text_run_on_canvas(
    c,
    run: dict,
    *,
    card_x: float,
    card_bottom_y: float,
    card_h_pt: float,
    scale: float,
) -> None:
    text = str(run.get("text") or "")
    if not text:
        return

    font_size_px = max(1, int(run.get("font_size") or 1))
    language = run.get("language") or "english"
    font_path = run.get("font_path") or ""
    color = tuple(int(max(0, min(255, value))) for value in (run.get("color") or (0, 0, 0)))

    enable_gradient = bool(run.get("enable_gradient", False))
    gradient_color_bottom = tuple(int(max(0, min(255, value))) for value in (run.get("gradient_color_bottom") or (51, 51, 51)))

    pil_font = _get_pil_font(font_path, font_size_px, language)
    bbox, _w, _h, _baseline_y_px, _width_px = _measure_raster_text_metrics(text, pil_font, language)
    pad_x = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.08)))
    pad_y = max(1, int(math.ceil(max(1, getattr(pil_font, "size", 0)) * 0.14)))
    anchor_offset_x = pad_x - bbox[0]
    anchor_offset_y = pad_y - bbox[1]
    
    img, _baseline_y_px, _width_px = _build_text_image(
        text,
        pil_font,
        (*color, 255),
        language,
        enable_gradient=enable_gradient,
        gradient_color_bottom=gradient_color_bottom
    )

    x_pt = float(card_x) + ((float(run.get("x", 0)) - float(anchor_offset_x)) * scale)
    y_top_px = float(run.get("y", 0)) - float(anchor_offset_y)
    y_pt = float(card_bottom_y) + (float(card_h_pt) - ((y_top_px + img.size[1]) * scale))
    c.drawImage(
        _pil_image_reader(img, preserve_alpha=True),
        x_pt,
        y_pt,
        width=img.size[0] * scale,
        height=img.size[1] * scale,
        mask="auto",
    )




def _draw_text_runs_on_canvas(
    c,
    runs: list[dict],
    *,
    card_x: float,
    card_bottom_y: float,
    card_h_pt: float,
    scale: float,
    mode: str,
) -> None:
    for run in runs or []:
        text = str(run.get("text") or "")
        if not text:
            continue
        enable_gradient = bool(run.get("enable_gradient", False))
        if enable_gradient:
            font_name = _safe_canvas_font_name(
                run.get("font_path"),
                run.get("language") or "english",
                "bold" if run.get("part") in {"label", "colon"} else "regular",
            )
            font_size_pt = max(1.0, float(run.get("font_size") or 1) * float(scale))
            color_top_rgb = tuple(int(max(0, min(255, v))) for v in (run.get("color") or (0, 0, 0)))
            color_bot_rgb = tuple(int(max(0, min(255, v))) for v in (run.get("gradient_color_bottom") or (51, 51, 51)))

            try:
                x_pt = float(card_x) + (float(run.get("x", 0)) * scale)
                y_pt = float(card_bottom_y) + (float(card_h_pt) - (_run_baseline_px(run) * scale))

                r0, g0, b0 = color_top_rgb[0]/255.0, color_top_rgb[1]/255.0, color_top_rgb[2]/255.0
                r1, g1, b1 = color_bot_rgb[0]/255.0, color_bot_rgb[1]/255.0, color_bot_rgb[2]/255.0

                # Top-to-Bottom coordinates
                grad_x0 = x_pt
                grad_y0 = y_pt + font_size_pt
                grad_x1 = x_pt
                grad_y1 = y_pt

                # 1. Draw the vector gradient using high-level text clipping
                c.saveState()
                t = c.beginText(x_pt, y_pt)
                t.setFont(font_name, font_size_pt)
                t.setTextRenderMode(7)  # 7 = Add text to path for clipping
                t.textOut(text)
                c.drawText(t)       # Text becomes clipping path

                if hasattr(c, 'linearGradient'):
                    c.linearGradient(
                        grad_x0, grad_y0, grad_x1, grad_y1,
                        (Color(r0, g0, b0), Color(r1, g1, b1))
                    )
                c.restoreState()

                continue
            except Exception as exc:
                logger.warning("High-level gradient setup failed: %s", exc)

            # Gradient fallback
            try:
                c.setFillColor(Color(
                    color_top_rgb[0]/255.0,
                    color_top_rgb[1]/255.0,
                    color_top_rgb[2]/255.0,
                ))
                c.setFont(font_name, font_size_pt)
                x_f = float(card_x) + (float(run.get("x", 0)) * scale)
                y_f = float(card_bottom_y) + (float(card_h_pt) - (_run_baseline_px(run) * scale))
                c.drawString(x_f, y_f, text)
            except Exception as fe:
                logger.warning("Flat-color gradient fallback failed: %s", fe)
            continue

        font_name = _safe_canvas_font_name(
            run.get("font_path"),
            run.get("language") or "english",
            "bold" if run.get("part") in {"label", "colon"} else "regular",
        )
        font_size_pt = max(1.0, float(run.get("font_size") or 1) * float(scale))
        color_rgb = tuple(int(max(0, min(255, value))) for value in (run.get("color") or (0, 0, 0)))

        try:
            c.setFillColor(Color(color_rgb[0] / 255.0, color_rgb[1] / 255.0, color_rgb[2] / 255.0))
            c.setFont(font_name, font_size_pt)
            x_pt = float(card_x) + (float(run.get("x", 0)) * scale)
            y_pt = float(card_bottom_y) + (float(card_h_pt) - (_run_baseline_px(run) * scale))
            c.drawString(x_pt, y_pt, text)
        except Exception as exc:
            logger.warning(
                "Editable text draw fallback for language=%s font=%s: %s",
                run.get("language"),
                run.get("font_path"),
                exc,
            )
            _draw_raster_text_run_on_canvas(
                c,
                run,
                card_x=card_x,
                card_bottom_y=card_bottom_y,
                card_h_pt=card_h_pt,
                scale=scale,
            )




def _pil_image_reader(image: Image.Image, *, preserve_alpha: bool = False) -> ImageReader:
    prepared = image
    if preserve_alpha:
        if prepared.mode not in {"RGBA", "LA"}:
            prepared = prepared.convert("RGBA")
    elif prepared.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", prepared.size, (255, 255, 255))
        alpha = prepared.getchannel("A") if "A" in prepared.getbands() else None
        background.paste(prepared.convert("RGBA"), mask=alpha)
        prepared = background
    elif prepared.mode != "RGB":
        prepared = prepared.convert("RGB")
    buffer = io.BytesIO()
    prepared.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)




def _student_qr_identifier(student) -> str:
    form_data = {
        "name": getattr(student, "name", "") or "",
        "father_name": getattr(student, "father_name", "") or "",
        "class_name": getattr(student, "class_name", "") or "",
        "dob": getattr(student, "dob", "") or "",
        "address": getattr(student, "address", "") or "",
        "phone": getattr(student, "phone", "") or "",
    }
    photo_ref = getattr(student, "photo_url", None) or getattr(student, "photo_filename", None) or ""
    return generate_data_hash(form_data, photo_ref)[:10]




def _draw_editable_media_overlays(
    c,
    *,
    template,
    student,
    side: str,
    card_x: float,
    card_bottom_y: float,
    card_h_pt: float,
    scale: float,
    load_student_photo_rgba_fn,
    process_photo_pil_fn=None,
) -> None:
    _font_settings, photo_settings, qr_settings, _orientation = get_template_settings(getattr(template, "id", None), side=side)

    if photo_settings.get("enable_photo", True):
        try:
            photo_w_px = max(1, int(float(photo_settings.get("photo_width", 100) or 100)))
            photo_h_px = max(1, int(float(photo_settings.get("photo_height", 100) or 100)))
            photo_img = None
            try:
                photo_img = load_student_photo_rgba_fn(
                    student,
                    photo_w_px,
                    photo_h_px,
                    timeout=8,
                    photo_settings=photo_settings,
                )
            except TypeError:
                try:
                    photo_img = load_student_photo_rgba_fn(student, photo_w_px, photo_h_px, timeout=8)
                except Exception:
                    photo_img = None
                if photo_img is not None and process_photo_pil_fn is not None:
                    try:
                        processed_photo = process_photo_pil_fn(
                            photo_img,
                            target_width=photo_w_px,
                            target_height=photo_h_px,
                        )
                        if processed_photo is not None:
                            photo_img = processed_photo
                    except Exception as photo_process_exc:
                        logger.warning(
                            "Editable photo processing fallback template_id=%s student_id=%s side=%s: %s",
                            getattr(template, "id", None),
                            getattr(student, "id", None),
                            side,
                            photo_process_exc,
                        )
            except Exception:
                photo_img = None
            if photo_img is None:
                if not template:
                    if os.path.exists(PLACEHOLDER_PATH):
                        photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                        photo_img = ImageOps.fit(photo_img, (photo_w_px, photo_h_px), Image.Resampling.LANCZOS)
                else:
                    logger.warning(f"Failed to load photo for student {getattr(student, 'id', 'unknown')}, using placeholder")
                    if os.path.exists(PLACEHOLDER_PATH):
                        photo_img = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                        photo_img = ImageOps.fit(photo_img, (photo_w_px, photo_h_px), Image.Resampling.LANCZOS)
            if photo_img is not None:
                radii = [
                    int(float(photo_settings.get("photo_border_top_left", 0) or 0)),
                    int(float(photo_settings.get("photo_border_top_right", 0) or 0)),
                    int(float(photo_settings.get("photo_border_bottom_right", 0) or 0)),
                    int(float(photo_settings.get("photo_border_bottom_left", 0) or 0)),
                ]
                photo_shape = photo_settings.get("photo_shape", "rectangle")
                if photo_img.mode != "RGBA":
                    photo_img = photo_img.convert("RGBA")
                photo_shape_inset = int(float(photo_settings.get("photo_shape_inset", 0) or 0))
                photo_img = round_photo(photo_img, radii, shape=photo_shape, shape_inset=photo_shape_inset)
                photo_x = float(card_x) + (float(photo_settings.get("photo_x", 0) or 0) * scale)
                photo_y = float(card_bottom_y) + (
                    float(card_h_pt)
                    - ((float(photo_settings.get("photo_y", 0) or 0) + float(photo_h_px)) * scale)
                )
                scaled_radii = [float(r) * scale for r in radii]
                c.saveState()
                _clip_photo_shape_reportlab(
                    c,
                    photo_x,
                    photo_y,
                    float(photo_w_px) * scale,
                    float(photo_h_px) * scale,
                    scaled_radii,
                    photo_shape,
                    photo_shape_inset * scale,
                    shape_geometry_scale=scale,
                )
                c.drawImage(
                    _pil_image_reader(photo_img, preserve_alpha=True),
                    photo_x,
                    photo_y,
                    width=float(photo_w_px) * scale,
                    height=float(photo_h_px) * scale,
                    mask="auto",
                )
                c.restoreState()

                if _corel_editable_photo_mode(photo_settings) == "frame_only":
                    c.saveState()
                    # Read user-configured frame color; default to dark-red #8c2440
                    _fr, _fg, _fb = _parse_hex_to_rgb_normalized(photo_settings.get("photo_frame_color"))
                    c.setStrokeColor(Color(_fr, _fg, _fb))
                    c.setLineWidth(max(0.8, 1.2 * scale))
                    _draw_photo_frame_reportlab(
                        c,
                        photo_x,
                        photo_y,
                        float(photo_w_px) * scale,
                        float(photo_h_px) * scale,
                        scaled_radii,
                        photo_shape,
                        photo_shape_inset * scale,
                        shape_geometry_scale=scale,
                    )
                    c.restoreState()
        except Exception as exc:
            logger.warning(
                "Editable photo overlay failed template_id=%s student_id=%s side=%s: %s",
                getattr(template, "id", None),
                getattr(student, "id", None),
                side,
                exc,
            )

    qr_id = _student_qr_identifier(student)
    if bool(qr_settings.get("enable_qr", False)):
        try:
            qr_type = qr_settings.get("qr_data_type", "student_id")
            if qr_type == "url":
                base = qr_settings.get("qr_base_url", "")
                if base and not base.endswith("/"):
                    base += "/"
                qr_payload = base + qr_id
            elif qr_type == "text":
                qr_payload = qr_settings.get("qr_custom_text", "Sample")
            elif qr_type == "json":
                qr_payload = json.dumps(
                    {
                        "student_id": qr_id,
                        "name": getattr(student, "name", "") or "",
                        "class": getattr(student, "class_name", "") or "",
                        "school_name": getattr(template, "school_name", "") or "",
                    }
                )
            else:
                qr_payload = qr_id

            qr_size_px = max(40, int(qr_settings.get("qr_size", 120) or 120))
            qr_x = float(card_x) + (float(qr_settings.get("qr_x", 50) or 50) * scale)
            qr_y = float(card_bottom_y) + (
                float(card_h_pt)
                - ((float(qr_settings.get("qr_y", 50) or 50) + float(qr_size_px)) * scale)
            )
            qr_rgb = tuple(qr_settings.get("qr_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
            qr_fill = Color(
                max(0, min(255, int(qr_rgb[0]))) / 255.0,
                max(0, min(255, int(qr_rgb[1]))) / 255.0,
                max(0, min(255, int(qr_rgb[2]))) / 255.0,
            )
            _draw_vector_qr(c, qr_payload, qr_x, qr_y, float(qr_size_px) * scale, float(qr_size_px) * scale, qr_fill)
        except Exception as exc:
            logger.warning(
                "Editable QR overlay failed template_id=%s student_id=%s side=%s: %s",
                getattr(template, "id", None),
                getattr(student, "id", None),
                side,
                exc,
            )

    if bool(qr_settings.get("enable_barcode", False)):
        try:
            barcode_type = qr_settings.get("barcode_data_type", "student_id")
            if barcode_type == "url":
                base = qr_settings.get("barcode_base_url", "")
                if base and not base.endswith("/"):
                    base += "/"
                barcode_payload = base + qr_id
            elif barcode_type == "text":
                barcode_payload = qr_settings.get("barcode_custom_text", "Sample")
            elif barcode_type == "json":
                barcode_payload = json.dumps(
                    {
                        "student_id": qr_id,
                        "name": getattr(student, "name", "") or "",
                        "class": getattr(student, "class_name", "") or "",
                        "school_name": getattr(template, "school_name", "") or "",
                    }
                )
            else:
                barcode_payload = qr_id

            barcode_w_px = max(40, int(qr_settings.get("barcode_width", 220) or 220))
            barcode_h_px = max(30, int(qr_settings.get("barcode_height", 70) or 70))
            barcode_x = float(card_x) + (float(qr_settings.get("barcode_x", 50) or 50) * scale)
            barcode_y = float(card_bottom_y) + (
                float(card_h_pt)
                - ((float(qr_settings.get("barcode_y", 200) or 200) + float(barcode_h_px)) * scale)
            )
            barcode_rgb = tuple(qr_settings.get("barcode_fill_color", [0, 0, 0])) if isinstance(qr_settings, dict) else (0, 0, 0)
            barcode_fill = Color(
                max(0, min(255, int(barcode_rgb[0]))) / 255.0,
                max(0, min(255, int(barcode_rgb[1]))) / 255.0,
                max(0, min(255, int(barcode_rgb[2]))) / 255.0,
            )
            _draw_vector_barcode(
                c,
                barcode_payload,
                barcode_x,
                barcode_y,
                float(barcode_w_px) * scale,
                float(barcode_h_px) * scale,
                barcode_fill,
            )
        except Exception as exc:
            logger.warning(
                "Editable barcode overlay failed template_id=%s student_id=%s side=%s: %s",
                getattr(template, "id", None),
                getattr(student, "id", None),
                side,
                exc,
            )




def _build_compiled_sheet_via_app_renderer(
    *,
    template,
    students: list,
    side: str,
    mode: str,
    sheet_w_pt: float,
    sheet_h_pt: float,
    card_w_pt: float,
    card_h_pt: float,
    start_x_pt: float,
    start_y_pt: float,
    gap_pt: float,
    cols: int,
    rows: int,
    scale: float,
) -> bytes:
    helpers = _get_app_card_render_helpers()
    render_full = helpers["render_student_card_side"]
    render_background = helpers["render_student_card_side_background"]
    build_runs = helpers["build_student_card_text_runs"]
    load_student_photo_rgba_fn = helpers["load_student_photo_rgba"]
    process_photo_pil_fn = helpers.get("process_photo_pil")
    template_path = get_template_path(getattr(template, "id", None), side=side)
    background_render_scale = 2.0 if (mode == "editable" and _is_probably_pdf_source(template_path or "")) else 1.0
    shared_editable_background = None
    if mode == "editable":
        try:
            shared_editable_background = render_background(
                template,
                students[0] if students else None,
                side=side,
                student_id=None,
                school_name=getattr(template, "school_name", None),
                render_scale=background_render_scale,
                include_photo=False,
                include_qr=False,
                include_barcode=False,
            )
        except Exception as exc:
            logger.warning(
                "Editable background pre-render failed template_id=%s side=%s: %s",
                getattr(template, "id", None),
                side,
                exc,
            )
            shared_editable_background = None

    buffer = io.BytesIO()
    c = reportlab_canvas.Canvas(
        buffer,
        pagesize=(sheet_w_pt, sheet_h_pt),
        pageCompression=0,
        pdfVersion=(1, 4),
    )

    cards_per_sheet = max(1, int(cols) * int(rows))
    for idx, student in enumerate(students):
        idx_on_sheet = idx % cards_per_sheet
        col_idx = idx_on_sheet % cols
        row_idx = idx_on_sheet // cols

        card_x = float(start_x_pt) + (float(col_idx) * (float(card_w_pt) + float(gap_pt)))
        card_top_y = float(start_y_pt) - (float(row_idx) * (float(card_h_pt) + float(gap_pt)))
        card_bottom_y = float(card_top_y) - float(card_h_pt)

        student_id = getattr(student, "id", None)
        school_name = getattr(template, "school_name", None)
        if mode == "print":
            rendered = render_full(template, student, side=side, student_id=student_id, school_name=school_name)
        else:
            rendered = shared_editable_background

        if rendered is None:
            continue

        c.drawImage(
            _pil_image_reader(rendered),
            card_x,
            card_bottom_y,
            width=float(card_w_pt),
            height=float(card_h_pt),
            mask="auto",
        )

        if mode == "editable":
            _draw_editable_media_overlays(
                c,
                template=template,
                student=student,
                side=side,
                card_x=card_x,
                card_bottom_y=card_bottom_y,
                card_h_pt=float(card_h_pt),
                scale=float(scale),
                load_student_photo_rgba_fn=load_student_photo_rgba_fn,
                process_photo_pil_fn=process_photo_pil_fn,
            )
            runs_info = build_runs(template, student, side=side)
            _draw_text_runs_on_canvas(
                c,
                runs_info.get("runs", []),
                card_x=card_x,
                card_bottom_y=card_bottom_y,
                card_h_pt=float(card_h_pt),
                scale=float(scale),
                mode=mode,
            )

        if ((idx + 1) % cards_per_sheet) == 0 and (idx + 1) < len(students):
            c.showPage()

    c.save()
    final_bytes = buffer.getvalue()
    logger.info(
        "Corel compiled sheet via app renderer template_id=%s side=%s mode=%s students=%s",
        getattr(template, "id", None),
        side,
        mode,
        len(students),
    )
    return _make_corel_friendly(final_bytes, mode=mode)




def _compose_vector_template_export_pypdf(
    template_pdf_bytes: bytes,
    overlay_pdf_bytes: bytes,
    placements: list[dict],
    sheet_w_pt: float,
    sheet_h_pt: float,
    *,
    mode: str = "editable",
) -> bytes:
    if PdfReader is None or PdfWriter is None:
        return _compose_vector_template_export(
            template_pdf_bytes,
            overlay_pdf_bytes,
            placements,
            sheet_w_pt,
            sheet_h_pt,
            mode=mode,
        )

    template_pdf_bytes = _flatten_optional_content_pdf_bytes(template_pdf_bytes)
    template_reader = PdfReader(io.BytesIO(template_pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_pdf_bytes))
    writer = PdfWriter()

    if not template_reader.pages:
        raise RuntimeError("Template PDF has no pages")

    template_page = template_reader.pages[0]
    template_w = float(template_page.mediabox.width or 1)
    template_h = float(template_page.mediabox.height or 1)
    placements_by_page: dict[int, list[dict]] = {}
    for item in placements:
        placements_by_page.setdefault(int(item["page_index"]), []).append(item)

    for page_index, overlay_page in enumerate(overlay_reader.pages):
        out_page = writer.add_blank_page(width=float(sheet_w_pt), height=float(sheet_h_pt))
        for item in placements_by_page.get(page_index, []):
            target_w = float(item["x1"]) - float(item["x0"])
            target_h = float(item["y1"]) - float(item["y0"])
            target_x = float(item["x0"])
            target_y = float(sheet_h_pt) - float(item["y1"])
            transform = (
                Transformation()
                .scale(target_w / max(template_w, 1.0), target_h / max(template_h, 1.0))
                .translate(target_x, target_y)
            )
            out_page.merge_transformed_page(template_page, transform, over=False, expand=False)
        out_page.merge_page(overlay_page, over=True, expand=False)

    _rebuild_optional_content_catalog(writer)
    out = io.BytesIO()
    writer.write(out)
    return _make_corel_friendly(out.getvalue(), mode=mode) if mode == "editable" else out.getvalue()




def _compose_card_pages_to_sheet_pypdf(
    card_pages_pdf_bytes: bytes,
    placements: list[dict],
    sheet_w_pt: float,
    sheet_h_pt: float,
    *,
    mode: str = "editable",
) -> bytes:
    if not placements:
        return card_pages_pdf_bytes

    if PdfReader is None or PdfWriter is None or DecodedStreamObject is None:
        card_doc = fitz.open(stream=card_pages_pdf_bytes, filetype="pdf")
        out_doc = fitz.open()
        try:
            total_sheets = max(int(item.get("page_index", 0)) for item in placements) + 1
            sheet_pages = [
                out_doc.new_page(width=float(sheet_w_pt), height=float(sheet_h_pt))
                for _ in range(max(1, total_sheets))
            ]
            for card_index, item in enumerate(placements):
                if card_index >= len(card_doc):
                    break
                sheet_index = int(item.get("page_index", 0))
                target_rect = fitz.Rect(
                    float(item["x0"]),
                    float(item["y0"]),
                    float(item["x1"]),
                    float(item["y1"]),
                )
                sheet_pages[sheet_index].show_pdf_page(
                    target_rect,
                    card_doc,
                    card_index,
                    keep_proportion=False,
                    overlay=True,
                )
            merged = _corel_safe_pdf_bytes(out_doc, garbage=4, clean=False)
            return _make_corel_friendly(merged, mode=mode) if mode == "editable" else merged
        finally:
            try:
                card_doc.close()
            except Exception:
                pass
            try:
                out_doc.close()
            except Exception:
                pass

    reader = PdfReader(io.BytesIO(card_pages_pdf_bytes))
    writer = PdfWriter()
    total_sheets = max(int(item.get("page_index", 0)) for item in placements) + 1
    out_pages = []
    out_resources = []
    out_contents = []
    for _ in range(max(1, total_sheets)):
        page = writer.add_blank_page(width=float(sheet_w_pt), height=float(sheet_h_pt))
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources
        out_pages.append(page)
        out_resources.append(resources)
        out_contents.append(ArrayObject())

    def _merge_procset(dst_array, src_array):
        existing = {str(item) for item in dst_array}
        for item in src_array:
            if str(item) not in existing:
                dst_array.append(item)
                existing.add(str(item))

    def _rename_resource_tokens(content_bytes: bytes, rename_map: dict[bytes, bytes]) -> bytes:
        if not rename_map or not content_bytes:
            return content_bytes
        updated = content_bytes
        for old_name in sorted(rename_map.keys(), key=len, reverse=True):
            updated = re.sub(
                re.escape(old_name) + rb"(?=[\s<>\[\]\(\)/%]|$)",
                rename_map[old_name],
                updated,
            )
        return updated

    def _page_content_bytes(page_obj) -> bytes:
        contents_ref = page_obj.get("/Contents")
        if contents_ref is None and hasattr(page_obj, "get_inherited"):
            contents_ref = page_obj.get_inherited("/Contents", None)
        if contents_ref is None:
            return b""
        contents_obj = contents_ref.get_object() if hasattr(contents_ref, "get_object") else contents_ref
        content_parts: list[bytes] = []
        if isinstance(contents_obj, ArrayObject):
            for item in contents_obj:
                stream_obj = item.get_object() if hasattr(item, "get_object") else item
                if hasattr(stream_obj, "get_data"):
                    chunk = stream_obj.get_data() or b""
                    if chunk:
                        content_parts.append(chunk)
        elif hasattr(contents_obj, "get_data"):
            chunk = contents_obj.get_data() or b""
            if chunk:
                content_parts.append(chunk)
        return b"\n".join(content_parts)

    for card_index, item in enumerate(placements):
        if card_index >= len(reader.pages):
            break
        src_page = reader.pages[card_index]
        src_w = float(src_page.mediabox.width or 1)
        src_h = float(src_page.mediabox.height or 1)
        target_w = float(item["x1"]) - float(item["x0"])
        target_h = float(item["y1"]) - float(item["y0"])
        target_x = float(item["x0"])
        target_y = float(sheet_h_pt) - float(item["y1"])
        sheet_index = int(item.get("page_index", 0))
        page_resources = out_resources[sheet_index]
        rename_map: dict[bytes, bytes] = {}
        src_resources_ref = None
        if hasattr(src_page, "get_inherited"):
            src_resources_ref = src_page.get_inherited("/Resources", None)
        if src_resources_ref is None:
            src_resources_ref = src_page.get("/Resources")
        src_resources = (
            src_resources_ref.get_object()
            if hasattr(src_resources_ref, "get_object")
            else (src_resources_ref or DictionaryObject())
        )

        for res_key, res_val in src_resources.items():
            key_name = NameObject(str(res_key))
            if key_name == NameObject("/ProcSet"):
                dst_procset = page_resources.get(key_name)
                if dst_procset is None:
                    dst_procset = ArrayObject()
                    page_resources[key_name] = dst_procset
                _merge_procset(dst_procset, res_val.get_object() if hasattr(res_val, "get_object") else res_val)
                continue

            src_dict = res_val.get_object() if hasattr(res_val, "get_object") else res_val
            if not isinstance(src_dict, DictionaryObject):
                if key_name not in page_resources:
                    page_resources[key_name] = src_dict.clone(writer) if hasattr(src_dict, "clone") else src_dict
                continue

            dst_dict = page_resources.get(key_name)
            if dst_dict is None:
                dst_dict = DictionaryObject()
                page_resources[key_name] = dst_dict

            for src_name, src_obj in src_dict.items():
                src_name_str = str(src_name)
                safe_suffix = re.sub(r"[^A-Za-z0-9_]", "_", src_name_str.lstrip("/")) or "R"
                new_name = NameObject(f"/S{sheet_index}C{card_index}_{safe_suffix}")
                dst_dict[new_name] = src_obj.clone(writer) if hasattr(src_obj, "clone") else src_obj
                rename_map[src_name_str.encode("latin1")] = str(new_name).encode("latin1")

        content_bytes = _page_content_bytes(src_page)
        content_bytes = _rename_resource_tokens(content_bytes, rename_map)
        wrapped_stream = DecodedStreamObject()
        wrapped_stream.set_data(
            (
                f"q\n{target_w / max(src_w, 1.0):.8f} 0 0 {target_h / max(src_h, 1.0):.8f} "
                f"{target_x:.8f} {target_y:.8f} cm\n"
            ).encode("ascii")
            + content_bytes
            + b"\nQ\n"
        )
        out_contents[sheet_index].append(writer._add_object(wrapped_stream))

    for page, contents in zip(out_pages, out_contents):
        page[NameObject("/Contents")] = contents

    _rebuild_optional_content_catalog(writer)
    out = io.BytesIO()
    writer.write(out)
    return _make_corel_friendly(out.getvalue(), mode=mode) if mode == "editable" else out.getvalue()




def _interleave_pdf_bytes(front_pdf_bytes: bytes, back_pdf_bytes: bytes, *, mode: str = "editable") -> bytes:
    if PdfReader is None or PdfWriter is None:
        front_doc = fitz.open(stream=front_pdf_bytes, filetype="pdf")
        back_doc = fitz.open(stream=back_pdf_bytes, filetype="pdf")
        merged_doc = fitz.open()
        try:
            max_pages = max(len(front_doc), len(back_doc))
            for page_index in range(max_pages):
                if page_index < len(front_doc):
                    merged_doc.insert_pdf(front_doc, from_page=page_index, to_page=page_index)
                if page_index < len(back_doc):
                    merged_doc.insert_pdf(back_doc, from_page=page_index, to_page=page_index)
            merged = _corel_safe_pdf_bytes(merged_doc, garbage=4, clean=False)
            return _make_corel_friendly(merged, mode=mode) if mode == "editable" else merged
        finally:
            try:
                back_doc.close()
            except Exception:
                pass
            try:
                front_doc.close()
            except Exception:
                pass
            try:
                merged_doc.close()
            except Exception:
                pass

    front_reader = PdfReader(io.BytesIO(front_pdf_bytes))
    back_reader = PdfReader(io.BytesIO(back_pdf_bytes))
    writer = PdfWriter()
    max_pages = max(len(front_reader.pages), len(back_reader.pages))
    for page_index in range(max_pages):
        if page_index < len(front_reader.pages):
            writer.add_page(front_reader.pages[page_index])
        if page_index < len(back_reader.pages):
            writer.add_page(back_reader.pages[page_index])
    _rebuild_optional_content_catalog(writer)
    out = io.BytesIO()
    writer.write(out)
    return _make_corel_friendly(out.getvalue(), mode=mode) if mode == "editable" else out.getvalue()


LANGUAGE_TO_TRANSLATE_CODE = {
    "english": "en",
    "urdu": "ur",
    "hindi": "hi",
    "arabic": "ar",
}
NON_TRANSLATABLE_FIELD_KEYS = {"DOB", "MOBILE"}
NON_TRANSLATABLE_FIELD_TYPES = {"date", "number", "tel", "email"}




def _detect_translation_source_language(raw_text: str, fallback: str = "english") -> str:
    text = str(raw_text or "").strip()
    if not text:
        return _normalize_language(fallback)
    if re.search(r"[\u0900-\u097F]", text):
        return "hindi"
    if re.search(r"[\u0600-\u06FF]", text):
        hinted = _normalize_language(fallback)
        return hinted if hinted in {"urdu", "arabic"} else "urdu"
    if re.search(r"[A-Za-z]", text):
        return "english"
    return _normalize_language(fallback)




def _should_skip_translation(raw_value, field_key=None, field_type=None):
    text = str(raw_value or "").strip()
    if not text:
        return True
    normalized_key = str(field_key or "").strip().upper()
    normalized_type = str(field_type or "").strip().lower()
    if normalized_key in NON_TRANSLATABLE_FIELD_KEYS or normalized_type in NON_TRANSLATABLE_FIELD_TYPES:
        return True
    if "@" in text or "://" in text:
        return True
    letters = re.findall(r"[A-Za-z\u0600-\u06FF\u0900-\u097F]", text)
    if not letters:
        return True
    compact = re.sub(r"\s+", "", text)
    return bool(compact and re.fullmatch(r"[\d\W_]+", compact))




def _extract_google_translate_text(payload):
    if not isinstance(payload, list) or not payload:
        return ""
    segments = payload[0]
    if not isinstance(segments, list):
        return ""
    return "".join(
        str(segment[0])
        for segment in segments
        if isinstance(segment, list) and segment and segment[0] is not None
    ).strip()


@lru_cache(maxsize=4096)


def _google_translate_text(raw_text: str, source_language: str, target_language: str) -> str:
    text = str(raw_text or "").strip()
    source = _normalize_language(source_language)
    target = _normalize_language(target_language)
    if not text or source == target:
        return text
    source_code = LANGUAGE_TO_TRANSLATE_CODE.get(source)
    target_code = LANGUAGE_TO_TRANSLATE_CODE.get(target)
    if not source_code or not target_code:
        return text
    try:
        if GOOGLE_TRANSLATE_API_KEY:
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": GOOGLE_TRANSLATE_API_KEY},
                json={"q": text, "source": source_code, "target": target_code, "format": "text"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            translated = payload.get("data", {}).get("translations", [{}])[0].get("translatedText", "")
            return str(translated or "").strip() or text

        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source_code, "tl": target_code, "dt": "t", "q": text},
            timeout=8,
        )
        response.raise_for_status()
        translated = _extract_google_translate_text(response.json())
        return translated or text
    except Exception as exc:
        logger.warning("Vector export translation failed for %s -> %s: %s", source, target, exc)
        return text




def _translate_value_for_export(raw_value, *, source_language: str, target_language: str, field_key=None, field_type=None):
    text = str(raw_value or "")
    actual_source = _detect_translation_source_language(text, fallback=source_language)
    actual_target = _normalize_language(target_language)
    if actual_source == actual_target:
        return text
    if _should_skip_translation(text, field_key=field_key, field_type=field_type):
        return text
    return _google_translate_text(text, actual_source, actual_target)




def _normalize_language(language: str) -> str:
    return (language or "english").strip().lower()


_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_ORDER_TO_KEY = {
    10: "NAME",
    20: "F_NAME",
    30: "CLASS",
    40: "DOB",
    50: "MOBILE",
    60: "ADDRESS",
}




def _field_key_from_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        return None
    return item.get("k") or item.get("key") or _ORDER_TO_KEY.get(item.get("ord"))




def _get_template_field_side_flags(template_obj, field_key: str | None, side: str = "front") -> dict | None:
    if not template_obj or not field_key:
        return None

    cache = getattr(template_obj, "_corel_field_side_visibility_cache", None)
    if cache is None:
        cache = {}
        try:
            db_fields = TemplateField.query.filter_by(template_id=template_obj.id).order_by(TemplateField.display_order.asc()).all()
        except Exception:
            db_fields = []
        for field in db_fields:
            cache[field.field_name] = {
                "front": {
                    "label": bool(getattr(field, "show_label_front", True)),
                    "value": bool(getattr(field, "show_value_front", True)),
                },
                "back": {
                    "label": bool(getattr(field, "show_label_back", False)),
                    "value": bool(getattr(field, "show_value_back", False)),
                },
            }
        setattr(template_obj, "_corel_field_side_visibility_cache", cache)

    field_flags = cache.get(field_key)
    if not field_flags:
        return None

    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    chosen = field_flags.get(side_name) or {}
    label_visible = bool(chosen.get("label", True))
    return {
        "label_visible": label_visible,
        "value_visible": bool(chosen.get("value", True)),
        "colon_visible": label_visible,
    }




def _resolve_pdf_field_layout(template_obj, field_key, default_label_x, default_value_x, default_y, *, side="front", text_direction="ltr"):
    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    layout_config = getattr(template_obj, "back_layout_config", None) if side_name == "back" else getattr(template_obj, "layout_config", None)
    field_side_flags = _get_template_field_side_flags(template_obj, field_key, side=side_name)
    default_visibility = field_side_flags or {}
    return get_field_layout_item(
        layout_config,
        field_key,
        default_label_x,
        default_value_x,
        default_y,
        text_direction=text_direction,
        default_label_visible=default_visibility.get("label_visible", True),
        default_value_visible=default_visibility.get("value_visible", True),
        default_colon_visible=default_visibility.get("colon_visible", default_visibility.get("label_visible", True)),
        prefer_nested_part_layout=field_side_flags is not None,
    )




def _initial_flow_y_px(template_obj, font_settings, *, side="front"):
    side_name = "back" if str(side or "front").strip().lower() == "back" else "front"
    try:
        default_start_y = int((font_settings or {}).get("start_y", 0) or 0)
    except Exception:
        default_start_y = 0

    if not template_obj:
        return default_start_y

    layout_config = getattr(template_obj, "back_layout_config", None) if side_name == "back" else getattr(template_obj, "layout_config", None)
    visibility_map = {}
    try:
        db_fields = TemplateField.query.filter_by(template_id=template_obj.id).order_by(TemplateField.display_order.asc()).all()
    except Exception:
        db_fields = []

    for field in db_fields:
        visibility_map[field.field_name] = {
            "label": bool(getattr(field, "show_label_back" if side_name == "back" else "show_label_front", side_name != "back")),
            "value": bool(getattr(field, "show_value_back" if side_name == "back" else "show_value_front", side_name != "back")),
        }

    return get_layout_flow_start_y(layout_config, default_start_y, visibility_map)



def _field_wrap_policy(field_key: str | None, address_max_lines: int | None = None) -> dict:
    key = str(field_key or "").strip().upper()
    defaults = {
        "max_lines": 3,
        "min_scale": 0.78,
        "line_height_factor": 1.15,
    }
    per_field = {
        "NAME": {"max_lines": 2, "min_scale": 0.84, "line_height_factor": 1.12},
        "F_NAME": {"max_lines": 2, "min_scale": 0.8, "line_height_factor": 1.12},
        "CLASS": {"max_lines": 1, "min_scale": 0.9, "line_height_factor": 1.08},
        "DOB": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "MOBILE": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "PHONE": {"max_lines": 1, "min_scale": 0.88, "line_height_factor": 1.08},
        "ADDRESS": {"max_lines": 2, "min_scale": 0.72, "line_height_factor": 1.15},
    }
    policy = dict(defaults)
    policy.update(per_field.get(key, {}))
    if key == "ADDRESS" and address_max_lines is not None:
        try:
            policy["max_lines"] = max(1, int(address_max_lines))
        except Exception:
            pass
    return policy




def _field_consumes_layout_space(layout_item: dict | None, raw_value: str = "") -> bool:
    if not isinstance(layout_item, dict):
        return bool(str(raw_value or "").strip())
    if layout_item.get("label_visible"):
        return True
    return bool(layout_item.get("value_visible")) and bool(str(raw_value or "").strip())




def _field_advances_layout_flow(layout_item: dict | None, raw_value: str = "", *, separate_colon: bool = False) -> bool:
    if not _field_consumes_layout_space(layout_item, raw_value):
        return False
    if not isinstance(layout_item, dict):
        return True

    has_value = bool(str(raw_value or "").strip())
    if layout_item.get("label_visible") and layout_item.get("label_manual_y"):
        return False
    if has_value and layout_item.get("value_visible") and layout_item.get("value_manual_y"):
        return False
    if separate_colon and layout_item.get("colon_visible") and layout_item.get("colon_manual_y"):
        return False
    return True




def _draw_custom_editor_objects_pdf(c, layout_config_raw, card_x, card_bottom_y, card_h_pt, scale, reg_font_name):
    parsed = parse_layout_config(layout_config_raw)
    objects = parsed.get("objects") if isinstance(parsed, dict) else None
    if not isinstance(objects, list):
        return
    for obj in objects:
        if not isinstance(obj, dict) or not obj.get("visible", True):
            continue
        kind = str(obj.get("type") or "").strip().lower()
        x = card_x + (float(obj.get("x", 0)) * scale)
        y = card_bottom_y + (card_h_pt - (float(obj.get("y", 0)) * scale))
        angle = float(obj.get("angle", 0) or 0)
        opacity = max(0.0, min(1.0, float(obj.get("opacity", 100) or 100) / 100.0))
        fill_hex = str(obj.get("fill") or "#1f4e8c")
        stroke_hex = str(obj.get("stroke") or fill_hex)
        def _hex_to_color(h):
            try:
                return Color(int(h[1:3],16)/255.0, int(h[3:5],16)/255.0, int(h[5:7],16)/255.0)
            except Exception:
                return Color(0.12,0.31,0.55)
        fill = _hex_to_color(fill_hex)
        stroke = _hex_to_color(stroke_hex)
        stroke_width = max(0.5, float(obj.get("stroke_width", 2)) * scale)
        if kind == "text":
            text = str(obj.get("text") if obj.get("text") is not None else "Text")
            if not text:
                continue
            c.saveState()
            c.translate(x, y)
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            c.setFillColor(fill)
            c.setFont(reg_font_name, max(6.0, float(obj.get("font_size", 24)) * scale))
            c.drawString(0, 0, text)
            c.restoreState()
        elif kind == "rect":
            w = max(1.0, float(obj.get("width", 120)) * scale)
            h = max(1.0, float(obj.get("height", 60)) * scale)
            c.saveState()
            c.translate(x + (w / 2.0), y - (h / 2.0))
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.rect(-(w / 2.0), -(h / 2.0), w, h, fill=1, stroke=1)
            c.restoreState()
        elif kind == "circle":
            w = max(1.0, float(obj.get("width", 80)) * scale)
            h = max(1.0, float(obj.get("height", obj.get("width", 80))) * scale)
            c.saveState()
            c.translate(x + (w / 2.0), y - (h / 2.0))
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.ellipse(-(w / 2.0), -(h / 2.0), (w / 2.0), (h / 2.0), fill=1, stroke=1)
            c.restoreState()
        elif kind == "triangle":
            w = max(1.0, float(obj.get("width", 80)) * scale)
            h = max(1.0, float(obj.get("height", obj.get("width", 80))) * scale)
            c.saveState()
            c.translate(x + (w / 2.0), y - (h / 2.0))
            if angle:
                c.rotate(-angle)
            if hasattr(c, "setFillAlpha"):
                c.setFillAlpha(opacity)
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            path = c.beginPath()
            path.moveTo(0, h / 2.0)
            path.lineTo(w / 2.0, -(h / 2.0))
            path.lineTo(-(w / 2.0), -(h / 2.0))
            path.close()
            c.drawPath(path, fill=1, stroke=1)
            c.restoreState()
        elif kind == "line":
            x2 = card_x + (float(obj.get("x2", obj.get("x", 0) + 120)) * scale)
            y2 = card_bottom_y + (card_h_pt - (float(obj.get("y2", obj.get("y", 0))) * scale))
            c.saveState()
            if hasattr(c, "setStrokeAlpha"):
                c.setStrokeAlpha(opacity)
            c.setStrokeColor(stroke)
            c.setLineWidth(stroke_width)
            c.line(x, y, x2, y2)
            c.restoreState()
        elif kind == "image":
            src = str(obj.get("src") or "").strip()
            if not src:
                continue
            try:
                if src.startswith("data:image"):
                    _, encoded = src.split(",", 1)
                    image_bytes = base64.b64decode(encoded)
                    image_reader = ImageReader(io.BytesIO(image_bytes))
                elif src.startswith(("http://", "https://")):
                    resp = requests.get(src, timeout=10)
                    resp.raise_for_status()
                    image_reader = ImageReader(io.BytesIO(resp.content))
                else:
                    image_path = src if os.path.isabs(src) else os.path.join(STATIC_DIR, src.lstrip("/"))
                    image_reader = ImageReader(image_path)
                w = max(1.0, float(obj.get("width", 120)) * scale)
                h = max(1.0, float(obj.get("height", 120)) * scale)
                if opacity < 0.999:
                    if src.startswith("data:image"):
                        overlay = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
                    elif src.startswith(("http://", "https://")):
                        overlay = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    else:
                        overlay = Image.open(image_path).convert("RGBA")
                    alpha_channel = overlay.getchannel("A").point(lambda px: int(px * opacity))
                    overlay.putalpha(alpha_channel)
                    image_reader = ImageReader(overlay)
                c.saveState()
                c.translate(x + (w / 2.0), y - (h / 2.0))
                if angle:
                    c.rotate(-angle)
                c.drawImage(image_reader, -(w / 2.0), -(h / 2.0), width=w, height=h, mask="auto")
                c.restoreState()
            except Exception as image_err:
                logger.warning("Skipping custom image object in PDF render due to error: %s", image_err)




def _contains_arabic_script(text: str) -> bool:
    if not text:
        return False
    for ch in str(text):
        cp = ord(ch)
        for start, end in _ARABIC_RANGES:
            if start <= cp <= end:
                return True
    return False




def _safe_bidi_get_display(text: str, base_dir: str = "R") -> str:
    """
    Compatibility wrapper for python-bidi versions that may not support `base_dir`.
    """
    try:
        return get_display(text, base_dir=base_dir)
    except TypeError:
        return get_display(text)




def _clean_bidi_controls(text: str) -> str:
    if text is None:
        return ""
    cleaned = []
    for ch in str(text):
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cp in {0xFFFD, 0xFEFF}:
            continue
        if cat in {"Cc", "Cs"}:
            continue
        if cat == "Cf" and ch not in {"\u200c", "\u200d"}:
            continue
        cleaned.append(ch)
    return "".join(cleaned)




def process_text_for_vector(text: str, language: str) -> str:
    """
    Prepare text for ReportLab drawing.

    Why this exists:
    - ReportLab does not do complex shaping (joining) or BiDi reordering by itself.
    - Arabic/Urdu need reshaping (glyph joining) + BiDi to display correctly.
    - Hindi (Devanagari) is LTR and does not need BiDi, so return unchanged.
    """
    text = _clean_bidi_controls(text)
    if not text:
        return ""
    language = _normalize_language(language)

    # If template language is English but the value contains Arabic-script, still process it.
    if language not in {"arabic", "urdu"} and _contains_arabic_script(text):
        language = "arabic"

    if language in {"arabic", "urdu"}:
        try:
            if _ARABIC_RESHAPER is not None:
                reshaped = _ARABIC_RESHAPER.reshape(text)
            else:
                reshaped = arabic_reshaper.reshape(text)
            # base_dir='R' ensures stable RTL display for ReportLab (which draws LTR only).
            return _clean_bidi_controls(_safe_bidi_get_display(reshaped, base_dir="R"))
        except Exception as exc:
            logger.warning("Vector text shaping failed for Arabic/Urdu: %s", exc)
            return text

    # Hindi / English / others
    return text




def _normalize_grow_mode(grow_mode, direction: str) -> str:
    direction = (direction or "ltr").strip().lower()
    if isinstance(grow_mode, str):
        mode = grow_mode.strip().lower()
        if mode in {"left", "center", "right"}:
            return mode
    return "right" if direction == "rtl" else "left"




def _x_for_direction(card_x, card_w_pt, x_px, text, font_name, font_size_pt, scale, direction: str, grow_mode=None) -> float:
    """
    Direction-aware X placement with anchor growth mode.
    """
    direction = (direction or "ltr").strip().lower()
    mode = _normalize_grow_mode(grow_mode, direction)
    try:
        text_w = pdfmetrics.stringWidth(text, font_name, font_size_pt)
    except Exception:
        text_w = 0

    anchor = card_x + ((card_w_pt - (x_px * scale)) if direction == "rtl" else (x_px * scale))
    if mode == "left":
        return anchor
    if mode == "center":
        return anchor - (text_w / 2.0)
    return anchor - text_w




def _x_for_direction_raster(card_x, card_w_pt, x_px, text_width_px: float, scale, direction: str, grow_mode=None) -> float:
    """
    Direction-aware X placement for rasterized runs (measured in px).
    """
    direction = (direction or "ltr").strip().lower()
    mode = _normalize_grow_mode(grow_mode, direction)
    text_w_pt = float(text_width_px or 0) * scale
    anchor = card_x + ((card_w_pt - (x_px * scale)) if direction == "rtl" else (x_px * scale))
    if mode == "left":
        return anchor
    if mode == "center":
        return anchor - (text_w_pt / 2.0)
    return anchor - text_w_pt


_PIL_FONT_CACHE: dict[tuple[str, int, str], ImageFont.ImageFont] = {}
_RASTER_TEXT_METRICS_CACHE: dict[tuple[tuple[str, str, int, str], str, str], tuple[tuple[int, int, int, int], int, int, float, float]] = {}
_VECTOR_TEXT_WIDTH_CACHE: dict[tuple[str, float, str], float] = {}




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



def draw_custom_rounded_rect(c, x, y, w, h, radii):
    tl, tr, br, bl = [float(r) for r in radii]
    path = c.beginPath()
    path.moveTo(x, y + h - tl)
    if tl > 0: path.arcTo(x, y + h - 2*tl, x + 2*tl, y + h, 180, -90)
    else: path.lineTo(x, y + h) 
    path.lineTo(x + w - tr, y + h)
    if tr > 0: path.arcTo(x + w - 2*tr, y + h - 2*tr, x + w, y + h, 90, -90)
    else: path.lineTo(x + w, y + h)
    path.lineTo(x + w, y + br)
    if br > 0: path.arcTo(x + w - 2*br, y, x + w, y + 2*br, 0, -90)
    else: path.lineTo(x + w, y)
    path.lineTo(x + bl, y)
    if bl > 0: path.arcTo(x, y, x + 2*bl, y + 2*bl, 270, -90)
    else: path.lineTo(x, y)
    path.close()
    return path


_PHOTO_SHAPE_POLYGON_SIDES = {
    "triangle": 3,
    "diamond": 4,
    "pentagon": 5,
    "hexagon": 6,
    "heptagon": 7,
    "octagon": 8,
}




def _photo_shape_points(x, y, w, h, shape_name, *, y_axis_down=False, inset=0, shape_geometry_scale=1.0):
    shape_name = normalize_photo_shape(shape_name)
    if shape_name.startswith("custom-polygon:"):
        try:
            import json
            normalized_points = json.loads(shape_name[len("custom-polygon:"):])
            x_val = float(x) + inset
            y_val = float(y) + inset
            w_val = max(1.0, float(w) - (inset * 2.0))
            h_val = max(1.0, float(h) - (inset * 2.0))
            points = []
            for px, py in normalized_points:
                pt_x = x_val + px * w_val
                if y_axis_down:
                    pt_y = y_val + py * h_val
                else:
                    pt_y = (y_val + h_val) - py * h_val
                points.append((pt_x, pt_y))
            return points
        except Exception:
            return []
    base_shape = shape_name.split(":")[0].lower()
    sides = _PHOTO_SHAPE_POLYGON_SIDES.get(base_shape)
    if not sides:
        return []
    inset = max(0.0, min(float(inset or 0), max(0.0, (min(float(w), float(h)) - 2.0) / 2.0)))
    x = float(x) + inset
    y = float(y) + inset
    w = max(1.0, float(w) - (inset * 2.0))
    h = max(1.0, float(h) - (inset * 2.0))
    cx = float(x) + (float(w) / 2.0)
    cy = float(y) + (float(h) / 2.0)
    rx = max(1.0, float(w) / 2.0)
    ry = max(1.0, float(h) / 2.0)
    rotation = -90.0 if y_axis_down else 90.0

    if int(sides) == 6 and rotation in (-90.0, 90.0, 270.0):
        cap_h = min(h / 2.0, w * 0.288675135)
        if ":" in shape_name:
            try:
                raw_cap = float(shape_name.split(":", 1)[1])
                cap_h = max(0.0, min(h / 2.0, raw_cap * float(shape_geometry_scale or 1.0)))
            except Exception:
                pass
        if y_axis_down:
            return [
                (cx, y),
                (cx + w/2.0, y + cap_h),
                (cx + w/2.0, y + h - cap_h),
                (cx, y + h),
                (cx - w/2.0, y + h - cap_h),
                (cx - w/2.0, y + cap_h),
            ]
        else:
            ymin = y
            ymax = y + h
            return [
                (cx, ymax),
                (cx + w/2.0, ymax - cap_h),
                (cx + w/2.0, ymin + cap_h),
                (cx, ymin),
                (cx - w/2.0, ymin + cap_h),
                (cx - w/2.0, ymax - cap_h),
            ]

    return [
        (
            cx + (rx * math.cos(math.radians(rotation + (360.0 * idx / sides)))),
            cy + (ry * math.sin(math.radians(rotation + (360.0 * idx / sides)))),
        )
        for idx in range(sides)
    ]




def _ellipse_path_reportlab(c, x, y, w, h, inset=0):
    inset = max(0.0, min(float(inset or 0), max(0.0, (min(float(w), float(h)) - 2.0) / 2.0)))
    x = float(x) + inset
    y = float(y) + inset
    w = max(1.0, float(w) - (inset * 2.0))
    h = max(1.0, float(h) - (inset * 2.0))
    k = 0.5522847498307936
    cx = float(x) + (float(w) / 2.0)
    cy = float(y) + (float(h) / 2.0)
    rx = float(w) / 2.0
    ry = float(h) / 2.0
    path = c.beginPath()
    path.moveTo(cx, cy + ry)
    path.curveTo(cx + (k * rx), cy + ry, cx + rx, cy + (k * ry), cx + rx, cy)
    path.curveTo(cx + rx, cy - (k * ry), cx + (k * rx), cy - ry, cx, cy - ry)
    path.curveTo(cx - (k * rx), cy - ry, cx - rx, cy - (k * ry), cx - rx, cy)
    path.curveTo(cx - rx, cy + (k * ry), cx - (k * rx), cy + ry, cx, cy + ry)
    path.close()
    return path




def _polygon_path_reportlab(c, points):
    path = c.beginPath()
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    for px, py in points[1:]:
        path.lineTo(px, py)
    path.close()
    return path




def _clip_photo_shape_reportlab(c, x, y, w, h, radii, shape_name, shape_inset=0, shape_geometry_scale=1.0):
    shape_name = normalize_photo_shape(shape_name)
    if shape_name == "circle":
        c.clipPath(_ellipse_path_reportlab(c, x, y, w, h, shape_inset), stroke=0)
        return True
    if shape_name not in {"rectangle", "rounded"}:
        points = _photo_shape_points(x, y, w, h, shape_name, inset=shape_inset, shape_geometry_scale=shape_geometry_scale)
        if points:
            c.clipPath(_polygon_path_reportlab(c, points), stroke=0)
            return True
        return False
    if all(r == radii[0] for r in radii) and radii[0] > 0:
        path = c.beginPath()
        path.roundRect(x, y, w, h, radii[0])
        c.clipPath(path, stroke=0)
        return True
    if any(r > 0 for r in radii):
        path = draw_custom_rounded_rect(c, x, y, w, h, radii)
        c.clipPath(path, stroke=0)
        return True
    return False




def _draw_photo_frame_reportlab(c, x, y, w, h, radii, shape_name, shape_inset=0, shape_geometry_scale=1.0):
    shape_name = normalize_photo_shape(shape_name)
    shape_inset = max(0.0, min(float(shape_inset or 0), max(0.0, (min(float(w), float(h)) - 2.0) / 2.0)))
    if shape_name == "circle":
        c.ellipse(x + shape_inset, y + shape_inset, x + w - shape_inset, y + h - shape_inset, stroke=1, fill=0)
        return
    if shape_name not in {"rectangle", "rounded"}:
        points = _photo_shape_points(x, y, w, h, shape_name, inset=shape_inset, shape_geometry_scale=shape_geometry_scale)
        if points:
            c.drawPath(_polygon_path_reportlab(c, points), stroke=1, fill=0)
        return
    if all(r == radii[0] for r in radii) and radii[0] > 0:
        c.roundRect(x, y, w, h, radii[0], stroke=1, fill=0)
    elif any(r > 0 for r in radii):
        path = draw_custom_rounded_rect(c, x, y, w, h, radii)
        c.drawPath(path, stroke=1, fill=0)
    else:
        c.rect(x, y, w, h, stroke=1, fill=0)


PRINT_DPI = 600
DEFAULT_EXPORT_MODE = "print"
SUPPORTED_EXPORT_MODES = {"editable", "print"}
SUPPORTED_COREL_PHOTO_MODES = {"embed", "frame_only"}




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




def _load_template_for_pdf(path_or_url: str, target_dpi: int, min_size: tuple[int, int] | None = None) -> Image.Image | None:
    """
    Load template at requested DPI for PDF export.
    For PDF templates we render via PyMuPDF at the target DPI.
    """
    if not path_or_url:
        return None
    try:
        src = str(path_or_url)
        is_url = src.startswith(("http://", "https://"))
        content_type = ""
        payload: bytes | None = None

        if is_url:
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.content

        if _is_probably_pdf_source(src, content_type=content_type, content=payload):
            if is_url:
                pdf_doc = fitz.open(stream=payload, filetype="pdf")
            else:
                pdf_doc = fitz.open(src)
            try:
                page = pdf_doc[0]
                pix = page.get_pixmap(dpi=max(72, int(target_dpi)), alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            finally:
                pdf_doc.close()
        else:
            if is_url and payload is not None:
                img = Image.open(io.BytesIO(payload))
            else:
                img = load_template_smart(src)
            if img.mode in ("RGBA", "LA"):
                rgb = Image.new("RGB", img.size, (255, 255, 255))
                rgb.paste(img, mask=img.split()[-1])
                img = rgb
            elif img.mode != "RGB":
                img = img.convert("RGB")

        if min_size:
            min_w = max(1, int(min_size[0]))
            min_h = max(1, int(min_size[1]))
            if img.size[0] < min_w or img.size[1] < min_h:
                img = img.resize((max(min_w, img.size[0]), max(min_h, img.size[1])), Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("Template preload failed for PDF export (%s): %s", path_or_url, exc)
        return None



def _read_template_pdf_bytes(path_or_url: str) -> bytes | None:
    """Return original PDF bytes for a template source, preserving vector content."""
    if not path_or_url:
        return None

    src = str(path_or_url).strip()
    is_url = src.startswith(("http://", "https://"))
    content_type = ""
    payload = b""

    try:
        if is_url:
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.content or b""
        else:
            with open(src, "rb") as fh:
                payload = fh.read()

        if not _is_probably_pdf_source(src, content_type=content_type, content=payload):
            return None

        pdf_header_pos = payload.find(b"%PDF")
        if pdf_header_pos < 0:
            return None
        pdf_bytes = payload[pdf_header_pos:]
        if len(pdf_bytes) < 128:
            return None
        return pdf_bytes
    except Exception as exc:
        logger.warning("Failed to read template PDF bytes (%s): %s", path_or_url, exc)
        return None



def _compose_vector_template_export(
    template_pdf_bytes: bytes,
    overlay_pdf_bytes: bytes,
    placements: list[dict],
    sheet_w_pt: float,
    sheet_h_pt: float,
    *,
    mode: str = "editable",
) -> bytes:
    """
    Compose a vector-preserving export by placing the original template PDF page repeatedly
    under the generated overlay PDF pages.
    """
    template_doc = fitz.open(stream=template_pdf_bytes, filetype="pdf")
    overlay_doc = fitz.open(stream=overlay_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        if len(template_doc) < 1:
            raise RuntimeError("Template PDF has no pages")

        placements_by_page: dict[int, list[dict]] = {}
        for item in placements:
            placements_by_page.setdefault(int(item["page_index"]), []).append(item)

        for page_index in range(len(overlay_doc)):
            out_page = out_doc.new_page(width=float(sheet_w_pt), height=float(sheet_h_pt))

            for item in placements_by_page.get(page_index, []):
                rect = fitz.Rect(
                    float(item["x0"]),
                    float(item["y0"]),
                    float(item["x1"]),
                    float(item["y1"]),
                )
                out_page.show_pdf_page(rect, template_doc, 0, keep_proportion=False, overlay=False)

            out_page.show_pdf_page(out_page.rect, overlay_doc, page_index, keep_proportion=False, overlay=True)

        merged = _corel_safe_pdf_bytes(out_doc, garbage=4, clean=False)
        return _make_corel_friendly(merged, mode=mode) if mode == "editable" else merged
    finally:
        try:
            template_doc.close()
        except Exception:
            pass
        try:
            overlay_doc.close()
        except Exception:
            pass
        try:
            out_doc.close()
        except Exception:
            pass



def _generate_direct_editable_pdf_template_export(
    *,
    template,
    template_id: int,
    students: list,
    template_pdf_bytes: bytes,
    font_settings: dict,
    photo_settings: dict,
    qr_settings: dict,
    layout_config_raw,
    labels_map: dict,
    sheet_w_pt: float,
    sheet_h_pt: float,
    card_w_pt: float,
    card_h_pt: float,
    start_x_pt: float,
    start_y_pt: float,
    gap_pt: float,
    cols: int,
    rows: int,
    card_w_px: int,
    card_h_px: int,
    lang: str,
    direction: str,
    reg_font_name: str,
    bold_font_name: str,
    reg_font_path: str | None,
    bold_font_path: str | None,
    side: str = "front",
    source_language: str = "english",
    include_template_background: bool = True,
    mode: str = "editable",
) -> bytes:
    template_doc = fitz.open(stream=template_pdf_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        if len(template_doc) < 1:
            raise RuntimeError("Template PDF has no pages")

        db_fields = TemplateField.query.filter_by(template_id=template_id).order_by(TemplateField.display_order.asc()).all()
        fitz_reg_font = f"fz_reg_{template_id}"
        fitz_bold_font = f"fz_bold_{template_id}"
        archive = fitz.Archive(FONTS_FOLDER)
        css_cache: dict[str, tuple[str, str]] = {}
        measure_reg_font_name = reg_font_name
        measure_bold_font_name = bold_font_name
        native_reg_font_name = "helv"
        native_bold_font_name = "hebo"
        safe_editable_builtin_text = mode == "editable" and _normalize_language(lang) not in {"urdu", "arabic", "hindi"}
        app_helpers = _get_app_card_render_helpers()
        load_student_photo_rgba_fn = app_helpers["load_student_photo_rgba"]

        def _register_side_font(font_path: str | None, fallback_name: str, role: str) -> tuple[str, str]:
            if not font_path or not os.path.exists(font_path):
                return fallback_name, fallback_name
            ext = os.path.splitext(font_path)[1].lower()
            native_name = f"pdf_{template_id}_{side}_{role}_{abs(hash(os.path.basename(font_path)))}"
            if ext in {".ttf", ".ttc", ".otf"}:
                try:
                    if native_name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(native_name, font_path))
                    return native_name, native_name
                except Exception:
                    logger.warning("Failed to register side font for PDF export: %s", font_path)
            return fallback_name, native_name

        measure_reg_font_name, native_reg_font_name = _register_side_font(reg_font_path, reg_font_name, "reg")
        measure_bold_font_name, native_bold_font_name = _register_side_font(bold_font_path, bold_font_name, "bold")
        if safe_editable_builtin_text:
            measure_reg_font_name = "Helvetica"
            measure_bold_font_name = "Helvetica-Bold"
            native_reg_font_name = "helv"
            native_bold_font_name = "hebo"

        def _fitz_rgb(rgb):
            r, g, b = rgb
            return (
                max(0, min(255, int(r))) / 255.0,
                max(0, min(255, int(g))) / 255.0,
                max(0, min(255, int(b))) / 255.0,
            )

        def _load_student_photo_stream(student, target_w_px: int, target_h_px: int):
            has_real_student_photo = bool(
                str(getattr(student, "photo_url", "") or "").strip()
                or str(getattr(student, "photo_filename", "") or "").strip()
            )
            try:
                prepared = load_student_photo_rgba_fn(
                    student,
                    target_w_px,
                    target_h_px,
                    timeout=10,
                    photo_settings=photo_settings,
                    allow_placeholder=True,
                )
            except TypeError:
                prepared = load_student_photo_rgba_fn(student, target_w_px, target_h_px, timeout=10)
            except Exception:
                prepared = None

            if prepared is None:
                logger.warning(f"Failed to load photo for student {getattr(student, 'id', 'unknown')}, using placeholder")
                if os.path.exists(PLACEHOLDER_PATH):
                    prepared = Image.open(PLACEHOLDER_PATH).convert("RGBA")
                    prepared = ImageOps.fit(prepared, (target_w_px, target_h_px), Image.Resampling.LANCZOS)
                    has_real_student_photo = False  # since using placeholder

            if prepared is None:
                return None, has_real_student_photo

            buf = io.BytesIO()
            prepared.save(buf, format="PNG")
            buf.seek(0)
            return buf, has_real_student_photo

        def _insert_complex_text_box(
            page: fitz.Page,
            rect: fitz.Rect,
            text: str,
            *,
            font_file: str | None,
            font_size_pt: float,
            color_rgb: tuple[int, int, int],
            direction: str,
            align: str,
            prefer_native_text: bool = False,
            enable_gradient: bool = False,
            gradient_color_bottom: tuple[int, int, int] = (51, 51, 51),
        ):
            if not text or rect.width < 1 or rect.height < 1:
                return
            font_basename = os.path.basename(font_file or "") if font_file else ""

            if prefer_native_text and not enable_gradient and font_file and os.path.exists(font_file):
                try:
                    native_font_name = f"fitz_native_{abs(hash(font_basename or font_file))}"
                    align_mode = str(align or "left").strip().lower()
                    if align_mode == "center":
                        fitz_align = fitz.TEXT_ALIGN_CENTER
                    elif align_mode == "right":
                        fitz_align = fitz.TEXT_ALIGN_RIGHT
                    else:
                        fitz_align = fitz.TEXT_ALIGN_LEFT
                    page.insert_textbox(
                        rect,
                        text,
                        fontname=native_font_name,
                        fontfile=font_file,
                        fontsize=float(font_size_pt),
                        color=_fitz_rgb(color_rgb),
                        align=fitz_align,
                        overlay=True,
                    )
                    return
                except Exception:
                    logger.warning("Native textbox insert failed for editable PDF text; falling back to raster text")

            try:
                language_hint = "urdu" if direction == "rtl" else lang
                px_per_pt = LAYOUT_DPI / 72.0
                font_size_px = max(8, int(round(float(font_size_pt) * px_per_pt)))
                pil_font = _get_pil_font(font_file or reg_font_path or bold_font_path or "", font_size_px, language_hint)
                fill = (
                    int(color_rgb[0]),
                    int(color_rgb[1]),
                    int(color_rgb[2]),
                    255,
                )
                img, _baseline_y_px, _width_px = _build_text_image(
                    text,
                    pil_font,
                    fill,
                    language_hint,
                    enable_gradient=enable_gradient,
                    gradient_color_bottom=gradient_color_bottom,
                )
                scale_ratio = min(float(rect.width) / max(1, img.width), float(rect.height) / max(1, img.height))
                scale_ratio = max(0.01, min(1.0, scale_ratio))
                draw_w = max(1.0, img.width * scale_ratio)
                draw_h = max(1.0, img.height * scale_ratio)
                align_mode = str(align or "left").strip().lower()
                if align_mode == "center":
                    x0 = rect.x0 + ((rect.width - draw_w) / 2.0)
                elif align_mode == "right":
                    x0 = rect.x1 - draw_w
                else:
                    x0 = rect.x0
                y0 = rect.y0
                target_rect = fitz.Rect(float(x0), float(y0), float(x0 + draw_w), float(y0 + draw_h))
                png_buf = io.BytesIO()
                img.save(png_buf, format="PNG")
                page.insert_image(target_rect, stream=png_buf.getvalue(), overlay=True, keep_proportion=False)
            except Exception:
                logger.warning("Raster text fallback failed for editable PDF text")

        def _text_rect(
            card_x: float,
            card_w_pt: float,
            slot_top: float,
            x_px: float,
            y_px: float,
            width_pt: float,
            height_pt: float,
            direction: str,
            grow_mode=None,
        ) -> fitz.Rect:
            width_pt = max(6.0, float(width_pt))
            height_pt = max(6.0, float(height_pt))
            direction_norm = (direction or "ltr").strip().lower()
            mode = _normalize_grow_mode(grow_mode, direction_norm)
            min_x = float(card_x) + (2 * x_scale)
            max_x = float(card_x + card_w_pt - (2 * x_scale))
            anchor_x = (
                float(card_x) + (float(card_w_pt) - (float(x_px) * x_scale))
                if direction_norm == "rtl"
                else float(card_x) + (float(x_px) * x_scale)
            )
            if mode == "center":
                x0 = anchor_x - (width_pt / 2.0)
                x1 = anchor_x + (width_pt / 2.0)
            elif mode == "right":
                x1 = anchor_x
                x0 = x1 - width_pt
            else:
                x0 = anchor_x
                x1 = x0 + width_pt
            if x0 < min_x:
                shift = min_x - x0
                x0 += shift
                x1 += shift
            if x1 > max_x:
                shift = x1 - max_x
                x0 -= shift
                x1 -= shift
            x0 = max(min_x, min(x0, max_x - 2.0))
            x1 = max(x0 + 2.0, min(max_x, x1))
            y0 = float(slot_top) + (float(y_px) * y_scale)
            y1 = y0 + height_pt
            return fitz.Rect(x0, y0, max(x1, x0 + 2.0), max(y1, y0 + 2.0))

        def _prepare_box_image(photo_bytes_io, target_w_px: int, target_h_px: int, radii=None, photo_shape="rectangle", photo_shape_inset=0) -> bytes | None:
            if photo_bytes_io is None:
                return None
            try:
                photo_bytes_io.seek(0)
                img = Image.open(photo_bytes_io)
                img = ImageOps.exif_transpose(img)
                img.load()
                if img.mode not in {"RGBA", "LA"}:
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGBA")
                target_size = (max(1, int(target_w_px)), max(1, int(target_h_px)))
                if img.size != target_size:
                    img = ImageOps.fit(
                        img,
                        target_size,
                        method=Image.LANCZOS,
                        centering=(0.5, 0.35),
                    )
                normalized_radii = [int(float(r or 0)) for r in (radii or [])]
                img = round_photo(img, normalized_radii, shape=photo_shape, shape_inset=photo_shape_inset)
                out = io.BytesIO()
                img.save(out, format="PNG")
                return out.getvalue()
            except Exception:
                return None

        def _rounded_clip_stream(image_name: str, rect: fitz.Rect, page_height: float, radii: list[float], box_w_px: int, box_h_px: int) -> bytes:
            k = 0.5522847498307936
            tl, tr, br, bl = [max(0.0, float(r or 0.0)) for r in radii]
            rx_tl = min(tl / max(1.0, float(box_w_px)), 0.5)
            ry_tl = min(tl / max(1.0, float(box_h_px)), 0.5)
            rx_tr = min(tr / max(1.0, float(box_w_px)), 0.5)
            ry_tr = min(tr / max(1.0, float(box_h_px)), 0.5)
            rx_br = min(br / max(1.0, float(box_w_px)), 0.5)
            ry_br = min(br / max(1.0, float(box_h_px)), 0.5)
            rx_bl = min(bl / max(1.0, float(box_w_px)), 0.5)
            ry_bl = min(bl / max(1.0, float(box_h_px)), 0.5)

            def fmt(v: float) -> str:
                return f"{v:.6f}".rstrip("0").rstrip(".") or "0"

            parts = [
                "q",
                f"{fmt(rect.width)} 0 0 {fmt(rect.height)} {fmt(rect.x0)} {fmt(page_height - rect.y1)} cm",
                f"{fmt(rx_tl)} 1 m",
                f"{fmt(1 - rx_tr)} 1 l",
            ]
            if rx_tr > 0 or ry_tr > 0:
                parts.append(
                    f"{fmt(1 - rx_tr + rx_tr * k)} 1 {fmt(1)} {fmt(1 - ry_tr + ry_tr * k)} {fmt(1)} {fmt(1 - ry_tr)} c"
                )
            else:
                parts.append("1 1 l")
            parts.append(f"1 {fmt(ry_br)} l")
            if rx_br > 0 or ry_br > 0:
                parts.append(
                    f"{fmt(1)} {fmt(ry_br - ry_br * k)} {fmt(1 - rx_br + rx_br * k)} 0 {fmt(1 - rx_br)} 0 c"
                )
            else:
                parts.append("1 0 l")
            parts.append(f"{fmt(rx_bl)} 0 l")
            if rx_bl > 0 or ry_bl > 0:
                parts.append(
                    f"{fmt(rx_bl - rx_bl * k)} 0 0 {fmt(ry_bl - ry_bl * k)} 0 {fmt(ry_bl)} c"
                )
            else:
                parts.append("0 0 l")
            parts.append(f"0 {fmt(1 - ry_tl)} l")
            if rx_tl > 0 or ry_tl > 0:
                parts.append(
                    f"0 {fmt(1 - ry_tl + ry_tl * k)} {fmt(rx_tl - rx_tl * k)} 1 {fmt(rx_tl)} 1 c"
                )
            else:
                parts.append("0 1 l")
            parts.extend(["h", "W n", f"/{image_name} Do", "Q", ""])
            return "\n".join(parts).encode("ascii")

        def _apply_rounded_image_clip(
            page: fitz.Page,
            image_xref: int,
            rect: fitz.Rect,
            radii: list[float],
            box_w_px: int,
            box_h_px: int,
        ) -> None:
            if not any(float(r or 0) > 0 for r in radii):
                return
            try:
                images = page.get_images(full=True)
                image_name = None
                for img in images:
                    if img and img[0] == image_xref:
                        image_name = img[7]
                        break
                if not image_name:
                    return

                content_xrefs = list(page.get_contents() or [])
                if not content_xrefs:
                    return

                clip_stream = _rounded_clip_stream(
                    image_name,
                    rect,
                    float(page.rect.height),
                    radii,
                    box_w_px,
                    box_h_px,
                )
                page.parent.update_stream(content_xrefs[-1], clip_stream, compress=0)
            except Exception:
                logger.exception("Failed to apply rounded clip to editable PDF photo")

        def _draw_rounded_photo_frame(
            page: fitz.Page,
            rect: fitz.Rect,
            radii: list[float],
            box_w_px: int,
            box_h_px: int,
            stroke_width: float,
            color: tuple[float, float, float],
        ) -> None:
            tl, tr, br, bl = [max(0.0, float(r or 0.0)) for r in radii]
            if not any(v > 0 for v in (tl, tr, br, bl)):
                page.draw_rect(rect, color=color, width=stroke_width, overlay=True)
                return

            k = 0.5522847498307936
            rx_tl = min((tl / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_tl = min((tl / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_tr = min((tr / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_tr = min((tr / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_br = min((br / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_br = min((br / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)
            rx_bl = min((bl / max(1.0, float(box_w_px))) * rect.width, rect.width / 2.0)
            ry_bl = min((bl / max(1.0, float(box_h_px))) * rect.height, rect.height / 2.0)

            x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
            shape = page.new_shape()
            shape.draw_line((x0 + rx_tl, y0), (x1 - rx_tr, y0))
            if rx_tr > 0 or ry_tr > 0:
                shape.draw_bezier(
                    (x1 - rx_tr, y0),
                    (x1 - rx_tr + rx_tr * k, y0),
                    (x1, y0 + ry_tr - ry_tr * k),
                    (x1, y0 + ry_tr),
                )
            else:
                shape.draw_line((x1, y0), (x1, y0))

            shape.draw_line((x1, y0 + ry_tr), (x1, y1 - ry_br))
            if rx_br > 0 or ry_br > 0:
                shape.draw_bezier(
                    (x1, y1 - ry_br),
                    (x1, y1 - ry_br + ry_br * k),
                    (x1 - rx_br + rx_br * k, y1),
                    (x1 - rx_br, y1),
                )
            else:
                shape.draw_line((x1, y1), (x1, y1))

            shape.draw_line((x1 - rx_br, y1), (x0 + rx_bl, y1))
            if rx_bl > 0 or ry_bl > 0:
                shape.draw_bezier(
                    (x0 + rx_bl, y1),
                    (x0 + rx_bl - rx_bl * k, y1),
                    (x0, y1 - ry_bl + ry_bl * k),
                    (x0, y1 - ry_bl),
                )
            else:
                shape.draw_line((x0, y1), (x0, y1))

            shape.draw_line((x0, y1 - ry_bl), (x0, y0 + ry_tl))
            if rx_tl > 0 or ry_tl > 0:
                shape.draw_bezier(
                    (x0, y0 + ry_tl),
                    (x0, y0 + ry_tl - ry_tl * k),
                    (x0 + rx_tl - rx_tl * k, y0),
                    (x0 + rx_tl, y0),
                )
            else:
                shape.draw_line((x0, y0), (x0, y0))

            shape.finish(
                width=stroke_width,
                color=color,
                fill=None,
                closePath=True,
            )
            shape.commit(overlay=True)

        def _draw_photo_frame_fitz(
            page: fitz.Page,
            rect: fitz.Rect,
            radii: list[float],
            box_w_px: int,
            box_h_px: int,
            stroke_width: float,
            color: tuple[float, float, float],
            photo_shape="rectangle",
            photo_shape_inset=0,
        ) -> None:
            normalized_shape = normalize_photo_shape(photo_shape)
            photo_shape_inset = max(0.0, min(float(photo_shape_inset or 0), max(0.0, (min(rect.width, rect.height) - 2.0) / 2.0)))
            if normalized_shape == "circle":
                inset_rect = fitz.Rect(
                    rect.x0 + photo_shape_inset,
                    rect.y0 + photo_shape_inset,
                    rect.x1 - photo_shape_inset,
                    rect.y1 - photo_shape_inset,
                )
                page.draw_oval(inset_rect, color=color, width=stroke_width, overlay=True)
                return
            if normalized_shape not in {"rectangle", "rounded"}:
                points = _photo_shape_points(
                    rect.x0,
                    rect.y0,
                    rect.width,
                    rect.height,
                    normalized_shape,
                    y_axis_down=True,
                    inset=photo_shape_inset,
                    shape_geometry_scale=(rect.height / max(1.0, float(box_h_px or rect.height))),
                )
                if not points:
                    page.draw_rect(rect, color=color, width=stroke_width, overlay=True)
                    return
                shape = page.new_shape()
                shape.draw_line(points[0], points[1])
                for start, end in zip(points[1:], points[2:]):
                    shape.draw_line(start, end)
                shape.draw_line(points[-1], points[0])
                shape.finish(width=stroke_width, color=color, fill=None, closePath=True)
                shape.commit(overlay=True)
                return
            _draw_rounded_photo_frame(page, rect, radii, box_w_px, box_h_px, stroke_width, color)

        page = None
        x_scale = float(card_w_pt) / max(1.0, float(card_w_px))
        y_scale = float(card_h_pt) / max(1.0, float(card_h_px))
        text_scale = min(x_scale, y_scale)
        clone_template_page_directly = bool(
            include_template_background
            and int(cols) == 1
            and int(rows) == 1
            and abs(float(start_x_pt or 0)) < 0.01
            and abs(float(gap_pt or 0)) < 0.01
        )

        cards_per_sheet = max(1, int(cols) * int(rows))
        for idx, student in enumerate(students):
            idx_on_sheet = idx % cards_per_sheet
            col_idx = idx_on_sheet % cols
            row_idx = idx_on_sheet // cols

            if idx_on_sheet == 0:
                if clone_template_page_directly:
                    out_doc.insert_pdf(template_doc, from_page=0, to_page=0)
                    page = out_doc[out_doc.page_count - 1]
                else:
                    page = out_doc.new_page(width=float(sheet_w_pt), height=float(sheet_h_pt))

            card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
            card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
            card_bottom_y = card_top_y - card_h_pt
            slot_top = float(sheet_h_pt - card_top_y)
            slot_bottom = float(sheet_h_pt - card_bottom_y)
            card_rect = fitz.Rect(float(card_x), slot_top, float(card_x + card_w_pt), slot_bottom)

            if include_template_background and not clone_template_page_directly:
                page.show_pdf_page(card_rect, template_doc, 0, keep_proportion=False, overlay=False)
            page_w_pt = float(page.rect.width)
            text_scale = min(x_scale, y_scale)

            # Keep editable PDF-template exports on Corel-safer base PDF fonts.
            page_reg_font = native_reg_font_name or "helv"
            page_bold_font = native_bold_font_name or "hebo"

            label_default_rgb = tuple(font_settings.get("label_font_color", [0, 0, 0]))
            value_default_rgb = tuple(font_settings.get("value_font_color", [0, 0, 0]))
            # Use the same safe _rgb_tuple logic: handle list or hex-string format
            _raw_colon_color = font_settings.get("colon_font_color", list(label_default_rgb))
            if isinstance(_raw_colon_color, str) and _raw_colon_color.startswith("#"):
                try:
                    _h = _raw_colon_color.lstrip("#")
                    colon_default_rgb = (int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16))
                except Exception:
                    colon_default_rgb = label_default_rgb
            else:
                try:
                    colon_default_rgb = tuple(int(v) for v in (_raw_colon_color or label_default_rgb))[:3]
                    if len(colon_default_rgb) < 3:
                        colon_default_rgb = label_default_rgb
                except Exception:
                    colon_default_rgb = label_default_rgb
            text_case = font_settings.get("text_case", "normal")
            show_label_colon = bool(font_settings.get("show_label_colon", True))
            align_label_colon = bool(font_settings.get("align_label_colon", True))
            # Fix 2/10: dedicated colon font size (falls back to label size)
            colon_font_size_px = int(font_settings.get("colon_font_size") or font_settings.get("label_font_size", 40))
            config_address_max_lines = int(font_settings.get("address_max_lines", 2) or 2)
            label_colon_gap = int(font_settings.get("label_colon_gap", 8) or 8)

            enable_label_gradient = bool(font_settings.get("enable_label_gradient", False))
            label_fill_bottom = tuple(font_settings.get("label_font_color_bottom", [51, 51, 51]))
            enable_value_gradient = bool(font_settings.get("enable_value_gradient", False))
            value_fill_bottom = tuple(font_settings.get("value_font_color_bottom", [51, 51, 51]))
            enable_colon_gradient = bool(font_settings.get("enable_colon_gradient", False))
            colon_fill_bottom = tuple(font_settings.get("colon_font_color_bottom", [51, 51, 51]))

            try:
                px_px = photo_settings.get("photo_x", 0)
                py_px = photo_settings.get("photo_y", 0)
                pw_px = photo_settings.get("photo_width", 100)
                ph_px = photo_settings.get("photo_height", 100)
                photo_rect = fitz.Rect(
                    float(card_x) + (float(px_px) * x_scale),
                    slot_top + (float(py_px) * y_scale),
                    float(card_x) + (float(px_px + pw_px) * x_scale),
                    slot_top + (float(py_px + ph_px) * y_scale),
                )
                radii = [
                    photo_settings.get("photo_border_top_left", 0),
                    photo_settings.get("photo_border_top_right", 0),
                    photo_settings.get("photo_border_bottom_right", 0),
                    photo_settings.get("photo_border_bottom_left", 0),
                ]
                photo_shape = photo_settings.get("photo_shape", "rectangle")
                photo_shape_inset = int(float(photo_settings.get("photo_shape_inset", 0) or 0))
                editable_photo_mode = _corel_editable_photo_mode(photo_settings)
                draw_editable_photo_frame = editable_photo_mode == "frame_only"
                if photo_settings.get("enable_photo", True):
                    photo_bytes_io, has_real_student_photo = _load_student_photo_stream(student, pw_px, ph_px)
                    if photo_bytes_io and (has_real_student_photo or not draw_editable_photo_frame):
                        prepared_photo = _prepare_box_image(
                            photo_bytes_io,
                            pw_px,
                            ph_px,
                            radii=radii,
                            photo_shape=photo_shape,
                            photo_shape_inset=photo_shape_inset,
                        )
                        if prepared_photo:
                            before_contents = list(page.get_contents() or [])
                            image_xref = page.insert_image(
                                photo_rect,
                                stream=prepared_photo,
                                overlay=True,
                                keep_proportion=False,
                            )
                            after_contents = list(page.get_contents() or [])
                            if (
                                mode != "editable"
                                and normalize_photo_shape(photo_shape) in {"rectangle", "rounded"}
                                and len(after_contents) > len(before_contents)
                            ):
                                _apply_rounded_image_clip(
                                    page,
                                    image_xref,
                                    photo_rect,
                                    radii,
                                    pw_px,
                                    ph_px,
                                )
                    if draw_editable_photo_frame:
                        try:
                            _fr, _fg, _fb = _parse_hex_to_rgb_normalized(photo_settings.get("photo_frame_color"))
                            if mode == "editable" and normalize_photo_shape(photo_shape) == "rectangle":
                                page.draw_rect(
                                    photo_rect,
                                    color=(_fr, _fg, _fb),
                                    width=max(0.8, 1.2 * text_scale),
                                    overlay=True,
                                )
                            else:
                                _draw_photo_frame_fitz(
                                    page,
                                    photo_rect,
                                    radii,
                                    pw_px,
                                    ph_px,
                                    max(0.8, 1.2 * text_scale),
                                    (_fr, _fg, _fb),
                                    photo_shape,
                                    photo_shape_inset * text_scale,
                                )
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                form_data = {
                    "name": student.name,
                    "father_name": student.father_name,
                    "class_name": student.class_name,
                    "dob": student.dob,
                    "address": student.address,
                    "phone": student.phone,
                }
                photo_ref = getattr(student, "photo_url", None) or getattr(student, "photo_filename", None) or ""
                data_hash = generate_data_hash(form_data, photo_ref)
                qr_id = data_hash[:10]

                if bool(qr_settings.get("enable_qr", False)):
                    qr_type = qr_settings.get("qr_data_type", "student_id")
                    if qr_type == "url":
                        base = qr_settings.get("qr_base_url", "")
                        if base and not base.endswith("/"):
                            base += "/"
                        qr_payload = base + qr_id
                    elif qr_type == "text":
                        qr_payload = qr_settings.get("qr_custom_text", "Sample")
                    elif qr_type == "json":
                        qr_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name,
                        })
                    else:
                        qr_payload = qr_id

                    size_px = max(40, int(qr_settings.get("qr_size", 120)))
                    q_x_px = int(qr_settings.get("qr_x", 50))
                    q_y_px = int(qr_settings.get("qr_y", 50))
                    qr_rect = fitz.Rect(
                        float(card_x) + (float(q_x_px) * x_scale),
                        slot_top + (float(q_y_px) * y_scale),
                        float(card_x) + (float(q_x_px + size_px) * x_scale),
                        slot_top + (float(q_y_px + size_px) * y_scale),
                    )
                    qr_img = generate_qr_code(qr_payload, qr_settings, max(40, size_px)).convert("RGB")
                    qr_buf = io.BytesIO()
                    qr_img.save(qr_buf, format="PNG")
                    page.insert_image(qr_rect, stream=qr_buf.getvalue(), overlay=True, keep_proportion=False)

                if bool(qr_settings.get("enable_barcode", False)):
                    barcode_type = qr_settings.get("barcode_data_type", "student_id")
                    if barcode_type == "url":
                        base = qr_settings.get("barcode_base_url", "")
                        if base and not base.endswith("/"):
                            base += "/"
                        barcode_payload = base + qr_id
                    elif barcode_type == "text":
                        barcode_payload = qr_settings.get("barcode_custom_text", "Sample")
                    elif barcode_type == "json":
                        barcode_payload = json.dumps({
                            "student_id": qr_id,
                            "name": student.name,
                            "class": student.class_name,
                            "school_name": template.school_name,
                        })
                    else:
                        barcode_payload = qr_id

                    barcode_w_px = max(40, int(qr_settings.get("barcode_width", 220)))
                    barcode_h_px = max(30, int(qr_settings.get("barcode_height", 70)))
                    barcode_x_px = int(qr_settings.get("barcode_x", 50))
                    barcode_y_px = int(qr_settings.get("barcode_y", 200))
                    barcode_rect = fitz.Rect(
                        float(card_x) + (float(barcode_x_px) * x_scale),
                        slot_top + (float(barcode_y_px) * y_scale),
                        float(card_x) + (float(barcode_x_px + barcode_w_px) * x_scale),
                        slot_top + (float(barcode_y_px + barcode_h_px) * y_scale),
                    )
                    barcode_img = generate_barcode_code128(
                        barcode_payload,
                        qr_settings,
                        width=barcode_w_px,
                        height=barcode_h_px,
                    ).convert("RGB")
                    barcode_buf = io.BytesIO()
                    barcode_img.save(barcode_buf, format="PNG")
                    page.insert_image(barcode_rect, stream=barcode_buf.getvalue(), overlay=True, keep_proportion=False)
            except Exception:
                pass

            fields = [
                {"k": "NAME", "l": local_apply_text_case(labels_map["NAME"], text_case), "v": local_apply_text_case(_translate_value_for_export(student.name, source_language=source_language, target_language=lang, field_key="NAME", field_type="text"), text_case), "ord": 10, "field_type": "text", "translate_label": False},
                {"k": "F_NAME", "l": local_apply_text_case(labels_map["F_NAME"], text_case), "v": local_apply_text_case(_translate_value_for_export(student.father_name, source_language=source_language, target_language=lang, field_key="F_NAME", field_type="text"), text_case), "ord": 20, "field_type": "text", "translate_label": False},
                {"k": "CLASS", "l": local_apply_text_case(labels_map["CLASS"], text_case), "v": local_apply_text_case(_translate_value_for_export(student.class_name, source_language=source_language, target_language=lang, field_key="CLASS", field_type="text"), text_case), "ord": 30, "field_type": "text", "translate_label": False},
                {"k": "DOB", "l": local_apply_text_case(labels_map["DOB"], text_case), "v": local_apply_text_case(student.dob, text_case), "ord": 40, "field_type": "date", "translate_label": False},
                {"k": "MOBILE", "l": local_apply_text_case(labels_map["MOBILE"], text_case), "v": local_apply_text_case(student.phone, text_case), "ord": 50, "field_type": "tel", "translate_label": False},
                {"k": "ADDRESS", "l": local_apply_text_case(labels_map["ADDRESS"], text_case), "v": local_apply_text_case(_translate_value_for_export(student.address, source_language=source_language, target_language=lang, field_key="ADDRESS", field_type="textarea"), text_case), "ord": 60, "field_type": "textarea", "translate_label": False},
            ]
            from app.services.render_service import normalize_custom_data
            custom_data = normalize_custom_data(getattr(student, "custom_data", None))
            for f in db_fields:
                translated_label = f.field_label
                if _normalize_language(source_language) != _normalize_language(lang):
                    translated_label = _translate_value_for_export(
                        f.field_label,
                        source_language=source_language,
                        target_language=lang,
                        field_key=f"{f.field_name}_LABEL",
                        field_type="label",
                    )
                fields.append(
                    {
                        "k": f.field_name,
                        "l": local_apply_text_case(translated_label, text_case),
                        "v": local_apply_text_case(
                            _translate_value_for_export(
                                custom_data.get(f.field_name, ""),
                                source_language=source_language,
                                target_language=lang,
                                field_key=f.field_name,
                                field_type=f.field_type,
                            ),
                            text_case,
                        ),
                        "ord": f.display_order,
                        "field_type": f.field_type,
                        "translate_label": True,
                    }
                )
            fields.sort(key=lambda x: x["ord"])

            start_y_text_px = font_settings.get("start_y", 200)
            label_x_px = font_settings.get("label_x", 50)
            value_x_px = font_settings.get("value_x", 250)
            current_y_px = _initial_flow_y_px(template, font_settings, side=side)
            line_height_px = font_settings.get("line_height", 50)
            photo_enabled = bool(photo_settings.get("enable_photo", True))
            p_x_px = photo_settings.get("photo_x", 0) if photo_enabled else 0
            p_y_px = photo_settings.get("photo_y", 0) if photo_enabled else 0
            p_h_px = photo_settings.get("photo_height", 0) if photo_enabled else 0
            p_bottom_px = p_y_px + p_h_px

            for field in fields:
                field_key = _field_key_from_item(field)
                layout_item = _resolve_pdf_field_layout(
                    template,
                    field_key,
                    label_x_px,
                    value_x_px,
                    current_y_px,
                    side=side,
                    text_direction=direction,
                )
                label_x_eff = layout_item["label_x"]
                value_x_eff = layout_item["value_x"]
                label_y_eff = layout_item["label_y"]
                value_y_eff = layout_item["value_y"]
                label_visible = layout_item["label_visible"]
                value_visible = layout_item["value_visible"]
                label_grow = layout_item.get("label_grow")
                value_grow = layout_item.get("value_grow")
                label_rgb = layout_item.get("label_color") or label_default_rgb
                value_rgb = layout_item.get("value_color") or value_default_rgb
                # Per-field colon color: respect layout_item.colon_color, fall back to global colon_default_rgb
                colon_rgb = layout_item.get("colon_color") or colon_default_rgb
                label_size_px_eff = max(1, int(layout_item.get("label_font_size") or font_settings.get("label_font_size", 40)))
                value_size_px_eff = max(1, int(layout_item.get("value_font_size") or font_settings.get("value_font_size", 36)))
                # Fix 2/10: per-field colon size; fall back to colon_font_size_px
                colon_size_px_eff = max(1, int(layout_item.get("colon_font_size") or colon_font_size_px))
                lbl_size_pt_eff = label_size_px_eff * text_scale
                val_size_pt_eff = value_size_px_eff * text_scale
                colon_size_pt_eff = colon_size_px_eff * text_scale

                if not _field_consumes_layout_space(layout_item, field.get("v", "")):
                    continue
                advances_flow = _field_advances_layout_flow(
                    layout_item,
                    field.get("v", ""),
                    separate_colon=bool(show_label_colon and align_label_colon),
                )
                if advances_flow:
                    current_y_px = max(int(current_y_px), int(label_y_eff), int(value_y_eff))

                if label_visible:
                    label_text, colon_text = split_label_and_colon(
                        process_text_for_vector(field["l"], lang, for_native_bidi=True),
                        lang,
                        direction,
                        include_colon=show_label_colon,
                        align_colon=align_label_colon,
                    )
                    baseline_y_pt = (label_y_eff * y_scale) + lbl_size_pt_eff
                    if label_text:
                        if lang == "hindi" or (enable_label_gradient and mode != "editable"):
                            label_rect = _text_rect(
                                card_x,
                                card_w_pt,
                                slot_top,
                                label_x_eff,
                                label_y_eff,
                                card_w_pt * 0.45,
                                lbl_size_pt_eff * 1.7,
                                direction,
                                grow_mode=label_grow,
                            )
                            _insert_complex_text_box(
                                page,
                                label_rect,
                                label_text,
                                font_file=bold_font_path or reg_font_path,
                                font_size_pt=lbl_size_pt_eff,
                                color_rgb=label_rgb,
                                direction=direction,
                                align="center" if label_grow == "center" else ("right" if direction == "rtl" else "left"),
                                prefer_native_text=not enable_label_gradient,
                                enable_gradient=enable_label_gradient,
                                gradient_color_bottom=label_fill_bottom,
                            )
                        else:
                            label_x = _x_for_direction(
                                float(card_x),
                                float(card_w_pt),
                                label_x_eff,
                                label_text,
                                measure_bold_font_name,
                                lbl_size_pt_eff,
                                x_scale,
                                direction,
                                grow_mode=label_grow,
                            )
                            page.insert_text(
                                fitz.Point(label_x, slot_top + baseline_y_pt),
                                label_text,
                                fontsize=lbl_size_pt_eff,
                                fontname=page_bold_font,
                                fontfile=None if safe_editable_builtin_text else bold_font_path,
                                color=_fitz_rgb(label_rgb),
                                overlay=True,
                            )
                    if colon_text:
                        colon_anchor_px, colon_grow = colon_anchor_for_value(value_x_eff, direction, gap_px=label_colon_gap)
                        if lang == "hindi" or (enable_colon_gradient and mode != "editable"):
                            colon_rect = _text_rect(
                                card_x,
                                card_w_pt,
                                slot_top,
                                colon_anchor_px,
                                label_y_eff,
                                18 * text_scale,
                                colon_size_pt_eff * 1.7,
                                direction,
                                grow_mode=colon_grow,
                            )
                            _insert_complex_text_box(
                                page,
                                colon_rect,
                                colon_text,
                                font_file=bold_font_path or reg_font_path,
                                font_size_pt=colon_size_pt_eff,
                                color_rgb=colon_rgb,
                                direction=direction,
                                align="center" if colon_grow == "center" else ("right" if direction == "rtl" else "left"),
                                prefer_native_text=not enable_colon_gradient,
                                enable_gradient=enable_colon_gradient,
                                gradient_color_bottom=colon_fill_bottom,
                            )
                        else:
                            colon_x = _x_for_direction(
                                float(card_x),
                                float(card_w_pt),
                                colon_anchor_px,
                                colon_text,
                                measure_bold_font_name,
                                colon_size_pt_eff,
                                x_scale,
                                direction,
                                grow_mode=colon_grow,
                            )
                            page.insert_text(
                                fitz.Point(colon_x, slot_top + baseline_y_pt),
                                colon_text,
                                fontsize=colon_size_pt_eff,
                                fontname=page_bold_font,
                                fontfile=None if safe_editable_builtin_text else bold_font_path,
                                color=_fitz_rgb(colon_rgb),
                                overlay=True,
                            )

                val_text = process_text_for_vector(field["v"], lang, for_native_bidi=True)
                if field.get("k") == "ADDRESS" and text_case == "normal" and val_text and val_text.isupper() and len(val_text) > 10:
                    val_text = val_text.title()

                max_w_px = get_anchor_max_text_width(
                    card_width=card_w_px,
                    anchor_x=value_x_eff,
                    text_direction=direction,
                    line_y=value_y_eff,
                    line_height=line_height_px,
                    grow_mode=value_grow,
                    photo_x=p_x_px,
                    photo_y=p_y_px,
                    photo_width=(photo_settings.get("photo_width", 0) if photo_enabled else 0),
                    photo_height=(photo_settings.get("photo_height", 0) if photo_enabled else 0),
                    page_margin=20,
                    photo_gap=15,
                    min_width=50,
                )

                max_width_pt = float(max_w_px) * x_scale
                remaining_h_px = max(1.0, float(card_h_px - 20) - float(value_y_eff))
                remaining_h_pt = max(text_scale, remaining_h_px * y_scale)
                wrap_policy = _field_wrap_policy(field_key, config_address_max_lines)
                line_height_factor = float(wrap_policy.get("line_height_factor", 1.15))
                min_font_size_pt = max(8 * text_scale, val_size_pt_eff * float(wrap_policy.get("min_scale", 0.78)))
                field_max_lines = max(
                    1,
                    min(
                        int(wrap_policy.get("max_lines", 3)),
                        int(remaining_h_pt / max(min_font_size_pt * line_height_factor, text_scale)),
                    ),
                )
                curr_font_size, lines = _fit_wrapped_text(
                    val_text,
                    font_name=measure_reg_font_name,
                    start_size_pt=val_size_pt_eff,
                    min_size_pt=min_font_size_pt,
                    max_width_pt=max_width_pt,
                    max_lines=field_max_lines,
                    max_height_pt=remaining_h_pt,
                    line_height_factor=line_height_factor,
                )
                line_spacing = curr_font_size * line_height_factor

                for i, line in enumerate(lines[:field_max_lines]):
                    if not value_visible:
                        continue
                    baseline_y_pt = (value_y_eff * y_scale) + curr_font_size + (i * line_spacing)
                    if lang == "hindi" or (enable_value_gradient and mode != "editable"):
                         line_rect = _text_rect(
                             card_x,
                             card_w_pt,
                             slot_top,
                             value_x_eff,
                             value_y_eff + ((i * line_spacing) / max(y_scale, 0.001)),
                             max_width_pt,
                             curr_font_size * line_height_factor * 1.25,
                             direction,
                             grow_mode=value_grow,
                         )
                         _insert_complex_text_box(
                             page,
                             line_rect,
                             line,
                             font_file=reg_font_path or bold_font_path,
                             font_size_pt=curr_font_size,
                             color_rgb=value_rgb,
                             direction=direction,
                             align="center" if value_grow == "center" else ("right" if direction == "rtl" else "left"),
                             prefer_native_text=not enable_value_gradient,
                             enable_gradient=enable_value_gradient,
                             gradient_color_bottom=value_fill_bottom,
                         )
                    else:
                         vx = _x_for_direction(
                             float(card_x),
                             float(card_w_pt),
                             value_x_eff,
                             line,
                             measure_reg_font_name,
                             curr_font_size,
                             x_scale,
                             direction,
                             grow_mode=value_grow,
                         )
                         page.insert_text(
                             fitz.Point(vx, slot_top + baseline_y_pt),
                             line,
                             fontsize=curr_font_size,
                             fontname=page_reg_font,
                             fontfile=None if safe_editable_builtin_text else reg_font_path,
                             color=_fitz_rgb(value_rgb),
                             overlay=True,
                         )

                if advances_flow and len(lines) > 1:
                    extra_h_px = ((len(lines) - 1) * line_spacing) / max(y_scale, 0.001)
                    current_y_px += extra_h_px
                if advances_flow:
                    current_y_px += line_height_px

        export_bytes = _corel_safe_pdf_bytes(out_doc, garbage=4, clean=False)
        return _make_corel_friendly(export_bytes, mode=mode) if mode == "editable" else export_bytes
    finally:
        try:
            template_doc.close()
        except Exception:
            pass
        try:
            out_doc.close()
        except Exception:
            pass




def _draw_vector_qr(c, payload: str, x: float, y: float, width: float, height: float, fill_color: Color):
    """Draw vector QR for editable PDFs."""
    qr_widget = rl_qr.QrCodeWidget(str(payload or ""))
    qr_widget.barFillColor = fill_color
    bounds = qr_widget.getBounds()
    bw = max(1e-6, bounds[2] - bounds[0])
    bh = max(1e-6, bounds[3] - bounds[1])
    sx = width / bw
    sy = height / bh
    drawing = Drawing(width, height, transform=[sx, 0, 0, sy, -bounds[0] * sx, -bounds[1] * sy])
    drawing.add(qr_widget)
    renderPDF.draw(drawing, c, x, y)




def _draw_vector_barcode(c, payload: str, x: float, y: float, width: float, height: float, fill_color: Color):
    """Draw vector Code128 barcode for editable PDFs."""
    value = str(payload or "")
    drawing = createBarcodeDrawing(
        "Code128",
        value=value,
        barHeight=max(1.0, float(height)),
        humanReadable=False,
        barFillColor=fill_color,
    )
    bw = max(1e-6, float(getattr(drawing, "width", 1.0)))
    bh = max(1e-6, float(getattr(drawing, "height", 1.0)))
    c.saveState()
    c.translate(x, y)
    c.scale(float(width) / bw, float(height) / bh)
    renderPDF.draw(drawing, c, 0, 0)
    c.restoreState()




def _queue_hb_run(
    runs: list[dict],
    *,
    page_index: int,
    card_x: float,
    card_w_pt: float,
    card_bottom_y: float,
    card_h_pt: float,
    x_px: float,
    y_px: float,
    max_w_pt: float,
    box_h_pt: float,
    scale: float,
    direction: str,
    text: str,
    font_file: str,
    font_size_pt: float,
    color_rgb: tuple[int, int, int],
):
    """Queue a HarfBuzz/Pango text run for post-render PDF overlay."""
    text = "" if text is None else str(text)
    if not text.strip():
        return

    direction = (direction or "ltr").strip().lower()
    max_w_pt = max(8.0, float(max_w_pt))
    box_h_pt = max(float(font_size_pt) * 1.2, float(box_h_pt))

    if direction == "rtl":
        anchor_x = card_x + (card_w_pt - (float(x_px) * scale))
        x1 = max(card_x + (2 * scale), anchor_x)
        x0 = max(card_x + (2 * scale), x1 - max_w_pt)
        align = "right"
    else:
        x0 = card_x + (float(x_px) * scale)
        x1 = min(card_x + card_w_pt - (2 * scale), x0 + max_w_pt)
        align = "left"

    y_top_bottom_space = card_bottom_y + card_h_pt - (float(y_px) * scale)
    y1_bottom = max(card_bottom_y + 1.0, y_top_bottom_space)
    y0_bottom = max(card_bottom_y + 0.5, y1_bottom - box_h_pt)

    runs.append(
        {
            "page_index": int(page_index),
            "x0": float(x0),
            "x1": float(max(x1, x0 + 2.0)),
            "y0_bottom": float(y0_bottom),
            "y1_bottom": float(max(y1_bottom, y0_bottom + 2.0)),
            "text": text,
            "font_file": (font_file or "").strip(),
            "font_size_pt": float(max(1.0, font_size_pt)),
            "color_rgb": tuple(int(max(0, min(255, c))) for c in (color_rgb or (0, 0, 0))),
            "direction": direction,
            "align": align,
        }
    )




def _apply_hb_text_overlay(pdf_bytes: bytes, runs: list[dict], page_height_pt: float) -> bytes:
    """
    Overlay shaped text runs using PyMuPDF HTML engine (HarfBuzz-backed).
    This keeps Unicode text objects in the output PDF instead of rasterized text.
    """
    if not runs:
        return pdf_bytes

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    archive = fitz.Archive(FONTS_FOLDER)
    css_cache: dict[str, tuple[str, str]] = {}

    for run in runs:
        page_index = int(run.get("page_index", 0))
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc[page_index]

        y0_top = float(page_height_pt) - float(run["y1_bottom"])
        y1_top = float(page_height_pt) - float(run["y0_bottom"])
        rect = fitz.Rect(float(run["x0"]), y0_top, float(run["x1"]), y1_top)
        if rect.width < 1 or rect.height < 1:
            continue

        font_file = run.get("font_file", "")
        font_basename = os.path.basename(font_file) if font_file else ""

        if font_basename in css_cache:
            font_family, css = css_cache[font_basename]
        else:
            if font_basename and os.path.exists(os.path.join(FONTS_FOLDER, font_basename)):
                safe_name = f"hb_{abs(hash(font_basename))}"
                font_family = safe_name
                css = (
                    f"@font-face {{ font-family: '{safe_name}'; src: url('{font_basename}'); }}\n"
                    "body { margin: 0; padding: 0; }\n"
                )
            else:
                font_family = "sans-serif"
                css = "body { margin: 0; padding: 0; }\n"
            css_cache[font_basename] = (font_family, css)

        r, g, b = run["color_rgb"]
        direction = "rtl" if run.get("direction") == "rtl" else "ltr"
        align = "right" if run.get("align") == "right" else "left"
        font_size_pt = float(run.get("font_size_pt", 10.0))
        text = html.escape(run.get("text", ""))

        html_text = (
            "<div "
            f"style=\"font-family:'{font_family}';"
            f"font-size:{font_size_pt:.2f}pt;"
            "line-height:1.1;"
            f"color:rgb({r},{g},{b});"
            f"direction:{direction};text-align:{align};"
            "white-space:pre-wrap;\">"
            f"{text}</div>"
        )

        try:
            page.insert_htmlbox(rect, html_text, css=css, archive=archive, scale_low=0)
        except Exception as hb_exc:
            logger.warning("HB overlay insert failed (page=%s): %s", page_index, hb_exc)

    out = _corel_safe_pdf_bytes(doc, garbage=4, clean=False)
    doc.close()
    return out

# Refactored module imports — these provide cleaner organizational structure.
# The original function definitions above shadow these imports at runtime,
# preserving exact backward-compatible behavior.
#
# PDF cleanup / sanitization functions:
# from app.services.corel_pdf_cleanup import (
#     _corel_safe_pdf_bytes, _is_valid_pdf_bytes,
#     _strip_marked_content_operators, _strip_page_level_pdf_keys,
#     _strip_optional_content_pypdf_page, _save_pikepdf_corel,
#     _normalize_pdf_for_corel, _rebuild_optional_content_catalog,
#     _flatten_optional_content_pdf_bytes, _aggressive_corel_flatten,
#     _make_corel_friendly, _template_pdf_has_corel_hostile_features,
#     _rasterize_template_pdf_for_editable_overlay,
# )
#
# Translation functions:
# from app.services.corel_translation import (
#     _detect_translation_source_language, _should_skip_translation,
#     _extract_google_translate_text, _google_translate_text,
#     _translate_value_for_export, _normalize_language,
# )
#
# Text shaping / BiDi functions:
# from app.services.corel_text import (
#     _contains_arabic_script, _safe_bidi_get_display, _clean_bidi_controls,
#     process_text_for_vector, _normalize_grow_mode,
#     _x_for_direction, _x_for_direction_raster,
# )
