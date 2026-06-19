"""
Enterprise Features Blueprint
New routes for all enterprise features: analytics, security, webhooks,
reports, search, monitoring, archive, API keys.
All routes are NEW - no existing routes are modified.
"""
import os
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify, send_file, session

from app.legacy_app import admin_required, super_admin_required
from app.extensions import limiter
from models import (
    db, Organization, Branch, Department, LoginHistory, UserSession,
    ApiKey, ApiKeyLog, WebhookEndpoint, AccessPolicy, SystemMetric,
    DataArchive, OcrResult
)
from app.services.security_service import (
    record_login_attempt, get_login_history, get_active_sessions,
    get_login_analytics, create_managed_session, invalidate_session,
    invalidate_all_sessions, setup_two_factor, verify_two_factor,
    is_two_factor_enabled, disable_two_factor, regenerate_backup_codes,
    get_recent_failed_attempts
)
from app.services.analytics_service import (
    get_dashboard_stats, get_recent_activity, get_student_analytics,
    get_bulk_job_analytics, get_verification_stats, get_template_usage_stats,
    get_system_health_snapshot
)
from app.services.webhook_service import (
    create_webhook, trigger_event, get_delivery_stats, WEBHOOK_EVENTS
)
from app.services.report_service import (
    generate_student_report, generate_activity_report, generate_bulk_job_report
)
from app.services.search_service import (
    search_students, search_templates, search_activity_logs
)
from app.services.monitoring_service import (
    collect_system_metrics, get_latest_metrics, get_metric_history,
    get_health_status
)
from app.services.archive_service import (
    archive_old_students, archive_old_activities, list_archives, restore_archive
)
from app.services.ocr_service import extract_text_from_image, get_ocr_results

logger = logging.getLogger(__name__)

enterprise_bp = Blueprint('enterprise', __name__)


# ================== Analytics Dashboard ==================

@enterprise_bp.route('/admin/analytics')
@admin_required
def analytics_dashboard():
    stats = get_dashboard_stats()
    recent = get_recent_activity(limit=15)
    student_analytics = get_student_analytics()
    bulk_stats = get_bulk_job_analytics()
    verify_stats = get_verification_stats()
    template_stats = get_template_usage_stats()
    return render_template('enterprise/analytics.html',
                           stats=stats, recent=recent,
                           student_analytics=student_analytics,
                           bulk_stats=bulk_stats,
                           verify_stats=verify_stats,
                           template_stats=template_stats)


@enterprise_bp.route('/admin/api/analytics/stats')
@admin_required
def api_analytics_stats():
    return jsonify(get_dashboard_stats())


@enterprise_bp.route('/admin/api/analytics/recent')
@admin_required
def api_recent_activity():
    limit = request.args.get('limit', 20, type=int)
    return jsonify(get_recent_activity(limit=limit))


@enterprise_bp.route('/admin/api/analytics/students')
@admin_required
def api_student_analytics():
    days = request.args.get('days', 30, type=int)
    return jsonify(get_student_analytics(days=days))


@enterprise_bp.route('/admin/api/analytics/verifications')
@admin_required
def api_verification_stats():
    days = request.args.get('days', 7, type=int)
    return jsonify(get_verification_stats(days=days))


# ================== Security Dashboard ==================

@enterprise_bp.route('/admin/security')
@admin_required
def security_dashboard():
    username = session.get('admin_school') or request.args.get('username', '')
    login_history = get_login_history(limit=50)
    analytics = get_login_analytics(days=30)
    return render_template('enterprise/security.html',
                           login_history=login_history,
                           analytics=analytics,
                           username=username)


@enterprise_bp.route('/admin/security/login-history')
@admin_required
def api_login_history():
    username = request.args.get('username')
    failed_only = request.args.get('failed_only', 'false').lower() == 'true'
    limit = request.args.get('limit', 50, type=int)
    history = get_login_history(username=username, limit=limit, failed_only=failed_only)
    return jsonify({
        'history': [{
            'id': h.id,
            'username': h.username,
            'ip_address': h.ip_address,
            'device_type': h.device_type,
            'browser': h.browser,
            'os': h.os,
            'login_success': h.login_success,
            'failure_reason': h.failure_reason,
            'created_at': h.created_at.isoformat() if h.created_at else None,
        } for h in history]
    })


@enterprise_bp.route('/admin/security/sessions')
@admin_required
def api_active_sessions():
    """Get active sessions for the current admin."""
    # Get admin_id from session - we look up the AdminUser
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    if username:
        admin = AdminUser.query.filter_by(username=username).first()
        if admin:
            sessions = get_active_sessions(admin.id)
            return jsonify({
                'sessions': [{
                    'id': s.id,
                    'ip_address': s.ip_address,
                    'user_agent': s.user_agent,
                    'two_factor_verified': s.two_factor_verified,
                    'is_active': s.is_active,
                    'last_activity_at': s.last_activity_at.isoformat() if s.last_activity_at else None,
                    'expires_at': s.expires_at.isoformat() if s.expires_at else None,
                    'created_at': s.created_at.isoformat() if s.created_at else None,
                } for s in sessions]
            })
    return jsonify({'sessions': []})


@enterprise_bp.route('/admin/security/sessions/<int:session_id>/revoke', methods=['POST'])
@admin_required
def api_revoke_session(session_id):
    sess = db.session.get(UserSession, session_id)
    if sess:
        invalidate_session(sess.session_token)
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Session not found'}), 404


@enterprise_bp.route('/admin/security/sessions/revoke-all', methods=['POST'])
@admin_required
def api_revoke_all_sessions():
    """Revoke all sessions for the current admin except the current one."""
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    if username:
        admin = AdminUser.query.filter_by(username=username).first()
        if admin:
            invalidate_all_sessions(admin.id)
            return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Admin not found'}), 404


# ================== 2FA Management ==================

@enterprise_bp.route('/admin/security/2fa/setup', methods=['POST'])
@admin_required
def api_2fa_setup():
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    admin = AdminUser.query.filter_by(username=username).first() if username else None
    if not admin:
        return jsonify({'success': False, 'message': 'Admin not found'}), 404

    result = setup_two_factor(admin.id)
    return jsonify({'success': True, **result})


@enterprise_bp.route('/admin/security/2fa/verify', methods=['POST'])
@admin_required
def api_2fa_verify():
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    admin = AdminUser.query.filter_by(username=username).first() if username else None
    if not admin:
        return jsonify({'success': False, 'message': 'Admin not found'}), 404

    code = request.json.get('code', '') if request.json else request.form.get('code', '')
    success, method = verify_two_factor(admin.id, code)
    return jsonify({'success': success, 'method': method})


@enterprise_bp.route('/admin/security/2fa/disable', methods=['POST'])
@admin_required
def api_2fa_disable():
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    admin = AdminUser.query.filter_by(username=username).first() if username else None
    if admin:
        disable_two_factor(admin.id)
    return jsonify({'success': True})


@enterprise_bp.route('/admin/security/2fa/status')
@admin_required
def api_2fa_status():
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    admin = AdminUser.query.filter_by(username=username).first() if username else None
    enabled = is_two_factor_enabled(admin.id) if admin else False
    return jsonify({'enabled': enabled})


@enterprise_bp.route('/admin/security/2fa/regenerate-backup', methods=['POST'])
@admin_required
def api_regenerate_backup():
    from models import AdminUser
    username = session.get('admin_school') or session.get('student_email')
    admin = AdminUser.query.filter_by(username=username).first() if username else None
    if admin:
        codes = regenerate_backup_codes(admin.id)
        return jsonify({'success': True, 'backup_codes': codes})
    return jsonify({'success': False}), 404


# ================== API Key Management ==================

@enterprise_bp.route('/admin/api-keys')
@admin_required
def api_keys_page():
    keys = ApiKey.query.order_by(ApiKey.created_at.desc()).all()
    return render_template('enterprise/api_keys.html', keys=keys)


@enterprise_bp.route('/admin/api-keys', methods=['POST'])
@admin_required
def api_create_key():
    import hashlib, secrets
    data = request.json or request.form
    name = data.get('name', 'API Key')
    org_id = data.get('organization_id', 1)
    scopes = data.get('scopes', {'read': True, 'write': False})
    raw_key = secrets.token_hex(20)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    key = ApiKey(
        organization_id=org_id,
        name=name,
        key_prefix=raw_key[:8],
        key_hash=key_hash,
        scopes=scopes,
        created_by=session.get('admin_school') or 'admin',
    )
    db.session.add(key)
    db.session.commit()

    return jsonify({
        'success': True,
        'key': raw_key,  # Only shown once
        'prefix': raw_key[:8],
        'id': key.id,
    })


@enterprise_bp.route('/admin/api-keys/<int:key_id>', methods=['DELETE'])
@admin_required
def api_delete_key(key_id):
    key = db.session.get(ApiKey, key_id)
    if key:
        db.session.delete(key)
        db.session.commit()
    return jsonify({'success': True})


# ================== Webhook Management ==================

@enterprise_bp.route('/admin/webhooks')
@admin_required
def webhooks_page():
    webhooks = WebhookEndpoint.query.order_by(WebhookEndpoint.created_at.desc()).all()
    return render_template('enterprise/webhooks.html',
                           webhooks=webhooks,
                           events=WEBHOOK_EVENTS)


@enterprise_bp.route('/admin/webhooks', methods=['POST'])
@admin_required
def api_create_webhook():
    data = request.json or request.form
    webhook = create_webhook(
        organization_id=data.get('organization_id', 1),
        name=data.get('name', 'Webhook'),
        url=data.get('url', ''),
        events=data.get('events', []),
        created_by=session.get('admin_school') or 'admin',
    )
    return jsonify({'success': True, 'id': webhook.id})


@enterprise_bp.route('/admin/webhooks/<int:wh_id>', methods=['DELETE'])
@admin_required
def api_delete_webhook(wh_id):
    wh = db.session.get(WebhookEndpoint, wh_id)
    if wh:
        db.session.delete(wh)
        db.session.commit()
    return jsonify({'success': True})


@enterprise_bp.route('/admin/webhooks/<int:wh_id>/test', methods=['POST'])
@admin_required
def api_test_webhook(wh_id):
    trigger_event(
        organization_id=1,
        event_type='webhook.test',
        payload={'message': 'Test webhook', 'timestamp': datetime.now(timezone.utc).isoformat()},
    )
    return jsonify({'success': True, 'message': 'Test event triggered'})


# ================== Report Center ==================

@enterprise_bp.route('/admin/reports')
@admin_required
def reports_page():
    return render_template('enterprise/reports.html')


@enterprise_bp.route('/admin/reports/students')
@admin_required
def report_students():
    fmt = request.args.get('format', 'xlsx')
    school = request.args.get('school')
    buf = generate_student_report(format=fmt, school_name=school)
    ext = 'xlsx' if fmt == 'xlsx' else 'csv'
    mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if ext == 'xlsx' else 'text/csv'
    return send_file(buf, mimetype=mimetype, as_attachment=True,
                     download_name=f'student_report.{ext}')


@enterprise_bp.route('/admin/reports/activity')
@admin_required
def report_activity():
    fmt = request.args.get('format', 'xlsx')
    days = request.args.get('days', 30, type=int)
    buf = generate_activity_report(format=fmt, days=days)
    ext = 'xlsx' if fmt == 'xlsx' else 'csv'
    mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if ext == 'xlsx' else 'text/csv'
    return send_file(buf, mimetype=mimetype, as_attachment=True,
                     download_name=f'activity_report.{ext}')


@enterprise_bp.route('/admin/reports/bulk-jobs')
@admin_required
def report_bulk_jobs():
    fmt = request.args.get('format', 'xlsx')
    buf = generate_bulk_job_report(format=fmt)
    ext = 'xlsx' if fmt == 'xlsx' else 'csv'
    mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if ext == 'xlsx' else 'text/csv'
    return send_file(buf, mimetype=mimetype, as_attachment=True,
                     download_name=f'bulk_job_report.{ext}')


# ================== Advanced Search API ==================

@enterprise_bp.route('/admin/api/search/students')
@admin_required
def api_search_students():
    result = search_students(
        query=request.args.get('q'),
        school_name=request.args.get('school'),
        class_name=request.args.get('class'),
        has_photo=request.args.get('has_photo'),
        has_card=request.args.get('has_card'),
        date_from=request.args.get('date_from'),
        date_to=request.args.get('date_to'),
        page=request.args.get('page', 1, type=int),
        per_page=request.args.get('per_page', 25, type=int),
    )
    return jsonify(result)


@enterprise_bp.route('/admin/api/search/templates')
@admin_required
def api_search_templates():
    result = search_templates(
        query=request.args.get('q'),
        orientation=request.args.get('orientation'),
        language=request.args.get('language'),
        is_double_sided=request.args.get('is_double_sided'),
        page=request.args.get('page', 1, type=int),
        per_page=request.args.get('per_page', 25, type=int),
    )
    return jsonify(result)


@enterprise_bp.route('/admin/api/search/activities')
@admin_required
def api_search_activities():
    result = search_activity_logs(
        query=request.args.get('q'),
        actor=request.args.get('actor'),
        action=request.args.get('action'),
        date_from=request.args.get('date_from'),
        page=request.args.get('page', 1, type=int),
    )
    return jsonify(result)


# ================== System Monitoring ==================

@enterprise_bp.route('/admin/monitoring')
@admin_required
def monitoring_page():
    health = get_health_status()
    metrics = get_latest_metrics()
    return render_template('enterprise/monitoring.html',
                           health=health, metrics=metrics)


@enterprise_bp.route('/admin/api/monitoring/health')
@admin_required
def api_health_check():
    return jsonify(get_health_status())


@enterprise_bp.route('/admin/api/monitoring/metrics')
@admin_required
def api_metrics():
    return jsonify(get_latest_metrics())


@enterprise_bp.route('/admin/api/monitoring/metrics/<metric_name>')
@admin_required
def api_metric_history(metric_name):
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_metric_history(metric_name, hours=hours))


@enterprise_bp.route('/admin/api/monitoring/collect', methods=['POST'])
@admin_required
def api_collect_metrics():
    count = collect_system_metrics()
    return jsonify({'success': True, 'collected': count})


# ================== Data Archive ==================

@enterprise_bp.route('/admin/archive')
@admin_required
def archive_page():
    archives = list_archives()
    return render_template('enterprise/archive.html', archives=archives)


@enterprise_bp.route('/admin/archive', methods=['POST'])
@admin_required
def api_create_archive():
    data = request.json or request.form
    entity_type = data.get('entity_type', 'student')
    days = int(data.get('days', 365))
    created_by = session.get('admin_school') or 'admin'

    if entity_type == 'student':
        result = archive_old_students(older_than_days=days, created_by=created_by)
    elif entity_type == 'activity':
        result = archive_old_activities(older_than_days=days, created_by=created_by)
    else:
        return jsonify({'success': False, 'message': 'Unknown entity type'}), 400

    return jsonify({'success': True, **result})


@enterprise_bp.route('/admin/archive/<int:archive_id>/restore', methods=['POST'])
@admin_required
def api_restore_archive(archive_id):
    result = restore_archive(archive_id)
    return jsonify(result)


# ================== OCR Service ==================

@enterprise_bp.route('/admin/ocr')
@admin_required
def ocr_page():
    results = get_ocr_results(limit=20)
    return render_template('enterprise/ocr.html', results=results)


@enterprise_bp.route('/admin/ocr/scan', methods=['POST'])
@admin_required
def api_ocr_scan():
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image uploaded'}), 400

    file = request.files['image']
    image_bytes = file.read()
    student_id = request.form.get('student_id', type=int)

    result = extract_text_from_image(image_bytes, student_id=student_id)
    return jsonify({'success': True, **result})


# ================== Organization Management ==================

@enterprise_bp.route('/admin/organizations')
@admin_required
def organizations_page():
    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    return render_template('enterprise/organizations.html', organizations=orgs)


@enterprise_bp.route('/admin/organizations', methods=['POST'])
@admin_required
def api_create_org():
    import re
    data = request.json or request.form
    name = data.get('name', '')
    slug = data.get('slug', re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-'))

    org = Organization(name=name, slug=slug)
    db.session.add(org)
    db.session.commit()
    return jsonify({'success': True, 'id': org.id, 'slug': slug})


@enterprise_bp.route('/admin/organizations/<int:org_id>/branches', methods=['POST'])
@admin_required
def api_create_branch(org_id):
    data = request.json or request.form
    branch = Branch(
        organization_id=org_id,
        name=data.get('name', ''),
        code=data.get('code', ''),
        address=data.get('address', ''),
        phone=data.get('phone', ''),
        email=data.get('email', ''),
    )
    db.session.add(branch)
    db.session.commit()
    return jsonify({'success': True, 'id': branch.id})


@enterprise_bp.route('/admin/organizations/<int:org_id>/departments', methods=['POST'])
@admin_required
def api_create_department(org_id):
    data = request.json or request.form
    dept = Department(
        organization_id=org_id,
        branch_id=data.get('branch_id'),
        parent_id=data.get('parent_id'),
        name=data.get('name', ''),
        code=data.get('code', ''),
    )
    db.session.add(dept)
    db.session.commit()
    return jsonify({'success': True, 'id': dept.id})


# ================== Access Policies ==================

@enterprise_bp.route('/admin/access-policies')
@admin_required
def access_policies_page():
    policies = AccessPolicy.query.order_by(AccessPolicy.role, AccessPolicy.resource).all()
    return render_template('enterprise/access_policies.html', policies=policies)


@enterprise_bp.route('/admin/access-policies', methods=['POST'])
@admin_required
def api_create_policy():
    data = request.json or request.form
    policy = AccessPolicy(
        organization_id=data.get('organization_id', 1),
        role=data.get('role', ''),
        resource=data.get('resource', ''),
        action=data.get('action', ''),
        allowed=data.get('allowed', 'true').lower() == 'true',
    )
    db.session.add(policy)
    db.session.commit()
    return jsonify({'success': True, 'id': policy.id})
