"""Per-action automation handlers.

Each module in this subpackage exposes one top-level handler function
(or a small cluster of related handlers) of the form::

    def auto_<action_name>(config: dict, deps: AutomationDeps) -> dict

The ``register_all`` helper in :mod:`registration` wires every handler
to the engine in one place. ``web_server.py`` calls
``register_all(deps)`` once at startup.
"""

from core.automation.handlers.process_wishlist import auto_process_wishlist
from core.automation.handlers.scan_watchlist import auto_scan_watchlist
from core.automation.handlers.scan_library import auto_scan_library
from core.automation.handlers.registration import register_all

__all__ = [
    'auto_process_wishlist',
    'auto_scan_watchlist',
    'auto_scan_library',
    'register_all',
]
