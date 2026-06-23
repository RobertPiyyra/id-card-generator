# School Admin Photo-First ID Card Generation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a "Photo Batch" tab to the admin panel where school admins upload photos (assigned serial numbers), then fill in student details by logging into the index page and searching by serial number — generating ID cards for their school's template.

**Architecture:** Build on the existing `SerialBatch` + `SerialCard` models and `serial_batch_service.py` (already fully implemented). Add a new admin panel tab with photo upload, a serial-number lookup flow on the index page, and wire up the existing service to routes. School admins are restricted to their own school's templates via `school_admin_required`.

**Tech Stack:** Flask, SQLAlchemy, PyMuPDF, Pillow, existing SerialBatch/SerialCard models

---

## Current State Assessment

### What Already Exists
- **`models.py`**: `SerialBatch` and `SerialCard` models with full fields (serial_no, photo_path, name, father_name, class_name, dob, address, phone, custom_data, status, etc.)
- **`app/services/serial_batch_service.py`**: Complete service with `create_batch()`, `upload_photos()`, `update_card_details()`, `get_batch_cards()`, `delete_card()`, `import_details_from_csv()`, thumbnail generation
- **`app/routes/auth_routes.py`**: `school_admin_login` route, `register_school_admin` (super admin only)
- **`app/routes/dashboard_routes.py`**: `index()` already auto-selects template for school admins, shows form
- **`app/routes/editor_routes.py`**: Already enforces `school_admin` RBAC on templates
- **Templates**: `school_admin_login.html`, `admin.html` (with tab system), `admin_student_credentials.html`

### What's Missing
1. No routes wired to `serial_batch_service` (the service exists but has zero HTTP endpoints)
2. No admin panel tab for photo batch management
3. No serial-number lookup on the index page for school admins
4. No card generation endpoint that uses `SerialCard` data + template rendering

---

## Step-by-Step Plan

### Task 1: Create Serial Batch Routes (Backend)

**Objective:** Wire `serial_batch_service` to Flask routes with proper RBAC.

**Files:**
- Create: `app/routes/serial_batch_routes.py`
- Modify: `app/__init__.py` (register blueprint)

**Step 1: Create the blueprint**

```python
# app/routes/serial_batch_routes.py
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, flash, send_file
from app.decorators import school_admin_required, super_admin_required
from app.services.serial_batch_service import (
    create_batch, get_batch, list_batches, get_batch_cards,
    upload_photos, update_card_details, delete_card,
)
from models import db, Template
from utils import FONTS_FOLDER
import logging

logger = logging.getLogger(__name__)
serial_batch_bp = Blueprint('serial_batch', __name__)
```

**Step 2: Add routes**

- `GET /admin/serial_batches` — List batches (school admin sees own, super admin sees all)
- `POST /admin/serial_batches` — Create new batch (school admin only)
- `GET /admin/serial_batches/<id>` — View batch cards with thumbnails
- `POST /admin/serial_batches/<id>/upload` — Upload photos to batch
- `GET /admin/serial_batches/<id>/cards` — JSON list of cards (for search)
- `GET /admin/serial_batches/<id>/cards/<card_id>` — Get single card details
- `POST /admin/serial_batches/<id>/cards/<card_id>` — Update card details
- `DELETE /admin/serial_batches/<id>/cards/<card_id>` — Delete card
- `POST /admin/serial_batches/<id>/generate/<card_id>` — Generate ID card PDF
- `GET /admin/serial_batches/<id>/download_all` — Download all cards as PDF

**Step 3: Register blueprint**

In `app/__init__.py`, add:
```python
from app.routes.serial_batch_routes import serial_batch_bp
_app.register_blueprint(serial_batch_bp, url_prefix='/admin/serial_batches')
```

**Step 4: Verify import**

```bash
cd /home/robertpiyyra/id_project && python3 -c "from app import app; print('OK')"
```

**Step 5: Commit**

```bash
git add app/routes/serial_batch_routes.py app/__init__.py
git commit -m "feat: add serial batch routes with RBAC"
```

---

### Task 2: Add "Photo Batch" Tab to Admin Panel

**Objective:** Add a new tab in the admin panel for managing photo batches.

**Files:**
- Modify: `templates/admin.html` (add tab button + tab content div)
- Create: `templates/admin/_photo_batch_tab.html` (tab content partial)

**Step 1: Add tab button**

In `templates/admin.html` line ~1360 (inside `.tabs` div), add:
```html
<div class="tab" onclick="switchTab('photoBatch')"><i class="fas fa-camera"></i> Photo Batch</div>
```

**Step 2: Add tab content**

```html
<div id="photoBatch-tab" class="tab-content">
    {% include 'admin/_photo_batch_tab.html' %}
</div>
```

**Step 3: Create the partial**

`templates/admin/_photo_batch_tab.html`:
- "Create New Batch" section: dropdown for template (filtered to school), prefix input, "Create" button
- Batch list: table showing batch ID, school, template, status, card count, date
- Clicking a batch opens a detail modal/section with:
  - Photo upload area (drag & drop, multi-file)
  - Card grid showing thumbnails + serial numbers
  - Search by serial number input
  - "Generate Card" button per card
  - "Download All Cards" button

**Step 4: Add CSS**

Add styles for:
- `.upload-zone` (drag-and-drop area with dashed border)
- `.card-grid` (grid layout for thumbnails)
- `.card-thumb` (thumbnail + serial number overlay)
- `.search-bar` (serial number lookup)

**Step 5: Commit**

```bash
git add templates/admin.html templates/admin/_photo_batch_tab.html
git commit -m "feat: add Photo Batch tab to admin panel"
```

---

### Task 3: Add Serial Number Lookup to Index Page

**Objective:** School admins can search by serial number on the index page to fill in details and generate cards.

**Files:**
- Modify: `app/routes/dashboard_routes.py` (add serial search endpoint)
- Modify: `templates/index.html` or school admin dashboard (add serial search UI)

**Step 1: Add serial search API**

In `app/routes/dashboard_routes.py` or `serial_batch_routes.py`:
```python
@serial_batch_bp.route('/api/serial_lookup/<serial_no>', methods=['GET'])
@school_admin_required
def serial_lookup(serial_no):
    """Look up a serial card by number for the current school admin."""
    school_name = session.get('admin_school')
    card = SerialCard.query.join(SerialBatch).filter(
        SerialCard.serial_no == serial_no,
        SerialBatch.school_name == school_name
    ).first()
    if not card:
        return jsonify({'error': 'Serial number not found'}), 404
    return jsonify({
        'id': card.id,
        'serial_no': card.serial_no,
        'name': card.name,
        'father_name': card.father_name,
        'class_name': card.class_name,
        'dob': card.dob,
        'address': card.address,
        'phone': card.phone,
        'photo_thumbnail': card.photo_thumbnail,
        'status': card.status,
        'batch_id': card.batch_id,
    })
```

**Step 2: Add serial search UI to school admin's dashboard**

When `admin_role == school_admin`, show a prominent "Find by Serial #" input at the top of the index page. On enter/submit, fetch card details and populate the form.

**Step 3: Wire form population**

JavaScript on the index page:
- On serial search result, populate name, father_name, class, dob, address, phone fields
- Show photo thumbnail
- Store batch_id and card_id in hidden fields for generation

**Step 4: Commit**

```bash
git add app/routes/dashboard_routes.py templates/index.html
git commit -m "feat: add serial number lookup on index page for school admins"
```

---

### Task 4: Card Generation Endpoint

**Objective:** Generate a completed ID card PDF using the template + photo + details.

**Files:**
- Modify: `app/routes/serial_batch_routes.py` (add generate endpoint)
- Uses: `app/services/render_service.py` (existing `render_student_card_side`)

**Step 1: Add generate endpoint**

```python
@serial_batch_bp.route('/<int:batch_id>/generate/<int:card_id>', methods=['POST'])
@school_admin_required
def generate_card(batch_id, card_id):
    """Generate ID card for a single SerialCard."""
    school_name = session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    card = SerialCard.query.filter_by(id=card_id, batch_id=batch_id).first()
    if not card or not card.name:
        return jsonify({'error': 'Card not found or details incomplete'}), 400
    
    template = db.session.get(Template, batch.template_id)
    
    # Build a temporary student-like dict for rendering
    student_data = {
        'name': card.name,
        'father_name': card.father_name,
        'class_name': card.class_name,
        'dob': card.dob,
        'address': card.address,
        'phone': card.phone,
        'photo_path': card.photo_path,
        'template_id': template.id,
        'custom_data': card.custom_data or {},
    }
    
    # Use existing render service
    from app.services.render_service import render_student_card_side
    rendered_bytes = render_student_card_side(
        student_data, template, side='front'
    )
    
    # Update card record
    output_path = os.path.join(_batch_dir(batch_id), 'rendered', f'card_{card_id}.pdf')
    with open(output_path, 'wb') as f:
        f.write(rendered_bytes)
    card.rendered_path = output_path
    card.status = 'rendered'
    db.session.commit()
    
    return send_file(
        io.BytesIO(rendered_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'IDCard_{card.serial_no}.pdf'
    )
```

**Step 2: Add download-all endpoint**

```python
@serial_batch_bp.route('/<int:batch_id>/download_all', methods=['GET'])
@school_admin_required
def download_all_cards(batch_id):
    """Generate a combined PDF of all cards in the batch."""
    school_name = session.get('admin_school')
    batch = get_batch(batch_id, school_name=school_name)
    cards = SerialCard.query.filter_by(batch_id=batch_id).filter(
        SerialCard.status.in_(['details_filled', 'rendered'])
    ).order_by(SerialCard.serial_no).all()
    
    # Use PyMuPDF to merge all rendered cards into one PDF
    import fitz
    out_doc = fitz.open()
    for card in cards:
        if card.rendered_path and os.path.exists(card.rendered_path):
            card_doc = fitz.open(card.rendered_path)
            out_doc.insert_pdf(card_doc)
            card_doc.close()
    
    pdf_bytes = out_doc.tobytes()
    out_doc.close()
    
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'IDCards_{batch.school_name}_batch{batch.id}.pdf'
    )
```

**Step 3: Commit**

```bash
git add app/routes/serial_batch_routes.py
git commit -m "feat: add single and batch card generation endpoints"
```

---

### Task 5: JavaScript Frontend Logic

**Objective:** Wire up the Photo Batch tab UI to the backend APIs.

**Files:**
- Create: `static/js/photo_batch.js`
- Modify: `templates/admin/_photo_batch_tab.html` (include script)

**Step 1: Create JS module**

`static/js/photo_batch.js`:
```javascript
const PhotoBatch = {
    currentBatchId: null,
    
    init() {
        this.loadBatches();
        this.bindUpload();
        this.bindSearch();
    },
    
    async loadBatches() {
        const resp = await fetch('/admin/serial_batches');
        const data = await resp.json();
        this.renderBatchList(data.batches);
    },
    
    async createBatch() {
        const templateId = document.getElementById('batch_template').value;
        const prefix = document.getElementById('batch_prefix').value || 'SCH-';
        const resp = await fetch('/admin/serial_batches', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({template_id: templateId, prefix: prefix})
        });
        const data = await resp.json();
        if (data.success) {
            this.loadBatches();
            this.viewBatch(data.batch_id);
        }
    },
    
    async uploadPhotos(batchId, files) {
        const formData = new FormData();
        for (let f of files) formData.append('photos', f);
        const resp = await fetch(`/admin/serial_batches/${batchId}/upload`, {
            method: 'POST', body: formData
        });
        const data = await resp.json();
        this.viewBatch(batchId); // Refresh card grid
    },
    
    async searchSerial(serialNo) {
        const resp = await fetch(`/admin/serial_batches/api/serial_lookup/${serialNo}`);
        const data = await resp.json();
        if (data.id) this.populateCardForm(data);
        else alert('Serial number not found');
    },
    
    async generateCard(batchId, cardId) {
        const resp = await fetch(`/admin/serial_batches/${batchId}/generate/${cardId}`, {
            method: 'POST'
        });
        const blob = await resp.blob();
        // Download
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `card_${cardId}.pdf`;
        a.click();
    }
};
document.addEventListener('DOMContentLoaded', () => PhotoBatch.init());
```

**Step 2: Include in template**

```html
<script src="{{ url_for('static', filename='js/photo_batch.js') }}"></script>
```

**Step 3: Commit**

```bash
git add static/js/photo_batch.js templates/admin/_photo_batch_tab.html
git commit -m "feat: add photo batch frontend JavaScript"
```

---

### Task 6: Add "Fill Details" Link from Index Page

**Objective:** School admins can click "Fill Details" next to a serial number search result, which opens the card form pre-filled.

**Files:**
- Modify: `app/routes/dashboard_routes.py` (pass serial search result to template)
- Modify: `templates/index.html` (add serial search section for school admins)

**Step 1: Add serial search section to index**

When `session.get('admin_role') == 'school_admin'`, show:
```html
<div class="serial-search-box">
    <h3>Find Student by Serial Number</h3>
    <input type="text" id="serial_search" placeholder="e.g. SCH-001" />
    <button onclick="lookupSerial()">Search</button>
    <div id="serial_result"></div>
</div>
```

**Step 2: Add JS lookup function**

```javascript
async function lookupSerial() {
    const serial = document.getElementById('serial_search').value.trim();
    if (!serial) return;
    const resp = await fetch(`/admin/serial_batches/api/serial_lookup/${serial}`);
    const data = await resp.json();
    if (data.id) {
        // Populate form fields
        document.getElementById('name').value = data.name || '';
        document.getElementById('father_name').value = data.father_name || '';
        // ... etc
        document.getElementById('card_id').value = data.id;
        document.getElementById('batch_id').value = data.batch_id;
        document.getElementById('serial_result').innerHTML = 
            `<img src="/${data.photo_thumbnail}" style="width:80px" /> Found: ${data.serial_no}`;
    }
}
```

**Step 3: Commit**

```bash
git add app/routes/dashboard_routes.py templates/index.html
git commit -m "feat: add serial lookup UI on index page for school admins"
```

---

### Task 7: Testing & Validation

**Objective:** Verify the full flow works end-to-end.

**Test Plan:**

1. **Login as school admin**
   ```bash
   # Start app, login with school admin credentials
   # Verify redirect to index with serial search box visible
   ```

2. **Create batch from admin panel**
   - Navigate to Admin > Photo Batch tab
   - Select template, set prefix "SCH-"
   - Click "Create Batch"
   - Verify batch appears in list with status "uploading"

3. **Upload photos**
   - Click batch to open detail view
   - Upload 3-5 photos (JPG/PNG)
   - Verify thumbnails appear with serial numbers SCH-001, SCH-002, etc.

4. **Search by serial on index page**
   - Go to index page
   - Type "SCH-001" in serial search
   - Verify photo thumbnail and details appear

5. **Fill details and generate**
   - Fill in name, father_name, class, etc.
   - Click "Generate Card"
   - Verify PDF downloads with correct photo + details on template

6. **Download all cards**
   - Click "Download All" in batch detail
   - Verify multi-page PDF with all cards

**Step 7: Commit fixes**

```bash
git add -A
git commit -m "fix: address testing feedback"
```

---

## Files Summary

| File | Action | Purpose |
|------|--------|---------|
| `app/routes/serial_batch_routes.py` | Create | All serial batch HTTP endpoints |
| `app/__init__.py` | Modify | Register serial_batch blueprint |
| `templates/admin.html` | Modify | Add Photo Batch tab button + content div |
| `templates/admin/_photo_batch_tab.html` | Create | Tab content (batch list, upload, card grid) |
| `static/js/photo_batch.js` | Create | Frontend JS for batch CRUD + card generation |
| `app/routes/dashboard_routes.py` | Modify | Add serial lookup API + pass to template |
| `templates/index.html` | Modify | Add serial search UI for school admins |

---

## Risks & Considerations

1. **Photo storage**: Photos stored in `static/serial_batches/` — ensure this directory is writable and backed up
2. **PDF generation**: Uses existing `render_student_card_side` which expects a student dict — may need adaptation for `SerialCard` data structure
3. **Large batches**: For 100+ photos, consider pagination in the card grid and async processing
4. **RBAC**: All endpoints must check `school_admin` can only access their own school's batches (already handled by `get_batch(batch_id, school_name=...)`)
5. **Existing index page**: The current index already has a student detail form — the serial search should integrate cleanly without breaking existing single-student flow

---

## Open Questions

1. Should school admins be able to edit card details directly in the Photo Batch tab, or only from the index page? → **Recommendation**: Both — quick edit in batch detail, full form from index
2. Should generated cards be stored on disk or generated on-the-fly? → **Recommendation**: Store on disk (update `rendered_path`), regenerate on request if stale
3. Should there be a "Mark as Complete" workflow for batches? → **Recommendation**: Yes — add `status='completed'` lock to prevent further edits
