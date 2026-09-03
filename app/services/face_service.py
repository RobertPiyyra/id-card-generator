"""
Face detection and photo cropping service (Production version).

- MediaPipe short-range + full-range detection
- Largest-face selection
- Aspect-ratio aware headroom / composition
- Safe padding + center-crop fallback
- Never raises – always returns a valid image
"""

import logging
import threading
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageOps, ImageFilter

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Lazy MediaPipe loading + thread-safe detectors
# -------------------------------------------------
_mp_face = None
# Face detection can call the detector factory while holding this lock.  Use a
# re-entrant lock so the lazy factory remains safe without self-deadlocking.
_detector_lock = threading.RLock()
_detector_short = None
_detector_full = None


def _get_mediapipe_face():
    global _mp_face
    if _mp_face is not None:
        return _mp_face
    try:
        import mediapipe as mp
        _mp_face = mp.solutions.face_detection
    except Exception as e:
        logger.warning("MediaPipe not available – face detection disabled: %s", e)
        _mp_face = False          # mark as permanently unavailable
    return _mp_face if _mp_face is not False else None


def _get_face_detectors():
    global _detector_short, _detector_full
    face_module = _get_mediapipe_face()
    if face_module is None:
        return None, None

    with _detector_lock:
        try:
            if _detector_short is None:
                _detector_short = face_module.FaceDetection(
                    model_selection=0,          # short-range (closer faces)
                    min_detection_confidence=0.35
                )
            if _detector_full is None:
                _detector_full = face_module.FaceDetection(
                    model_selection=1,          # full-range
                    min_detection_confidence=0.35
                )
        except Exception as e:
            logger.warning("Failed to create MediaPipe detectors: %s", e)
            return None, None
    return _detector_short, _detector_full


def _get_face_detector():
    """Backward-compatible short-range detector accessor for legacy callers."""
    detector_short, _ = _get_face_detectors()
    return detector_short


# -------------------------------------------------
# Helper functions
# -------------------------------------------------
def _crop_with_padding(pil_img: Image.Image, crop_box, fill_rgb=(255, 255, 255)) -> Image.Image:
    """Crop a region, padding with solid color if the box goes outside the image."""
    x1, y1, x2, y2 = [int(round(v)) for v in crop_box]
    crop_w = max(1, x2 - x1)
    crop_h = max(1, y2 - y1)

    src = pil_img.convert("RGB")
    canvas = Image.new("RGB", (crop_w, crop_h), fill_rgb)

    src_x1 = max(0, x1)
    src_y1 = max(0, y1)
    src_x2 = min(src.width, x2)
    src_y2 = min(src.height, y2)

    if src_x2 <= src_x1 or src_y2 <= src_y1:
        return canvas

    region = src.crop((src_x1, src_y1, src_x2, src_y2))
    paste_x = max(0, -x1)
    paste_y = max(0, -y1)
    canvas.paste(region, (paste_x, paste_y))
    return canvas


def _center_crop_box(img_w: int, img_h: int, target_ratio: float) -> Tuple[float, float, float, float]:
    """Simple center crop box for a given aspect ratio."""
    if img_w <= 0 or img_h <= 0:
        return (0, 0, max(1, img_w), max(1, img_h))

    current_ratio = img_w / img_h
    if current_ratio > target_ratio:
        crop_w = max(1, int(round(img_h * target_ratio)))
        left = (img_w - crop_w) / 2.0
        return (left, 0, left + crop_w, img_h)
    else:
        crop_h = max(1, int(round(img_w / target_ratio)))
        top = (img_h - crop_h) / 2.0
        return (0, top, img_w, top + crop_h)


def _fit_crop_box_to_image(crop_box, img_w, img_h, target_ratio):
    """Keep the crop inside the image while preserving aspect ratio and focus point."""
    if img_w <= 0 or img_h <= 0 or target_ratio <= 0:
        return crop_box

    x1, y1, x2, y2 = crop_box
    focus_x = (x1 + x2) / 2.0
    focus_y = (y1 + y2) / 2.0

    requested_h = max(1.0, y2 - y1)
    requested_w = requested_h * target_ratio

    # Never enlarge beyond the available image
    max_w = min(float(img_w), float(img_h) * target_ratio)
    crop_w = min(requested_w, max_w)
    crop_h = crop_w / target_ratio

    left = min(max(0.0, focus_x - crop_w / 2.0), img_w - crop_w)
    top  = min(max(0.0, focus_y - crop_h / 2.0), img_h - crop_h)

    return (left, top, left + crop_w, top + crop_h)


def _face_crop_preferences(target_ratio: float):
    """
    Return (face_to_crop_height_ratio, vertical_anchor)
    Tuned for typical ID-card photo frames.
    """
    if target_ratio <= 0.90:          # classic portrait (e.g. 260×324)
        # Shifted down further by 5 points
        return 0.50, 0.46
    if target_ratio <= 1.15:          # near-square
        return 0.54, 0.38
    return 0.46, 0.40                 # landscape


def _detect_face_crop_box(pil_img: Image.Image, target_width: int, target_height: int):
    """Return a face-guided crop box or None if no usable face is found."""
    try:
        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        h_orig, w_orig = img_np.shape[:2]
        if h_orig < 32 or w_orig < 32:          # too small to detect reliably
            return None

        results = None
        with _detector_lock:
            d_short, d_full = _get_face_detectors()
            if d_short is not None:
                results = d_short.process(img_np.copy())
            if (not results or not results.detections) and d_full is not None:
                results = d_full.process(img_np.copy())

        if not results or not results.detections:
            return None

        # Choose the largest face (by area)
        detection = max(
            results.detections,
            key=lambda d: (
                d.location_data.relative_bounding_box.width *
                d.location_data.relative_bounding_box.height
            )
        )

        box = detection.location_data.relative_bounding_box
        face_h = max(1, int(box.height * h_orig))
        face_cx = int((box.xmin + box.width / 2.0) * w_orig)
        face_cy = int((box.ymin + box.height / 2.0) * h_orig)

        target_ratio = float(target_width) / float(max(1, target_height))
        face_to_image_ratio, face_center_y_ratio = _face_crop_preferences(target_ratio)

        crop_h = max(1, int(round(face_h / face_to_image_ratio)))
        crop_w = max(1, int(round(crop_h * target_ratio)))

        x1 = face_cx - (crop_w // 2)
        y1 = face_cy - int(round(crop_h * face_center_y_ratio))

        return _fit_crop_box_to_image(
            (x1, y1, x1 + crop_w, y1 + crop_h),
            w_orig, h_orig, target_ratio
        )
    except Exception as e:
        logger.warning("Face detection failed – falling back to center crop: %s", e)
        return None


# -------------------------------------------------
# Public API
# -------------------------------------------------
def process_face_crop_pil(
    pil_img: Image.Image,
    target_width: int = 260,
    target_height: int = 313,
    fill_rgb=(255, 255, 255),
    sharpen: bool = True
) -> Image.Image:
    """
    Main entry point – always returns a correctly sized RGB image.
    Never raises.
    """
    try:
        pil_img.load()
        img = pil_img.copy()

        # Fix phone orientation
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        target_w = max(1, int(target_width))
        target_h = max(1, int(target_height))
        target_ratio = target_w / target_h

        base = img.convert("RGB")

        # 1. Try face-guided crop
        crop_box = _detect_face_crop_box(base, target_w, target_h)

        # 2. Fallback to center crop
        if crop_box is None:
            crop_box = _center_crop_box(base.width, base.height, target_ratio)

        # 3. Perform the crop (with padding if needed)
        cropped = _crop_with_padding(base, crop_box, fill_rgb=fill_rgb)

        # 4. Exact size
        if cropped.size != (target_w, target_h):
            cropped = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # 5. Optional light sharpen (makes ID photos look crisper)
        if sharpen:
            cropped = cropped.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))

        return cropped.convert("RGB")

    except Exception as e:
        logger.error("process_face_crop_pil failed completely: %s", e)
        # Ultimate safe fallback
        return Image.new("RGB", (max(1, target_width), max(1, target_height)), (220, 220, 220))


def fallback_center_crop(pil_img: Image.Image, save_path: str, target_w: int, target_h: int) -> bool:
    """Convenience helper that also saves the result (used by some legacy paths)."""
    try:
        result = process_face_crop_pil(pil_img, target_w, target_h, sharpen=False)
        ext = (save_path or "").rsplit(".", 1)[-1].lower()
        if ext == "webp":
            result.save(save_path, "WEBP", quality=90)
        else:
            result.save(save_path, "JPEG", quality=95, optimize=True)
        return True
    except Exception as e:
        logger.warning("fallback_center_crop failed: %s", e)
        return False


# Existing helper imports use this historical private name.
_fallback_center_crop = fallback_center_crop


# -------------------------------------------------
# Face Embedding & Duplicate Detection Helpers
# -------------------------------------------------
def compute_face_phash(pil_img: Image.Image) -> Optional[str]:
    """Compute a fast 64-bit difference hash (dHash) on normalized face/image."""
    try:
        # Resize to 9x8 grayscale
        gray = pil_img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        pixels = np.array(gray)
        # Compare adjacent pixels
        diff = pixels[:, 1:] > pixels[:, :-1]
        # Convert 64 boolean array to 16-hex character string
        return "{:016x}".format(int("".join(["1" if b else "0" for b in diff.flatten()]), 2))
    except Exception as e:
        logger.warning("compute_face_phash error: %s", e)
        return None


def compute_face_embedding(pil_img: Image.Image) -> Tuple[Optional[list], Optional[str]]:
    """
    Extracts a 128-dimensional normalized facial feature vector and a 64-bit face pHash.
    Returns (embedding_vector_as_list, face_phash_str) or (None, None).
    """
    try:
        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        h_orig, w_orig = img_np.shape[:2]
        if h_orig < 24 or w_orig < 24:
            return None, None

        results = None
        with _detector_lock:
            d_short, d_full = _get_face_detectors()
            if d_short is not None:
                results = d_short.process(img_np.copy())
            if (not results or not results.detections) and d_full is not None:
                results = d_full.process(img_np.copy())

        if not results or not results.detections:
            # Fallback embedding on normalized center region if no face detected
            center_chip = rgb.resize((64, 64), Image.Resampling.BILINEAR)
            phash = compute_face_phash(center_chip)
            arr = np.array(center_chip, dtype=np.float32).flatten()
            norm = np.linalg.norm(arr)
            if norm > 1e-6:
                arr = arr / norm
            # Reduce to 128-d via uniform downsampling
            sampled = arr[::len(arr) // 128][:128]
            sampled = sampled / (np.linalg.norm(sampled) + 1e-9)
            return sampled.tolist(), phash

        # Choose the largest detected face
        detection = max(
            results.detections,
            key=lambda d: (
                d.location_data.relative_bounding_box.width *
                d.location_data.relative_bounding_box.height
            )
        )

        box = detection.location_data.relative_bounding_box
        x1 = max(0, int(box.xmin * w_orig))
        y1 = max(0, int(box.ymin * h_orig))
        x2 = min(w_orig, int((box.xmin + box.width) * w_orig))
        y2 = min(h_orig, int((box.ymin + box.height) * h_orig))

        face_chip = rgb.crop((x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)))
        phash = compute_face_phash(face_chip)

        # 1. Keypoint geometry features
        geo_feats = []
        raw_kps = getattr(detection.location_data, 'relative_keypoints', None) or getattr(detection.location_data, 'keypoints', None)
        if raw_kps:
            kps = [(kp.x, kp.y) for kp in raw_kps]
            # Pairwise relative distances between detected keypoints
            for i in range(len(kps)):
                for j in range(i + 1, len(kps)):
                    dx = kps[i][0] - kps[j][0]
                    dy = kps[i][1] - kps[j][1]
                    dist = np.sqrt(dx * dx + dy * dy)
                    geo_feats.append(dist)
        
        # 2. Appearance features (Spatial 4x4 grid histograms & mean gradients)
        chip_resized = np.array(face_chip.resize((48, 48), Image.Resampling.BILINEAR), dtype=np.float32)
        grid_feats = []
        for r in range(4):
            for c in range(4):
                block = chip_resized[r*12:(r+1)*12, c*12:(c+1)*12]
                mean_r = np.mean(block[:, :, 0]) / 255.0
                mean_g = np.mean(block[:, :, 1]) / 255.0
                mean_b = np.mean(block[:, :, 2]) / 255.0
                std_lum = np.std(np.mean(block, axis=2)) / 255.0
                grid_feats.extend([mean_r, mean_g, mean_b, std_lum])

        # Combine into 128-d vector
        combined = np.array(geo_feats + grid_feats, dtype=np.float32)
        if len(combined) < 128:
            combined = np.pad(combined, (0, 128 - len(combined)), 'constant')
        else:
            combined = combined[:128]

        # L2-normalize
        norm = np.linalg.norm(combined)
        if norm > 1e-9:
            combined = combined / norm

        return combined.tolist(), phash

    except Exception as e:
        logger.warning("compute_face_embedding error: %s", e)
        return None, None


def calculate_face_similarity(vec1: list, vec2: list) -> float:
    """Calculates cosine similarity between two face embedding vectors (0.0 to 1.0)."""
    if not vec1 or not vec2:
        return 0.0
    try:
        a = np.array(vec1, dtype=np.float32)
        b = np.array(vec2, dtype=np.float32)
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a < 1e-9 or norm_b < 1e-9:
            return 0.0
        sim = float(dot / (norm_a * norm_b))
        return max(0.0, min(1.0, sim))
    except Exception as e:
        logger.warning("calculate_face_similarity error: %s", e)
        return 0.0
