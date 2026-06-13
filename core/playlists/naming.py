"""Effective name for a mirrored playlist.

A mirrored playlist tracks an upstream playlist, so its ``name`` column is
rewritten from upstream on every refresh (see ``mirror_playlist``). Users can set
a ``custom_name`` alias that overrides what's shown in the UI and what's used when
syncing the playlist to the media server — while staying tied to the original
(the upstream ``name`` keeps tracking; the alias just overrides the visible/synced
label, and lives in its own column so refresh never clobbers it).

This module is the single, pure source of truth for "which name wins", so the API
payload, the card, and the sync path all agree.
"""

from __future__ import annotations

from typing import Any, Mapping


def effective_mirrored_name(playlist: Mapping[str, Any]) -> str:
    """Return the name to DISPLAY + SYNC: the user's ``custom_name`` alias when
    set (non-blank), otherwise the upstream ``name``. Always returns a string."""
    if not isinstance(playlist, Mapping):
        return ''
    custom = str(playlist.get('custom_name') or '').strip()
    if custom:
        return custom
    return str(playlist.get('name') or '').strip()


__all__ = ['effective_mirrored_name']
