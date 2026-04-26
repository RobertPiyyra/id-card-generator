🎓 ID Card Generator – Multi-School System

A production-ready web application to generate student ID cards with automatic photo processing, background removal, face-aware cropping, QR codes, and printable layouts.
Built for schools, madrassas, institutes, and colleges.

🚀 Features
🪪 ID Card Generation

Dynamic ID card templates (portrait & landscape)

Supports multiple schools / institutions

Bulk & individual card generation

Printable high-quality output (JPEG / PDF)

🖼️ Smart Photo Processing

Automatic face detection & cropping

AI background removal (Rembg + ONNX)

Hijab / cap / turban safe cropping

Transparent edge trimming (no extra white space)

Configurable background colors

✍️ Dynamic Text & Layout

Editable fields (Name, Class, Address, etc.)

Auto text wrapping & font scaling

RTL / Arabic / Urdu support

Custom fonts per template

🔐 Admin & Editor

Admin panel for templates & students

Editable fields per template

Secure authentication (Flask-Login)

Activity logging

⚙️ Technical

Flask + Gunicorn production server

MediaPipe face detection

OpenCV image handling

SQLAlchemy database

QR code generation

CSRF protection

Rate limiting

🧠 Tech Stack

Backend: Python, Flask

Frontend: Jinja2, HTML, CSS

Image Processing: Pillow, OpenCV, MediaPipe

AI Background Removal: rembg (ONNX Runtime)

Database: SQLite / SQLAlchemy

Server: Gunicorn

Deployment: Render (HTTPS enabled)

📁 Project Structure
id-card-generator/
│
├── app.py
├── utils.py
├── models.py
├── editor_routes.py
├── corel_routes.py
│
├── templates/
├── static/
│   ├── fonts/
│   ├── generated/
│   └── uploads/
│
├── requirements.txt
├── runtime.txt
├── .gitignore
└── README.md

🧪 Local Development Setup
1️⃣ Clone Repository
git clone https://github.com/RobertPiyyra/id-card-generator.git
cd id-card-generator

2️⃣ Create Virtual Environment
python -m venv id_venv

3️⃣ Activate Virtual Environment

Windows (PowerShell):

.\id_venv\Scripts\Activate.ps1


Linux / macOS:

source id_venv/bin/activate

4️⃣ Install Dependencies
pip install -r requirements.txt

5️⃣ Run App
python app.py


Open in browser:

http://127.0.0.1:5000

🌐 Deployment (Render)
Build Command
pip install -r requirements.txt

Start Command
gunicorn app:app --bind 0.0.0.0:$PORT


⚠️ Important: The app must bind to $PORT for Render.

HTTPS

Render automatically provides HTTPS

No SSL configuration needed in Flask

🚆 Deployment (Railway)

Attach Railway Postgres and set:

DATABASE_URL=<Railway Postgres connection URL>
SECRET_KEY=<long random secret>
STORAGE_BACKEND=cloudinary
CLOUDINARY_CLOUD_NAME=<your Cloudinary cloud name>
CLOUDINARY_API_KEY=<your Cloudinary API key>
CLOUDINARY_API_SECRET=<your Cloudinary API secret>

Attach Railway Redis and set:

REDIS_URL=<Railway Redis connection URL>
REDIS_PUBLIC_URL=<Railway public TCP proxy Redis URL>

Redis is optional. The app tries REDIS_URL first and automatically falls back to REDIS_PUBLIC_URL when the private Railway hostname is not reachable. If both are unavailable, it skips Redis caching/RQ queueing and continues rendering cards.

Use `redis.railway.internal` only inside Railway services in the same project/environment as Redis. For local testing, use Railway's public TCP proxy URL/REDIS_PUBLIC_URL.

MediaPipe requires native libraries on Railway. The Dockerfile installs:

libgl1
libglib2.0-0
libsm6
libxext6
libxrender1
libgomp1

These packages fix libGL.so.1 errors during MediaPipe/OpenGL imports.

The app creates these fallback static files at startup if they are missing:

static/placeholder.jpg
static/photo_placeholder.png
static/logos/qr_placeholder.png

📦 Environment Notes

Do NOT commit virtual environments

.venv, id_venv, env are ignored via .gitignore

Fonts can be committed if legally allowed

Uploads & generated files should stay ignored

📊 Performance (Free Plan – Render)

Cold start delay: ~30–50 seconds

Suitable for:

Multiple schools

Small to medium daily usage

Recommended upgrade for:

High traffic

Bulk generation

Faster response times

⚠️ Known Limitations (Free Tier)

Single worker

No GPU (CPU-only AI)

Cold starts after inactivity

📄 Corel PDF Export Modes (New)

Editable PDF: text and QR/barcode are exported as vector/text objects for CorelDRAW editing.

Print PDF (600 DPI): optimized for output quality with high-resolution raster assets.

Compatibility: generated Corel PDFs target PDF 1.4.

🛡️ Security

CSRF protection enabled

Password hashing

Rate limiting

Secure file handling

📌 Use Cases

Schools & Colleges

Madrassas

Coaching Centers

Training Institutes

NGOs & Education Trusts

👨‍💻 Author

Robert Piyyra
GitHub: https://github.com/RobertPiyyra

⭐ Support

If you find this project useful:

⭐ Star the repository

🐛 Report issues

🤝 Contribute improvements



