"""Shared text comparison for import paths — wraps ``MusicMatchingEngine``.

Auto-import, manual album import, and related helpers should use these
functions instead of ad-hoc ``SequenceMatcher`` + strip-punctuation
normalizers so artist initials, unicode, and version penalties stay
consistent across the app.
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from core.matching_engine import MusicMatchingEngine
    _MATCHING_ENGINE_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover
    MusicMatchingEngine = None  # type: ignore[assignment,misc]
    _MATCHING_ENGINE_IMPORT_ERROR = exc

_ENGINE: Any = None


def get_matching_engine() -> Any:
    """Return the process-wide ``MusicMatchingEngine`` singleton."""
    global _ENGINE
    if _ENGINE is None:
        if MusicMatchingEngine is None:
            raise RuntimeError("Music matching engine is unavailable") from _MATCHING_ENGINE_IMPORT_ERROR
        _ENGINE = MusicMatchingEngine()
    return _ENGINE


def title_similarity(a: str, b: str) -> float:
    engine = get_matching_engine()
    return engine.similarity_score(engine.clean_title(a or ""), engine.clean_title(b or ""))


def artist_similarity(a: str, b: str) -> float:
    engine = get_matching_engine()
    return engine.similarity_score(engine.clean_artist(a or ""), engine.clean_artist(b or ""))


def album_similarity(a: str, b: str) -> float:
    engine = get_matching_engine()
    return engine.similarity_score(
        engine.clean_album_name(a or ""),
        engine.clean_album_name(b or ""),
    )


def generic_similarity(a: str, b: str) -> float:
    """Normalized string compare when no field-specific cleaner applies."""
    engine = get_matching_engine()
    return engine.similarity_score(
        engine.normalize_string(a or ""),
        engine.normalize_string(b or ""),
    )
