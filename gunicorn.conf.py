"""Gunicorn configuration for production deployments."""

bind = "0.0.0.0:8008"
worker_class = "gthread"
workers = 1
threads = 8

# Keep requests from hanging forever on slow external services.
timeout = 120

# Keep shutdowns under Docker's stop window so container restarts stay graceful.
graceful_timeout = 8

# Logging goes to stdout/stderr and is filtered by the custom logger class.
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s - - "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
loglevel = "info"
logger_class = "utils.gunicorn_logger.FilteredGunicornLogger"
