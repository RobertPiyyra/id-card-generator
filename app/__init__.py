from dotenv import load_dotenv

load_dotenv()

_app = None
_db = None


def create_app(config_object=None):
    """
    Application factory entrypoint.

    The existing production app is initialized in `app.legacy_app` to preserve
    import-time migration, scheduler, route, rendering, Redis, and deployment behavior.
    """
    global _app, _db
    if _app is None:
        from app.legacy_app import app as legacy_app, db as legacy_db

        _app = legacy_app
        _db = legacy_db

        # Register Blueprints
        from app.routes import auth_bp, api_bp, dashboard_bp, corel_bp, editor_bp
        from app.legacy_app import student_bp
        _app.register_blueprint(auth_bp)
        _app.register_blueprint(api_bp)
        _app.register_blueprint(dashboard_bp)
        _app.register_blueprint(corel_bp, url_prefix='/corel')
        _app.register_blueprint(editor_bp)
        _app.register_blueprint(student_bp)

    if config_object:
        _app.config.from_object(config_object)
    return _app


def __getattr__(name):
    if name == "app":
        return create_app()
    if name == "db":
        create_app()
        return _db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
