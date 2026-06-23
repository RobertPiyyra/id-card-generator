"""
Photo and shape rendering functions for CorelDRAW export.

Extracted from app/services/corel_export_service.py — handles photo shape
points, ellipse/polygon paths, photo clipping, and frame drawing.

USAGE: These functions are identical copies of those in corel_export_service.py.
The original definitions shadow these imports at runtime.
"""
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



