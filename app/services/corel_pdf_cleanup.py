"""
PDF cleanup and sanitization functions for CorelDRAW export.

Extracted from app/services/corel_export_service.py — handles PDF byte-level
cleanup, optional content flattening, transparency removal, and rasterization.

USAGE: These functions are identical copies of those in corel_export_service.py.
The original definitions shadow these imports at runtime.
"""
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
