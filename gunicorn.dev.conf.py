"""Gunicorn configuration for local development."""

bind = "127.0.0.1:8008"
worker_class = "gthread"
workers = 1
threads = 4
reload = True

# Keep requests from hanging forever on slow external services.
timeout = 120

# Don't let local reloads wait too long for shutdown.
graceful_timeout = 1

# Logging goes to stdout/stderr so the shell launcher can collect it.
accesslog = "-"
errorlog = "-"
loglevel = "info"
