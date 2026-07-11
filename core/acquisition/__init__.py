"""Acquisition layer (audit Kap. 11.4, Phase 4)."""

from __future__ import annotations

from typing import Any


def ensure_acquisition_schema(conn: Any) -> None:
    """Create every durable acquisition table known to this build."""
    from core.acquisition.candidates import ensure_release_candidates_schema
    from core.acquisition.grabs import ensure_acquisition_grabs_schema
    from core.acquisition.requests import ensure_acquisition_requests_schema

    ensure_acquisition_requests_schema(conn)
    ensure_release_candidates_schema(conn)
    ensure_acquisition_grabs_schema(conn)


__all__ = ["ensure_acquisition_schema"]
