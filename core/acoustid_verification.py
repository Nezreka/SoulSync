"""
AcoustID Verification Service

Verifies downloaded audio files match expected track metadata by comparing
title/artist from AcoustID fingerprint results against the expected track info.

If the audio fingerprint confidently identifies a DIFFERENT song than expected,
the file is flagged as incorrect.
"""

import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, Tuple, List
from enum import Enum
from utils.logging_config import get_logger
from core.acoustid_client import AcoustIDClient
from core.matching_engine import MusicMatchingEngine
from core.matching.version_mismatch import is_acceptable_version_mismatch
from core.matching.script_compat import is_cross_script_mismatch
from core.musicbrainz_client import MusicBrainzClient

logger = get_logger("acoustid.verification")

# Thresholds — single definition lives in the shared core; re-exported here so
# existing importers keep working and the values can't drift between paths.
from core.matching.audio_verification import (  # noqa: E402
    MIN_ACOUSTID_SCORE, TITLE_MATCH_THRESHOLD, ARTIST_MATCH_THRESHOLD,
)

# Single matching-engine instance so version detection reuses the same patterns
# used by the pre-download Soulseek matcher (remix / live / acoustic /
# instrumental / etc). detect_version_type doesn't use self state, so one
# shared instance is fine.
_match_engine_for_version = MusicMatchingEngine()


def _detect_title_version(title: str) -> str:
    """Return version label for a track title.

    Returns ``'original'`` when no version marker is detected, otherwise one
    of the labels produced by ``MusicMatchingEngine.detect_version_type``
    (``'instrumental'``, ``'live'``, ``'acoustic'``, ``'remix'``, etc).
    """
    if not title:
        return 'original'
    version_type, _ = _match_engine_for_version.detect_version_type(title)
    return version_type


class VerificationResult(Enum):
    """Possible outcomes of audio verification."""
    PASS = "pass"       # Title/artist match - file is correct
    FAIL = "fail"       # Title/artist mismatch - wrong file downloaded
    SKIP = "skip"       # Genuinely couldn't verify (no match in DB) - continue normally
    DISABLED = "disabled"  # Verification not enabled
    ERROR = "error"     # Lookup errored (invalid key / rate limit / no backend) - continue, but flag it


# normalize() + similarity() + the alias-aware comparison now live in the shared
# decision core (core/matching/audio_verification.py) so import-time verification
# and the library scan share ONE definition — the <>-strip fix, CJK handling and
# thresholds can't drift apart again. Names kept (`_normalize` etc.) for existing
# importers/tests.
from core.matching.audio_verification import (  # noqa: E402
    normalize as _normalize,
    similarity as _similarity,
    _alias_aware_artist_sim,
    _recording_identity as _core_recording_identity,
    _find_best_title_artist_match as _core_find_best_title_artist_match,
    evaluate as _core_evaluate,
    Decision as _CoreDecision,
)


def _find_best_title_artist_match(recordings, expected_title, expected_artist,
                                  expected_artist_aliases=None):
    """Back-compat wrapper around the shared core matcher (keeps the
    ``expected_artist_aliases`` kwarg name for existing callers/tests)."""
    return _core_find_best_title_artist_match(
        recordings, expected_title, expected_artist, expected_artist_aliases,
    )


# Shared MusicBrainz client for enrichment lookups
_mb_client = None
_mb_client_lock = threading.Lock()

# Shared MusicBrainzService for alias lookups (issue #442). Service
# layer wraps the raw client + adds caching + DB access — all of which
# the alias resolution chain (library DB → cache → live MB) needs.
_mb_service = None
_mb_service_lock = threading.Lock()

MAX_MB_ENRICHMENT_LOOKUPS = 3


def _get_mb_client() -> MusicBrainzClient:
    """Get or create a shared MusicBrainz client instance."""
    global _mb_client
    if _mb_client is None:
        with _mb_client_lock:
            if _mb_client is None:
                _mb_client = MusicBrainzClient()
    return _mb_client


def _get_mb_service():
    """Get or create a shared MusicBrainzService instance.

    Used by the alias-resolution chain in `verify_audio_file`. Lazy
    init so importing this module doesn't trigger a DB connection on
    paths that never run AcoustID verification (test runs, dry runs).
    """
    global _mb_service
    if _mb_service is None:
        with _mb_service_lock:
            if _mb_service is None:
                from core.musicbrainz_service import MusicBrainzService
                from database.music_database import get_database
                _mb_service = MusicBrainzService(get_database())
    return _mb_service


def _resolve_expected_artist_aliases(expected_artist_name: str) -> List[str]:
    """Look up alternate-spelling aliases for the expected artist.

    Issue #442 — bridges cross-script artist comparisons (Japanese
    kanji ↔ romanized, Cyrillic ↔ Latin, etc.) without forcing the
    verifier to know about the resolution chain. Best-effort: any
    failure (no MB service, network down, no library DB) returns
    empty list so verification falls back to the prior direct
    similarity check.
    """
    if not expected_artist_name:
        return []
    try:
        return _get_mb_service().lookup_artist_aliases(expected_artist_name)
    except Exception as e:
        logger.debug("alias lookup failed for %r: %s", expected_artist_name, e)
        return []


def _enrich_recordings_from_musicbrainz(
    recordings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Enrich recordings that are missing title/artist by looking up their
    MBIDs via MusicBrainz.

    AcoustID often returns recordings with title=None, artist=None even though
    the MBIDs are valid. This resolves the metadata so verification can compare
    title/artist instead of skipping.

    Args:
        recordings: List of recording dicts from fingerprint_and_lookup()

    Returns:
        The same list, with title/artist filled in where possible.
    """
    # Fast path: if any recording already has title AND artist, no enrichment needed
    if any(rec.get('title') and rec.get('artist') for rec in recordings):
        return recordings

    logger.info(f"Enriching {len(recordings)} recordings via MusicBrainz (all missing title/artist)...")

    mb = _get_mb_client()
    enriched_count = 0

    for rec in recordings[:MAX_MB_ENRICHMENT_LOOKUPS]:
        mbid = rec.get('mbid')
        if not mbid:
            continue

        try:
            data = mb.get_recording(mbid, includes=['artist-credits'])
            if not data:
                logger.debug(f"MusicBrainz returned no data for recording {mbid}")
                continue

            title = data.get('title')
            artist_credit = data.get('artist-credit', [])

            # Build artist string from artist-credit array
            # Each entry has {"artist": {"name": "..."}, "joinphrase": "..."}
            artist_parts = []
            for credit in artist_credit:
                name = credit.get('artist', {}).get('name', '')
                joinphrase = credit.get('joinphrase', '')
                if name:
                    artist_parts.append(name + joinphrase)
            artist = ''.join(artist_parts).strip() if artist_parts else None

            if title:
                rec['title'] = title
                logger.debug(f"Enriched {mbid}: title='{title}'")
            if artist:
                rec['artist'] = artist
                logger.debug(f"Enriched {mbid}: artist='{artist}'")

            if title or artist:
                enriched_count += 1

        except Exception as e:
            logger.debug(f"Failed to enrich recording {mbid}: {e}")
            continue

    logger.info(f"Enriched {enriched_count}/{min(len(recordings), MAX_MB_ENRICHMENT_LOOKUPS)} recordings from MusicBrainz")
    return recordings


def _collect_recording_titles(data: Dict[str, Any],
                              mbid: str) -> Tuple[List[str], List[str]]:
    """The titles a MusicBrainz recording payload names for the recording,
    as ``(release_titles, alias_titles)``.

    ``release_titles``: the canonical title plus its track title on each
    release in the payload — the strong identity evidence. ``alias_titles``:
    the recording's own aliases — also official alternate names, but
    crowd-sourced and weaker, so the decision core consults them second
    and reports them as aliases, never as release listings. Each list is
    deduped case-insensitively, payload order preserved.

    A recording lookup (``inc=releases+media``) returns only this
    recording's track per medium, and those track objects carry no
    recording reference — but a payload shaped any other way (a
    full-tracklist response, a third-party MusicBrainz mirror) can carry
    sibling tracks, so a track that DOES name a different recording is
    never collected: only tracks with no recording reference, or this
    recording's own, count as its titles.

    ``Search hint`` aliases are excluded: MusicBrainz marks common
    misspellings and misattributions as search hints, and a title a
    recording is commonly MISlabelled as is precisely the wrong-download
    shape — it is not a name the recording is legitimately known by.
    """
    release_titles: List[str] = []
    alias_titles: List[str] = []
    seen = set()

    def _add(titles: List[str], title: Any) -> None:
        title = (title or '').strip() if isinstance(title, str) else ''
        if title and title.lower() not in seen:
            seen.add(title.lower())
            titles.append(title)

    _add(release_titles, data.get('title'))
    for release in data.get('releases') or []:
        if not isinstance(release, dict):
            continue
        for medium in release.get('media') or []:
            if not isinstance(medium, dict):
                continue
            for track in medium.get('tracks') or []:
                if not isinstance(track, dict):
                    continue
                track_rec = track.get('recording')
                if isinstance(track_rec, dict):
                    track_rec_id = track_rec.get('id')
                elif isinstance(track_rec, str):
                    track_rec_id = track_rec
                else:
                    track_rec_id = None
                if track_rec_id and track_rec_id != mbid:
                    continue
                _add(release_titles, track.get('title'))
    for alias in data.get('aliases') or []:
        if isinstance(alias, dict):
            if (alias.get('type') or '').strip().lower() == 'search hint':
                continue
            _add(alias_titles, alias.get('name'))
    return release_titles, alias_titles


# Alternate-title resolutions, per recording MBID: the RAW collected
# titles plus when they were read. Raw, not filtered — which titles are
# "alternate" depends on the canonical title of the recording dict at
# hand, and the same MBID can be presented under different canonical
# titles, so the canonical filter is applied per recording at attach
# time, never baked into the cache.
#
# Both outcomes are cached: a positive resolution for a day, a negative
# one (the recording has no other names — the ordinary case, and the one
# a bulk scan would otherwise re-pay for on every file; also a genuine
# MusicBrainz 404 for the MBID) for an hour. "Positive" is classified at
# put time: the collected titles name MORE than the one canonical name
# (the release list always contains the canonical title, so a bare
# non-empty list cannot be the test). Transport failures are NOT cached:
# a MusicBrainz outage must not suppress resolution after it ends.
# Bounded at 512 entries, cleared wholesale when full — a pure
# optimisation; correctness never depends on it, only cost does.
_alternate_titles_cache: Dict[
    str, Tuple[float, bool, List[str], List[str]]] = {}
_alternate_titles_cache_lock = threading.Lock()
_ALTERNATE_TITLES_CACHE_MAX = 512
_ALTERNATE_TITLES_TTL_POSITIVE_S = 24 * 3600.0
_ALTERNATE_TITLES_TTL_NEGATIVE_S = 3600.0


def _alternate_titles_cache_get(
    mbid: str,
) -> Optional[Tuple[List[str], List[str]]]:
    with _alternate_titles_cache_lock:
        entry = _alternate_titles_cache.get(mbid)
        if entry is None:
            return None
        stored_at, positive, release_titles, alias_titles = entry
        ttl = (_ALTERNATE_TITLES_TTL_POSITIVE_S if positive
               else _ALTERNATE_TITLES_TTL_NEGATIVE_S)
        if time.monotonic() - stored_at > ttl:
            del _alternate_titles_cache[mbid]
            return None
    return list(release_titles), list(alias_titles)


def _alternate_titles_cache_put(
    mbid: str, release_titles: List[str], alias_titles: List[str],
) -> None:
    positive = (len(release_titles) + len(alias_titles)) > 1
    with _alternate_titles_cache_lock:
        if (mbid not in _alternate_titles_cache
                and len(_alternate_titles_cache) >= _ALTERNATE_TITLES_CACHE_MAX):
            _alternate_titles_cache.clear()
        _alternate_titles_cache[mbid] = (
            time.monotonic(), positive,
            list(release_titles), list(alias_titles))


def _genuinely_different(titles: List[str], canonical_title: str) -> List[str]:
    """The subset of ``titles`` that are genuinely different names from
    the canonical title. "Different" is judged on the qualifier-preserving
    recording identity, so a release's bare "Song" IS an alternate of a
    canonical "Song (Instrumental)" (the core's version gates decide
    whether that may identify anything), while a mere case variant of
    the canonical title is not an alternate at all."""
    canonical_identity = _core_recording_identity(canonical_title or '')
    return [
        t for t in titles
        if _core_recording_identity(t) != canonical_identity
    ]


def _resolve_alternate_titles(
    recordings: List[Dict[str, Any]],
) -> bool:
    """
    Fill ``alternate_titles`` / ``alias_titles`` on recordings from
    MusicBrainz (#1700).

    AcoustID reports a recording under its canonical MusicBrainz title,
    but the same recording appears under different track titles on
    different releases — regional editions retitle it, and the expected
    metadata usually follows ONE release's title. One
    ``get_recording(inc=releases+media)`` call returns this recording's
    track title on each release in the lookup response, plus the
    recording's own aliases: the set of names this recording is
    legitimately known by, keyed by its MusicBrainz ID. (A MusicBrainz
    lookup links at most 25 releases, so for a compilation-heavy
    recording the set can be a subset — a title on a release past the
    cap simply stays unresolved and the original verdict stands.)

    The lookup budget (``MAX_MB_ENRICHMENT_LOOKUPS`` distinct MBIDs,
    shared in spirit with the title/artist enrichment — which may spend
    its own budget earlier in the same verification) is spent on the
    highest-scored candidates first: those are the only ones the
    decision core's identity check can use. Every recording dict that
    shares a resolved MBID gains the titles, not just the first copy —
    the fingerprint's top-scored copy is the one that matters.

    Best-effort like the alias resolution: any failure (no MB client,
    network down, unknown MBID, malformed payload) leaves the recording
    untouched and the caller keeps the verdict it already had. Returns
    True when at least one recording gained a genuinely different
    alternate or alias title.

    Args:
        recordings: List of recording dicts from fingerprint_and_lookup()

    Returns:
        True if any recording gained alternate titles, else False.
    """
    def _score(rec: Dict[str, Any]) -> float:
        try:
            return float(rec.get('score'))
        except (TypeError, ValueError):
            return -1.0

    by_mbid: Dict[str, List[Dict[str, Any]]] = {}
    for rec in recordings:
        mbid = rec.get('mbid')
        if mbid and (not rec.get('alternate_titles')
                     or not rec.get('alias_titles')):
            by_mbid.setdefault(mbid, []).append(rec)
    if not by_mbid:
        return False
    # Highest-scored occurrence decides an MBID's priority.
    chosen = sorted(
        by_mbid.items(),
        key=lambda item: max(_score(r) for r in item[1]),
        reverse=True,
    )[:MAX_MB_ENRICHMENT_LOOKUPS]

    mb = None
    resolved_any = False

    for mbid, recs in chosen:
        collected = _alternate_titles_cache_get(mbid)
        if collected is None:
            if mb is None:
                try:
                    mb = _get_mb_client()
                except Exception as e:
                    logger.debug(
                        f"Alternate-title resolution unavailable: {e}")
                    return resolved_any
            try:
                # raise_on_error separates a genuine 404 (MusicBrainz
                # does not know this MBID — a stable negative worth
                # caching) from a transport failure (returns None only
                # for the 404; anything else raises and stays uncached).
                data = mb.get_recording(
                    mbid, includes=['releases', 'media', 'aliases'],
                    raise_on_error=True)
                if not data:
                    _alternate_titles_cache_put(mbid, [], [])
                    continue
                collected = _collect_recording_titles(data, mbid)
            except Exception as e:
                logger.debug(
                    f"Failed to resolve alternate titles for {mbid}: {e}")
                continue
            _alternate_titles_cache_put(mbid, collected[0], collected[1])
        release_titles, alias_titles = collected
        for rec in recs:
            alternates = _genuinely_different(
                release_titles, rec.get('title') or '')
            aliases = _genuinely_different(
                alias_titles, rec.get('title') or '')
            if alternates:
                rec['alternate_titles'] = alternates
                resolved_any = True
            if aliases:
                rec['alias_titles'] = aliases
                resolved_any = True
            if alternates or aliases:
                logger.debug(
                    f"Alternate titles for {mbid}: "
                    f"releases={alternates} aliases={aliases}")

    return resolved_any


class AcoustIDVerification:
    """
    Verification service that compares audio fingerprint identity
    against expected track metadata using title/artist matching.

    Design Principle: FAIL OPEN
    - Only returns FAIL when we are CONFIDENT the file is wrong
    - Any error or uncertainty results in SKIP (continue normally)
    - Never blocks downloads due to verification infrastructure issues

    Usage:
        verifier = AcoustIDVerification()
        result, message = verifier.verify_audio_file(
            "/path/to/downloaded.mp3",
            "Expected Song Title",
            "Expected Artist"
        )

        if result == VerificationResult.FAIL:
            # Move to quarantine
        else:
            # Continue with normal processing (PASS, SKIP, or DISABLED)
    """

    def __init__(self):
        """Initialize verification service."""
        self.acoustid_client = AcoustIDClient()

    def verify_audio_file(
        self,
        audio_file_path: str,
        expected_track_name: str,
        expected_artist_name: str,
        context: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
    ) -> Tuple[VerificationResult, str]:
        """
        Verify that an audio file matches expected track metadata.

        Compares title/artist from AcoustID fingerprint results against
        the expected track info. No MusicBrainz lookup needed.

        This is the ONE verification path. The library scan calls it too
        (``core/repair_jobs/acoustid_scanner``) rather than repeating the
        availability check, the lookup, the score gate, the MusicBrainz
        enrichment of title-less recordings and the alias wiring — which is how
        those five drifted apart in the first place, while only the final
        decision was shared.

        Args:
            audio_file_path: Path to the downloaded audio file
            expected_track_name: Track name we expected to download
            expected_artist_name: Artist name we expected
            context: Optional dict. Used for download-specific hints on the way
                in, and written back on the way out with what this lookup saw
                (``_acoustid_recordings``, ``_acoustid_best_score``,
                ``_acoustid_decision``) so a caller needing the evidence does
                not have to fingerprint the file a second time.
            min_score: Override the fingerprint-confidence floor. The scan
                exposes it as a job setting; the download uses the constant.

        Returns:
            Tuple of (VerificationResult, reason_message)
        """
        try:
            # Step 1: Check availability. A caller that injected its own client
            # has already decided the client is usable, and need not implement
            # the probe — the library scan passes whatever the job context
            # carries.
            probe_available = getattr(self.acoustid_client, 'is_available', None)
            if callable(probe_available):
                available, reason = probe_available()
                if not available:
                    logger.debug(f"AcoustID verification skipped: {reason}")
                    return VerificationResult.SKIP, reason

            # Step 2: Fingerprint and lookup in AcoustID (structured so an
            # actual error — invalid key / rate limit / no chromaprint — is
            # reported distinctly from a genuine no-match, instead of both
            # silently surfacing as "Skipped").
            logger.info(f"Fingerprinting and looking up: {audio_file_path}")
            if hasattr(self.acoustid_client, 'lookup_with_status'):
                lookup = self.acoustid_client.lookup_with_status(audio_file_path) or {}
            else:
                # Older client shape: dict-or-None, with no way to tell a real
                # no-match from a failed lookup. Treated as a no-match, which is
                # the safe reading — it makes no claim about the file.
                lookup = self.acoustid_client.fingerprint_and_lookup(audio_file_path) or {}
            status = lookup.get('status')
            # Infer status by content when absent (a caller/stub that returned
            # just recordings): recordings => matched, none => no match.
            if status is None:
                status = 'ok' if lookup.get('recordings') else 'no_match'

            if status in ('error', 'no_backend', 'fingerprint_error', 'unavailable'):
                if isinstance(context, dict):
                    context['_acoustid_status'] = status
                    if lookup.get('invalid_key'):
                        context['_acoustid_invalid_key'] = True
                # Something is broken (not the track's fault) — never quarantine
                # on this; surface it so the user can fix it.
                return VerificationResult.ERROR, lookup.get('error', 'AcoustID lookup failed')

            if status != 'ok':
                # no_match / unsupported / not_found — genuinely could not verify.
                return VerificationResult.SKIP, lookup.get('error', 'No match in AcoustID database')

            acoustid_result = lookup
            recordings = acoustid_result.get('recordings', [])
            best_score = acoustid_result.get('best_score', 0)

            if not recordings:
                return VerificationResult.SKIP, "No match in AcoustID database"

            if isinstance(context, dict):
                context['_acoustid_best_score'] = best_score

            logger.debug(
                f"AcoustID returned {len(recordings)} recording(s) "
                f"(best fingerprint score: {best_score:.2f})"
            )

            # Step 3: Check fingerprint confidence
            floor = MIN_ACOUSTID_SCORE if min_score is None else float(min_score)
            if best_score < floor:
                msg = f"AcoustID fingerprint score too low ({best_score:.2f}) to verify"
                logger.info(msg)
                return VerificationResult.SKIP, msg

            # Enrich recordings that are missing title/artist via MusicBrainz lookup
            recordings = _enrich_recordings_from_musicbrainz(recordings)

            # Issue #442 — alias resolution is LAZY. We pass a memoising
            # thunk to the artist-comparison sites; it only fires the
            # multi-tier lookup (library DB → cache → live MB) when
            # direct artist similarity falls below threshold. Verifications
            # where the direct match already passes (the common case for
            # same-script artist names) never trigger any lookup work,
            # so the fix doesn't add a per-verification DB query for the
            # happy path. When the thunk DOES fire, the result is cached
            # in the closure so the 3 comparison sites within one
            # verification share a single resolution pass.
            _alias_cache: Dict[str, Any] = {}

            def _aliases_provider() -> List[str]:
                if 'value' not in _alias_cache:
                    resolved = _resolve_expected_artist_aliases(expected_artist_name)
                    _alias_cache['value'] = resolved
                    if resolved:
                        logger.debug(
                            "Resolved %d aliases for expected artist '%s'",
                            len(resolved), expected_artist_name,
                        )
                return _alias_cache['value']

            # Steps 4-5: delegate the PASS/SKIP/FAIL decision to the shared core
            # (core/matching/audio_verification.evaluate) so import verification
            # and the library scan apply identical logic.
            # A version we went after on purpose (Settings → prefer a version)
            # is stamped on the context by the download walk. Without it the
            # version gate compares the source's "Song" against the
            # fingerprinted "Song (Extended Mix)" and quarantines the file the
            # setting just spent a search finding.
            _accept_version = None
            if isinstance(context, dict):
                _accept_version = str(context.get('_preferred_version_taken') or '') or None

            outcome = _core_evaluate(
                expected_track_name, expected_artist_name, recordings,
                fingerprint_score=best_score,
                aliases_provider=_aliases_provider,
                accept_version=_accept_version,
            )
            if (outcome.decision == _CoreDecision.FAIL
                    or (outcome.decision == _CoreDecision.SKIP
                        and best_score >= 0.95)):
                # Same recording, different release title (#1700): the
                # decision just made rests on the recording's canonical
                # MusicBrainz title, but the expected title may be the
                # title this recording carries on another release (a
                # regional edition's retitle scores 0% against the
                # canonical title while naming the same recording).
                # Resolve the recordings' alternate titles from
                # MusicBrainz — keyed by the AcoustID recording IDs, one
                # call per distinct MBID (capped, highest scores first) —
                # and re-decide ONCE.
                #
                # The trigger is deliberately narrow, because resolution
                # costs MusicBrainz calls on the non-PASS path: a FAIL is
                # the harm being fixed (a correct file quarantined /
                # flagged), and a SKIP is only worth the calls when the
                # fingerprint is near-certain (>= 0.95) — the shape where
                # identity evidence can actually change the outcome, and
                # the one `require_verified` turns into a rejection. An
                # ordinary ambiguous SKIP in a bulk scan of a foreign
                # catalogue pays nothing. A direct PASS never reaches
                # here at all, and a resolution failure keeps the
                # original verdict.
                try:
                    if _resolve_alternate_titles(recordings):
                        outcome = _core_evaluate(
                            expected_track_name, expected_artist_name,
                            recordings,
                            fingerprint_score=best_score,
                            aliases_provider=_aliases_provider,
                            accept_version=_accept_version,
                        )
                except Exception as e:
                    logger.debug(
                        "Alternate-title resolution failed, keeping "
                        f"original verdict: {e}")
            logger.info(
                "Best match: '%s' by '%s' (title_sim=%.2f, artist_sim=%.2f) -> %s",
                outcome.matched_title, outcome.matched_artist,
                outcome.title_sim, outcome.artist_sim, outcome.decision.value,
            )
            _decision_map = {
                _CoreDecision.PASS: VerificationResult.PASS,
                _CoreDecision.SKIP: VerificationResult.SKIP,
                _CoreDecision.FAIL: VerificationResult.FAIL,
            }
            if isinstance(context, dict):
                # The evidence, for a caller that has to say more than
                # pass/fail about it — which candidates were in contention, and
                # what the core concluded.
                context['_acoustid_recordings'] = recordings
                context['_acoustid_decision'] = outcome
            result = _decision_map[outcome.decision]
            if result == VerificationResult.PASS:
                logger.info("AcoustID verification PASSED - %s", outcome.reason)
            elif result == VerificationResult.FAIL:
                logger.warning("AcoustID verification FAILED - %s", outcome.reason)
            else:
                logger.info("AcoustID verification SKIPPED - %s", outcome.reason)
            return result, outcome.reason

        except Exception as e:
            # An unexpected fault in OUR code, or in a lookup it depends on, is
            # not a statement about the file. Reported as SKIP it became one:
            # the library scan turns a SKIP on an untagged file into
            # 'unverified', so a database error or a MusicBrainz outage
            # mid-verification got written down as a verdict — and with
            # `require_verified` on, the download path turns the same SKIP into
            # a rejection. ERROR says what it is, and both callers already
            # handle it without touching the file's standing.
            logger.error(f"Unexpected error during AcoustID verification: {e}")
            return VerificationResult.ERROR, f"Verification error: {str(e)}"

    def quick_check_available(self) -> Tuple[bool, str]:
        """
        Quick check if verification is available without doing a full verification.

        Returns:
            Tuple of (is_available, reason)
        """
        return self.acoustid_client.is_available()
