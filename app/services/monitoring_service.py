"""
System Health & Performance Monitoring Service
Collects and serves system metrics.
Isolated module - uses SystemMetric model.
"""
import os
import time
import logging
import psutil
from datetime import datetime, timezone, timedelta

from models import db, SystemMetric

logger = logging.getLogger(__name__)


def collect_system_metrics():
    """Collect current system metrics and store them."""
    try:
        metrics = []

        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=0.5)
        metrics.append(SystemMetric(
            metric_name='cpu_percent',
            metric_value=cpu_percent,
            metric_unit='percent',
        ))

        # Memory usage
        mem = psutil.virtual_memory()
        metrics.append(SystemMetric(
            metric_name='memory_percent',
            metric_value=mem.percent,
            metric_unit='percent',
        ))
        metrics.append(SystemMetric(
            metric_name='memory_used_mb',
            metric_value=round(mem.used / (1024 * 1024), 1),
            metric_unit='MB',
        ))

        # Disk usage
        disk = psutil.disk_usage('/')
        metrics.append(SystemMetric(
            metric_name='disk_percent',
            metric_value=disk.percent,
            metric_unit='percent',
        ))
        metrics.append(SystemMetric(
            metric_name='disk_free_gb',
            metric_value=round(disk.free / (1024 * 1024 * 1024), 2),
            metric_unit='GB',
        ))

        # Process count
        metrics.append(SystemMetric(
            metric_name='process_count',
            metric_value=len(psutil.pids()),
            metric_unit='count',
        ))

        for m in metrics:
            db.session.add(m)
        db.session.commit()
        return len(metrics)
    except Exception as e:
        logger.error(f"Failed to collect metrics: {e}")
        db.session.rollback()
        return 0


def get_latest_metrics() -> dict:
    """Get the most recent values for each metric type."""
    metric_names = ['cpu_percent', 'memory_percent', 'memory_used_mb',
                    'disk_percent', 'disk_free_gb', 'process_count']
    result = {}
    for name in metric_names:
        latest = SystemMetric.query.filter_by(
            metric_name=name
        ).order_by(SystemMetric.created_at.desc()).first()
        if latest:
            result[name] = {
                'value': latest.metric_value,
                'unit': latest.metric_unit,
                'timestamp': latest.created_at.isoformat() if latest.created_at else None,
            }
    return result


def get_metric_history(metric_name: str, hours: int = 24) -> list:
    """Get historical values for a specific metric."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    records = SystemMetric.query.filter(
        SystemMetric.metric_name == metric_name,
        SystemMetric.created_at >= cutoff,
    ).order_by(SystemMetric.created_at.asc()).all()

    return [{
        'value': r.metric_value,
        'unit': r.metric_unit,
        'timestamp': r.created_at.isoformat() if r.created_at else None,
    } for r in records]


def cleanup_old_metrics(days: int = 30):
    """Remove metrics older than specified days."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = SystemMetric.query.filter(
            SystemMetric.created_at < cutoff
        ).delete()
        db.session.commit()
        logger.info(f"Cleaned up {deleted} old metric records")
        return deleted
    except Exception as e:
        logger.error(f"Metric cleanup failed: {e}")
        db.session.rollback()
        return 0


def get_health_status() -> dict:
    """Get overall system health status."""
    latest = get_latest_metrics()

    status = 'healthy'
    issues = []

    cpu = latest.get('cpu_percent', {}).get('value', 0)
    if cpu > 90:
        status = 'critical'
        issues.append(f'CPU usage critical: {cpu}%')
    elif cpu > 75:
        status = 'warning'
        issues.append(f'CPU usage high: {cpu}%')

    mem = latest.get('memory_percent', {}).get('value', 0)
    if mem > 90:
        status = 'critical'
        issues.append(f'Memory usage critical: {mem}%')
    elif mem > 80:
        if status != 'critical':
            status = 'warning'
        issues.append(f'Memory usage high: {mem}%')

    disk = latest.get('disk_percent', {}).get('value', 0)
    if disk > 95:
        status = 'critical'
        issues.append(f'Disk usage critical: {disk}%')
    elif disk > 85:
        if status != 'critical':
            status = 'warning'
        issues.append(f'Disk usage high: {disk}%')

    return {
        'status': status,
        'issues': issues,
        'metrics': latest,
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }
