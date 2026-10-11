"""The subtitle provider interface.

Phase 1 ships OpenSubtitles; provider #2 is a small isolated job: subclass
``SubtitleProvider``, implement the five members, add one line to
``get_providers()``. Whisper local transcription (Phase 4) fits this interface
too — ``needs_key = False``, local, slow — nothing here assumes a network or a key.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SubtitleQuery:
    """What we're looking for.

    ``identity`` is ``{imdb_id?, tmdb_id?, season?, episode?}`` — the same shape the
    old single-fetcher took. ``filename``/``moviehash`` feed Phase 2 scoring
    (hash-first); providers that can't use them ignore them.
    """

    identity: dict = field(default_factory=dict)
    language: str = "en"
    hi: bool = False
    forced: bool = False
    filename: str | None = None   # video filename, for release-name similarity
    moviehash: str | None = None  # OpenSubtitles file hash, for hash-match search
    file_path: str | None = None  # absolute path to the video file itself.
    # Providers that need the media (Whisper local transcription) use this;
    # network providers ignore it.


@dataclass
class SubtitleCandidate:
    """One downloadable subtitle found by a provider's ``search()``.

    ``download_ref`` is opaque and provider-specific (e.g. an OpenSubtitles
    ``file_id``); only the provider that produced it can ``download()`` it.
    ``hash_match``/``download_count`` feed Phase 2 scoring.
    """

    provider_id: str
    language: str
    hi: bool
    forced: bool
    title: str  # subtitle/release name, for display
    download_ref: Any
    hash_match: bool = False
    download_count: int = 0


class SubtitleProvider(ABC):
    """A subtitle source. Implementations are stateless apart from config cached by
    ``is_configured()``; ``fetch_subtitle()`` always calls ``is_configured()``
    before ``search()``/``download()``."""

    id: str  # class attr, e.g. "opensubtitles"
    display_name: str  # class attr, e.g. "OpenSubtitles"

    #: True when a successful ``download()`` consumes metered NETWORK quota
    #: (counted via ``on_download`` → ``subtitle_quota_bump``). Local providers
    #: (Whisper) set this False — their downloads must never burn, or be
    #: blocked by, the network providers' daily quota.
    burns_network_quota: bool = True

    #: True when ``download()`` is long-running local work (minutes of CPU)
    #: that must only run on a background worker thread — never inline in the
    #: import hook / download monitor, and never in a request thread. The
    #: wanted-loop worker is the only chain caller that runs these; the import
    #: hook defers them (rows stay 'wanted' for the worker), and the eager
    #: ``rank_candidates`` (upgrade loop, manual search) skips them entirely.
    background_download_only: bool = False

    @property
    @abstractmethod
    def needs_key(self) -> bool:
        """True when the provider needs an API key to work."""

    @abstractmethod
    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        """True when the provider has what it needs (key present, etc.). May cache
        the resolved config on the instance for ``search()``/``download()``."""

    @abstractmethod
    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        """Candidates for the query, best first; empty list on miss. A provider-side
        failure should surface as a miss — ``fetch_subtitle()`` treats a raise as
        one anyway."""

    @abstractmethod
    def download(self, candidate: SubtitleCandidate) -> str | None:
        """The subtitle text (srt) for a candidate from ``search()``, or None."""
