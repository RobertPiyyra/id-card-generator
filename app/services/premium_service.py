import hashlib
import io
import json
from datetime import datetime, timezone

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from PIL import Image, ImageStat

from models import db, Template, TemplateField, Student
from utils import parse_layout_config, get_card_size, get_template_path, load_template_smart


def _rect_overlap(a, b):
    return not (a["x2"] <= b["x1"] or a["x1"] >= b["x2"] or a["y2"] <= b["y1"] or a["y1"] >= b["y2"])


def run_design_qa(template):
    issues = []
    card_w, card_h = get_card_size(template.id)
    for side in ("front", "back"):
        layout_raw = template.back_layout_config if side == "back" else template.layout_config
        fs = (template.back_font_settings if side == "back" else template.font_settings) or {}
        parsed = parse_layout_config(layout_raw) or {}
        fields = parsed.get("fields") if isinstance(parsed, dict) else {}
        if not isinstance(fields, dict):
            fields = {}

        boxes = []
        for key, cfg in fields.items():
            if not isinstance(cfg, dict):
                continue
            label = cfg.get("label") or {}
            value = cfg.get("value") or {}
            for part_name, part, fallback_size, width_factor in (
                ("label", label, int(fs.get("label_font_size", 32) or 32), 9),
                ("value", value, int(fs.get("value_font_size", 30) or 30), 10),
            ):
                if part.get("visible") is False:
                    continue
                x = int(part.get("x", 0) or 0)
                y = int(part.get("y", 0) or 0)
                fsz = int(part.get("font_size", fallback_size) or fallback_size)
                w = max(40, int(len(str(key)) * max(6, fsz // 2) * width_factor / 10))
                h = max(12, int(fsz * 1.35))
                box = {"name": f"{side}:{key}:{part_name}", "x1": x, "y1": y, "x2": x + w, "y2": y + h}
                boxes.append(box)
                if x < 0 or y < 0 or box["x2"] > card_w or box["y2"] > card_h:
                    issues.append({"severity": "error", "type": "out_of_bounds", "message": f"{box['name']} is out of card bounds"})

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _rect_overlap(boxes[i], boxes[j]):
                    issues.append({"severity": "warning", "type": "overlap", "message": f"{boxes[i]['name']} overlaps {boxes[j]['name']}"})

    required_fields = TemplateField.query.filter_by(template_id=template.id, is_required=True).all()
    for field in required_fields:
        if not any([field.show_label_front, field.show_value_front, field.show_label_back, field.show_value_back]):
            issues.append({"severity": "error", "type": "hidden_mandatory", "message": f"Required field '{field.field_label}' is hidden on all sides"})

    missing_photo = (
        db.session.query(Student.id)
        .filter(Student.template_id == template.id)
        .filter((Student.photo_filename.is_(None)) & (Student.photo_url.is_(None)))
        .count()
    )
    if missing_photo > 0:
        issues.append({"severity": "warning", "type": "missing_photo", "message": f"{missing_photo} student(s) have no photo"})

    # Low-contrast heuristic using template brightness vs configured text colors.
    try:
        tpath = get_template_path(template.id, side="front")
        if tpath:
            img = load_template_smart(tpath).convert("RGB")
            avg = ImageStat.Stat(img).mean
            bg_luma = (0.2126 * avg[0] + 0.7152 * avg[1] + 0.0722 * avg[2]) / 255.0
            font = template.font_settings or {}
            label = font.get("label_font_color", [0, 0, 0]) or [0, 0, 0]
            fg_luma = (0.2126 * label[0] + 0.7152 * label[1] + 0.0722 * label[2]) / 255.0
            contrast = (max(bg_luma, fg_luma) + 0.05) / (min(bg_luma, fg_luma) + 0.05)
            if contrast < 2.2:
                issues.append({"severity": "warning", "type": "low_contrast", "message": f"Front text/background contrast is low ({contrast:.2f})"})
    except Exception:
        pass

    score = max(0, 100 - len([x for x in issues if x["severity"] == "error"]) * 20 - len([x for x in issues if x["severity"] == "warning"]) * 8)
    return {"score": score, "issues": issues, "ok": not any(i["severity"] == "error" for i in issues)}


def _serializer(secret_key):
    return URLSafeTimedSerializer(secret_key, salt="verify-v2")


def build_signed_verify_token(secret_key, student_id, template_id, token_id, issued_at=None):
    issued = issued_at or datetime.now(timezone.utc).isoformat()
    payload = {"sid": int(student_id), "tid": int(template_id), "jti": str(token_id), "iat": issued}
    raw = json.dumps(payload, sort_keys=True)
    payload["sig"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return _serializer(secret_key).dumps(payload)


def parse_signed_verify_token(secret_key, token, max_age_seconds):
    try:
        payload = _serializer(secret_key).loads(token, max_age=max_age_seconds)
    except SignatureExpired:
        return None, "expired"
    except BadSignature:
        return None, "invalid"

    sig = payload.get("sig")
    check_src = {k: v for k, v in payload.items() if k != "sig"}
    raw = json.dumps(check_src, sort_keys=True)
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    if sig != expected:
        return None, "tampered"
    return payload, "ok"


def simple_photo_quality_score(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        stat = ImageStat.Stat(img)
        brightness = sum(stat.mean) / 3.0
        score = 50
        if w >= 300 and h >= 300:
            score += 20
        if 40 <= brightness <= 215:
            score += 20
        if stat.stddev and (sum(stat.stddev) / 3.0) > 30:
            score += 10
        score = max(0, min(100, int(score)))
        status = "pass" if score >= 70 else "fail"
        return score, status
    except Exception:
        return 0, "fail"
