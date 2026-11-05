"""The suite never logs into the checkout's logs/app.log.

web_server and acoustid_client log to config logging.path, which the test
config leaves at the default. every run wrote its errors into the live log
and, from WSL, rotated the file the windows server held open (Nov 4 2026).
"""

import logging
import os
from pathlib import Path

from utils.logging_config import LOGGER_NAMESPACE, app_log_path

REPO_LOGS = (Path(__file__).resolve().parent.parent / "logs").resolve()


def test_env_wins_over_the_configured_path(monkeypatch):
    monkeypatch.setenv("SOULSYNC_LOG_FILE", "/elsewhere/app.log")
    assert app_log_path("logs/app.log") == "/elsewhere/app.log"


def test_configured_path_without_the_env(monkeypatch):
    monkeypatch.delenv("SOULSYNC_LOG_FILE", raising=False)
    assert app_log_path("custom/app.log") == "custom/app.log"
    assert app_log_path(None) == "logs/app.log"


def test_the_suite_points_logs_outside_the_checkout():
    configured = os.environ.get("SOULSYNC_LOG_FILE")
    assert configured, "conftest must point SOULSYNC_LOG_FILE away from the checkout"
    path = Path(configured).resolve()
    assert REPO_LOGS not in path.parents


def test_no_file_handler_writes_into_the_checkout_logs():
    import core.acoustid_client  # noqa: F401 - installs its file handler
    import web_server  # noqa: F401 - installs the app log handler

    loggers = [logging.getLogger(LOGGER_NAMESPACE), logging.getLogger("soulsync.acoustid")]
    files = [Path(h.baseFilename).resolve() for lg in loggers for h in lg.handlers
             if isinstance(h, logging.FileHandler)]
    assert files, "expected the app and acoustid file handlers"
    for f in files:
        assert REPO_LOGS not in f.parents, f
