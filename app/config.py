import os


def _database_url():
    # Prefer modern `DATABASE_URL` (Railway/Heroku style), but also support the more typical
    # Flask-SQLAlchemy env var name used in many local setups.
    database_url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")

    # Fix old Railway / Heroku postgres:// URLs
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    # If still no DATABASE_URL, use SQLite🔥
    if not database_url:
        database_url = "sqlite:///local_dev.db"

    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Check Railway Variables.")

    return database_url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    TEMPLATES_AUTO_RELOAD = True

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    EMAIL_FROM = os.environ.get("EMAIL_FROM")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

    REDIS_URL = (os.environ.get("REDIS_URL") or "").strip()
    REDIS_PUBLIC_URL = (os.environ.get("REDIS_PUBLIC_URL") or "").strip()
    REDIS_CACHE_TTL = int(os.environ.get("REDIS_CACHE_TTL", "86400"))
    REDIS_CONNECT_TIMEOUT = float(os.environ.get("REDIS_CONNECT_TIMEOUT", "2"))
    REDIS_SOCKET_TIMEOUT = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "2"))
    REDIS_RETRY_SECONDS = int(os.environ.get("REDIS_RETRY_SECONDS", "30"))


class DevelopmentConfig(Config):
    SESSION_COOKIE_SECURE = False
    TEMPLATES_AUTO_RELOAD = True


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    TEMPLATES_AUTO_RELOAD = False
    PREFERRED_URL_SCHEME = "https"

    # CORS
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "").strip()

    # Sentry
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
    SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "production")
    SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME", "3600"))

    # SQLAlchemy production tuning
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
    }



def get_config():
    env_name = (os.getenv("FLASK_ENV") or os.getenv("APP_ENV") or "").strip().lower()
    if env_name in {"development", "dev", "local"}:
        return DevelopmentConfig

    return ProductionConfig
