import os
import io
import json
import math
import requests
from flask import Blueprint, send_file, session, redirect, url_for, current_app
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader, simpleSplit
from PIL import Image

# Import models and utils
from models import db, Student, Template, TemplateField
from utils import (
    UPLOAD_FOLDER, GENERATED_FOLDER, FONTS_FOLDER, 
    get_template_settings, get_template_path, get_card_size, 
    get_template_orientation, generate_qr_code, generate_data_hash,
    load_template
)
from utils import load_template_smart
corel_bp = Blueprint('corel', __name__)


def local_apply_text_case(text, case_type):
    if not text: return ""
    text = str(text)
    if case_type == "uppercase": return text.upper()
    elif case_type == "lowercase": return text.lower()
    elif case_type == "capitalize": return text.title()
    return text

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

@corel_bp.route("/download_compiled_vector_pdf/<int:template_id>")
def download_compiled_vector_pdf(template_id):
    if not session.get("admin"):
        return redirect(url_for("login"))

    try:
        # 1. Fetch Data
        template = db.session.get(Template, template_id)
        students = Student.query.filter_by(template_id=template_id).all()
        if not template or not students: return "No data found", 404

        # 2. Settings
        font_settings, photo_settings, qr_settings, orientation = get_template_settings(template_id)
        template_path = get_template_path(template_id)
        
        buffer = io.BytesIO()
        
        # =========================================================
        # 3. DYNAMIC DIMENSIONS & GRID
        # =========================================================
        # Get Dimensions from DB (Pixels @ 300 DPI)
        sheet_w_px = template.sheet_width if template.sheet_width else 2480
        sheet_h_px = template.sheet_height if template.sheet_height else 3508
        
        card_w_px = template.card_width if template.card_width else 1015
        card_h_px = template.card_height if template.card_height else 661

        # Get Grid Layout from DB
        cols = template.grid_cols if template.grid_cols else 2
        rows = template.grid_rows if template.grid_rows else 5

        # Scale Factor: 300 DPI -> 72 DPI (PDF Points)
        scale = 72.0 / 300.0
        
        sheet_w_pt = sheet_w_px * scale
        sheet_h_pt = sheet_h_px * scale
        card_w_pt = card_w_px * scale
        card_h_pt = card_h_px * scale
        gap_pt = 10 * scale
        
        # Calculate Layout & Centering
        total_grid_w_pt = (cols * card_w_pt) + ((cols - 1) * gap_pt)
        total_grid_h_pt = (rows * card_h_pt) + ((rows - 1) * gap_pt)
        
        start_x_pt = (sheet_w_pt - total_grid_w_pt) / 2
        bottom_margin = (sheet_h_pt - total_grid_h_pt) / 2
        start_y_pt = bottom_margin + total_grid_h_pt

        # Create Canvas
        c = canvas.Canvas(buffer, pagesize=(sheet_w_pt, sheet_h_pt))

        # --- REGISTER FONTS ---
        reg_font_name = f"Font_{template_id}_Reg"
        bold_font_name = f"Font_{template_id}_Bold"

        font_reg_file = font_settings.get("font_regular", "arial.ttf")
        font_bold_file = font_settings.get("font_bold", "arialbd.ttf")
        
        path_reg = os.path.join(FONTS_FOLDER, font_reg_file)
        path_bold = os.path.join(FONTS_FOLDER, font_bold_file)
        
        if not os.path.exists(path_reg): path_reg = os.path.join(FONTS_FOLDER, "arial.ttf")
        if not os.path.exists(path_bold): path_bold = os.path.join(FONTS_FOLDER, "arialbd.ttf")

        try:
            pdfmetrics.registerFont(TTFont(reg_font_name, path_reg))
            pdfmetrics.registerFont(TTFont(bold_font_name, path_bold))
        except:
            reg_font_name = "Helvetica"
            bold_font_name = "Helvetica-Bold"

        def get_rl_color(color_list):
            if not color_list or len(color_list) < 3: return Color(0,0,0)
            return Color(color_list[0]/255.0, color_list[1]/255.0, color_list[2]/255.0)

        l_color = get_rl_color(font_settings.get("label_font_color", [0,0,0]))
        v_color = get_rl_color(font_settings.get("value_font_color", [0,0,0]))

        lbl_size_pt = font_settings.get('label_font_size', 40) * scale
        val_size_pt = font_settings.get('value_font_size', 36) * scale
        
        # 6. Process Loop
        cards_per_sheet = cols * rows
        card_count = 0
        
        # PRELOAD BACKGROUND
        bg_image_reader = None
        if template_path:
            try:
                bg_pil = load_template_smart(template_path)
                if bg_pil.mode in ("RGBA", "LA"):
                    bg_rgb = Image.new("RGB", bg_pil.size, (255, 255, 255))
                    bg_rgb.paste(bg_pil, mask=bg_pil.split()[-1])
                    bg_pil = bg_rgb
                elif bg_pil.mode != "RGB":
                    bg_pil = bg_pil.convert("RGB")
                
                
                bg_stream = io.BytesIO()
                bg_pil.save(bg_stream, format="PNG")
                bg_stream.seek(0)
                bg_image_reader = ImageReader(bg_stream)
            except Exception as e:
                print(f"Background Preload Error: {e}")

        for student in students:
            idx_on_sheet = card_count % cards_per_sheet
            col_idx = idx_on_sheet % cols
            row_idx = idx_on_sheet // cols

            # Calculate Card Position
            card_x = start_x_pt + (col_idx * (card_w_pt + gap_pt))
            card_top_y = start_y_pt - (row_idx * (card_h_pt + gap_pt))
            card_bottom_y = card_top_y - card_h_pt

            # --- A. BACKGROUND ---
            c.setFillColor(Color(1, 1, 1))
            c.rect(card_x, card_bottom_y, card_w_pt, card_h_pt, fill=1, stroke=0)

            if bg_image_reader:
                try:
                    c.drawImage(bg_image_reader, card_x, card_bottom_y, width=card_w_pt, height=card_h_pt)
                except Exception as e:
                    print(f"Draw BG Error: {e}")

            # --- B. PHOTO ---
            # Support photo stored as Cloudinary URL (`photo_url`) or legacy local filename
            photo_bytes_io = None

            # 1️⃣ Prefer Cloudinary student photo (image_url)
            if student.image_url:
                try:
                    resp = requests.get(student.image_url, timeout=10)
                    if resp.status_code == 200:
                        photo_bytes_io = io.BytesIO(resp.content)
                except Exception:
                    photo_bytes_io = None
            
            # 2️⃣ Fallback: legacy local photo
            if photo_bytes_io is None and student.photo_filename:
                p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
                if os.path.exists(p_path):
                    with open(p_path, "rb") as fh:
                        photo_bytes_io = io.BytesIO(fh.read())
            

            if photo_bytes_io:
                px_px = photo_settings.get('photo_x', 0)
                py_px = photo_settings.get('photo_y', 0)
                pw_px = photo_settings.get('photo_width', 100)
                ph_px = photo_settings.get('photo_height', 100)
                
                photo_x = card_x + (px_px * scale)
                photo_y = card_bottom_y + (card_h_pt - (py_px * scale) - (ph_px * scale))
                photo_w = pw_px * scale
                photo_h = ph_px * scale

                r_tl = float(photo_settings.get('photo_border_top_left', 0)) * scale
                r_tr = float(photo_settings.get('photo_border_top_right', 0)) * scale
                r_br = float(photo_settings.get('photo_border_bottom_right', 0)) * scale
                r_bl = float(photo_settings.get('photo_border_bottom_left', 0)) * scale
                radii = [r_tl, r_tr, r_br, r_bl]

                c.saveState()
                if all(r == r_tl for r in radii) and r_tl > 0:
                    path = c.beginPath()
                    path.roundRect(photo_x, photo_y, photo_w, photo_h, r_tl)
                    c.clipPath(path, stroke=0)
                elif any(r > 0 for r in radii):
                    path = draw_custom_rounded_rect(c, photo_x, photo_y, photo_w, photo_h, radii)
                    c.clipPath(path, stroke=0)

                try:
                    reader = ImageReader(photo_bytes_io)
                    c.drawImage(reader, photo_x, photo_y, width=photo_w, height=photo_h)
                except Exception:
                    pass
                c.restoreState()

            # --- C. QR CODE ---
            if qr_settings.get("enable_qr", False):
                try:
                    form_data = {
                        'name': student.name, 'father_name': student.father_name,
                        'class_name': student.class_name, 'dob': student.dob,
                        'address': student.address, 'phone': student.phone
                    }
                    photo_ref = student.image_url or student.photo_filename or ""
                    data_hash = generate_data_hash(form_data, photo_ref)

                    qr_id = data_hash[:10]
                    
                    qr_type = qr_settings.get("qr_data_type", "student_id")
                    if qr_type == "url":
                        base = qr_settings.get("qr_base_url", "")
                        qr_payload = (base + '/' + qr_id) if base and not base.endswith('/') else (base + qr_id)
                    elif qr_type == "text":
                        qr_payload = qr_settings.get("qr_custom_text", "Sample")
                    else:
                        qr_payload = json.dumps({"id": str(student.id), "name": student.name})

                    size_px = qr_settings.get("qr_size", 120)
                    qr_pil = generate_qr_code(qr_payload, qr_settings, size_px).convert("RGB")

                    q_x_px = qr_settings.get("qr_x", 50)
                    q_y_px = qr_settings.get("qr_y", 50)
                    qr_x = card_x + (q_x_px * scale)
                    qr_y = card_bottom_y + (card_h_pt - (q_y_px * scale) - (size_px * scale))
                    qr_w = size_px * scale
                    qr_h = size_px * scale

                    qr_reader = ImageReader(qr_pil)
                    c.drawImage(qr_reader, qr_x, qr_y, width=qr_w, height=qr_h)
                except: pass

            # --- D. TEXT (UPDATED WIDTH LOGIC) ---
           # --- D. TEXT (DYNAMIC WIDTH & ADDRESS SHRINKING) ---
            text_case = font_settings.get("text_case", "normal")
            
            fields = [
                {'l': "NAME", 'v': local_apply_text_case(student.name, text_case), 'ord': 10},
                {'l': "F.NAME", 'v': local_apply_text_case(student.father_name, text_case), 'ord': 20},
                {'l': "CLASS", 'v': local_apply_text_case(student.class_name, text_case), 'ord': 30},
                {'l': "D.O.B.", 'v': student.dob, 'ord': 40},
                {'l': "MOBILE", 'v': student.phone, 'ord': 50},
                {'l': "ADDRESS", 'v': local_apply_text_case(student.address, text_case), 'ord': 60}
            ]
            
            if student.custom_data:
                db_fields = TemplateField.query.filter_by(template_id=template_id).all()
                for f in db_fields:
                    val = student.custom_data.get(f.field_name, "")
                    fields.append({'l': f.field_label.upper(), 'v': local_apply_text_case(val, text_case), 'ord': f.display_order})
            
            fields.sort(key=lambda x: x['ord'])

            start_y_text_px = font_settings.get('start_y', 200)
            label_x_px = font_settings.get('label_x', 50)
            value_x_px = font_settings.get('value_x', 250)
            current_y_px = start_y_text_px
            line_height_px = font_settings.get('line_height', 50)

            # Photo Vertical Boundaries (Pixels)
            p_x_px = photo_settings.get("photo_x", 0)
            p_y_px = photo_settings.get("photo_y", 0)
            p_h_px = photo_settings.get("photo_height", 0)
            p_bottom_px = p_y_px + p_h_px

            for field in fields:
                # Calculate Y position in PDF points (bottom-up)
                text_pdf_y = card_bottom_y + (card_h_pt - (current_y_px * scale) - lbl_size_pt)
                
                # Draw Label
                c.setFillColor(l_color)
                c.setFont(bold_font_name, lbl_size_pt) 
                c.drawString(card_x + (label_x_px * scale), text_pdf_y, f"{field['l']}:")
                
                c.setFillColor(v_color)
                val_text = field['v']

                # --- 1. DYNAMIC WIDTH CALCULATION ---
                # Check overlap using Pixel coordinates (Top-Down)
                # We check if the current text line Y intersects with the Photo Y range
                is_vertically_overlapping = (current_y_px < p_bottom_px) and ((current_y_px + line_height_px) > p_y_px)

                if is_vertically_overlapping and (p_x_px > value_x_px):
                     # Overlap detected: Restrict width to stop before photo
                     max_w_px = p_x_px - value_x_px - 15
                else:
                     # No overlap (Photo is above/below/left): Use full width
                     max_w_px = card_w_px - value_x_px - 20
                
                max_width_pt = max_w_px * scale
                # ------------------------------------

                # --- 2. ADDRESS FIELD LOGIC (SHRINK TO FIT 2 LINES) ---
                if field['l'] == "ADDRESS":
                    # Title case for better readability if all caps
                    if text_case == "normal" and val_text and val_text.isupper() and len(val_text) > 10:
                        val_text = val_text.title()

                    curr_font_size = val_size_pt
                    min_font_size = 8 * scale # Minimum readable size
                    
                    # Initial check
                    lines = simpleSplit(val_text, reg_font_name, curr_font_size, max_width_pt)
                    
                    # Shrink Loop: Keep reducing font size until it fits in 2 lines
                    while len(lines) > 2 and curr_font_size > min_font_size:
                        curr_font_size -= 0.5 
                        lines = simpleSplit(val_text, reg_font_name, curr_font_size, max_width_pt)
                    
                    # Draw up to 2 lines
                    c.setFont(reg_font_name, curr_font_size)
                    line_spacing = curr_font_size * 1.15 
                    
                    for i, line in enumerate(lines[:2]):
                        draw_y = text_pdf_y - (i * line_spacing)
                        c.drawString(card_x + (value_x_px * scale), draw_y, line)
                    
                    # If we used 2 lines, add a little extra spacing for the next field
                    if len(lines) > 1:
                        # Add half a line height extra
                        current_y_px += (line_height_px * 0.5)

                # --- 3. STANDARD FIELDS LOGIC ---
                else:
                    c.setFont(reg_font_name, val_size_pt)
                    text_width = c.stringWidth(val_text, reg_font_name, val_size_pt)
                    
                    # If text is too long for the calculated width, wrap it standardly
                    if text_width > max_width_pt:
                        curr_font_size = val_size_pt
                        lines = simpleSplit(val_text, reg_font_name, curr_font_size, max_width_pt)
                        
                        # Slight shrink if wrapping causes too many lines (optional)
                        if len(lines) > 2: 
                            curr_font_size *= 0.9
                            lines = simpleSplit(val_text, reg_font_name, curr_font_size, max_width_pt)

                        c.setFont(reg_font_name, curr_font_size)
                        line_spacing = curr_font_size * 1.15
                        
                        for i, line in enumerate(lines):
                            draw_y = text_pdf_y - (i * line_spacing)
                            c.drawString(card_x + (value_x_px * scale), draw_y, line)
                        
                        # Add extra vertical space for multi-line standard fields
                        if len(lines) > 1:
                            extra_h_px = ((len(lines) - 1) * line_spacing) / scale
                            current_y_px += extra_h_px
                    else:
                        # Fits in one line
                        c.drawString(card_x + (value_x_px * scale), text_pdf_y, val_text)
                
                # Move to next field position
                current_y_px += line_height_px
                
            card_count += 1
            if card_count % cards_per_sheet == 0:
                c.showPage()
                c.setFillColor(Color(0, 0, 0))

        c.save()
        buffer.seek(0)
          
        filename = f"COREL_VECTOR_{template.school_name}.pdf"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error generating vector PDF: {str(e)}", 500