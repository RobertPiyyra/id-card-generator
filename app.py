from app import app, create_app, db

__all__ = ["app", "create_app", "db"]


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
