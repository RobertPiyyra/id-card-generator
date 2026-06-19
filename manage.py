"""
Management CLI commands for the ID Card Generator.

Usage:
    python manage.py migrate          — Run pending migrations
    python manage.py migrate-create   — Create new migration
    python manage.py create-admin     — Create admin user
    python manage.py verify-fonts     — Verify font availability
    python manage.py health           — Run health check
    python manage.py stats            — Show database statistics
"""
import os
import sys
import click
from getpass import getpass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@click.group()
def cli():
    """ID Card Generator management commands."""
    pass


@cli.command()
def migrate():
    """Run database migrations."""
    from app.legacy_app import app, migrate_database
    with app.app_context():
        migrate_database()
        click.echo("✓ Migrations complete")


@cli.command()
@click.option("--message", "-m", required=True, help="Migration description")
def migrate_create(message):
    """Create a new migration."""
    os.system(f'flask db migrate -m "{message}"')


@cli.command()
def verify_fonts():
    """Verify all required fonts are available."""
    from app.legacy_app import app, verify_fonts_available
    with app.app_context():
        try:
            verify_fonts_available()
            click.echo("✓ All fonts available")
        except Exception as exc:
            click.echo(f"✗ Font check failed: {exc}", err=True)
            sys.exit(1)


@cli.command()
def health():
    """Run a health check against the running application."""
    import requests
    url = os.environ.get("APP_BASE_URL", "http://localhost:5000")
    try:
        r = requests.get(f"{url}/health/ready", timeout=5)
        data = r.json()
        click.echo(f"Status: {data['status']}")
        for check, status in data.get("checks", {}).items():
            click.echo(f"  {check}: {status}")
        if data["status"] != "ready":
            sys.exit(1)
    except Exception as exc:
        click.echo(f"✗ Health check failed: {exc}", err=True)
        sys.exit(1)


@cli.command()
def stats():
    """Show database statistics."""
    from app.legacy_app import app
    from models import db, Student, Template, AdminUser, BulkJob

    with app.app_context():
        click.echo("Database Statistics")
        click.echo("─" * 40)
        click.echo(f"  Students:    {Student.query.count()}")
        click.echo(f"  Templates:   {Template.query.count()}")
        click.echo(f"  Admins:      {AdminUser.query.count()}")
        click.echo(f"  Bulk Jobs:   {BulkJob.query.count()}")

        # File storage stats
        upload_dir = os.path.join(app.root_path, '..', 'static', 'uploads')
        if os.path.isdir(upload_dir):
            total_size = 0
            file_count = 0
            for root, dirs, files in os.walk(upload_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    file_count += 1
            click.echo(f"  Uploads:     {file_count} files ({total_size / 1024 / 1024:.1f} MB)")


@cli.command()
def create_admin():
    """Create a new admin user interactively."""
    from app.legacy_app import app
    from models import db, AdminUser
    from werkzeug.security import generate_password_hash

    username = click.prompt("Admin username")
    password = getpass("Admin password: ")
    confirm = getpass("Confirm password: ")

    if password != confirm:
        click.echo("✗ Passwords don't match", err=True)
        sys.exit(1)

    if len(password) < 8:
        click.echo("✗ Password must be at least 8 characters", err=True)
        sys.exit(1)

    with app.app_context():
        if AdminUser.query.filter_by(username=username).first():
            click.echo(f"✗ Admin '{username}' already exists", err=True)
            sys.exit(1)

        admin = AdminUser(
            username=username,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        )
        db.session.add(admin)
        db.session.commit()
        click.echo(f"✓ Admin '{username}' created successfully")


if __name__ == "__main__":
    cli()
