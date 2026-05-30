from app.routes import corel_routes as _corel_routes
from app.routes.corel_routes import *  # noqa: F401,F403

for _name in dir(_corel_routes):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_corel_routes, _name)
