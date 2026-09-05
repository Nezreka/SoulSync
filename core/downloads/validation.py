"""Soulseek/streaming candidate validation — lifted from web_server.py.

Body is byte-identical to the original. ``matching_engine`` and
``download_orchestrator`` are injected via init() because both are
constructed in web_server.py and referenced by name throughout
the body.
"""
from utils.logging_config import get_logger
import re

from core.settings import config_manager
from core.imports.file_integrity import resolve_duration_tolerance
# One definition of "could this release satisfy the profile", shared with the
# album-bundle picker. It lived here and the picker used the probed-file rule
# instead, so the same release passed per-track and was refused as an album.
from core.quality.model import (
    satisfies_a_target_on_stated_facts as _satisfies_a_target_on_stated_facts,
)

logger = get_logger("downloads.validation")

# Injected at runtime via init().
matching_engine = None
download_orchestrator = None

# Structured-metadata sources. Soulseek peers use the sharing username, so they
# are everything else. A mixed best-quality pool must score each family with
# its own checker — not whoever happens to be results[0].
_STREAMING_USERNAMES = frozenset({
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'soundcloud',
    'amazon', 'torrent', 'usenet',
})


def init(matching_engine_obj, download_orchestrator_obj):
    """Bind the matching engine and download orchestrator from web_server."""
    global matching_engine, download_orchestrator
    matching_engine = matching_engine_obj
    download_orchestrator = download_orchestrator_obj


def _youtube_probe_targets(profile_id=None):
    """Profile targets for YouTube itag probing. None if the DB is unavailable."""
    try:
        from core.quality.selection import load_profile_by_id, targets_from_profile
        if profile_id:
            targets, _ = targets_from_profile(load_profile_by_id(profile_id))
            return targets
        from core.quality.selection import load_profile_targets
        targets, _ = load_profile_targets()
        return targets
    except Exception:  # noqa: BLE001 - probe still works with search claims
        return None


def _filter_youtube_by_quality(candidates, profile_id=None):
    """Pre-download: keep YouTube hits the quality profile would accept.

    Called from ``get_valid_candidates`` after match scoring (and on the
    YouTube filename-matching fallback). There is no file yet — ranking
    uses ``youtube_claimed_quality`` (re-encode on → converted output such
    as MP3 320; re-encode off → original Opus/AAC). Only hits within 0.05
    confidence of the top match are probed and ranked, so a distant cover
    cannot beat a better title match on itags. The import quality guard
    later verifies the real file on disk.

    Empty result means the profile rejected them (fallback off). Test stubs
    without ``audio_quality`` pass through unchanged so match-only tests
    stay isolated. Non-YouTube rows in a mixed best-quality pool are kept
    and never enter the YouTube confidence band.
    """
    if not candidates:
        return []
    if not all(hasattr(c, 'audio_quality') for c in candidates):
        return list(candidates)
    yt = [c for c in candidates if getattr(c, 'username', None) == 'youtube']
    other = [c for c in candidates if getattr(c, 'username', None) != 'youtube']
    if not yt:
        return list(other)
    youtube = None
    if download_orchestrator is not None:
        try:
            youtube = download_orchestrator.client('youtube')
        except Exception:  # noqa: BLE001 - probe is optional
            youtube = None
    from core.youtube_client import youtube_quality_rank_band
    band = youtube_quality_rank_band(yt)
    if youtube is not None and hasattr(youtube, 'refresh_claimed_quality'):
        try:
            youtube.refresh_claimed_quality(band, targets=_youtube_probe_targets(profile_id))
        except Exception as e:  # noqa: BLE001 - ranking still uses the search claim
            logger.info("YouTube format probe skipped (%s: %s)", type(e).__name__, e)
    if profile_id:
        from core.quality.selection import (
            load_profile_by_id, rank_with_targets, targets_from_profile,
        )
        targets, fallback_enabled = targets_from_profile(load_profile_by_id(profile_id))
        ranked, _ = rank_with_targets(band, targets, fallback_enabled=fallback_enabled)
    else:
        from core.quality.selection import rank_for_profile
        ranked, _ = rank_for_profile(band)
    if not ranked:
        if other:
            return list(other)
        logger.error(
            "[Youtube] No candidates match quality profile - download will fail per user preferences"
        )
        return []
    logger.info(
        "[Youtube] Quality filter: %d/%d candidates remain (best: %s)",
        len(ranked), len(yt), ranked[0].audio_quality.label(),
    )
    return list(other) + list(ranked)


def _torrent_usenet_artist_is_fallback(result):
    """True when a release result has no parsed artist, only indexer filler."""
    if getattr(result, 'username', None) not in ('torrent', 'usenet'):
        return False
    artist = (getattr(result, 'artist', None) or '').strip()
    if not artist:
        return True
    metadata = getattr(result, '_source_metadata', None) or {}
    indexer = str(metadata.get('indexer') or '').strip()
    if artist.lower() in ('torrent', 'usenet'):
        return True
    return bool(indexer and artist.lower() == indexer.lower())


def filter_soundcloud_previews(results, expected_track):
    """Drop SoundCloud preview snippets so they never reach the cache,
    the modal, or the auto-download attempt.

    SoundCloud serves a ~30s preview clip for tracks gated behind Go+ /
    login. yt-dlp accepts the preview as the download payload, the
    integrity check catches the truncated file, but the user just sees
    "all candidates failed" with previews still listed in the modal
    (and clickable for manual retry, which downloads another preview).

    Filter at every spot raw search results enter the task: validation
    scoring, modal-cache fallback when validation drops everything,
    and the not-found raw-results cache. Keep candidates that genuinely
    are short (intros, sound effects) when the expected track is also
    short.
    """
    if not results or not expected_track:
        return results
    expected_ms = getattr(expected_track, 'duration_ms', 0) or 0
    if expected_ms <= 0:
        return results
    expected_secs = expected_ms / 1000.0
    if expected_secs <= 60:
        return results

    def _is_preview(r):
        if getattr(r, 'username', None) != 'soundcloud':
            return False
        cand_ms = getattr(r, 'duration', None) or 0
        if cand_ms <= 0:
            return False
        cand_secs = cand_ms / 1000.0
        return cand_secs < 35 or cand_secs < expected_secs * 0.5

    return [r for r in results if not _is_preview(r)]


def _duration_tolerance_seconds(expected_duration_ms):
    override = resolve_duration_tolerance(
        config_manager.get('post_processing.duration_tolerance_seconds', 0)
    )
    if override is not None:
        return override
    expected_seconds = expected_duration_ms / 1000.0
    return 5.0 if expected_seconds > 600.0 else 3.0


def _duration_mismatch_exceeds_integrity_tolerance(expected_duration_ms, candidate_duration_ms):
    if not expected_duration_ms or not candidate_duration_ms:
        return False
    tolerance = _duration_tolerance_seconds(expected_duration_ms)
    drift = abs((candidate_duration_ms / 1000.0) - (expected_duration_ms / 1000.0))
    return drift > tolerance


# Version / alternate-recording markers — a candidate carrying one of these
# when the expected title doesn't is a different recording, not the song.
# Shared by the structured scoring lane AND the YouTube fallthrough lane.
_VERSION_KEYWORDS = ['remix', 'live', 'acoustic', 'instrumental', 'radio edit',
                     'extended', 'slowed', 'sped up', 'reverb', 'karaoke',
                     # Producer-tag noise common on SoundCloud — "type beat" is
                     # an instrumental produced in someone's style, tagged with
                     # the artist name to game search. NEVER the real song.
                     'type beat',
                     # YouTube chaff (Kazimir's downloads folder): none of
                     # these are ever the studio recording.
                     'react', 'reaction', 'cover', 'nightcore',
                     'mashup', 'parody', '8d audio', '3d sound']


def _has_version_kw(text, kw) -> bool:
    # WORD-boundary match — a plain substring check penalized "Staying Alive"
    # for containing 'live' and "Undercover" for containing 'cover'.
    return bool(re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text or ''))


# Words a legitimate YouTube upload adds around the real title — never
# identity-bearing. Everything OUTSIDE this set + the wanted title/artist
# words counts as a foreign word (a different song, a reaction, a rename).
_UPLOAD_NOISE_WORDS = frozenset({
    'official', 'video', 'audio', 'music', 'lyrics', 'lyric', 'visualizer',
    'visualiser', 'mv', 'hd', 'hq', '4k', 'full', 'song', 'topic', 'by',
    'ft', 'feat', 'featuring', 'with', 'prod', 'explicit', 'clean',
})


def _title_words_are_expected(candidate_title, expected_title, expected_artists) -> bool:
    """True when every significant word of a candidate's title belongs to the
    wanted TITLE, the wanted ARTIST(s), or known upload noise.

    The apostrophe trap this closes: "We're Shameless" normalizes to
    "were shameless", which is a ~0.9 char-similarity to "we were shameless"
    — a different song by a different artist. Char ratios can't see the
    foreign word; a word-membership check can. Only consulted for YouTube
    candidates with NO artist evidence, so a video titled
    "Artist - Song (Official Video)" (artist parses → evidence) never
    reaches it."""
    cand = matching_engine.normalize_string(candidate_title or '')
    if not cand:
        return False
    allowed = set(_UPLOAD_NOISE_WORDS)
    allowed.update(matching_engine.normalize_string(expected_title or '').split())
    for artist in (expected_artists or []):
        allowed.update(matching_engine.normalize_string(artist).split())
    return all(word in allowed for word in cand.split())


def get_valid_candidates(results, spotify_track, query, profile_id=None):
    """Score each source with its own checker, then return match-passing hits.

    Streaming/torrent rows use title/artist/duration. Soulseek peers use the
    file-path matcher. A mixed best-quality pool must not pick one recipe from
    ``results[0]`` and apply it to every source.

    ``profile_id`` is the item's own quality profile, taken from the wishlist
    row's ``quality_profile_id``. It reaches the Soulseek quality filter so a
    per-item profile decides what is CONSIDERED, not just what survives the
    import guard (#1150). None means the app-wide default, which is what manual
    downloads and staging imports want.
    """
    if not results:
        return []

    # Pre-filter: drop SoundCloud preview snippets when expected
    # duration is non-trivially long. Same helper is also applied at
    # the modal-cache fallback path so previews never reach the UI.
    results = filter_soundcloud_previews(results, spotify_track)
    if not results:
        return []

    streaming = [r for r in results if getattr(r, 'username', None) in _STREAMING_USERNAMES]
    p2p = [r for r in results if getattr(r, 'username', None) not in _STREAMING_USERNAMES]

    accepted = []
    if streaming:
        scored = _score_streaming_candidates(streaming, spotify_track)
        if scored:
            if any(getattr(r, 'username', None) == 'youtube' for r in scored):
                scored = _filter_youtube_by_quality(scored, profile_id)
            scored = _filter_prowlarr_by_quality(scored, profile_id)
            accepted.extend(scored)
        elif any(getattr(r, 'username', None) == 'youtube' for r in streaming):
            # YouTube artist data is unreliable; Tidal/Qobuz/etc. do not fall through.
            yt = [r for r in streaming if getattr(r, 'username', None) == 'youtube']
            logger.warning(
                "[Youtube] No streaming results passed validation — falling through to filename matching"
            )
            accepted.extend(_match_filename_candidates(yt, spotify_track, profile_id))
        else:
            logger.warning(
                "[Streaming] No streaming results passed validation "
                "(threshold: 0.60, artist gate: 0.50) — rejecting streaming candidates"
            )

    if p2p:
        accepted.extend(_match_filename_candidates(p2p, spotify_track, profile_id))
    return accepted


def _filter_prowlarr_by_quality(candidates, profile_id=None):
    """Apply the item's quality ladder to torrent/Usenet search hits.

    Those sources enter the structured-metadata matching lane because their
    release title is already split into artist/title.  That lane historically
    skipped quality filtering for every non-YouTube source on the assumption
    that the source handled it internally.  Prowlarr does not: its search API
    exposes a release title, not normalized audio properties.  The projection
    now parses those properties, and this is where they become an actual
    profile decision before any torrent/NZB is grabbed.

    Other streaming candidates are preserved unchanged.  A DB/profile read
    failure is also non-fatal; the post-download quality guard remains the
    final authority.
    """
    rows = list(candidates or [])
    prowlarr = [
        row for row in rows
        if getattr(row, 'username', None) in ('torrent', 'usenet')
    ]
    if not prowlarr:
        return rows

    try:
        from core.quality.model import AudioQuality
        from core.quality.selection import load_profile_by_id, targets_from_profile

        profile = load_profile_by_id(profile_id)
        targets, fallback_enabled = targets_from_profile(profile)
        if fallback_enabled or not targets:
            return rows
        ranked = [
            row for row in prowlarr
            if _satisfies_a_target_on_stated_facts(
                AudioQuality(
                    format=str(getattr(row, 'quality', '') or 'unknown'),
                    bitrate=getattr(row, 'bitrate', None),
                    sample_rate=getattr(row, 'sample_rate', None),
                    bit_depth=getattr(row, 'bit_depth', None),
                ),
                targets,
            )
        ]
    except Exception as exc:  # noqa: BLE001 - never turn config I/O into no hits
        logger.debug("Prowlarr quality filtering unavailable: %s", exc)
        return rows

    kept_ids = {id(row) for row in ranked}
    filtered = [
        row for row in rows
        if getattr(row, 'username', None) not in ('torrent', 'usenet')
        or id(row) in kept_ids
    ]
    if len(ranked) != len(prowlarr):
        logger.info(
            "Prowlarr quality filter: kept %d/%d release(s) for %s",
            len(ranked),
            len(prowlarr),
            f"item profile {profile_id}" if profile_id else "app default",
        )
    return filtered


def _score_streaming_candidates(results, spotify_track):
    """Match-filter structured-metadata hits (YouTube, Tidal, torrent, …)."""
    source_label = results[0].username.replace('_dl', '').title()
    expected_artists = spotify_track.artists if spotify_track else []
    expected_title = spotify_track.name if spotify_track else ''
    expected_duration = spotify_track.duration_ms if spotify_track else 0

    # Detect if the expected track is a specific version (live, remix, acoustic, etc.)
    expected_title_lower = (expected_title or '').lower()
    _version_keywords = _VERSION_KEYWORDS
    expected_is_version = any(_has_version_kw(expected_title_lower, kw)
                              for kw in _version_keywords)

    scored = []
    _strict_duration_sources = {'tidal', 'qobuz', 'hifi', 'deezer_dl', 'amazon'}
    for r in results:
        if (
            r.username in _strict_duration_sources
            and _duration_mismatch_exceeds_integrity_tolerance(expected_duration, r.duration or 0)
        ):
            logger.info(
                "[%s] Rejecting candidate due to duration mismatch before download: "
                "expected %.1fs, candidate %.1fs",
                source_label,
                expected_duration / 1000.0,
                (r.duration or 0) / 1000.0,
            )
            continue

        # Score using matching engine's generic scorer (same weights as Soulseek).
        # Torrent/usenet release projections sometimes only have the indexer name
        # in the artist field when a title did not parse as "Artist - Release".
        # Treat that as unknown artist, not as a real mismatch.
        has_only_fallback_artist = _torrent_usenet_artist_is_fallback(r)
        candidate_artists = [] if has_only_fallback_artist else ([r.artist] if r.artist else [])
        confidence, match_type = matching_engine.score_track_match(
            source_title=expected_title,
            source_artists=expected_artists,
            source_duration_ms=expected_duration,
            candidate_title=r.title or '',
            candidate_artists=candidate_artists,
            candidate_duration_ms=r.duration or 0,
        )

        # Album-name fallback for torrent / usenet per-track results.
        #
        # When this fallback runs: hybrid mode + non-album batch (single
        # track wishlist / playlist of singles). Album-context batches
        # never reach here — the album-bundle gate in
        # core/downloads/album_bundle_dispatch.py engages the bulk-
        # download flow in single-source mode, and the hybrid chain
        # filter in core/downloads/task_worker.py strips torrent /
        # usenet from album batches in hybrid mode. What's left is the
        # single-track-in-hybrid case where a user is searching for one
        # track and the only torrent / usenet result is the album that
        # contains it.
        #
        # Without this fallback, "Luther (with SZA)" against a
        # candidate titled "GNX (2024) [FLAC]" scores ~0 on track-title
        # alone — even though the album torrent does in fact contain
        # the wanted track. Scoring the candidate title against the
        # wanted track's ALBUM name and taking the max gives album-
        # level releases a fair shot. The Auto-Import sweep then picks
        # the right file out of the downloaded album folder.
        expected_album = getattr(spotify_track, 'album', None) if spotify_track else None
        if r.username in ('torrent', 'usenet') and expected_album:
            album_conf, _ = matching_engine.score_track_match(
                source_title=expected_album,
                source_artists=expected_artists,
                source_duration_ms=0,            # albums don't have one duration
                candidate_title=r.title or '',
                candidate_artists=candidate_artists,
                candidate_duration_ms=0,
            )
            if album_conf > confidence:
                confidence = album_conf
                match_type = 'album_release'

        # Version detection penalty — reject live/remix/acoustic when expecting original
        r_title_lower = (r.title or '').lower()
        is_wrong_version = False
        if not expected_is_version:
            # Expecting original — penalize versions
            for kw in _version_keywords:
                if _has_version_kw(r_title_lower, kw) and not _has_version_kw(expected_title_lower, kw):
                    confidence *= 0.4  # Heavy penalty
                    is_wrong_version = True
                    break
        else:
            # Expecting specific version — penalize results that don't have it
            for kw in _version_keywords:
                if _has_version_kw(expected_title_lower, kw) and not _has_version_kw(r_title_lower, kw):
                    confidence *= 0.5
                    is_wrong_version = True
                    break

        # Artist gate — streaming APIs (Tidal/Qobuz/HiFi/Deezer) have reliable metadata,
        # so "My Will" by "B. Starr" should never match expected "B小町".
        # Torrent/usenet must also pass this gate so title-only matches
        # from the wrong artist do not get downloaded. YouTube gets a SOFT
        # version below: its artist field (title-parse or channel) is
        # unreliable, but "no artist evidence at all" now raises the bar
        # instead of waiving it — the old full exemption downloaded
        # "We Were Shameless" by the wrong band for "We're Shameless"
        # (apostrophe folding made the titles near-identical, and nothing
        # else was checked).
        if not has_only_fallback_artist:
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
            if r.username == 'youtube':
                if _best_artist < 0.5:
                    # No artist evidence (random channel, unparsable video
                    # title). The title must then carry the WHOLE identity:
                    # near-exact confidence AND no significant words beyond
                    # the wanted title/artist + upload noise. This is what
                    # rejects "We Were Shameless" (the extra 'we'), reaction
                    # videos and mislabeled uploads while keeping legit
                    # "Song by Artist (lyrics)"-style uploads alive.
                    if confidence < 0.75 or not _title_words_are_expected(
                            r.title, expected_title, expected_artists):
                        logger.info(
                            "[%s] Rejecting candidate without artist evidence: "
                            "expected=%s candidate_artist=%r title=%r conf=%.2f",
                            source_label, list(expected_artists),
                            _cand_artist_raw, r.title or '', confidence,
                        )
                        continue
            elif r.username in ('torrent', 'usenet') and _best_artist < 0.5:
                logger.info(
                    "[%s] Rejecting candidate due to artist mismatch: "
                    "expected=%s candidate=%r title=%r",
                    source_label,
                    list(expected_artists),
                    _cand_artist_raw,
                    r.title or '',
                )
                continue
            elif _best_artist < 0.5 and confidence < 0.85:
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
    return []


def _match_filename_candidates(results, spotify_track, profile_id=None):
    """Soulseek path matcher, or YouTube structured-score fallthrough."""
    # Uses the existing, powerful matching engine for scoring (Soulseek P2P results)
    _max_q = config_manager.get('soulseek.max_peer_queue', 0) or 0
    initial_candidates = matching_engine.find_best_slskd_matches_enhanced(spotify_track, results, max_peer_queue=_max_q)
    if not initial_candidates:
        return []

    # Skip quality filtering for streaming source results that somehow got here
    is_streaming_source = initial_candidates[0].username in _STREAMING_USERNAMES if initial_candidates else False

    if is_streaming_source:
        source_label = initial_candidates[0].username.title()
        if any(getattr(c, 'username', None) == 'youtube' for c in initial_candidates):
            quality_filtered_candidates = _filter_youtube_by_quality(
                initial_candidates, profile_id,
            )
            if not quality_filtered_candidates:
                logger.error("[Quality Filter] No YouTube candidates match quality profile - download will fail per user preferences")
                return []
        else:
            logger.info(f"[{source_label}] Skipping quality filter - streaming source handles quality internally")
            quality_filtered_candidates = initial_candidates
    else:
        # Filter by user's quality profile before artist verification (Soulseek only)
        # Use existing download_orchestrator to avoid re-initializing (which accesses download_path filesystem)
        quality_filtered_candidates = download_orchestrator.client('soulseek').filter_results_by_quality_preference(
            initial_candidates, profile_id=profile_id)

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
        # Streaming results: the matching engine already scored the title.
        # YouTube is the exception — this is the FALLTHROUGH lane for results
        # that failed structured validation, and it used to re-admit exactly
        # the chaff the gate above rejects (wrong-artist near-titles, renamed
        # uploads). Same discipline here: artist evidence in the title/artist
        # fields, or a title made only of wanted words + upload noise.
        if is_streaming_source:
            if candidate.username == 'youtube':
                # A cover/remix/reaction is never rescued by the fallthrough,
                # even from the right artist's own channel.
                _want_lower = (spotify_track.name or '').lower() if spotify_track else ''
                _cand_lower = (candidate.title or '').lower()
                if any(_has_version_kw(_cand_lower, kw) and not _has_version_kw(_want_lower, kw)
                       for kw in _VERSION_KEYWORDS):
                    logger.info("[Youtube] Fallthrough rejecting %r — alternate "
                                "recording marker", candidate.title or '')
                    continue
                if artist_word_sets:
                    cand_text_words = set(matching_engine.normalize_string(
                        f"{candidate.artist or ''} {candidate.title or ''}").split())
                    artist_evident = any(w.issubset(cand_text_words) for w in artist_word_sets)
                    if not artist_evident and not _title_words_are_expected(
                            candidate.title, spotify_track.name if spotify_track else '',
                            spotify_artists):
                        logger.info("[Youtube] Fallthrough rejecting %r — no artist "
                                    "evidence and foreign title words", candidate.title or '')
                        continue
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
