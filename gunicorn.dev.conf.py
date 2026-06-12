"""Gunicorn configuration for local development."""

from pathlib import Path
import os

# Localhost-only by default (a dev server shouldn't expose itself to the LAN
# without asking). To reach it from another device on your network, opt in with
#   SOULSYNC_WEB_BIND_HOST=0.0.0.0 python dev.py
# then browse to http://<this-pc-lan-ip>:8008 (and allow port 8008 in the firewall).
bind = f"{os.environ.get('SOULSYNC_WEB_BIND_HOST', '127.0.0.1')}:{os.environ.get('SOULSYNC_WEB_BIND_PORT', '8008')}"
worker_class = "gthread"
workers = 1
threads = 4
reload = True

_ROOT_DIR = Path(__file__).resolve().parent
_VITE_URL = os.environ.get('SOULSYNC_WEBUI_VITE_URL', 'http://127.0.0.1:5173').rstrip('/')
_VITE_LOG = os.environ.get('SOULSYNC_WEBUI_VITE_LOG', str(_ROOT_DIR / 'logs' / 'webui-vite.log'))

# Dev Gunicorn config and Vite dev server are paired on purpose.
raw_env = [
    "SOULSYNC_WEB_DEV_NO_CACHE=1",
    "SOULSYNC_WEBUI_VITE_DEV=1",
    f"SOULSYNC_CONFIG_PATH={os.environ.get('SOULSYNC_CONFIG_PATH', str(_ROOT_DIR / 'config' / 'config.json'))}",
    f"SOULSYNC_LOG_LEVEL={os.environ.get('SOULSYNC_LOG_LEVEL', '')}",
    f"SOULSYNC_WEBUI_VITE_URL={_VITE_URL}",
    f"SOULSYNC_WEBUI_VITE_LOG={_VITE_LOG}",
]

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
