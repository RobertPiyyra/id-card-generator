from app.routes.corel_routes import corel_bp
from app.routes.editor_routes import editor_bp
from app.routes.auth_routes import auth_bp
from app.routes.api_routes import api_bp
from app.routes.dashboard_routes import dashboard_bp
from app.routes.verify_routes import verify_bp
from app.routes.enterprise_routes import enterprise_bp

__all__ = ["corel_bp", "editor_bp", "auth_bp", "api_bp", "dashboard_bp", "verify_bp", "enterprise_bp"]
