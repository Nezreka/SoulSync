"""Download modules whose log lines never reached app.log.

``setup_logging`` attaches the file handler to the ``soulsync`` logger. Twelve
modules under ``core/downloads/`` used ``logging.getLogger(__name__)``, which
names them ``core.downloads.*`` — outside that hierarchy, so their records never
reached the handler. WARNING and above still hit stderr through Python's
fallback handler, so they showed in ``docker logs``; INFO went nowhere at all.

Either way they were absent from ``app.log``, which is exactly what the in-app
debug export pastes into an issue. ``lifecycle.py`` was one of them, so every
``[Completion Check V2]`` and ``[Stuck Detection V2]`` line was invisible: in
Sokhi's report, healing triggered 18 completion checks and not one result was
readable. We were diagnosing his stall half-blind.

Pinned as a rule rather than a list of twelve fixes, so a new module added to
this package can't quietly rejoin the silent set.
"""

import logging
import pathlib
import re

import pytest

from utils.logging_config import LOGGER_NAMESPACE, get_logger, setup_logging

DOWNLOAD_MODULES = sorted(
    p.stem for p in pathlib.Path('core/downloads').glob('*.py')
    if p.stem != '__init__'
)


@pytest.mark.parametrize("stem", DOWNLOAD_MODULES)
def test_no_download_module_builds_its_own_logger(stem):
    """``logging.getLogger(__name__)`` is the trap: it looks right and silently
    lands outside the namespace the file handler is attached to."""
    src = pathlib.Path(f'core/downloads/{stem}.py').read_text(encoding='utf-8')
    assert not re.search(r'^logger\s*=\s*logging\.getLogger\(__name__\)', src, re.M), (
        f"core/downloads/{stem}.py logs to core.downloads.{stem}, which app.log "
        f"never sees — use get_logger('downloads.{stem}')"
    )


def test_every_download_module_logs_under_the_soulsync_namespace():
    """The positive half: the handler is attached to ``soulsync``, so a logger
    has to sit under it to be recorded at all."""
    import importlib
    strays = []
    for stem in DOWNLOAD_MODULES:
        module = importlib.import_module(f'core.downloads.{stem}')
        log = getattr(module, 'logger', None)
        if log is not None and not log.name.startswith(f"{LOGGER_NAMESPACE}."):
            strays.append(f"{stem} -> {log.name}")
    assert strays == [], f"outside the namespace app.log records: {strays}"


def test_a_line_from_the_lifecycle_module_reaches_app_log(tmp_path):
    """End to end, through the real logging setup. lifecycle is the one that
    matters: it owns the completion checks and the stuck-task rescue."""
    log_path = tmp_path / "app.log"
    setup_logging('INFO', str(log_path))
    try:
        import core.downloads.lifecycle as lifecycle
        lifecycle.logger.warning("[Completion Check V2] probe: finished=3/5")
        for handler in logging.getLogger(LOGGER_NAMESPACE).handlers:
            handler.flush()
        assert "[Completion Check V2] probe: finished=3/5" in log_path.read_text(encoding='utf-8')
    finally:
        logging.getLogger(LOGGER_NAMESPACE).handlers.clear()


def test_the_shared_factory_is_what_puts_them_there():
    """Not a coincidence of naming — ``get_logger`` is the one mechanism that
    prefixes the namespace, so every module goes through it."""
    assert get_logger("downloads.example").name == f"{LOGGER_NAMESPACE}.downloads.example"
