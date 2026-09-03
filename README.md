# 🎓 ID Card Generator — Multi-School Smart ID System

A production-ready Flask web application for generating high-quality student ID cards with a browser-based visual editor, AI-powered photo processing, QR/barcode support, multilingual rendering, and bulk printing.

---

## ✨ Key Features

### 🖊️ Visual Template Editor
- Browser-based drag-and-drop canvas editor (Fabric.js 5.3)
- Real-time field positioning for label, value, and colon parts
- Custom shape objects: text blocks, rectangles, circles, lines, images
- Layer management with drag-to-reorder, lock, group/ungroup
- Snap-to-grid, alignment guides, rulers
- Undo / redo history (20-step stack)
- Front and back side editing for double-sided cards
- JSON layout export / import
- AI Design Studio: layout generator, color harmony, design validator, template analyzer

### 🪪 ID Card Generation
- Portrait and landscape card orientations
- Configurable card dimensions (default 1015 × 661 px)
- Bulk and individual card generation
- Printable HD output (JPEG + PDF)
- A4 sheet layout with configurable grid (rows × cols)
- CorelDRAW-compatible editable PDF export

### 🖼️ AI Photo Processing
- Face-aware automatic cropping (MediaPipe + OpenCV)
- AI background removal (rembg / ONNX Runtime)
- Transparent edge trimming
- Hijab / turban-safe cropping mode
- Photo shapes: rectangle, rounded, circle, polygon (hexagon, pentagon, star, diamond …)
- Per-corner border radius control
- Frame color customization

### 🔳 QR and Barcode System
- Dynamic QR code generation (qrcode library)
- Code128 barcode support
- Configurable payload: URL, JSON, plain text, student ID
- Custom fill / background colors
- Redis-cached rendering for performance
- Separate position / size control per template side

### 🌍 Multilingual and RTL Support
- English, Urdu, Hindi, Arabic
- RTL / LTR direction switching per template side
- Dynamic font selection per language
- Urdu/Arabic: Unicode-shaped rendering (arabic-reshaper + bidi)
- Numeral localization (Arabic-Indic numerals)
- Auto-fit text to bounding box

### 🔐 Multi-School Admin System
- Super-admin and school-admin roles
- School-scoped data isolation
- Secure login with session management
- CSRF protection on all forms
- Rate limiting (Flask-Limiter)
- Immutable audit event log
- Template version snapshots on every save
- Activity logging

### ⚡ Performance
- Redis caching with stampede protection
- Parallel card rendering (ThreadPoolExecutor)
- Background task queue (RQ / Celery)
- WebP image optimization throughout
- Smart QR caching
- Lazy Redis connection with safe fallback

### 📊 Analytics and Monitoring
- Prometheus metrics endpoint (`/metrics`)
- Health-check endpoint (`/health`)
- Sentry error tracking integration
- Structured JSON logging
- Dashboard analytics (cards generated, active schools …)

---

## 🧠 Tech Stack

| Layer | Libraries / Tools |
|---|---|
| **Web Framework** | Flask, Gunicorn, Blueprints |
| **Database** | SQLAlchemy ORM, SQLite (dev), PostgreSQL (prod), Alembic migrations |
| **Image Processing** | Pillow, OpenCV, MediaPipe, rembg (ONNX Runtime) |
| **Canvas / Frontend** | Fabric.js 5.3, SortableJS, WebFont Loader |
| **PDF Export** | ReportLab, PyMuPDF |
| **QR / Barcode** | qrcode, python-barcode |
| **Caching / Queue** | Redis, RQ, Celery |
| **AI Features** | Custom AI layout service, color harmony API |
| **Auth / Security** | Flask-Login, Flask-WTF (CSRF), itsdangerous tokens |
| **Notifications** | Email (SMTP), SMS (Twilio) |
| **Storage** | Cloudinary (prod), local filesystem (dev) |
| **Monitoring** | Prometheus, Sentry, structured JSON logs |

---

## 📁 Project Structure

```
id_project/
│
├── run.py                          # Dev entry point (python run.py)
├── models.py                       # SQLAlchemy models (~30 tables)
├── utils.py                        # Template/image/font utility functions
├── cloudinary_config.py            # Cloudinary upload helper
├── notifications.py                # Email + SMS notification logic
├── manage.py                       # CLI management commands
├── gunicorn.conf.py                # Gunicorn production config
├── conftest.py                     # Shared pytest fixtures
├── requirements.txt                # Python dependencies
├── requirements-dev.txt            # Dev/test dependencies
│
├── app/                            # Main application package
│   ├── __init__.py                 # App factory: create_app()
│   ├── config.py                   # Dev / Prod / Testing configs
│   ├── legacy_app.py               # Core Flask app, DB init, migrations
│   ├── extensions.py               # Flask extensions (CSRF, limiter, scheduler)
│   ├── auth_decorators.py          # @admin_required, @school_admin_required
│   ├── decorators.py               # Legacy decorator re-exports
│   ├── error_handlers.py           # HTTP error page registrations
│   ├── helpers.py                  # Template helpers, cache busting
│   ├── field_layout.py             # Field position computation logic
│   ├── db_migrations.py            # Dynamic column migration helpers
│   ├── template_ops.py             # Template CRUD operations
│   ├── logging_config.py           # Structured JSON logging setup
│   ├── observability.py            # Prometheus metrics + /health endpoint
│   ├── performance.py              # Caching, lazy Redis, connection pooling
│   ├── middleware.py               # Production middleware (security headers)
│   ├── sentry_config.py            # Sentry SDK initialisation
│   ├── celery_config.py            # Celery broker / task setup
│   ├── websocket.py                # WebSocket support
│   ├── exceptions.py               # Custom exception classes
│   │
│   ├── routes/                     # HTTP route blueprints
│   │   ├── dashboard_routes.py     # Student cards, bulk generation, dashboard UI
│   │   ├── auth_routes.py          # Login, logout, password reset
│   │   ├── editor_routes.py        # Visual template editor + save API
│   │   ├── api_routes.py           # Internal JSON API endpoints
│   │   ├── ai_routes.py            # AI Design Studio endpoints
│   │   ├── corel_routes.py         # CorelDRAW PDF export (editable + print)
│   │   ├── serial_batch_routes.py  # Photo-first serial batch generation
│   │   ├── enterprise_routes.py    # Enterprise admin features
│   │   ├── rest_api.py             # Public RESTful API
│   │   ├── analytics_routes.py     # Analytics dashboard
│   │   ├── verify_routes.py        # Card verification (QR scan landing)
│   │   ├── faq_routes.py           # FAQ CMS pages
│   │   ├── dashboard_helpers.py    # Dashboard route utilities
│   │   └── dashboard_render.py     # Dashboard render helper
│   │
│   ├── services/                   # Business logic layer (47 modules)
│   │   ├── render_service.py       # Core card image rendering (PIL)
│   │   ├── photo_service.py        # Photo processing, face crop, BG removal
│   │   ├── face_service.py         # MediaPipe face detection
│   │   ├── corel_export_service.py # CorelDRAW PDF generation
│   │   ├── ai_layout.py            # AI layout analysis + design API
│   │   ├── layout_service.py       # Layout config parsing + field resolution
│   │   ├── serial_batch_service.py # Serial batch CRUD + processing
│   │   ├── redis_service.py        # Redis client + cache helpers
│   │   ├── cache_service.py        # Application-level cache abstraction
│   │   ├── template_upload_service.py    # Template file upload + storage
│   │   ├── template_lifecycle_service.py # Version snapshots + audit events
│   │   ├── student_service.py      # Student CRUD helpers
│   │   ├── translation_service.py  # Multilingual text translation
│   │   ├── security_service.py     # Input sanitisation, token validation
│   │   ├── analytics_service.py    # Metric aggregation
│   │   ├── parallel_render.py      # ThreadPoolExecutor batch rendering
│   │   ├── tenant.py               # Multi-tenant school isolation
│   │   └── ...                     # 30+ additional service modules
│   │
│   ├── utils/                      # Internal utility modules
│   │   ├── image_utils.py
│   │   ├── text_utils.py
│   │   ├── layout_utils.py
│   │   ├── font_utils.py
│   │   └── helper_utils.py
│   │
│   ├── celery_tasks/               # Celery async task definitions
│   └── api/                        # GraphQL API (graphql.py)
│
├── templates/                      # Jinja2 HTML templates
│   ├── visual_editor.html          # Full visual editor (5400+ lines, Fabric.js)
│   └── ...                         # 40+ admin / student / email templates
│
├── static/                         # Static assets
│   ├── fonts/                      # Bundled Urdu/Arabic/Hindi fonts
│   ├── uploads/                    # Student photos (local dev)
│   └── generated/                  # Generated card images (local dev)
│
├── migrations/                     # Alembic DB migrations
├── instance/                       # SQLite databases (local dev only)
└── logs/                           # Application log files
```

---

## 🧪 Local Development Setup

### 1. Clone the Repository

```bash
git clone https://github.com/RobertPiyyra/id-card-generator.git
cd id-card-generator
```

### 2. Create and Activate Virtual Environment

```bash
# Create
python -m venv venv

# Activate — Linux / macOS
source venv/bin/activate

# Activate — Windows PowerShell
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy `.env.example` to `.env` and fill in values:

```env
SECRET_KEY=your-secret-key-here
DATABASE_URL=                        # leave blank for local SQLite
STORAGE_BACKEND=local                # or cloudinary
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
REDIS_URL=redis://localhost:6379/0   # optional, falls back gracefully
```

### 5. Run the Application

```bash
python run.py
```

Open in browser: `http://127.0.0.1:5000`

> **Note:** Redis is optional for local development. The app falls back to in-memory caching automatically.

---

## 🌐 Deployment

### Render / Railway

**Build Command:**
```bash
pip install -r requirements.txt
```

**Start Command:**
```bash
gunicorn -c gunicorn.conf.py "app:create_app()" --bind 0.0.0.0:$PORT
```

### Required Environment Variables (Production)

```env
SECRET_KEY=
DATABASE_URL=                    # PostgreSQL connection string
STORAGE_BACKEND=cloudinary
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
REDIS_URL=
REDIS_PUBLIC_URL=
SENTRY_DSN=                      # optional Sentry error tracking
```

---

## 🖊️ Visual Editor — Architecture Notes

The visual editor (`templates/visual_editor.html`) is a self-contained single-page editor built with Fabric.js 5.3.

### State Model

```js
state = {
  language,        // "english" | "urdu" | "hindi" | "arabic"
  direction,       // "ltr" | "rtl"
  font: { ... },   // global font settings (size, color, colon, auto-fit, etc.)
  photo: { ... },  // photo box position, size, shape, frame color, border radii
  qr: { ... },     // QR box position + enable/disable
  barcode: { ... },// barcode box position + enable/disable
  layout: {
    fields: {      // per-field position/color overrides keyed by field name
      [KEY]: { label: {...}, value: {...}, colon: {...} }
    },
    objects: [ ... ]  // custom canvas objects (text, rect, circle, line, image)
  }
}
```

### Save Flow

1. `saveLayout()` calls `applyBlockInputs()` to sync sidebar inputs into `state`
2. `flattenGroupsAndSerialize()` persists canvas object positions into `state.layout.objects`
3. POST to `/admin/save_field_settings` with merged font, photo, qr, and layout_config
4. Backend merges into the `Template` model and commits to DB
5. A version snapshot is automatically created via `template_lifecycle_service`
6. The undo/redo stack is cleared so Ctrl+Z cannot revert past the saved state

### Backend API Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/admin/template_editor/<id>` | Load the visual editor page |
| `GET` | `/editor/get_template_image/<id>` | Serve template background as WebP |
| `GET` | `/admin/get_editor_fields/<id>` | Get raw layout_config JSON |
| `GET` | `/admin/template_settings/<id>` | Get full side-aware settings payload |
| `POST` | `/admin/save_field_settings` | Save all settings from the editor |
| `POST` | `/api/ai/analyze-layout` | AI template region analyzer |
| `POST` | `/api/ai/design-from-prompt` | AI layout generator (prompt → positions) |
| `POST` | `/api/ai/color-palette` | AI color harmony generator |
| `POST` | `/api/ai/validate-design` | AI design quality validator |

---

## 📄 CorelDRAW PDF Export

| Mode | Description |
|---|---|
| **Editable PDF** | Text remains editable in CorelDRAW, QR/barcode as vector objects |
| **Print PDF (600 DPI)** | High-quality raster rendering for professional printing |

---

## 🛡️ Security Features

- CSRF token on every form and AJAX request (`X-CSRFToken` header)
- Password hashing (Werkzeug)
- School-admin data isolation (all queries scoped to `school_name`)
- Signed URL tokens for editor image serving (itsdangerous)
- Rate limiting on auth and public API routes (Flask-Limiter)
- Immutable audit event log (cannot be modified after write)
- Template version snapshots on every visual editor save

---

## 🧪 Running Tests

```bash
pip install -r requirements-dev.txt
pytest
pytest test_corel_export_mode.py
pytest test_face_service.py
```

---

## 📌 Use Cases

- School / college student ID systems
- Madrassa / coaching center enrollment
- NGO and education trust beneficiary cards
- Corporate staff ID cards
- Event badge / access pass generation

---

## 👨‍💻 Author

**Robert Piyyra**  
GitHub: https://github.com/RobertPiyyra

---

## ⭐ Support

If you find this project useful:

- ⭐ Star the repository
- 🐛 Report issues in the GitHub issue tracker
- 🤝 Submit pull requests with improvements
