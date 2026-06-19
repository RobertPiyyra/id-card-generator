"""
Tests for Enterprise Features
Tests are isolated and do not modify existing test fixtures.
"""
import pytest
import json
import io
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Import new models
from models import (
    Organization, Branch, Department, LoginHistory, UserSession,
    ApiKey, WebhookEndpoint, AccessPolicy, SystemMetric,
    DataArchive, OcrResult, TwoFactorBackupCode
)


class TestOrganizationModel:
    """Test Organization CRUD operations."""

    def test_create_organization(self, db_session):
        org = Organization(name="Test School", slug="test-school")
        db_session.add(org)
        db_session.commit()
        assert org.id is not None
        assert org.is_active is True
        assert org.max_users == 10

    def test_organization_relationships(self, db_session):
        org = Organization(name="Rel Test", slug="rel-test")
        db_session.add(org)
        db_session.commit()

        branch = Branch(organization_id=org.id, name="Main Branch", code="MAIN")
        db_session.add(branch)
        db_session.commit()

        assert org.branches.count() == 1
        assert org.branches.first().name == "Main Branch"

    def test_department_hierarchy(self, db_session):
        org = Organization(name="Dept Test", slug="dept-test")
        db_session.add(org)
        db_session.commit()

        parent = Department(organization_id=org.id, name="Engineering", code="ENG")
        db_session.add(parent)
        db_session.commit()

        child = Department(organization_id=org.id, name="Frontend", code="FE", parent_id=parent.id)
        db_session.add(child)
        db_session.commit()

        assert parent.children.count() == 1
        assert child.parent.name == "Engineering"

    def test_cascade_delete_organization(self, db_session):
        org = Organization(name="Cascade Test", slug="cascade-test")
        db_session.add(org)
        db_session.commit()

        Branch(organization_id=org.id, name="Branch 1", code="B1")
        ApiKey(organization_id=org.id, name="Key 1", key_prefix="abc", key_hash="hash1")
        db_session.commit()

        org_id = org.id
        db_session.delete(org)
        db_session.commit()

        assert Organization.query.get(org_id) is None
        assert Branch.query.filter_by(organization_id=org_id).count() == 0


class TestLoginHistory:
    """Test login history tracking."""

    def test_record_login(self, db_session):
        entry = LoginHistory(
            username="admin",
            ip_address="127.0.0.1",
            login_success=True,
            device_type="desktop",
        )
        db_session.add(entry)
        db_session.commit()
        assert entry.id is not None
        assert entry.login_success is True

    def test_failed_login(self, db_session):
        entry = LoginHistory(
            username="admin",
            ip_address="192.168.1.1",
            login_success=False,
            failure_reason="Invalid password",
        )
        db_session.add(entry)
        db_session.commit()
        assert entry.login_success is False
        assert entry.failure_reason == "Invalid password"


class TestSecurityService:
    """Test security service functions."""

    def test_totp_generation(self):
        from app.services.security_service import _generate_totp_secret, _verify_totp
        secret = _generate_totp_secret()
        assert len(secret) > 0

    def test_device_fingerprint(self):
        from app.services.security_service import _generate_device_fingerprint
        fp = _generate_device_fingerprint("Mozilla/5.0", "127.0.0.1")
        assert len(fp) == 32

    def test_parse_user_agent(self):
        from app.services.security_service import _parse_user_agent
        result = _parse_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")
        assert result['device_type'] == 'desktop'
        assert 'Chrome' in result['browser']

    def test_empty_user_agent(self):
        from app.services.security_service import _parse_user_agent
        result = _parse_user_agent("")
        assert result['device_type'] == 'unknown'


class TestWebhookService:
    """Test webhook delivery system."""

    def test_sign_payload(self):
        from app.services.webhook_service import _sign_payload
        sig = _sign_payload('{"test": true}', 'secret123')
        assert len(sig) == 64  # SHA-256 hex

    def test_webhook_events_list(self):
        from app.services.webhook_service import WEBHOOK_EVENTS
        assert 'student.created' in WEBHOOK_EVENTS
        assert 'template.approved' in WEBHOOK_EVENTS
        assert len(WEBHOOK_EVENTS) >= 8


class TestReportService:
    """Test report generation."""

    def test_csv_generation(self):
        from app.services.report_service import generate_student_report
        from models import Student
        buf = generate_student_report(format='csv')
        assert buf is not None
        content = buf.read().decode('utf-8')
        assert 'Name' in content or 'ID' in content


class TestArchiveService:
    """Test data archiving."""

    def test_list_archives_empty(self):
        from app.services.archive_service import list_archives
        result = list_archives()
        assert isinstance(result, list)


class TestSearchService:
    """Test advanced search."""

    def test_search_students_empty(self):
        from app.services.search_service import search_students
        result = search_students(query="nonexistent_xyz_123")
        assert result['total'] == 0
        assert result['page'] == 1

    def test_search_pagination(self):
        from app.services.search_service import search_students
        result = search_students(page=1, per_page=5)
        assert result['per_page'] == 5


class TestApiKeyModel:
    """Test API key model."""

    def test_api_key_fields(self, db_session):
        org = Organization(name="API Test", slug="api-test")
        db_session.add(org)
        db_session.commit()

        key = ApiKey(
            organization_id=org.id,
            name="Test Key",
            key_prefix="test123",
            key_hash="hashvalue",
            scopes={"read": True, "write": False},
        )
        db_session.add(key)
        db_session.commit()

        assert key.id is not None
        assert key.is_active is True
        assert key.key_prefix == "test123"


class TestSystemMetric:
    """Test system metrics model."""

    def test_metric_creation(self, db_session):
        metric = SystemMetric(
            metric_name='cpu_percent',
            metric_value=45.2,
            metric_unit='percent',
        )
        db_session.add(metric)
        db_session.commit()
        assert metric.id is not None
        assert metric.metric_value == 45.2


class TestAccessPolicy:
    """Test access policy model."""

    def test_policy_creation(self, db_session):
        org = Organization(name="Policy Test", slug="policy-test")
        db_session.add(org)
        db_session.commit()

        policy = AccessPolicy(
            organization_id=org.id,
            role="school_admin",
            resource="students",
            action="create",
            allowed=True,
        )
        db_session.add(policy)
        db_session.commit()

        assert policy.id is not None
        assert policy.allowed is True
