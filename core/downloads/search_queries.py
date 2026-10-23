"""The search queries an automatic download walks for one track.

The download worker and the library's Interactive Search ask the sources the
same questions, so a hit the automatic search could find shows up in the
interactive one too.
"""

from __future__ import annotations

import re
from typing import Any, List


def automatic_search_queries(matching_engine: Any, track: Any) -> List[str]:
    """The matching engine's smart queries, then the legacy fallbacks: the
    title with the artist's first word, the bare title and the title without
    brackets, the last two only when distinctive enough to broadcast."""
    artist_name = track.artists[0] if track.artists else None
    track_name = track.name

    queries = list(matching_engine.generate_download_queries(track))
    if artist_name:
        artist_words = artist_name.split()
        if artist_words:
            first_word = artist_words[0]
            if first_word.lower() == 'the' and len(artist_words) > 1:
                first_word = artist_words[1]
            if len(first_word) > 1:
                queries.append(f"{track_name} {first_word}".strip())

    # Add track-only query only when it is distinctive enough to broadcast.
    # generate_download_queries() already enforces this guard; the legacy
    # fallback must honor the same contract or short wishlist titles like
    # "Vortex" still fan out to hundreds of noisy Soulseek responses.
    if track_name.strip() and matching_engine._title_is_distinctive_enough_to_broadcast(track_name.strip()):
        queries.append(track_name.strip())

    cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
    cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
    if (
        cleaned_name
        and cleaned_name.lower() != track_name.lower()
        and matching_engine._title_is_distinctive_enough_to_broadcast(cleaned_name)
    ):
        queries.append(cleaned_name.strip())

    # Release plugins add the known artist/album hint locally. Keeping album
    # queries out of this list avoids sending them to Soulseek or streaming
    # sources and preserves the hybrid source order.
    unique, seen = [], set()
    for query in queries:
        if query and query.lower() not in seen:
            unique.append(query)
            seen.add(query.lower())
    return unique


__all__ = ["automatic_search_queries"]
