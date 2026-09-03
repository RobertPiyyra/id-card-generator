"""
Shared pytest fixtures for the ID Card Generator test suite.

Environment is forced to "testing" BEFORE the app is imported so that
app.config.get_config() selects TestingConfig (in-memory SQLite, CSRF off).
Importantly, legacy_app.py skips its runtime DB migration block when
TESTING is True — the harness owns schema setup via db.create_all().
"""
import os
import sys

import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force testing mode BEFORE importing the app so the app factory picks
# TestingConfig and skips import-time DB migrations / disk cleanup.
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")

from app import app as flask_app, db as database  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """Return the singleton test Flask application.

    The app is created once per session for speed. Per-test schema setup
    is handled by the function-scoped `db` fixture below.
    """
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
    """Create a fresh in-memory database schema for each test, then tear it down.

    This guarantees test isolation: no state leaks between tests even though
    the app object itself is session-scoped.
    """
    with app.app_context():
        database.create_all()
        yield database
        database.session.remove()
        database.drop_all()


@pytest.fixture(scope="function")
def client(app, db):
    """Create a test client with a ready-to-use database context.

    Depends on `db` so the schema exists before any request hits a route.
    """
    return app.test_client()


@pytest.fixture(scope="function")
def admin_client(client, app):
    """A test client pre-authenticated as a full admin (super_admin)."""
    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["admin_role"] = "super_admin"
    return client


@pytest.fixture(scope="function")
def school_admin_client(client, app):
    """A test client authenticated as a school-scoped admin."""
    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["admin_role"] = "school_admin"
        sess["admin_school"] = "Test School"
    return client


@pytest.fixture(scope="function")
def student_client(client, app):
    """A test client authenticated as a logged-in student."""
    with client.session_transaction() as sess:
        sess["student_email"] = "student@test.com"
    return client


@pytest.fixture
def make_template(db):
    """Factory fixture: create and persist a minimal Template, return it."""
    from models import Template

    def _make(school_name="Test School", **overrides):
        tmpl = Template(
            school_name=school_name,
            filename=None,
            template_url=None,
            card_orientation="landscape",
            language="english",
            text_direction="ltr",
        )
        for key, value in overrides.items():
            setattr(tmpl, key, value)
        database.session.add(tmpl)
        database.session.commit()
        return tmpl
    return _make


@pytest.fixture
def make_student(db):
    """Factory fixture: create and persist a Student, return it."""
    from models import Student

    def _make(name="Test Student", template_id=1, **overrides):
        student = Student(name=name, template_id=template_id, school_name="Test School")
        for key, value in overrides.items():
            setattr(student, key, value)
        database.session.add(student)
        database.session.commit()
        return student
    return _make
