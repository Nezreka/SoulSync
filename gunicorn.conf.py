"""Gunicorn configuration for production deployments."""

bind = "0.0.0.0:8008"
worker_class = "gthread"
workers = 1
threads = 8

# Keep requests from hanging forever on slow external services.
timeout = 120

# Keep shutdowns under Docker's stop window so container restarts stay graceful.
graceful_timeout = 8

# Logging goes to stdout/stderr so Docker can collect it.
accesslog = "-"
errorlog = "-"
loglevel = "info"
