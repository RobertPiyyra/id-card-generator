"""
Application entry point.
Development:  python run.py
Production:    gunicorn -c gunicorn.conf.py "app:create_app()"
"""
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "").lower() in ("development", "dev", "local")
    app.run(host="0.0.0.0", port=port, debug=debug)
