"""Gunicorn configuration for local development."""

bind = "127.0.0.1:8008"
worker_class = "gthread"
workers = 1
threads = 4
reload = True
raw_env = ["SOULSYNC_WEB_DEV_NO_CACHE=1"]

# Keep requests from hanging forever on slow external services.
timeout = 120

# Don't let local reloads wait too long for shutdown.
graceful_timeout = 1

# Logging goes to stdout/stderr and is filtered by the custom logger class.
accesslog = "-"
errorlog = "-"
# Mimic process log format
access_log_format = '%(h)s - - "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
loglevel = "info"
logger_class = "utils.gunicorn_logger.FilteredGunicornLogger"
