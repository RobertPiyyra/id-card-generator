"""
Student Photo Service – Production Version

Handles:
- Resolving photo sources (Cloudinary URL / local file / legacy)
- Safe downloading with retries
- Face-aware cropping via face_service
- Redis + in-memory caching
- Upload & Camera photo normalization
- Never crashes the card generation pipeline
"""

import os
import io
import re
import time
import base64
import logging
from typing import Optional, Tuple, Set

from PIL import Image, ImageOps
import requests

from models import Student
from utils import PLACEHOLDER_PATH, UPLOAD_FOLDER, STATIC_DIR
from app.services.http_pool import http_get
from app.services.redis_service import _redis_cache_key, _redis_get, _redis_set
from app.services.face_service import process_face_crop_pil

logger = logging.getLogger(__name__)


# =========================================================
# 1. Photo Reference Resolution
# =========================================================

def split_photo_reference(photo_ref: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Split a stored photo reference into (remote_url, local_path).
    Returns (None, None) for invalid / placeholder values.
    """
    value = str(photo_ref or "").strip()
    if not value or value.lower() in {"placeholder.jpg", "none", "null", ""}:
        return None, None

    # Remote URL
    if value.startswith(("http://", "https://")):
        return value, None

    normalized = value.replace("\\", "/")

    # Absolute path
    if os.path.isabs(value) and os.path.isfile(value):
        return None, value

    # /static/... or static/...
    for prefix in ("/static/", "static/"):
        if normalized.startswith(prefix):
            candidate = os.path.join(STATIC_DIR, normalized[len(prefix):])
            if os.path.isfile(candidate):
                return None, candidate

    # uploads/ or Uploads/
    if normalized.lower().startswith(("uploads/", "Uploads/")):
        candidate = os.path.join(STATIC_DIR, normalized)
        if os.path.isfile(candidate):
            return None, candidate

    # Plain filename → look inside UPLOAD_FOLDER
    candidate = os.path.join(UPLOAD_FOLDER, value)
    if os.path.isfile(candidate):
        return None, candidate

    # Last attempt: relative path as-is
    if os.path.isfile(normalized):
        return None, normalized

    return None, None


def resolve_student_photo_reference(student) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve the best available photo source for a student.
    Protects against the classic photo_url == image_url recursion bug.
    """
    image_url = str(getattr(student, "image_url", "") or "").strip()

    # Primary source
    photo_url, local_path = split_photo_reference(getattr(student, "photo_url", None))
    if photo_url and image_url and photo_url == image_url:
        logger.warning(
            "Student %s has photo_url == image_url – ignoring photo_url",
            getattr(student, "id", "unknown")
        )
        photo_url = None

    if photo_url or local_path:
        return photo_url, local_path

    # Legacy fallback (photo_filename)
    fallback_url, fallback_local = split_photo_reference(getattr(student, "photo_filename", None))
    if fallback_url and image_url and fallback_url == image_url:
        logger.warning(
            "Student %s has photo_filename URL == image_url – ignoring",
            getattr(student, "id", "unknown")
        )
        fallback_url = None

    return fallback_url, fallback_local


# =========================================================
# 2. Main Loading Function (Single Source of Truth)
# =========================================================

def load_student_photo_rgba_prepared(
    student,
    width: int,
    height: int,
    timeout: int = 12,
    photo_settings: dict = None,
    allow_placeholder: bool = True,
) -> Optional[Image.Image]:
    """
    Load + normalize a student photo for the target frame.

    This is the single source of truth used by:
    - Live preview
    - Individual card generation
    - Bulk generation
    - PDF / Corel vector export
    """
    photo_settings = photo_settings or {}
    target_w = max(1, int(width or 260))
    target_h = max(1, int(height or 313))

    # ----- In-memory cache on student object -----
    prepared_cache = getattr(student, "_prepared_photo_cache", None)
    if not isinstance(prepared_cache, dict):
        prepared_cache = {}
        try:
            student._prepared_photo_cache = prepared_cache
        except Exception:
            pass

    photo_url, local_path = resolve_student_photo_reference(student)
    cache_source = photo_url or local_path or ("__placeholder__" if allow_placeholder else "__none__")
    cache_key = (str(cache_source), target_w, target_h, allow_placeholder)

    # Return from cache if available
    if cache_key in prepared_cache:
        try:
            cached = Image.open(io.BytesIO(prepared_cache[cache_key]))
            cached.load()
            return cached.convert("RGBA")
        except Exception:
            pass

    def _load_bytes(data: bytes) -> Image.Image:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.copy()

    photo_img = None

    # 1. Remote URL (with retries)
    if photo_url:
        for attempt in range(3):
            try:
                response = http_get(photo_url, timeout=timeout)
                response.raise_for_status()
                photo_img = _load_bytes(response.content)
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning("Failed to download photo after 3 attempts: %s", e)
                else:
                    time.sleep(0.7 * (attempt + 1))

    # 2. Local file
    if photo_img is None and local_path and os.path.isfile(local_path):
        try:
            if os.path.getsize(local_path) > 0:
                with open(local_path, "rb") as f:
                    photo_img = _load_bytes(f.read())
        except Exception as e:
            logger.warning("Failed to load local photo %s: %s", local_path, e)

    # 3. Placeholder
    if photo_img is None and allow_placeholder and os.path.isfile(PLACEHOLDER_PATH):
        try:
            with open(PLACEHOLDER_PATH, "rb") as f:
                photo_img = _load_bytes(f.read())
        except Exception as e:
            logger.error("Placeholder photo also failed: %s", e)

    # 4. Absolute last resort
    if photo_img is None:
        if allow_placeholder:
            photo_img = Image.new("RGB", (target_w, target_h), (220, 220, 220))
        else:
            return None

    # ----- Face-aware processing -----
    try:
        prepared = process_face_crop_pil(
            photo_img,
            target_width=target_w,
            target_height=target_h,
            fill_rgb=(255, 255, 255),
            sharpen=True,
        )
        prepared = prepared.convert("RGBA")
    except Exception as e:
        logger.warning("Face crop failed, falling back to simple resize: %s", e)
        prepared = photo_img.convert("RGBA")
        if prepared.size != (target_w, target_h):
            prepared = prepared.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Cache the result
    try:
        buf = io.BytesIO()
        prepared.save(buf, format="PNG", optimize=True)
        prepared_cache[cache_key] = buf.getvalue()
    except Exception:
        pass

    return prepared


def load_student_photo_rgba(
    student,
    width,
    height,
    timeout=10,
    photo_settings=None,
    allow_placeholder=True,
):
    """Backward-compatible wrapper."""
    return load_student_photo_rgba_prepared(
        student=student,
        width=width,
        height=height,
        timeout=timeout,
        photo_settings=photo_settings,
        allow_placeholder=allow_placeholder,
    )


# =========================================================
# 3. Internal Processing (with Redis cache)
# =========================================================

def _process_photo_pil(
    pil_img: Image.Image,
    target_width: int = 260,
    target_height: int = 313,
    cache_key_extra=None,
) -> Image.Image:
    """
    Normalize any PIL image to the target frame using face detection.
    Includes Redis caching.
    """
    try:
        # Build cache key
        try:
            img_bytes = pil_img.tobytes()
        except Exception:
            tmp = io.BytesIO()
            pil_img.save(tmp, format="PNG")
            img_bytes = tmp.getvalue()

        cache_key = _redis_cache_key(
            "processed_photo",
            img_bytes,
            target_width,
            target_height,
            cache_key_extra,
        )

        # Try Redis
        cached = _redis_get(cache_key)
        if cached:
            try:
                return Image.open(io.BytesIO(cached)).convert("RGBA")
            except Exception:
                pass

        # Process
        result = process_face_crop_pil(
            pil_img,
            target_width=target_width,
            target_height=target_height,
            fill_rgb=(255, 255, 255),
            sharpen=True,
        ).convert("RGBA")

        # Save to Redis
        try:
            buf = io.BytesIO()
            result.save(buf, format="PNG", optimize=True, compress_level=6)
            _redis_set(cache_key, buf.getvalue())
        except Exception as e:
            logger.warning("Redis photo cache write failed: %s", e)

        return result

    except Exception as e:
        logger.warning("Photo processing failed: %s", e)
        fallback = pil_img.convert("RGBA")
        target = (max(1, int(target_width)), max(1, int(target_height)))
        if fallback.size != target:
            fallback = fallback.resize(target, Image.Resampling.LANCZOS)
        return fallback


# =========================================================
# 4. Upload & Camera Helpers
# =========================================================

def _prepare_student_photo_image_bytes(
    raw_bytes: bytes,
    photo_settings: dict = None,
    source_label: str = "photo",
) -> bytes:
    """Validate and normalize raw image bytes → JPEG bytes."""
    photo_settings = photo_settings or {}
    if not raw_bytes:
        raise ValueError("Uploaded photo is empty. Please choose the image again.")

    try:
        source = Image.open(io.BytesIO(raw_bytes))
        source.load()
        source = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as e:
        raise ValueError(f"Uploaded photo is not a valid image: {e}") from e

    try:
        processed = _process_photo_pil(
            source,
            target_width=photo_settings.get("photo_width", 260),
            target_height=photo_settings.get("photo_height", 313),
        )
    except Exception as e:
        logger.warning("Failed to process uploaded photo '%s': %s", source_label, e)
        processed = source

    # Flatten to RGB
    if processed.mode == "RGBA":
        bg = Image.new("RGB", processed.size, (255, 255, 255))
        bg.paste(processed, mask=processed.getchannel("A"))
        processed = bg
    elif processed.mode != "RGB":
        processed = processed.convert("RGB")

    output = io.BytesIO()
    processed.save(output, format="JPEG", quality=95, optimize=True)
    result = output.getvalue()

    if not result:
        raise ValueError("Processed photo is empty after conversion.")
    return result


def _prepare_uploaded_student_photo_bytes(file_storage, photo_settings=None):
    """Handle normal file upload."""
    from app.services.file_service import _read_uploaded_file_bytes
    raw = _read_uploaded_file_bytes(file_storage, file_label="photo")
    return _prepare_student_photo_image_bytes(
        raw,
        photo_settings=photo_settings,
        source_label=getattr(file_storage, "filename", "upload") or "upload",
    )


def _prepare_camera_student_photo_bytes(photo_data, photo_settings=None):
    """Handle camera canvas data-URL."""
    raw_value = str(photo_data or "").strip()
    if not raw_value:
        raise ValueError("Please capture a photo first.")
    if not raw_value.startswith("data:image"):
        raise ValueError("Captured photo data is invalid. Please retake the photo.")

    try:
        _, encoded = raw_value.split(",", 1)
        image_bytes = base64.b64decode(encoded, validate=True)
    except Exception as e:
        raise ValueError("Captured photo data is invalid. Please retake the photo.") from e

    return _prepare_student_photo_image_bytes(
        image_bytes,
        photo_settings=photo_settings,
        source_label="camera capture",
    )


# =========================================================
# 5. Utility Helpers
# =========================================================

def photo_match_aliases(value: str) -> Set[str]:
    """Build forgiving aliases for bulk photo matching."""
    raw = str(value or "").strip().lower()
    if not raw:
        return set()

    normalized = raw.replace("\\", "/")
    basename = os.path.basename(normalized)
    stem = os.path.splitext(basename)[0]
    path_stem = os.path.splitext(normalized)[0]

    aliases = set()
    for item in {raw, normalized, basename, stem, path_stem}:
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


def auto_crop_face_photo(photo_path: str, target_width=260, target_height=313) -> bool:
    """Normalize a photo file in-place (used by some bulk tools)."""
    try:
        pil_img = Image.open(photo_path)
        final = _process_photo_pil(pil_img, target_width, target_height)

        if final.mode == "RGBA":
            rgb = Image.new("RGB", final.size, (255, 255, 255))
            rgb.paste(final, mask=final.getchannel("A"))
            final = rgb
        elif final.mode != "RGB":
            final = final.convert("RGB")

        final.save(photo_path, "JPEG", quality=95, subsampling=0)
        return True
    except Exception as e:
        logger.exception("auto_crop_face_photo failed: %s", e)
        try:
            from app.services.face_service import fallback_center_crop
            return fallback_center_crop(Image.open(photo_path), photo_path, target_width, target_height)
        except Exception:
            return False