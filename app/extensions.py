from apscheduler.schedulers.background import BackgroundScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

from app.models import db


csrf = CSRFProtect()
limiter = Limiter(
    get_remote_address,
    default_limits=["12000 per day", "1000 per hour"], # Global default
    storage_uri="memory://" # Store limits in memory
)
scheduler = BackgroundScheduler()
