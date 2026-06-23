"""
Face detection and photo cropping service.

Provides MediaPipe-based face detection and center-crop fallback.
Extracted from legacy_app.py.
"""

import logging
import threading

import numpy as np
from PIL import Image

from app.services.photo_service import _process_photo_pil

logger = logging.getLogger(__name__)

# Lazy import mediapipe — only loaded when face detection is actually used
_mp_face = None

def _get_mediapipe_face():
    """Lazily import and return MediaPipe face detection module."""
    global _mp_face
    if _mp_face is not None:
        return _mp_face
    try:
        import mediapipe as mp
        _mp_face = mp.solutions.face_detection
    except Exception as e:
        logger.warning("MediaPipe face detection disabled: %s", e)
        _mp_face = None
    return _mp_face

_detector_lock = threading.Lock()


def _get_face_detector():
    """Create a MediaPipe FaceDetection instance."""
    face_module = _get_mediapipe_face()
    if face_module is None:
        return None
    try:
        return face_module.FaceDetection(model_selection=0, min_detection_confidence=0.5)
    except Exception as e:
        logger.warning("Error initializing MediaPipe face detector: %s", e)
        return None


def _fallback_center_crop(pil_img, save_path, target_w, target_h):
    """Save a center-cropped photo, respecting EXIF rotation."""
    final = _process_photo_pil(pil_img, target_width=target_w, target_height=target_h)
    if final.mode == "RGBA":
        rgb = Image.new("RGB", final.size, (255, 255, 255))
        rgb.paste(final, mask=final.getchannel("A"))
        final = rgb
    elif final.mode != "RGB":
        final = final.convert("RGB")
    final.save(save_path, "JPEG", quality=95)
    return True


def _crop_with_padding(pil_img, crop_box, fill_rgb=(255, 255, 255)):
    """Crop a region from an image, padding with fill color if needed."""
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


def _center_crop_box(img_w, img_h, target_ratio):
    """Calculate a center crop box for a target aspect ratio."""
    if img_w <= 0 or img_h <= 0:
        return (0, 0, max(1, img_w), max(1, img_h))
    current_ratio = float(img_w) / float(img_h)
    if current_ratio > target_ratio:
        crop_w = max(1, int(round(img_h * target_ratio)))
        left = int(round((img_w - crop_w) / 2.0))
        return (left, 0, left + crop_w, img_h)
    crop_h = max(1, int(round(img_w / target_ratio)))
    top = int(round((img_h - crop_h) / 2.0))
    return (0, top, img_w, top + crop_h)


def _detect_face_crop_box(pil_img, target_width, target_height):
    """Detect a face in the image and return a crop box around it."""
    try:
        rgb_img = pil_img.convert("RGB")
        img_np = np.array(rgb_img)
        h_orig, w_orig = img_np.shape[:2]
        if h_orig <= 0 or w_orig <= 0:
            return None

        with _detector_lock:
            detector = _get_face_detector()
            if detector is None:
                return None
            try:
                results = detector.process(img_np.copy())
            finally:
                try:
                    detector.close()
                except Exception:
                    pass

        if not results or not results.detections:
            return None

        detection = max(results.detections, key=lambda d: d.score[0])
        box = detection.location_data.relative_bounding_box
        face_h = max(1, int(box.height * h_orig))
        face_cx = int((box.xmin + (box.width / 2.0)) * w_orig)
        face_cy = int((box.ymin + (box.height / 2.0)) * h_orig)

        target_ratio = float(target_width) / float(max(1, target_height))
        face_to_image_ratio = 0.45
        face_center_y_ratio = 0.51
        crop_h = max(1, int(round(face_h / face_to_image_ratio)))
        crop_w = max(1, int(round(crop_h * target_ratio)))

        x1 = face_cx - (crop_w // 2)
        y1 = face_cy - int(round(crop_h * face_center_y_ratio))
        return (x1, y1, x1 + crop_w, y1 + crop_h)
    except Exception as exc:
        logger.warning("Face detection crop fallback triggered: %s", exc)
        return None
