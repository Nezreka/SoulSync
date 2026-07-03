"""Boot-phase guard for non-blocking container startup.

While the gunicorn worker is importing ``web_server`` (module-level client and
worker initialization), external provider API probes must not block startup.
Network validation is deferred until ``mark_boot_complete()`` runs at the end
of that import pass.
"""

from __future__ import annotations

import threading

_boot_lock = threading.Lock()
_boot_active = True


def is_boot_phase() -> bool:
    """Return True while module import must avoid blocking provider API calls."""
    with _boot_lock:
        return _boot_active


def mark_boot_complete() -> None:
    """End the boot phase — provider clients may perform network probes again."""
    global _boot_active
    with _boot_lock:
        _boot_active = False
