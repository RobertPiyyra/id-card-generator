"""
Shared pytest fixtures for the ID Card Generator test suite.
"""
import os
import sys
import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app as flask_app, db as database


@pytest.fixture(scope="session")
def app():
    """Create a test Flask application."""
    flask_app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
        "SECRET_KEY": "test-secret-key-for-testing-only",
    })
    return flask_app


@pytest.fixture(scope="function")
def db(app):
    """Create a fresh database for each test."""
    with app.app_context():
        database.create_all()
        yield database
        database.session.rollback()
        database.drop_all()


@pytest.fixture(scope="function")
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture(scope="function")
def admin_client(client, app):
    """Create a test client with admin session."""
    with client.session_transaction() as sess:
        sess["admin"] = True
    return client
