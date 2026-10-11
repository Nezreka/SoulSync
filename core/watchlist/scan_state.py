"""Shared watchlist scan state — single source of truth.

Previously, api/artist_watchlist.py (manual scans) and web_server.py
(auto-scans, socket emit loop) each maintained their own separate
watchlist_scan_state dict. They were never synced, so during a manual scan
the API endpoint reported 'scanning' while the socket kept emitting 'idle'
— causing the live scan deck on the watchlist page to disappear.

This module provides the one shared dict. Mutate it in place via get_state();
never rebind it. Use reset_state() to start a new scan.
"""

from datetime import datetime
from typing import Any, Dict

# The single shared state. Always mutate in place; never reassign this name.
_state: Dict[str, Any] = {
    'status': 'idle',
    'results': [],
    'summary': {},
    'error': None,
}


def get_state() -> Dict[str, Any]:
    """Return the shared scan state dict (mutate in place)."""
    return _state


def reset_state() -> Dict[str, Any]:
    """Reset to a fresh 'scanning' state, preserving object identity."""
    _state.clear()
    _state.update({
        'status': 'scanning',
        'started_at': datetime.now(),
        'results': [],
        'summary': {},
        'error': None,
        'cancel_requested': False,
    })
    return _state
