"""
GraphQL API using Strawberry.

Provides a type-safe, self-documenting API for all operations.
Single endpoint for queries, mutations, and subscriptions.

Usage:
    from app.api.graphql import schema
    # Mount at /graphql
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strawberry Schema Definition
# ---------------------------------------------------------------------------

try:
    import strawberry
    from strawberry.types import Info
    from strawberry.scalars import JSON

    @strawberry.type
    class StudentType:
        id: int
        name: str
        father_name: Optional[str] = None
        class_name: Optional[str] = None
        dob: Optional[str] = None
        phone: Optional[str] = None
        email: Optional[str] = None
        photo_url: Optional[str] = None
        image_url: Optional[str] = None
        pdf_url: Optional[str] = None
        created_at: Optional[str] = None

    @strawberry.type
    class TemplateType:
        id: int
        school_name: str
        card_orientation: str = "landscape"
        is_double_sided: bool = False
        language: str = "english"
        created_at: Optional[str] = None
        student_count: int = 0

    @strawberry.type
    class BulkJobType:
        task_id: str
        state: str
        status: str
        current: int = 0
        total: int = 0
        success_count: int = 0
        error_count: int = 0
        created_at: Optional[str] = None

    @strawberry.type
    class VerificationType:
        id: int
        student_id: int
        status: str
        scanned_at: Optional[str] = None
        ip_address: Optional[str] = None

    @strawberry.type
    class DashboardStatsType:
        total_templates: int = 0
        total_students: int = 0
        total_cards_generated: int = 0
        active_templates: int = 0

    @strawberry.type
    class DesignValidationType:
        valid: bool
        score: int = 0
        issues: JSON = strawberry.field(default_factory=list)
        warnings: JSON = strawberry.field(default_factory=list)

    # ---------------------------------------------------------------------------
    # Input Types
    # ---------------------------------------------------------------------------

    @strawberry.input
    class StudentInput:
        name: str
        father_name: Optional[str] = None
        class_name: Optional[str] = None
        dob: Optional[str] = None
        phone: Optional[str] = None
        email: Optional[str] = None
        address: Optional[str] = None
        template_id: int = 0

    @strawberry.input
    class TemplateInput:
        school_name: str
        card_orientation: Optional[str] = "landscape"
        is_double_sided: Optional[bool] = False
        language: Optional[str] = "english"
        card_width: Optional[int] = 1015
        card_height: Optional[int] = 661

    @strawberry.input
    class BulkGenerateInput:
        template_id: int
        import_mapping_id: Optional[int] = None

    # ---------------------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------------------

    @strawberry.type
    class Query:
        @strawberry.field
        def students(self, info: Info, template_id: Optional[int] = None,
                     search: Optional[str] = None, limit: int = 50,
                     offset: int = 0) -> List[StudentType]:
            """Query students with optional filtering."""
            try:
                from models import Student
                query = Student.query
                if template_id:
                    query = query.filter_by(template_id=template_id)
                if search:
                    query = query.filter(Student.name.ilike(f"%{search}%"))
                students = query.order_by(Student.id.desc()).limit(limit).offset(offset).all()
                return [
                    StudentType(
                        id=s.id, name=s.name, father_name=s.father_name,
                        class_name=s.class_name, dob=s.dob, phone=s.phone,
                        email=getattr(s, 'email', None),
                        photo_url=s.photo_url, image_url=s.image_url,
                        pdf_url=s.pdf_url,
                        created_at=s.created_at.isoformat() if s.created_at else None,
                    ) for s in students
                ]
            except Exception as exc:
                logger.error("GraphQL students query failed: %s", exc)
                return []

        @strawberry.field
        def student(self, info: Info, id: int) -> Optional[StudentType]:
            """Get a single student by ID."""
            try:
                from models import Student
                s = Student.query.get(id)
                if not s:
                    return None
                return StudentType(
                    id=s.id, name=s.name, father_name=s.father_name,
                    class_name=s.class_name, dob=s.dob, phone=s.phone,
                    email=getattr(s, 'email', None),
                    photo_url=s.photo_url, image_url=s.image_url,
                    pdf_url=s.pdf_url,
                    created_at=s.created_at.isoformat() if s.created_at else None,
                )
            except Exception as exc:
                logger.error("GraphQL student query failed: %s", exc)
                return None

        @strawberry.field
        def templates(self, info: Info, search: Optional[str] = None,
                      limit: int = 50) -> List[TemplateType]:
            """Query templates."""
            try:
                from models import Template
                query = Template.query
                if search:
                    query = query.filter(Template.school_name.ilike(f"%{search}%"))
                templates = query.order_by(Template.id.desc()).limit(limit).all()
                return [
                    TemplateType(
                        id=t.id, school_name=t.school_name,
                        card_orientation=t.card_orientation or "landscape",
                        is_double_sided=t.is_double_sided or False,
                        language=t.language or "english",
                        created_at=t.created_at.isoformat() if t.created_at else None,
                        student_count=t.students.count() if hasattr(t, 'students') else 0,
                    ) for t in templates
                ]
            except Exception as exc:
                logger.error("GraphQL templates query failed: %s", exc)
                return []

        @strawberry.field
        def bulk_jobs(self, info: Info, limit: int = 20) -> List[BulkJobType]:
            """Query recent bulk jobs."""
            try:
                from app.services.bulk_job_service import _list_bulk_job_states
                jobs = _list_bulk_job_states(limit=limit)
                return [
                    BulkJobType(
                        task_id=j.get("task_id", ""),
                        state=j.get("state", "UNKNOWN"),
                        status=j.get("status", ""),
                        current=j.get("current", 0),
                        total=j.get("total", 0),
                        success_count=j.get("success_count", 0),
                        error_count=j.get("error_count", 0),
                        created_at=j.get("created_at"),
                    ) for j in jobs
                ]
            except Exception as exc:
                logger.error("GraphQL bulk_jobs query failed: %s", exc)
                return []

        @strawberry.field
        def dashboard_stats(self, info: Info) -> DashboardStatsType:
            """Get dashboard statistics."""
            try:
                from app.services.analytics_service import get_dashboard_stats
                stats = get_dashboard_stats()
                return DashboardStatsType(**stats)
            except Exception as exc:
                logger.error("GraphQL dashboard_stats query failed: %s", exc)
                return DashboardStatsType()

        @strawberry.field
        def verifications(self, info: Info, student_id: Optional[int] = None,
                          limit: int = 50) -> List[VerificationType]:
            """Query verification audit logs."""
            try:
                from models import VerificationAudit
                query = VerificationAudit.query
                if student_id:
                    query = query.filter_by(student_id=student_id)
                audits = query.order_by(VerificationAudit.id.desc()).limit(limit).all()
                return [
                    VerificationType(
                        id=a.id, student_id=a.student_id,
                        status=a.status or "unknown",
                        scanned_at=a.scanned_at.isoformat() if a.scanned_at else None,
                        ip_address=a.ip_address,
                    ) for a in audits
                ]
            except Exception as exc:
                logger.error("GraphQL verifications query failed: %s", exc)
                return []

    # ---------------------------------------------------------------------------
    # Mutations
    # ---------------------------------------------------------------------------

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def create_student(self, info: Info, input: StudentInput) -> StudentType:
            """Create a new student."""
            from models import db, Student
            student = Student(
                name=input.name,
                father_name=input.father_name,
                class_name=input.class_name,
                dob=input.dob,
                phone=input.phone,
                template_id=input.template_id,
            )
            db.session.add(student)
            db.session.commit()
            return StudentType(
                id=student.id, name=student.name,
                father_name=student.father_name,
                class_name=student.class_name,
            )

        @strawberry.mutation
        def delete_student(self, info: Info, id: int) -> bool:
            """Delete a student."""
            from models import db, Student
            student = Student.query.get(id)
            if student:
                db.session.delete(student)
                db.session.commit()
                return True
            return False

        @strawberry.mutation
        def validate_design(self, info: Info, layout_config: JSON) -> DesignValidationType:
            """Validate a template design."""
            try:
                from app.services.ai_layout import validate_design
                config_dict = layout_config if isinstance(layout_config, dict) else json.loads(str(layout_config))
                result = validate_design(config_dict)
                return DesignValidationType(
                    valid=result["valid"],
                    score=result["score"],
                    issues=result["issues"],
                    warnings=result["warnings"],
                )
            except Exception as exc:
                logger.error("GraphQL validate_design failed: %s", exc)
                return DesignValidationType(valid=False, score=0, issues=[{"error": str(exc)}])

        @strawberry.mutation
        def generate_color_palette(self, info: Info, base_color: str,
                                    scheme: str = "complementary") -> List[str]:
            """Generate a color palette."""
            from app.services.ai_layout import generate_color_palette
            return generate_color_palette(base_color, scheme)

    # ---------------------------------------------------------------------------
    # Schema
    # ---------------------------------------------------------------------------

    schema = strawberry.Schema(query=Query, mutation=Mutation)
    GRAPHQL_AVAILABLE = True

except ImportError:
    logger.warning("strawberry-graphql not installed — GraphQL API disabled")
    schema = None
    GRAPHQL_AVAILABLE = False


# ---------------------------------------------------------------------------
# GraphQL View (for mounting in Flask)
# ---------------------------------------------------------------------------

def init_graphql_view(app):
    """
    Mount the GraphQL endpoint on the Flask app.

    Usage:
        from app.api.graphql import init_graphql_view
        init_graphql_view(app)
    """
    if not GRAPHQL_AVAILABLE:
        logger.warning("GraphQL not available — skipping")
        return

    try:
        from strawberry.flask.views import GraphQLView

        app.add_url_rule(
            "/graphql",
            view_func=GraphQLView.as_view(
                "graphql",
                schema=schema,
                graphiql=True,  # enable GraphQL Playground
                allow_queries_via_get=True,
            ),
        )
        logger.info("GraphQL API mounted at /graphql")

    except ImportError:
        logger.warning("strawberry-graphql[flask] not installed")
