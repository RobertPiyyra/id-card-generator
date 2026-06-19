"""
Production gunicorn configuration.
Usage: gunicorn -c gunicorn.conf.py "app:create_app()"
"""
import os
import multiprocessing

# Server socket
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")
backlog = 2048

# Worker processes
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_connections = 1000
timeout = 120
keepalive = 5
graceful_timeout = 30

# Preload app for faster worker spawning
preload_app = True

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "idcard-generator"

# Server mechanics
daemon = False
pidfile = os.environ.get("GUNICORN_PIDFILE", "/tmp/gunicorn.pid")

# SSL (terminate at reverse proxy in production)
forwarded_allow_ips = "*"
secure_scheme_headers = {"X-Forwarded-Proto": "https"}


def on_starting(server):
    """Called just before the master process is initialized."""
    pass


def post_fork(server, worker):
    """Called just after a worker has been forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)


def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forked child, re-exiting")


def worker_exit(server, worker):
    """Called when a worker exits."""
    server.log.info("Worker exited (pid: %s)", worker.pid)
