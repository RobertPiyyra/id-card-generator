"""Public template-selection flow.

This module adds the explicit workflow shown in the product flow:
Choose Template -> Login -> Student Dashboard/Form.

It deliberately reuses the existing Template and TemplateWorkflow models and
never exposes draft/review templates to public users.
"""

import logging
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash

from models import db, Student, Template, TemplateWorkflow
from app.extensions import limiter
from app.services.core_services import _normalize_school_name

logger = logging.getLogger(__name__)

template_flow_bp = Blueprint("template_flow", __name__)


def _published_templates():
    """Return templates explicitly published by the existing admin workflow."""
    rows = (
        db.session.query(Template, TemplateWorkflow)
        .join(TemplateWorkflow, TemplateWorkflow.template_id == Template.id)
        .filter(TemplateWorkflow.state == "published")
        .order_by(Template.created_at.desc())
        .all()
    )
    return [template for template, _workflow in rows]


def _template_card(template):
    """Serialize only the public metadata required by the gallery."""
    image_url = template.template_url
    if not image_url and template.filename:
        image_url = url_for("static", filename=str(template.filename).lstrip("/"))
    return {
        "id": template.id,
        "name": template.filename or f"Template {template.id}",
        "school_name": template.school_name,
        "image_url": image_url,
        "orientation": template.card_orientation or "landscape",
        "double_sided": bool(template.is_double_sided),
    }


@template_flow_bp.route("/student_login/templates", methods=["GET"])
def choose_template():
    """Public template gallery; only published templates are selectable."""
    templates = [_template_card(t) for t in _published_templates()]
    selected_id = request.args.get("template_id", type=int)
    return render_template(
        "template_gallery.html",
        templates=templates,
        selected_id=selected_id,
    )


@template_flow_bp.route("/student_login/template-login/<int:template_id>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def template_login(template_id):
    """Authenticate a student against the exact template selected in the gallery."""
    template = (
        db.session.query(Template)
        .join(TemplateWorkflow, TemplateWorkflow.template_id == Template.id)
        .filter(
            Template.id == template_id,
            TemplateWorkflow.state == "published",
        )
        .first()
    )
    if not template:
        flash("That template is not currently available.", "error")
        return redirect(url_for("template_flow.choose_template"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            error = "Email and password are required."
        else:
            accounts = Student.query.filter(
                db.func.lower(Student.email) == email,
                Student.password.isnot(None),
            ).order_by(Student.created_at.asc()).all()
            student = next(
                (
                    row for row in accounts
                    if _normalize_school_name(row.school_name)
                    == _normalize_school_name(template.school_name)
                ),
                None,
            )

            if not student:
                error = "No account found for this email and school."
            elif not check_password_hash(student.password, password):
                error = "Invalid password."
            else:
                session.clear()
                session["student_email"] = student.email
                session["student_school_name"] = student.school_name or template.school_name
                session["student_template_id"] = template.id
                session.modified = True
                logger.info(
                    "Template-first student login: email=%s template_id=%s school=%s",
                    student.email,
                    template.id,
                    template.school_name,
                )
                return redirect(url_for("dashboard.index"))

    return render_template(
        "template_flow_login.html",
        template=_template_card(template),
        error=error,
    )


__all__ = ["template_flow_bp"]
