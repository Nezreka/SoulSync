"""Gunicorn configuration for production deployments."""

import os

bind = "0.0.0.0:8008"
worker_class = "gthread"
workers = 1
# every open browser tab keeps a socket.io websocket, and each one holds a
# request thread for as long as it's open; 8 threads ran out with a few tabs
# and devices and every page then hung. SOULSYNC_GUNICORN_THREADS overrides.
threads = max(4, int(os.environ.get("SOULSYNC_GUNICORN_THREADS") or 32))
# the request watchdog reads this to know when every thread is taken
os.environ["SOULSYNC_REQUEST_THREADS"] = str(threads)

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
