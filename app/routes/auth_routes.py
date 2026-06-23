from app.legacy_app import admin_required, super_admin_required, school_admin_required, student_required
import os
import random
import string
import re
import logging
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

from models import db, Student, AdminUser, Template
from app.extensions import limiter, csrf
from app.services.core_services import get_templates, log_activity, send_email, _find_template_dict_by_school, _normalize_school_name

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

from app.extensions import limiter

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        # Load from Environment Variables
        env_user = os.environ.get("ADMIN_USERNAME")
        env_hash = os.environ.get("ADMIN_PASSWORD_HASH")

        # Security Check: Ensure env vars are set
        if not env_user or not env_hash:
            logger.error("Admin credentials not set in environment variables!")
            return render_template("login.html", error="Server configuration error. Contact support."), 500

        # Check Username and Password Hash
        username_input = request.form.get("username", "")
        password_input = request.form.get("password", "")

        # 1. Check DB for RBAC user (School Admin or custom Super Admin)
        admin_user = AdminUser.query.filter(db.func.lower(AdminUser.username) == username_input.lower()).first()
        
        if admin_user and check_password_hash(admin_user.password_hash, password_input):
            session.clear()
            session["admin"] = True
            session["admin_role"] = admin_user.role
            session["admin_school"] = admin_user.school_name
            logger.info(f"Admin logged in successfully: {username_input} ({admin_user.role})")
            return redirect("/admin")
            
        # 2. Fallback to Root Super Admin via Env Vars
        if env_user and env_hash and username_input == env_user and check_password_hash(env_hash, password_input):
            session.clear()
            session["admin"] = True
            session["admin_role"] = "super_admin"
            session["admin_school"] = None
            logger.info("SuperAdmin logged in via environment variables")
            return redirect("/admin")
            
        logger.warning("Failed login attempt: Invalid credentials")
        return render_template("login.html", error="Invalid login credentials"), 401
            
    return render_template("login.html")


@limiter.limit("10 per minute")
@auth_bp.route("/student_login", methods=["GET", "POST"])
def student_login():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        selected_template = _find_template_dict_by_school(templates, school_name)
      
        logger.info(f"Login attempt for email: {email}, school: {school_name}")
      
        if not email or not password or not school_name:
            error = "All fields are required."
            logger.warning("Login failed: Missing required fields")
        elif not selected_template:
            error = "Selected school is not available."
            logger.warning("Login failed: Unknown school selection '%s'", school_name)
        else:
            try:
                accounts = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email),
                    Student.password.isnot(None),
                ).order_by(Student.created_at.asc()).all()
                student = next(
                    (row for row in accounts if _normalize_school_name(row.school_name) == _normalize_school_name(school_name)),
                    None,
                )
                
                if not student:
                    error = "No account found for this email and school."
                    logger.warning("Login failed: No account found for email %s in school %s", email, school_name)
                elif check_password_hash(student.password, password):
                    session.clear()
                    session["student_email"] = student.email
                    session["student_school_name"] = student.school_name or selected_template["school_name"]
                    session["student_template_id"] = selected_template["id"]
                    logger.info(f"Login successful for email: {student.email}")
                    return redirect(url_for('dashboard.index'))
                else:
                    error = "Invalid password."
                    logger.warning(f"Login failed: Invalid password for email {email}")
            except Exception as e:
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during login for email {email}: {e}")
  
    return render_template("login_student.html", templates=templates, error=error)


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def register():
    templates = get_templates()
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        school_name = request.form.get("school_name", "").strip()
        name = request.form.get("name", "").strip()
        selected_template = _find_template_dict_by_school(templates, school_name)
      
        if not all([email, password, confirm_password, school_name, name]):
            error = "All fields (name, email, password, confirm password, school name) are required."
            logger.warning("Registration failed: Missing required fields")
        elif not selected_template:
            error = "Selected school is not available."
            logger.warning("Registration failed: Unknown school selection '%s'", school_name)
        elif not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            error = "Invalid email address."
            logger.warning(f"Registration failed: Invalid email {email}")
        elif password != confirm_password:
            error = "Passwords do not match."
            logger.warning("Registration failed: Passwords do not match")
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
            logger.warning("Registration failed: Password too short")
        elif len(name) < 2:
            error = "Name must be at least 2 characters."
            logger.warning("Registration failed: Name too short")
        else:
            try:
                existing_student = Student.query.filter(
                    db.func.lower(Student.email) == db.func.lower(email),
                    Student.password.isnot(None),
                ).first()
                
                if existing_student:
                    error = "Email already registered."
                    logger.warning(f"Registration failed: Email {email} already registered")
                else:
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    if not hashed_password:
                        error = "Failed to hash password."
                        logger.error("Registration failed: Password hashing error")
                    else:
                        student = Student(
                            name=name,
                            email=email.lower(),
                            password=hashed_password,
                            school_name=selected_template["school_name"],
                            created_at = datetime.now(timezone.utc)
                        )
                        db.session.add(student)
                        db.session.commit()
                        
                        session["student_email"] = email.lower()
                        session["student_school_name"] = selected_template["school_name"]
                        session["student_template_id"] = selected_template["id"]
                        logger.info(f"Registered new student: {email}, name: {name}")
                        return redirect(url_for('dashboard.index'))
            except Exception as e:
                db.session.rollback()
                error = f"Database error: {str(e)}"
                logger.error(f"Database error during registration for email {email}: {e}")
    return render_template("register.html", templates=templates, error=error)


@limiter.limit("3 per minute")
@auth_bp.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        
        # Check if student exists
        student = Student.query.filter(db.func.lower(Student.email) == email).first()
        
        if student:
            # Generate secure token (Contains email + timestamp)
            serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
            token = serializer.dumps(email, salt='password-reset-salt')
            
            # Generate the link pointing to the reset route
            link = url_for('auth.reset_password_with_token', token=token, _external=True)
            
            # Send Email
            subject = "Password Reset Request"
            body = f"""Hello {student.name},

You requested to reset your password. Please click the link below to set a new one:

{link}

This link is valid for 1 hour.
If you did not request this, please ignore this email.
"""
            if send_email(email, subject, body):
                logger.info(f"Password reset link sent to {email}")
                flash("A reset link has been sent to your email. Please check your inbox.", "success")
            else:
                logger.error(f"Failed to send reset email to {email}")
                flash("Error sending email. Please try again later.", "error")
        else:
            # Generic message to prevent email enumeration
            flash("If an account exists with that email, a reset link has been sent.", "info")
            
        return redirect(url_for('auth.student_login'))
        
    return render_template("forgot_password.html")


@auth_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password_with_token(token):
    try:
        # Verify Token (Expires in 3600 seconds = 1 hour)
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash("The reset link has expired. Please request a new one.", "error")
        return redirect(url_for('auth.forgot_password'))
    except BadTimeSignature:
        flash("Invalid reset link.", "error")
        return redirect(url_for('auth.forgot_password'))
    
    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")
        
        if not password or not confirm:
            flash("Both password fields are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            try:
                student = Student.query.filter(db.func.lower(Student.email) == email).first()
                if student:
                    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
                    student.password = hashed_password
                    db.session.commit()
                    
                    flash("Your password has been successfully reset! Please log in.", "success")
                    return redirect(url_for("auth.student_login"))
                else:
                    flash("User not found.", "error")
            except Exception as e:
                db.session.rollback()
                logger.error(f"Database error during password reset: {e}")
                flash("An error occurred. Please try again.", "error")

    return render_template("reset_password_token.html", token=token)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.student_login"))


@auth_bp.route("/school_admin_login", methods=["GET", "POST"])
def school_admin_login():
    """Login page for school admins"""
    if request.method == "POST":
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        admin_user = AdminUser.query.filter_by(username=username, role='school_admin').first()
        
        if admin_user and check_password_hash(admin_user.password_hash, password):
            session.clear()
            session['admin'] = True
            session['admin_role'] = 'school_admin'
            session['admin_school'] = admin_user.school_name
            session['student_email'] = username  # Reuse for display
            session.modified = True
            logger.info(f"School admin logged in: {username} (school: {admin_user.school_name})")
            return redirect(url_for('dashboard.index'))
        
        flash('Invalid credentials or access denied.', 'error')
    
    return render_template("school_admin_login.html")


@auth_bp.route("/admin/school_admins", methods=["GET"])
@super_admin_required
def list_school_admins():
    """List all school admins - only for super admin"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = max(10, min(per_page, 200))

    pagination = AdminUser.query.filter_by(role='school_admin').order_by(
        AdminUser.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    return render_template("school_admins.html", school_admins=pagination.items, pagination=pagination)


@auth_bp.route("/admin/register_school_admin", methods=["POST"])
@super_admin_required
def register_school_admin():
    """Register a new school admin - only for super admin"""
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    school_name = request.form.get('school_name', '').strip()
    
    if not username or not password or not school_name:
        return jsonify({'success': False, 'error': 'All fields are required'}), 400
    
    # Check if username already exists
    existing = AdminUser.query.filter_by(username=username).first()
    if existing:
        return jsonify({'success': False, 'error': 'Username already exists'}), 400
    
    # Check if school_name already has an admin
    existing_school = AdminUser.query.filter_by(school_name=school_name, role='school_admin').first()
    if existing_school:
        return jsonify({'success': False, 'error': 'School admin already exists for this school'}), 400
    
    try:
        new_admin = AdminUser(
            username=username,
            password_hash=generate_password_hash(password),
            role='school_admin',
            school_name=school_name
        )
        db.session.add(new_admin)
        db.session.commit()
        logger.info(f"School admin registered: {username} for school: {school_name}")
        return jsonify({'success': True, 'message': f'School admin for {school_name} created successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error registering school admin: {e}")
        return jsonify({'success': False, 'error': 'Failed to create school admin'}), 500


@auth_bp.route("/admin/delete_school_admin/<int:admin_id>", methods=["POST"])
@super_admin_required
def delete_school_admin(admin_id):
    """Delete a school admin - only for super admin"""
    
    try:
        admin_user = db.session.get(AdminUser, admin_id)
        if not admin_user:
            return jsonify({'success': False, 'error': 'School admin not found'}), 404
        
        if admin_user.role == 'super_admin':
            return jsonify({'success': False, 'error': 'Cannot delete super admin'}), 400
        
        username = admin_user.username
        school_name = admin_user.school_name
        db.session.delete(admin_user)
        db.session.commit()
        logger.info(f"School admin deleted: {username} (school: {school_name})")
        return jsonify({'success': True, 'message': f'School admin for {school_name} deleted'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting school admin: {e}")
        return jsonify({'success': False, 'error': 'Failed to delete school admin'}), 500


__all__ = ["auth_bp"]

@auth_bp.route("/admin_student_credentials")
@admin_required
def admin_student_credentials():
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = max(10, min(per_page, 200))

        query = Student.query.filter(
            Student.email.isnot(None),
            Student.email != ''
        ).order_by(Student.created_at.desc())

        if session.get("admin_role") == "school_admin":
            query = query.filter_by(school_name=session.get("admin_school"))

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return render_template(
            "admin_student_credentials.html",
            students=pagination.items,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"Error fetching student credentials: {e}")
        flash(f"Error fetching student credentials: {str(e)}", "error")
        return redirect(url_for("dashboard.admin"))

@auth_bp.route("/admin_add_student_credential", methods=["POST"])
@admin_required
def admin_add_student_credential():
  
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
      
        if not all([name, email, password, school_name]):
            flash("All fields are required", "error")
            return redirect(url_for("auth.admin_student_credentials"))
      
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            flash("Invalid email address", "error")
            return redirect(url_for("auth.admin_student_credentials"))
      
        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return redirect(url_for("auth.admin_student_credentials"))
      
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
      
        # Check if email already exists
        existing = Student.query.filter_by(email=email).first()
        if existing:
            flash("Email already registered", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        # Insert new student credential
        student = Student(
            name=name,
            email=email,
            password=hashed_password,
            school_name=school_name,
            created_at = datetime.now(timezone.utc)
        )
        db.session.add(student)
        db.session.commit()
        
        logger.info(f"Admin added student credential: {email}")
        flash("Student credential added successfully", "success")
        return redirect(url_for("auth.admin_student_credentials"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding student credential: {e}")
        flash(f"Error adding student credential: {str(e)}", "error")
        return redirect(url_for("auth.admin_student_credentials"))

@auth_bp.route("/admin_update_student_credential/<int:student_id>", methods=["POST"])
@admin_required
def admin_update_student_credential(student_id):
  
    try:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        school_name = request.form.get("school_name", "").strip()
      
        if not all([name, email, school_name]):
            flash("Name, email, and school name are required", "error")
            return redirect(url_for("auth.admin_student_credentials"))
      
        if not re.match(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", email):
            flash("Invalid email address", "error")
            return redirect(url_for("auth.admin_student_credentials"))
      
        student = db.session.get(Student, student_id)
        if not student:
            flash("Student not found", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        # Check if email already exists for other students
        existing = Student.query.filter(
            Student.email == email,
            Student.id != student_id
        ).first()
        
        if existing:
            flash("Email already registered to another student", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        student.name = name
        student.email = email
        student.school_name = school_name
        
        # Update password only if provided
        if password:
            if len(password) < 6:
                flash("Password must be at least 6 characters", "error")
                return redirect(url_for("auth.admin_student_credentials"))
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            student.password = hashed_password
        
        db.session.commit()
        logger.info(f"Admin updated student credential for ID: {student_id}")
        flash("Student credential updated successfully", "success")
        return redirect(url_for("auth.admin_student_credentials"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating student credential: {e}")
        flash(f"Error updating student credential: {str(e)}", "error")
        return redirect(url_for("auth.admin_student_credentials"))

@auth_bp.route("/admin_delete_student_credential/<int:student_id>", methods=["POST"])
@admin_required
def admin_delete_student_credential(student_id):
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            flash("Student not found", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        # Check if student has any ID cards created
        if student.photo_filename or student.generated_filename:
            flash("Cannot delete student with existing ID cards. Delete the ID cards first.", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        db.session.delete(student)
        db.session.commit()
        
        logger.info(f"Admin deleted student credential for ID: {student_id}")
        flash("Student credential deleted successfully", "success")
        return redirect(url_for("auth.admin_student_credentials"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting student credential: {e}")
        flash(f"Error deleting student credential: {str(e)}", "error")
        return redirect(url_for("auth.admin_student_credentials"))

@auth_bp.route("/admin_reset_student_password/<int:student_id>", methods=["POST"])
@admin_required
def admin_reset_student_password(student_id):
  
    try:
        student = db.session.get(Student, student_id)
        if not student:
            flash("Student not found", "error")
            return redirect(url_for("auth.admin_student_credentials"))
        
        import secrets
        alphabet = string.ascii_letters + string.digits
        new_password = ''.join(secrets.choice(alphabet) for _ in range(16))
        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
        
        student.password = hashed_password
        db.session.commit()
        
        # Send the new password
        if student.email:
            try:
                send_email(student.email, "Password Reset", f"Your new password is: {new_password}")
                flash("Password reset successfully. New password sent to student's email.", "success")
            except Exception as email_error:
                logger.error(f"Error sending email: {email_error}")
                flash(f"Password reset successfully. New password: {new_password} (Email failed to send)", "success")
        else:
            flash(f"Password reset successfully. New password: {new_password} (No email address)", "success")
        
        logger.info(f"Admin reset password for student ID: {student_id}")
        return redirect(url_for("auth.admin_student_credentials"))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resetting student password: {e}")
        flash(f"Error resetting password: {str(e)}", "error")
        return redirect(url_for("auth.admin_student_credentials"))
