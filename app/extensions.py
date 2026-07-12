import os
from apscheduler.schedulers.background import BackgroundScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from models import db  # noqa: F401 — exported for legacy imports

csrf = CSRFProtect()
limiter = Limiter(
    get_remote_address,
    default_limits=["12000 per day", "1000 per hour"],
    storage_uri=os.environ.get("REDIS_URL", "memory://"),
)
scheduler = BackgroundScheduler()
