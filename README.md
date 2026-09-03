# 🎓 ID Card Generator – Multi-School Smart ID System

A production-ready Flask web application for generating high-quality student ID cards with AI-powered photo processing, QR codes, barcode support, multilingual rendering, and bulk printing.

Designed for:
- Schools
- Colleges
- Madrassas
- Coaching Centers
- Training Institutes
- NGOs & Education Trusts

---

# 🚀 Features

## 🪪 Smart ID Card Generation
- Portrait & landscape templates
- Bulk and individual card generation
- Multi-school support
- Printable HD output
- JPEG & PDF export
- CorelDRAW editable PDF support

## 🖼️ AI Photo Processing
- Face-aware automatic cropping
- AI background removal (Rembg + ONNX)
- Transparent edge trimming
- Hijab / turban safe cropping
- Rounded corner support
- Dynamic photo placement

## 🔳 QR & Barcode System
- Dynamic QR generation
- Code128 barcode support
- Custom payload support
- JSON / URL / Text QR modes
- Cached rendering for performance

## 🌍 Multilingual Rendering
- English
- Urdu
- Hindi
- Arabic
- RTL text support
- Dynamic font switching

## 🔐 Admin Features
- Secure authentication
- Template editor
- Student management
- Activity logging
- Notification system
- Rate limiting
- CSRF protection

## ⚡ Performance Optimizations
- Redis caching
- Cache stampede protection
- WEBP optimization
- Lazy Redis connection
- Background task queue (RQ)
- ThreadPoolExecutor rendering

---

# 🧠 Tech Stack

## Backend
- Python
- Flask
- SQLAlchemy
- Gunicorn

## Image Processing
- Pillow
- OpenCV
- MediaPipe
- rembg (ONNX Runtime)

## Database
- SQLite
- PostgreSQL (Railway)

## Queue & Cache
- Redis
- RQ

## PDF & Export
- ReportLab
- PyMuPDF

---

# 📁 Project Structure

```bash
id-card-generator/
│
├── run.py                   # Application entry point (dev: python run.py)
├── models.py                # SQLAlchemy data models (~30 tables)
├── utils.py                 # Template/image/font utility functions
├── cloudinary_config.py     # Cloudinary upload helper
├── notifications.py         # Email + SMS notification logic
├── manage.py                # CLI management commands
├── gunicorn.conf.py         # Gunicorn config for production
├── conftest.py              # Shared pytest fixtures
│
├── app/                     # Main application package
│   ├── __init__.py          # App factory (create_app)
│   ├── config.py            # Config classes (Dev/Prod/Testing)
│   ├── legacy_app.py        # Core Flask app, DB init, migrations
│   ├── extensions.py        # Flask extensions (csrf, limiter, scheduler)
│   ├── middleware.py         # Production middleware
│   ├── auth_decorators.py   # Auth decorator definitions
│   ├── error_handlers.py    # Error handler registrations
│   ├── helpers.py            # Template/cache-bust helpers
│   ├── decorators.py        # Legacy decorator re-exports
│   ├── logging_config.py    # Structured JSON logging
│   ├── observability.py     # Prometheus metrics, health checks
│   ├── performance.py       # Caching, lazy loading, connection pooling
│   ├── celery_config.py     # Celery task queue setup
│   │
│   ├── routes/              # HTTP route blueprints
│   │   ├── dashboard_routes.py   # Main admin/student card pages
│   │   ├── auth_routes.py        # Login, logout, password reset
│   │   ├── api_routes.py         # Internal API endpoints
│   │   ├── corel_routes.py       # CorelDRAW PDF export
│   │   ├── editor_routes.py      # Visual template editor
│   │   ├── verify_routes.py      # Card verification
│   │   ├── ai_routes.py          # AI Design Studio API
│   │   ├── serial_batch_routes.py # Photo-first batch generation
│   │   ├── enterprise_routes.py  # Enterprise admin features
│   │   ├── rest_api.py           # RESTful API endpoints
│   │   ├── analytics_routes.py   # Analytics dashboard
│   │   └── faq_routes.py         # FAQ pages
│   │
│   ├── services/            # Business logic layer
│   │   ├── render_service.py     # Card image rendering (PIL)
│   │   ├── photo_service.py      # Photo processing, face crop
│   │   ├── ai_layout.py          # AI layout analysis
│   │   ├── serial_batch_service.py # Serial batch CRUD
│   │   ├── redis_service.py      # Redis cache/client
│   │   ├── template_upload_service.py
│   │   ├── student_service.py
│   │   ├── layout_service.py
│   │   ├── translation_service.py
│   │   └── ...                   # 30+ service modules
│   │
│   ├── utils/               # Internal utilities
│   │   ├── image_utils.py
│   │   ├── text_utils.py
│   │   ├── layout_utils.py
│   │   ├── font_utils.py
│   │   └── helper_utils.py
│   │
│   └── api/                 # GraphQL API
│       └── graphql.py
│
├── templates/               # Jinja2 HTML templates
├── static/                  # Fonts, images, uploads, generated cards
├── migrations/              # Alembic DB migrations
├── instance/                # SQLite databases (local dev)
│
├── requirements.txt         # Python dependencies
├── requirements-dev.txt     # Dev/test dependencies
└── README.md
```

---

# 🧪 Local Development Setup

## 1️⃣ Clone Repository

```bash
git clone https://github.com/RobertPiyyra/id-card-generator.git
cd id-card-generator
```

## 2️⃣ Create Virtual Environment

```bash
python -m venv id_venv
```

## 3️⃣ Activate Environment

### Windows

```powershell
.\id_venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
source id_venv/bin/activate
```

## 4️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

## 5️⃣ Run Application

```bash
python run.py
```

Open browser:

```text
http://127.0.0.1:5000
```

---

# 🌐 Render Deployment

## Build Command

```bash
pip install -r requirements.txt
```

## Start Command

```bash
gunicorn -c gunicorn.conf.py "app:create_app()" --bind 0.0.0.0:$PORT
```

---

# 🚆 Railway Deployment

## Required Environment Variables

```env
DATABASE_URL=
SECRET_KEY=
STORAGE_BACKEND=cloudinary
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
REDIS_URL=
REDIS_PUBLIC_URL=
```

---

# 📄 Corel PDF Export Modes

## Editable PDF
- Text remains editable
- QR and barcode remain vector objects
- Compatible with CorelDRAW

## Print PDF (600 DPI)
- High-quality raster rendering
- Optimized for professional printing

---

# 🛡️ Security Features

- CSRF Protection
- Password hashing
- Secure file handling
- Rate limiting
- Redis-safe fallback system
- Activity logging

---

# 📊 Performance Notes

## Free Hosting Limitations
- Cold starts possible
- CPU-only AI rendering
- Single worker limitations

## Optimizations Included
- Redis caching
- WEBP conversion
- Background processing
- Smart QR caching
- Media caching

---

# 📌 Use Cases

- School ID systems
- Student database systems
- Coaching institutes
- Digital card generation
- Smart education management

---

# 👨‍💻 Author

## Robert Piyyra
GitHub:
https://github.com/RobertPiyyra

---

# ⭐ Support

If you find this project useful:

- ⭐ Star the repository
- 🐛 Report issues
- 🤝 Contribute improvements
