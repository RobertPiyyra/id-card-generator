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
├── app.py
├── models.py
├── utils.py
├── editor_routes.py
├── corel_routes.py
│
├── templates/
├── static/
│   ├── fonts/
│   ├── uploads/
│   └── generated/
│
├── requirements.txt
├── runtime.txt
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
python app.py
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
gunicorn app:app --bind 0.0.0.0:$PORT
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
