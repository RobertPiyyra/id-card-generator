from app import app
from app.config import DevelopmentConfig, get_config


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    debug = get_config() is DevelopmentConfig
    app.run(host="0.0.0.0", port=port, debug=debug)
