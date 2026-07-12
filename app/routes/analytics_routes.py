"""
Analytics API routes for dashboard data.
"""
from flask import Blueprint, jsonify, request

from app.services.analytics_service import AnalyticsService
from app.auth_decorators import admin_required

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api/v1/analytics")


@analytics_bp.route("/dashboard")
@admin_required
def dashboard_summary():
    """Get dashboard summary statistics."""
    days = request.args.get("days", 30, type=int)
    data = AnalyticsService.get_dashboard_summary(days=days)
    return jsonify({"success": True, "data": data})


@analytics_bp.route("/schools")
@admin_required
def school_breakdown():
    """Get statistics grouped by school."""
    limit = request.args.get("limit", 20, type=int)
    data = AnalyticsService.get_school_breakdown(limit=limit)
    return jsonify({"success": True, "data": data})


@analytics_bp.route("/trend")
@admin_required
def generation_trend():
    """Get card generation trend over time."""
    days = request.args.get("days", 30, type=int)
    granularity = request.args.get("granularity", "day")
    data = AnalyticsService.get_generation_trend(days=days, granularity=granularity)
    return jsonify({"success": True, "data": data})


@analytics_bp.route("/bulk-jobs")
@admin_required
def bulk_job_stats():
    """Get bulk job statistics."""
    days = request.args.get("days", 30, type=int)
    data = AnalyticsService.get_bulk_job_stats(days=days)
    return jsonify({"success": True, "data": data})


@analytics_bp.route("/performance")
@admin_required
def performance_metrics():
    """Get system performance metrics."""
    data = AnalyticsService.get_performance_metrics()
    return jsonify({"success": True, "data": data})


@analytics_bp.route("/activities")
@admin_required
def recent_activities():
    """Get recent system activities."""
    limit = request.args.get("limit", 50, type=int)
    data = AnalyticsService.get_recent_activities(limit=limit)
    return jsonify({"success": True, "data": data})
