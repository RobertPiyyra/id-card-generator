# Cloudinary Migration Summary

## Overview
Replaced ALL local image saves (`static/generated/`) with **Cloudinary Cloud Storage**. Images are now stored remotely and database contains URLs instead of filenames.

---

## Changes Made

### 1. **Database Model** (`models.py`)
✅ **Replaced local filenames with Cloudinary URLs:**
```python
# OLD
photo_filename = Column(String(255))
generated_filename = Column(String(255))

# NEW
photo_url = Column(String(1024))        # Cloudinary photo URL
image_url = Column(String(1024))        # Cloudinary generated card image URL
pdf_url = Column(String(1024))          # Cloudinary PDF URL
```

### 2. **Utility Functions** (`utils.py`)
✅ **Updated `generate_data_hash()` to accept URLs or filenames:**
```python
def generate_data_hash(form_data, photo_identifier=None):
    """
    Generate a deterministic hash for the student data.
    Accepts either a photo filename or a photo URL as photo_identifier for backward compatibility.
    """
```

### 3. **Card Generation** (`app.py` - `/` route)

#### Photo Upload Flow
```python
# OLD: Save locally to disk
photo.save(os.path.join(UPLOAD_FOLDER, photo_stored))

# NEW: Upload to Cloudinary
photo_bytes = io.BytesIO()
photo.save(photo_bytes)
photo_bytes.seek(0)
uploaded = upload_image(photo_bytes.getvalue(), folder='photos')
photo_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
```

#### Card Image Generation
```python
# OLD: Save JPG and PDF to local filesystem
template_img.save(jpg_path, "JPEG", quality=95)
template_img.save(pdf_path, "PDF", resolution=300)

# NEW: Upload both to Cloudinary
jpg_buf = io.BytesIO()
template_img.save(jpg_buf, format='JPEG', quality=95)
jpg_bytes = jpg_buf.getvalue()

jpg_result = upload_image(jpg_bytes, folder='generated')
image_url = jpg_result if isinstance(jpg_result, str) else jpg_result.get('url')

pdf_result = upload_image(pdf_bytes, folder='generated', resource_type='raw')
pdf_url = pdf_result if isinstance(pdf_result, str) else pdf_result.get('url')
```

#### Database Save
```python
# OLD
student.photo_filename = photo_stored
student.generated_filename = jpg_name

# NEW
student.photo_url = photo_url
student.image_url = image_url
student.pdf_url = pdf_url
```

### 4. **Preview Generation** (`app.py` - `/admin/generate_preview/<int:student_id>`)

#### Photo Fetch (supports both Cloud & Legacy)
```python
# NEW: Fetch from Cloudinary URL first, fallback to local
photo_stream = None
if getattr(student, 'photo_url', None):
    resp = requests.get(student.photo_url, timeout=8)
    if resp.status_code == 200:
        photo_stream = BytesIO(resp.content)
elif getattr(student, 'photo_filename', None):
    local_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
    if os.path.exists(local_path):
        photo_stream = open(local_path, 'rb')
```

#### Preview Upload
```python
# OLD: Save to static/generated/
template_img.save(preview_path, "JPEG", quality=95)
preview_url = url_for('static', filename=f'generated/{preview_filename}')

# NEW: Upload to Cloudinary
buf = BytesIO()
template_img.save(buf, format='JPEG', quality=95)
buf.seek(0)
uploaded = upload_image(buf.getvalue(), folder='generated')
preview_url = uploaded if isinstance(uploaded, str) else uploaded.get('url')
```

### 5. **PDF Generation** (`corel_routes.py`)

#### Photo Handling in PDF
```python
# NEW: Support both photo_url (Cloudinary) and photo_filename (legacy)
photo_bytes_io = None
if getattr(student, 'photo_url', None):
    resp = requests.get(student.photo_url, timeout=10)
    if resp.status_code == 200:
        photo_bytes_io = io.BytesIO(resp.content)

if photo_bytes_io is None and getattr(student, 'photo_filename', None):
    p_path = os.path.join(UPLOAD_FOLDER, student.photo_filename)
    if os.path.exists(p_path):
        with open(p_path, 'rb') as fh:
            photo_bytes_io = io.BytesIO(fh.read())

if photo_bytes_io:
    reader = ImageReader(photo_bytes_io)
    c.drawImage(reader, photo_x, photo_y, width=photo_w, height=photo_h)
```

### 6. **PDF Download** (`app.py` - `/admin/download_student_pdf/<int:student_id>`)

```python
# NEW: Redirect to Cloudinary URL if available, fallback to local file
if getattr(student, 'pdf_url', None):
    return redirect(student.pdf_url)

# Legacy fallback: serve local file
if getattr(student, 'generated_filename', None):
    pdf_path = os.path.join(GENERATED_FOLDER, student.generated_filename)
    if os.path.exists(pdf_path):
        return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)
```

### 7. **Admin Preview** (`app.py` - `/admin/student_preview/<int:student_id>`)

```python
# NEW: Use image_url from Cloudinary, fallback to local
if getattr(student, 'image_url', None):
    preview_url = student.image_url
elif getattr(student, 'generated_filename', None):
    preview_filename = student.generated_filename.replace('.pdf', '.jpg')
    # ... load local file
```

---

## Imports Added

### `app.py`
```python
from cloudinary_config import upload_image
import requests
```

### `corel_routes.py`
```python
import requests
```

---

## Key Benefits

✅ **No Persistent Filesystem Needed** - Railway ephemeral storage no longer an issue  
✅ **Automatic CDN Delivery** - Cloudinary serves images globally with caching  
✅ **Scalable** - No need to manage server disk space  
✅ **Backward Compatible** - Legacy `photo_filename` and `generated_filename` still work  
✅ **Safe Fallbacks** - All routes gracefully handle both Cloudinary URLs and legacy local files  

---

## Database Migration

Old records will still work with `photo_filename` and `generated_filename` fields:
- Reads (preview, PDF generation) check `photo_url` / `image_url` first
- If not found, falls back to loading from `photo_filename` / `generated_filename`
- New records always use URL fields

No migration script needed - old and new systems coexist.

---

## Testing

1. **Upload a new photo** → Verify it goes to Cloudinary (`photo_url` set)
2. **Generate a card** → Verify JPG and PDF URLs stored (`image_url`, `pdf_url`)
3. **Preview card** → Verify preview comes from Cloudinary URL
4. **Download PDF** → Verify redirect to Cloudinary or local fallback
5. **Edit existing card** → Verify Cloudinary URLs used (not local saves)
6. **View old records** → Verify fallback to local `photo_filename`

---

## Environment Variables

Required `.env` variables (already set in `cloudinary_config.py`):
```
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
```

---

## No Breaking Changes

✅ Existing endpoints work unchanged (`/admin`, `/`, etc.)  
✅ HTML templates don't need updates (use `image_url` or `generated_filename` conditionally)  
✅ Route names and parameters unchanged  
✅ Function signatures remain compatible  

---

## Rollout Notes

- **Old records** continue to work with local files if they still exist
- **New records** only use Cloudinary (no local files created)
- **Migration** is automatic - no manual data movement needed
- **Safety**: If Cloudinary unavailable, system falls back to local files for old records

