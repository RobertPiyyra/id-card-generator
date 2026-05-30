from app.routes import editor_routes as _editor_routes
from app.routes.editor_routes import *  # noqa: F401,F403

for _name in dir(_editor_routes):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_editor_routes, _name)
