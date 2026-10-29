"""WSGI entrypoint: HTTP is available while protected database boot runs."""
from core.startup_application import StartupApplication


def _load_application():
    from web_server import app, start_runtime_services

    start_runtime_services()
    return app


application = StartupApplication(_load_application)
