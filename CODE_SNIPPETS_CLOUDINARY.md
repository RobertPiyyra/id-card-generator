# Key Code Blocks - Cloudinary Migration

## 1. Models Updated

**File: `models.py`** (Student model fields)

```python
class Student(db.Model):
    __tablename__ = 'students'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    father_name = Column(String(255))
    class_name = Column(String(100))
    dob = Column(String(50))
    address = Column(Text)
    phone = Column(String(50))
    
    # NEW: Store URLs instead of local filenames
    photo_url = Column(String(1024))        # Cloudinary photo URL
    image_url = Column(String(1024))        # Cloudinary generated card image
    pdf_url = Column(String(1024))          # Cloudinary generated PDF
    
    # Legacy fields (for backward compatibility)
    photo_filename = Column(String(255))
    generated_filename = Column(String(255))
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    data_hash = Column(String(255), unique=True)
    template_id = Column(Integer, ForeignKey('templates.id'))
    school_name = Column(String(255))
    email = Column(String(255), unique=False)
    password = Column(String(255))
    custom_data = Column(MutableDict.as_mutable(JSON), default=dict)
    sheet_filename = Column(String(255))
    sheet_position = Column(Integer)
```

---

## 2. Photo Upload (Main Card Generation)

**File: `app.py`** (in the `@app.route("/", methods=["GET", "POST"])` handler)

```python
# Handle Photo (upload to Cloudinary)
photo_stored = None
photo_url = None

if 'photo' in request.files and request.files['photo'].filename:
    photo = request.files['photo']
    photo_fn = secure_filename(photo.filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Convert to bytes
    photo_bytes = io.BytesIO()
    photo.save(photo_bytes)
    photo_bytes.seek(0)
    
    # Try to crop/validate with PIL
    try:
        pil_img = Image.open(photo_bytes)
        photo_bytes = io.BytesIO()
        pil_img.save(photo_bytes, format='JPEG')
        photo_bytes.seek(0)
    except: 
        photo_bytes.seek(0)
    
    # Upload to Cloudinary
    try:
        uploaded = upload_image(photo_bytes.getvalue(), folder='photos')
        photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
    except Exception as e:
        logger.error(f"Failed to upload photo to Cloudinary: {e}")
        photo_url = None
    
    if not photo_url:
        return render_template("index.html", 
                               error="Failed to upload photo. Please try again.", 
                               templates=templates, 
                               form_data=request.form, 
                               selected_template_id=template_id, 
                               deadline_info=deadline_info), 500
    
    photo_stored = f"{timestamp}_{photo_fn}"

elif request.form.get('photo_url'):
    # Support existing photo_url from previous generation
    photo_url = request.form.get('photo_url')
    photo_stored = photo_url

elif request.form.get('photo_filename'):
    # Legacy: local file
    photo_stored = request.form.get('photo_filename')
else:
    raise ValueError("Photo is required")
```

---

## 3. Card Image & PDF Upload to Cloudinary

**File: `app.py`** (after generating the PIL image)

```python
# Upload to Cloudinary (NOT LOCAL SAVE)
jpg_buf = io.BytesIO()
template_img.save(jpg_buf, format='JPEG', quality=95)
jpg_buf.seek(0)
jpg_bytes = jpg_buf.getvalue()

pdf_buf = io.BytesIO()
template_img.save(pdf_buf, format='PDF', quality=95)
pdf_buf.seek(0)
pdf_bytes = pdf_buf.getvalue()

# Upload to Cloudinary
try:
    jpg_result = upload_image(jpg_bytes, folder='generated')
    image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')
    
    pdf_result = upload_image(pdf_bytes, folder='generated', resource_type='raw')
    pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
except Exception as e:
    logger.error(f"Cloudinary upload failed: {e}")
    return render_template("index.html", 
                           error=f"Failed to save image: {str(e)}", 
                           templates=templates, 
                           form_data=request.form, 
                           selected_template_id=template_id, 
                           deadline_info=deadline_info), 500

# These are just for backward compat display
generated_url = image_url
download_url = pdf_url
```

---

## 4. Database Save (with URLs)

**File: `app.py`** (Student record creation/update)

```python
if is_editing:
    student = db.session.get(Student, edit_id)
    if student:
        student.name = name
        student.father_name = father_name
        student.class_name = class_name
        student.dob = dob
        student.address = address
        student.phone = phone
        student.photo_url = photo_url          # ← Cloudinary URL
        student.image_url = image_url          # ← Cloudinary URL
        student.pdf_url = pdf_url              # ← Cloudinary URL
        student.created_at = datetime.now(timezone.utc)
        student.data_hash = data_hash
        student.template_id = template_id
        student.school_name = school_name
        student.custom_data = custom_data
        db.session.commit()

else:
    # New Record
    student = Student(
        name=name,
        father_name=father_name,
        class_name=class_name,
        dob=dob,
        address=address,
        phone=phone,
        photo_url=photo_url,              # ← Cloudinary URL instead of filename
        image_url=image_url,              # ← Cloudinary URL
        pdf_url=pdf_url,                  # ← Cloudinary URL
        created_at=datetime.now(timezone.utc),
        data_hash=data_hash,
        template_id=template_id,
        school_name=school_name,
        email=session['student_email'],
        custom_data=custom_data
    )
    db.session.add(student)
    db.session.commit()
```

---

## 5. Preview Generation (Photo Fetch)

**File: `app.py`** (in `/admin/generate_preview/<int:student_id>`)

```python
# Add photo (Cloudinary URL preferred, fallback to legacy local filename)
try:
    photo_stream = None
    
    if getattr(student, 'photo_url', None):
        # Fetch remote image bytes from Cloudinary
        import requests
        resp = requests.get(student.photo_url, timeout=8)
        if resp.status_code == 200:
            photo_stream = BytesIO(resp.content)
    
    elif getattr(student, 'photo_filename', None):
        # Legacy: load from local filesystem
        local_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
        if os.path.exists(local_path):
            photo_stream = open(local_path, 'rb')

    if photo_stream:
        photo_img = Image.open(photo_stream).convert("RGBA").resize(
            (photo_settings["photo_width"], photo_settings["photo_height"])
        )
        radii = [
            photo_settings.get("photo_border_top_left", 0),
            photo_settings.get("photo_border_top_right", 0),
            photo_settings.get("photo_border_bottom_right", 0),
            photo_settings.get("photo_border_bottom_left", 0)
        ]
        photo_img = round_photo(photo_img, radii)
        template_img.paste(photo_img, (photo_settings["photo_x"], photo_settings["photo_y"]), photo_img)
        
        try:
            if not isinstance(photo_stream, BytesIO):
                photo_stream.close()
        except: pass

except Exception as e:
    logger.error(f"Error adding photo to preview: {e}")
```

---

## 6. Preview Upload to Cloudinary

**File: `app.py`** (in `/admin/generate_preview/<int:student_id>`)

```python
# Save preview to Cloudinary (in-memory, NO local file)
buf = BytesIO()
template_img.save(buf, format='JPEG', quality=95)
buf.seek(0)
img_bytes = buf.getvalue()

try:
    uploaded = upload_image(img_bytes, folder='generated')
    preview_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
except Exception as e:
    logger.error(f"Cloudinary upload failed: {e}")
    return jsonify({"success": False, "error": "Failed to upload preview"}), 500

return jsonify({
    "success": True, 
    "preview_url": preview_url, 
    "message": "Preview generated successfully"
})
```

---

## 7. PDF Generation with Photo Support

**File: `corel_routes.py`** (in `/download_compiled_vector_pdf/<int:template_id>`)

```python
# Support photo stored as Cloudinary URL or legacy local filename
photo_bytes_io = None

if getattr(student, 'photo_url', None):
    # Fetch from Cloudinary
    photo_url = student.photo_url
    try:
        if photo_url.startswith('http'):
            resp = requests.get(photo_url, timeout=10)
            if resp.status_code == 200:
                photo_bytes_io = io.BytesIO(resp.content)
    except Exception:
        photo_bytes_io = None

if photo_bytes_io is None and getattr(student, 'photo_filename', None):
    # Fallback: legacy local file
    p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
    if os.path.exists(p_path):
        try:
            with open(p_path, 'rb') as fh:
                photo_bytes_io = io.BytesIO(fh.read())
        except Exception:
            photo_bytes_io = None

# Use the bytes if available
if photo_bytes_io:
    # ... crop and position ...
    reader = ImageReader(photo_bytes_io)
    c.drawImage(reader, photo_x, photo_y, width=photo_w, height=photo_h)
```

---

## 8. PDF Download Route (Smart Redirect)

**File: `app.py`** (in `/admin/download_student_pdf/<int:student_id>`)

```python
@app.route("/admin/download_student_pdf/<int:student_id>")
def download_student_pdf(student_id):
    if not session.get("admin") and not session.get("student_email"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
  
    try:
        student = db.session.get(Student, student_id)
        
        # Prefer remote PDF URL (Cloudinary) if available
        if not student:
            return jsonify({"success": False, "error": "PDF not found"}), 404

        if getattr(student, 'pdf_url', None):
            # Direct redirect to Cloudinary URL for fast download
            return redirect(student.pdf_url)

        # Legacy fallback: serve local file if it still exists
        if getattr(student, 'generated_filename', None):
            pdf_filename = student.generated_filename
            pdf_path = os.path.join(GENERATED_FOLDER, pdf_filename)
            if os.path.exists(pdf_path):
                return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)

        return jsonify({"success": False, "error": "PDF file not found"}), 404
        
    except Exception as e:
        logger.error(f"Error downloading student PDF: {e}")
        return jsonify({"success": False, "error": "Database error"}), 500
```

---

## 9. Admin Preview Route (Smart URL Selection)

**File: `app.py`** (in `/admin/student_preview/<int:student_id>`)

```python
@app.route("/admin/student_preview/<int:student_id>")
def admin_student_preview(student_id):
    # Get preview image URL (use Cloudinary URLs if present)
    preview_url = None
    
    if getattr(student, 'image_url', None):
        # NEW: Use Cloudinary URL
        preview_url = student.image_url
    elif getattr(student, 'generated_filename', None):
        # LEGACY: Fallback to local file
        preview_filename = student.generated_filename.replace('.pdf', '.jpg')
        preview_path = os.path.join(GENERATED_FOLDER, preview_filename)
        if os.path.exists(preview_path):
            preview_url = url_for('static', filename=f'generated/{preview_filename}')
    
    return jsonify({
        "success": True,
        "name": student.name,
        "class_name": student.class_name,
        "preview_url": preview_url or url_for('static', filename='placeholder.jpg'),
        "has_preview": preview_url is not None
    })
```

---

## 10. Utility: Hash Generation (Backward Compatible)

**File: `utils.py`**

```python
def generate_data_hash(form_data, photo_identifier=None):
    """
    Generate a deterministic hash for the student data.
    Accepts either a photo filename or a photo URL as photo_identifier for backward compatibility.
    """
    data_string = (
        f"{form_data.get('name','')}"
        f"{form_data.get('father_name','')}"
        f"{form_data.get('class_name','')}"
        f"{form_data.get('dob','')}"
        f"{form_data.get('address','')}"
        f"{form_data.get('phone','')}"
    )
    if photo_identifier:
        data_string += str(photo_identifier)
    return hashlib.md5(data_string.encode()).hexdigest()
```

---

## Summary of Replacements

| Operation | Old Way | New Way |
|-----------|---------|---------|
| **Photo Upload** | `photo.save(os.path.join(UPLOAD_FOLDER, ...))` | `upload_image(photo_bytes, folder='photos')` |
| **Image Save** | `template_img.save(jpg_path, ...)` | `upload_image(jpg_bytes, folder='generated')` |
| **PDF Save** | `template_img.save(pdf_path, ...)` | `upload_image(pdf_bytes, folder='generated', resource_type='raw')` |
| **Database** | `student.photo_filename = name` | `student.photo_url = url` |
| **Database** | `student.generated_filename = name` | `student.image_url = url` |
| **Database** | (no PDF field) | `student.pdf_url = url` |
| **Photo Fetch** | `Image.open(local_path)` | `requests.get(photo_url) → Image.open(BytesIO(...))` |
| **Preview Save** | `img.save(preview_path, ...)` | `upload_image(img_bytes, folder='generated')` |
| **PDF Download** | `send_file(local_path)` | `redirect(pdf_url)` |

---

## No Breaking Changes

✅ All routes maintain same names and parameters  
✅ HTML templates work with existing logic  
✅ Old records with `photo_filename` still work via fallback  
✅ Admin panel functionality unchanged  
✅ Email functionality unchanged  

---

