"""WSGI entrypoint: HTTP is available while protected database boot runs."""
from core.request_watchdog import track_requests
from core.startup_application import StartupApplication


def _load_application():
    from web_server import app, start_runtime_services

    start_runtime_services()
    return app


# outermost: a request counts from the moment it holds a gunicorn thread
application = track_requests(StartupApplication(_load_application))
