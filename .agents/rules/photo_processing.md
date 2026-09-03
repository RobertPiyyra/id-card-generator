# Photo Processing & Face Detection Standards

When processing, cropping, or rendering student photos for ID cards:

1. **Unified Service Invocation**:
   - Always route face detection calls through `app/services/face_service.py`.
   - Never create duplicate detector initializations in helpers without active MediaPipe loading.

2. **Detection & Framing Ratios**:
   - Short-range (`model_selection=0`) + Full-range (`model_selection=1`) fallback with `min_detection_confidence=0.3`.
   - Primary Subject Selection: Use bounding box area (`width * height`) to pick the main subject over background faces.
   - Ideal ID Framing Ratios:
     - `face_to_image_ratio = 0.38` (head-to-frame proportions).
     - `face_center_y_ratio = 0.40` (headroom preservation, zero scalp/hair clipping).

3. **Single-Pass Resampling**:
   - After `_crop_with_padding()`, resize the image directly using `Image.Resampling.LANCZOS` (`resize((target_w, target_h), Image.Resampling.LANCZOS)`).
   - DO NOT call `ImageOps.fit()` after padding crop, as it executes a redundant second crop.
