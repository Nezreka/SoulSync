"""Soulseek/streaming candidate validation — lifted from web_server.py.

Body is byte-identical to the original. ``matching_engine`` and
``soulseek_client`` are injected via init() because both are
constructed in web_server.py and referenced by name throughout
the body.
"""
import logging
import re

from config.settings import config_manager

logger = logging.getLogger(__name__)

# Injected at runtime via init().
matching_engine = None
soulseek_client = None


def init(matching_engine_obj, soulseek_client_obj):
    """Bind the matching engine and download orchestrator from web_server."""
    global matching_engine, soulseek_client
    matching_engine = matching_engine_obj
    soulseek_client = soulseek_client_obj


def get_valid_candidates(results, spotify_track, query):
    """
    This function is a direct port from sync.py. It scores and filters
    Soulseek search results against a Spotify track to find the best, most
    accurate download candidates.
    """
    if not results:
        return []

    # Streaming sources (YouTube, Tidal, Qobuz, HiFi, Deezer) return structured API results
    # with proper artist/title metadata — score using the same matching engine as Soulseek
    _streaming_sources = ("youtube", "tidal", "qobuz", "hifi", "deezer_dl")
    if results[0].username in _streaming_sources:
        source_label = results[0].username.replace('_dl', '').title()
        expected_artists = spotify_track.artists if spotify_track else []
        expected_title = spotify_track.name if spotify_track else ''
        expected_duration = spotify_track.duration_ms if spotify_track else 0

        # Detect if the expected track is a specific version (live, remix, acoustic, etc.)
        expected_title_lower = (expected_title or '').lower()
        _version_keywords = ['remix', 'live', 'acoustic', 'instrumental', 'radio edit',
                             'extended', 'slowed', 'sped up', 'reverb', 'karaoke']
        expected_is_version = any(kw in expected_title_lower for kw in _version_keywords)

        scored = []
        for r in results:
            # Score using matching engine's generic scorer (same weights as Soulseek)
            confidence, match_type = matching_engine.score_track_match(
                source_title=expected_title,
                source_artists=expected_artists,
                source_duration_ms=expected_duration,
                candidate_title=r.title or '',
                candidate_artists=[r.artist] if r.artist else [],
                candidate_duration_ms=r.duration or 0,
            )

            # Version detection penalty — reject live/remix/acoustic when expecting original
            r_title_lower = (r.title or '').lower()
            is_wrong_version = False
            if not expected_is_version:
                # Expecting original — penalize versions
                for kw in _version_keywords:
                    if kw in r_title_lower and kw not in expected_title_lower:
                        confidence *= 0.4  # Heavy penalty
                        is_wrong_version = True
                        break
            else:
                # Expecting specific version — penalize results that don't have it
                for kw in _version_keywords:
                    if kw in expected_title_lower and kw not in r_title_lower:
                        confidence *= 0.5
                        is_wrong_version = True
                        break

            # Artist gate — streaming APIs (Tidal/Qobuz/HiFi/Deezer) have reliable metadata,
            # so "My Will" by "B. Starr" should never match expected "B小町".
            # Skip for YouTube — artist is parsed from video titles and often unreliable.
            if r.username != 'youtube':
                from difflib import SequenceMatcher
                import re as _re
                _cand_artist_raw = r.artist or ''
                _cand_artist = matching_engine.normalize_string(_cand_artist_raw)
                _best_artist = 0.0
                for _ea in expected_artists:
                    _ea_norm = matching_engine.normalize_string(_ea)
                    if not _ea_norm:
                        continue
                    # For short normalized names (e.g. "B小町"→"b"), containment is useless.
                    # Compare original Unicode strings directly via similarity instead.
                    if len(_ea_norm) <= 2:
                        _best_artist = max(_best_artist, SequenceMatcher(None, _ea.lower(), _cand_artist_raw.lower()).ratio())
                    elif _re.search(r'\b' + _re.escape(_ea_norm) + r'\b', _cand_artist):
                        _best_artist = 1.0
                        break
                    elif _ea_norm == _cand_artist:
                        _best_artist = 1.0
                        break
                    else:
                        _best_artist = max(_best_artist, SequenceMatcher(None, _ea_norm, _cand_artist).ratio())
                # Raised from 0.4 → 0.5 to close a fencepost bug: SequenceMatcher
                # returns exactly 0.400 for "maduk" vs "tom walker" (5 chars vs
                # 10 chars with 2 coincidental char matches), which bypassed the
                # strict `< 0.4` check and let Tom Walker through as a candidate
                # for a Maduk track. The word-boundary containment check above
                # already short-circuits legitimate formatting variations
                # ("Beatles"/"The Beatles", "Maduk"/"Maduk feat. X") to sim=1.0,
                # so falling to SequenceMatcher means the strings are genuinely
                # different. 0.5 gives a safer buffer without blocking real
                # matches that would have scored above 0.85 anyway.
                if _best_artist < 0.5 and confidence < 0.85:
                    continue

            r.confidence = confidence
            r.version_type = 'wrong_version' if is_wrong_version else match_type
            if confidence >= 0.60:
                scored.append(r)

        if scored:
            # Sort by confidence (best match first)
            scored.sort(key=lambda x: x.confidence, reverse=True)
            best = scored[0]
            logger.info(f"[{source_label}] {len(scored)}/{len(results)} candidates passed validation "
                  f"(best: {best.confidence:.2f} '{best.artist} - {best.title}')")
            return scored
        else:
            if results[0].username == 'youtube':
                logger.warning(f"[{source_label}] No streaming results passed validation — falling through to filename matching")
                # YouTube artist data is unreliable, allow fallback to filename-based matching
            else:
                logger.warning(f"[{source_label}] No streaming results passed validation (threshold: 0.60, artist gate: 0.50) — rejecting all candidates")
                return []  # Tidal/Qobuz/HiFi/Deezer have structured metadata; don't fall back to filename matching

    # Uses the existing, powerful matching engine for scoring (Soulseek P2P results)
    _max_q = config_manager.get('soulseek.max_peer_queue', 0) or 0
    initial_candidates = matching_engine.find_best_slskd_matches_enhanced(spotify_track, results, max_peer_queue=_max_q)
    if not initial_candidates:
        return []

    # Skip quality filtering for streaming source results that somehow got here
    is_streaming_source = initial_candidates[0].username in _streaming_sources if initial_candidates else False

    if is_streaming_source:
        source_label = initial_candidates[0].username.title()
        logger.info(f"[{source_label}] Skipping quality filter - streaming source handles quality internally")
        quality_filtered_candidates = initial_candidates
    else:
        # Filter by user's quality profile before artist verification (Soulseek only)
        # Use existing soulseek_client to avoid re-initializing (which accesses download_path filesystem)
        quality_filtered_candidates = soulseek_client.soulseek.filter_results_by_quality_preference(initial_candidates)

        # IMPORTANT: Respect empty results from quality filter
        # If user has strict quality requirements (e.g., FLAC-only with fallback disabled),
        # and no results match, we should fail the download rather than force a fallback.
        # The quality filter already has its own fallback logic controlled by the user's settings.
        if not quality_filtered_candidates:
            logger.error("[Quality Filter] No candidates match quality profile - download will fail per user preferences")
            return []

    verified_candidates = []
    spotify_artists = spotify_track.artists if spotify_track.artists else []

    # Pre-normalize all artist names into word sets using the matching engine
    # This handles Cyrillic, accents, special chars ($), separators, etc.
    artist_word_sets = []
    for artist_name in spotify_artists:
        normalized = matching_engine.normalize_string(artist_name)
        words = set(normalized.split())
        if words:
            artist_word_sets.append(words)

    for candidate in quality_filtered_candidates:
        # Skip artist check for streaming results (title matching is sufficient as processed by matching engine)
        if is_streaming_source:
            verified_candidates.append(candidate)
            continue

        # No artist info available — can't verify, accept candidate
        if not artist_word_sets:
            verified_candidates.append(candidate)
            continue

        # Split the Soulseek path into segments (folders + filename) and check each one.
        # This prevents false positives where a short artist name like "Sia" accidentally
        # matches inside a folder name like "Enthusiastic" — by checking words within
        # individual segments rather than a flat substring of the entire path.
        path_segments = re.split(r'[/\\]', candidate.filename)

        artist_found = False
        for segment in path_segments:
            if not segment:
                continue
            seg_words = set(matching_engine.normalize_string(segment).split())
            if not seg_words:
                continue

            # Check if ANY artist's words are ALL present in this segment
            for artist_words in artist_word_sets:
                if artist_words.issubset(seg_words):
                    artist_found = True
                    break

            if artist_found:
                break

        if artist_found:
            verified_candidates.append(candidate)
    return verified_candidates
