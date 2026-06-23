import os
import io
import logging
import requests
import re
import base64
from PIL import Image, ImageOps

from models import db, Student
from utils import PLACEHOLDER_PATH, UPLOAD_FOLDER, STATIC_DIR, round_photo

from app.services.redis_service import _redis_cache_key, _redis_get, _redis_set

logger = logging.getLogger(__name__)





def split_photo_reference(photo_ref):
    """Split a stored photo reference into a remote URL or local upload path."""
    value = str(photo_ref or "").strip()
    if not value or value == "placeholder.jpg":
        return None, None
    if value.startswith(("http://", "https://")):
        return value, None
    normalized = value.replace("\\", "/")
    if os.path.isabs(value) and os.path.exists(value):
        return None, value
    if normalized.startswith("/static/"):
        local_candidate = os.path.join(STATIC_DIR, normalized[len("/static/"):])
        if os.path.exists(local_candidate):
            return None, local_candidate
    if normalized.startswith("static/"):
        local_candidate = os.path.join(STATIC_DIR, normalized[len("static/"):])
        if os.path.exists(local_candidate):
            return None, local_candidate
    if normalized.startswith("uploads/") or normalized.startswith("Uploads/"):
        local_candidate = os.path.join(STATIC_DIR, normalized)
        if os.path.exists(local_candidate):
            return None, local_candidate
    if os.path.exists(normalized):
        return None, normalized
    return None, os.path.join(UPLOAD_FOLDER, value)


def resolve_student_photo_reference(student):
    """
    Resolve the best available photo source for a student.

    Supports:
    - `photo_url` for current cloud records
    - `photo_filename` containing a legacy local filename
    - `photo_filename` containing a legacy/bulk remote URL
    """
    image_url = str(getattr(student, "image_url", "") or "").strip()

    photo_url, local_path = split_photo_reference(getattr(student, "photo_url", None))
    if photo_url and image_url and photo_url == image_url:
        logger.warning(
            f"Student {getattr(student, 'id', 'unknown')} has photo_url == image_url; ignoring photo_url"
        )
        photo_url = None

    if photo_url or local_path:
        return photo_url, local_path

    fallback_url, fallback_local_path = split_photo_reference(getattr(student, "photo_filename", None))
    if fallback_url and image_url and fallback_url == image_url:
        logger.warning(
            f"Student {getattr(student, 'id', 'unknown')} has photo_filename URL equal to image_url; ignoring fallback URL"
        )
        fallback_url = None

    return fallback_url, fallback_local_path


def load_student_photo_rgba(student, width, height, timeout=10, photo_settings=None, allow_placeholder=True):
    """Backward-compatible wrapper around the shared student photo preparation flow."""
    return load_student_photo_rgba_prepared(
        student,
        width,
        height,
        timeout=timeout,
        photo_settings=photo_settings,
        allow_placeholder=allow_placeholder,
    )


def load_student_photo_rgba_prepared(
    student,
    width,
    height,
    timeout=10,
    photo_settings=None,
    allow_placeholder=True,
):
    """
    Load and normalize a student photo for a target frame.

    This is the single source of truth for card photo behavior across previews,
    generated cards, bulk output, and PDF/Corel exports.
    """
    photo_settings = photo_settings or {}
    image_open = getattr(Image, "open_original", Image.open)
    photo_url, local_path = resolve_student_photo_reference(student)
    prepared_cache = getattr(student, "_prepared_photo_cache", None)
    cache_key = None
    if isinstance(prepared_cache, dict):
        cache_source = photo_url or local_path or ("__placeholder__" if allow_placeholder else "__none__")
        cache_key = (
            str(cache_source),
            int(width or 0),
            int(height or 0),
            bool(allow_placeholder),
        )
        cached_payload = prepared_cache.get(cache_key)
        if cached_payload:
            cached_img = image_open(io.BytesIO(cached_payload))
            cached_img.load()
            return cached_img.convert("RGBA")

    def _load_detached_image(image_bytes):
        photo_img = image_open(io.BytesIO(image_bytes))
        photo_img.load()
        return photo_img.copy()

    try:
        photo_img = None
        if photo_url:
            logger.info(f"Loading student photo from URL: {photo_url}")
            max_retries = 3
            import time
            for attempt in range(max_retries):
                try:
                    response = requests.get(photo_url, timeout=timeout)
                    response.raise_for_status()
                    photo_img = _load_detached_image(response.content)
                    break
                except (requests.exceptions.RequestException, Exception) as req_exc:
                    if attempt == max_retries - 1:
                        logger.warning(f"Failed to load student photo from URL after {max_retries} attempts: {req_exc}")
                    else:
                        logger.warning(f"Error loading student photo from URL (attempt {attempt + 1}/{max_retries}): {req_exc}. Retrying...")
                        time.sleep(1 * (attempt + 1))

        if photo_img is None and local_path and os.path.exists(local_path):
            try:
                if os.path.getsize(local_path) <= 0:
                    logger.warning("Student photo file is empty: %s", local_path)
                    local_path = None
            except OSError:
                local_path = None

        if photo_img is None and local_path and os.path.exists(local_path):
            logger.info(f"Loading student photo from local path: {local_path}")
            with open(local_path, "rb") as fh:
                photo_img = _load_detached_image(fh.read())

        if photo_img is None and allow_placeholder and os.path.exists(PLACEHOLDER_PATH):
            logger.info(f"Loading placeholder photo: {PLACEHOLDER_PATH}")
            with open(PLACEHOLDER_PATH, "rb") as fh:
                photo_img = _load_detached_image(fh.read())

        if photo_img is None:
            logger.error("No photo source available and placeholder not allowed or missing")
            return None

        prepared_img = _process_photo_pil(
            photo_img,
            target_width=width,
            target_height=height,
        )
        if prepared_img is None:
            return None
        prepared_img.load()
        prepared_img = prepared_img.copy()
        if cache_key and isinstance(prepared_cache, dict):
            cache_buffer = io.BytesIO()
            prepared_img.save(cache_buffer, format="PNG")
            prepared_cache[cache_key] = cache_buffer.getvalue()
        return prepared_img
    except Exception as exc:
        logger.warning("Unable to prepare student photo: %s", exc)
        return None


def photo_match_aliases(value):
    """
    Build forgiving lookup aliases for bulk photo matching.

    Supports matching by:
    - exact filename stem
    - basename from `photo_path` values like `photos/john.jpg`
    - names with spaces/underscores/hyphens normalized
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return set()

    normalized_path = raw.replace("\\", "/")
    basename = os.path.basename(normalized_path)
    stem = os.path.splitext(basename)[0]
    path_stem = os.path.splitext(normalized_path)[0]

    aliases = set()
    for item in {raw, normalized_path, basename, stem, path_stem}:
        cleaned = str(item or "").strip().lower().strip("./")
        if not cleaned:
            continue
        aliases.add(cleaned)

        compact = re.sub(r"[\s_\-]+", "", cleaned)
        if compact:
            aliases.add(compact)

        underscore = re.sub(r"[\s\-]+", "_", cleaned).strip("_")
        if underscore:
            aliases.add(underscore)

        spaced = re.sub(r"[_\-]+", " ", cleaned)
        spaced = re.sub(r"\s+", " ", spaced).strip()
        if spaced:
            aliases.add(spaced)

    return aliases


def auto_crop_face_photo(photo_path, target_width=260, target_height=313):
    """
    Normalize a photo file in-place using the shared card photo behavior.
    """
    try:
        image_open = getattr(Image, "open_original", Image.open)
        pil_img = image_open(photo_path)
        final_img = _process_photo_pil(
            pil_img,
            target_width=target_width,
            target_height=target_height,
        )
        if final_img.mode == "RGBA":
            rgb = Image.new("RGB", final_img.size, (255, 255, 255))
            rgb.paste(final_img, mask=final_img.getchannel("A"))
            final_img = rgb
        elif final_img.mode != "RGB":
            final_img = final_img.convert("RGB")
        final_img.save(photo_path, "JPEG", quality=95, subsampling=0)
        return True

    except Exception as e:
        logger.exception(f"Smart crop failed: {e}")
        try:
            from app.services.face_service import _fallback_center_crop
            image_open = getattr(Image, "open_original", Image.open)
            return _fallback_center_crop(image_open(photo_path), photo_path, target_width, target_height)
        except Exception:
            return False


def _process_photo_pil(pil_img, target_width=260, target_height=313, cache_key_extra=None):
    """
    Normalize a student photo to the requested card frame and return RGBA.
    Now includes Redis caching for performance.
    """
    try:
        # 🔑 Build cache key
        try:
            img_bytes = pil_img.tobytes()
        except Exception:
            buf_tmp = io.BytesIO()
            pil_img.save(buf_tmp, format="PNG")
            img_bytes = buf_tmp.getvalue()

        cache_key = _redis_cache_key(
            "processed_photo",
            img_bytes,
            target_width,
            target_height,
            cache_key_extra
        )

        # 🔍 Try cache
        cached = _redis_get(cache_key)
        if cached:
            try:
                return Image.open(io.BytesIO(cached)).convert("RGBA")
            except Exception:
                pass

        from app.services.face_service import (
            _detect_face_crop_box,
            _center_crop_box,
            _crop_with_padding,
        )

        pil_img.load()
        pil_img = pil_img.copy()

        try:
            pil_img = ImageOps.exif_transpose(pil_img)
        except Exception:
            pass

        target_width = max(1, int(target_width or 1))
        target_height = max(1, int(target_height or 1))

        base_img = pil_img.convert("RGB")
        fill_rgb = (255, 255, 255)
        target_ratio = float(target_width) / float(target_height)

        crop_box = _detect_face_crop_box(base_img, target_width, target_height)
        if crop_box is None:
            crop_box = _center_crop_box(base_img.width, base_img.height, target_ratio)

        cropped_img = _crop_with_padding(base_img, crop_box, fill_rgb=fill_rgb)

        result_img = cropped_img.convert("RGBA")

        if result_img.size != (target_width, target_height):
            result_img = ImageOps.fit(result_img, (target_width, target_height), Image.Resampling.LANCZOS)

        # 💾 Save to Redis
        try:
            buf = io.BytesIO()
            result_img.save(buf, format="PNG", optimize=True, compress_level=6)
            _redis_set(cache_key, buf.getvalue())
        except Exception as e:
            logger.warning(f"Photo cache save failed: {e}")

        return result_img

    except Exception as e:
        logger.warning(f"Photo processing failed: {e}, returning original")

        fallback = pil_img.convert("RGBA") if pil_img.mode != "RGBA" else pil_img.copy()

        if fallback.size != (max(1, int(target_width or 1)), max(1, int(target_height or 1))):
            fallback = ImageOps.fit(
                fallback,
                (max(1, int(target_width or 1)), max(1, int(target_height or 1))),
                Image.Resampling.LANCZOS
            )

        return fallback


def _prepare_uploaded_student_photo_bytes(file_storage, photo_settings=None):
    """
    Validate and normalize an uploaded student photo to JPEG bytes.
    Falls back to a normalized original if smart processing fails.
    """
    from app.services.file_service import _read_uploaded_file_bytes
    photo_settings = photo_settings or {}
    raw_bytes = _read_uploaded_file_bytes(file_storage, file_label="photo")
    return _prepare_student_photo_image_bytes(
        raw_bytes,
        photo_settings=photo_settings,
        source_label=getattr(file_storage, "filename", "uploaded photo") or "uploaded photo",
    )


def _prepare_student_photo_image_bytes(raw_bytes, photo_settings=None, source_label="photo"):
    """Validate and normalize raw student photo bytes to JPEG bytes."""
    photo_settings = photo_settings or {}
    raw_bytes = raw_bytes if isinstance(raw_bytes, bytes) else bytes(raw_bytes or b"")
    if not raw_bytes:
        raise ValueError("Uploaded photo is empty. Please choose the image again.")
    try:
        source_img = Image.open(io.BytesIO(raw_bytes))
        source_img.load()
    except Exception as exc:
        raise ValueError(f"Uploaded photo is not a valid image: {exc}") from exc

    source_img = ImageOps.exif_transpose(source_img).convert("RGB")
    try:
        processed_img = _process_photo_pil(
            source_img,
            target_width=photo_settings.get("photo_width", 260),
            target_height=photo_settings.get("photo_height", 313),
        )
        if processed_img is None:
            processed_img = source_img
    except Exception as exc:
        logger.warning("Failed to process uploaded photo '%s': %s", source_label, exc)
        processed_img = source_img

    if processed_img.mode == "RGBA":
        flattened = Image.new("RGB", processed_img.size, (255, 255, 255))
        flattened.paste(processed_img, mask=processed_img.getchannel("A"))
        processed_img = flattened
    elif processed_img.mode != "RGB":
        processed_img = processed_img.convert("RGB")

    output = io.BytesIO()
    processed_img.save(output, format="JPEG", quality=95)
    processed_bytes = output.getvalue()
    if not processed_bytes:
        raise ValueError("Processed photo is empty after conversion. Please try another image.")
    return processed_bytes


def _prepare_camera_student_photo_bytes(photo_data, photo_settings=None):
    """Decode the camera canvas data URL and normalize it like a regular uploaded photo."""
    raw_value = str(photo_data or "").strip()
    if not raw_value:
        raise ValueError("Please capture a photo first.")
    if not raw_value.startswith("data:image"):
        raise ValueError("Captured photo data is invalid. Please retake the photo.")

    try:
        _, encoded = raw_value.split(",", 1)
        image_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Captured photo data is invalid. Please retake the photo.") from exc

    return _prepare_student_photo_image_bytes(
        image_bytes,
        photo_settings=photo_settings,
        source_label="camera capture",
    )
