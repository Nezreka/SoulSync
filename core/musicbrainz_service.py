from typing import Optional, Dict, Any
import json
import re
import threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from utils.logging_config import get_logger
from core.musicbrainz_client import MusicBrainzClient
from core.worker_utils import catalog_overlap_score, pick_artist_by_catalog
from database.music_database import MusicDatabase

logger = get_logger("musicbrainz_service")

# The cached form of "MusicBrainz answered, and the answer is no aliases".
# ``resolved`` is what separates it from the row the old failure path wrote,
# which had the same empty alias list but no answer behind it — see
# ``lookup_artist_aliases``. Rows predating this marker and carrying no MBID
# are ambiguous, so they get one retry rather than standing forever.
_NO_ALIASES = {'aliases': [], 'resolved': True}


class MusicBrainzService:
    """Service layer for MusicBrainz integration with caching and matching logic"""
    
    def __init__(self, database: MusicDatabase, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        self.db = database
        self.mb_client = MusicBrainzClient(app_name, app_version, contact_email)
        self.retry_days = 30  # Retry 'not_found' items after 30 days
    
    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate string similarity score (0.0 to 1.0)"""
        if not str1 or not str2:
            return 0.0
        
        # Normalize for comparison
        s1 = str1.lower().strip()
        s2 = str2.lower().strip()
        
        if s1 == s2:
            return 1.0
        
        return SequenceMatcher(None, s1, s2).ratio()
    
    def _check_cache(self, entity_type: str, entity_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Check if we have a cached MusicBrainz result"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            # Fix: Match exact artist_name (not OR artist_name IS NULL)
            # This prevents getting wrong cached results
            if artist_name is not None:
                cursor.execute("""
                    SELECT musicbrainz_id, metadata_json, match_confidence, last_updated
                    FROM musicbrainz_cache
                    WHERE entity_type = ? AND entity_name = ? AND artist_name = ?
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (entity_type, entity_name, artist_name))
            else:
                cursor.execute("""
                    SELECT musicbrainz_id, metadata_json, match_confidence, last_updated
                    FROM musicbrainz_cache
                    WHERE entity_type = ? AND entity_name = ? AND artist_name IS NULL
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (entity_type, entity_name))
            
            row = cursor.fetchone()
            
            if row:
                # Shorter TTL for null results (failed lookups) so they get retried sooner
                last_updated = datetime.fromisoformat(row[3]) if row[3] else None
                ttl_days = 30 if row[0] is None else 90  # row[0] is musicbrainz_id
                if last_updated and (datetime.now() - last_updated).days > ttl_days:
                    logger.debug(f"Cache entry for {entity_type} '{entity_name}' is stale (> {ttl_days} days)")
                    return None
                
                # Parse JSON with error handling
                try:
                    metadata = json.loads(row[1]) if row[1] else None
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in cache for {entity_type} '{entity_name}', ignoring")
                    metadata = None
                
                return {
                    'musicbrainz_id': row[0],
                    'metadata': metadata,
                    'confidence': row[2]
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking cache: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def _cached_aliases(cached: Optional[Dict[str, Any]], *,
                        for_mbid: Optional[str] = None) -> Optional[list]:
        """What a cache row settles about an artist's aliases, or None.

        An EMPTY list is an answer only when the row records that MusicBrainz
        actually answered (the ``resolved`` marker). A stored MBID used to count
        as that proof, and it is not: ``fetch_artist_aliases`` returned ``[]``
        for a timeout exactly as readily as for a genuine absence, so a single
        rate-limited fetch wrote "this artist has no aliases" against a perfectly
        good identity and held it for the row's whole 90-day TTL. That is what
        left a correct download unverifiable on every later scan.

        ``for_mbid`` restricts the answer to a row resolved against that
        identity — a name-keyed row for some other entity says nothing about it.
        """
        if not cached:
            return None
        metadata = cached.get('metadata')
        metadata = metadata if isinstance(metadata, dict) else {}
        if for_mbid is not None and str(cached.get('musicbrainz_id') or '') != str(for_mbid):
            return None
        raw = metadata.get('aliases')
        cleaned = ([str(x).strip() for x in raw if x]
                   if isinstance(raw, list) else [])
        if cleaned:
            return cleaned
        return [] if metadata.get('resolved') else None

    def _save_to_cache(self, entity_type: str, entity_name: str, artist_name: Optional[str],
                       musicbrainz_id: Optional[str], metadata: Optional[Dict], confidence: int):
        """Save MusicBrainz result to cache"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            metadata_json = json.dumps(metadata) if metadata else None

            cursor.execute("""
                INSERT OR REPLACE INTO musicbrainz_cache
                (entity_type, entity_name, artist_name, musicbrainz_id, metadata_json, match_confidence, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entity_type, entity_name, artist_name, musicbrainz_id, metadata_json, confidence, datetime.now()))

            conn.commit()

            logger.debug(f"Cached {entity_type} '{entity_name}' (MBID: {musicbrainz_id}, confidence: {confidence})")

        except Exception as e:
            logger.error(f"Error saving to cache: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def _cross_script_catalogue_match(self, artist_name, scored, owned_titles):
        """A match for an artist whose MusicBrainz name is in another script.

        Returns the same dict shape as :meth:`match_artist`, or None when no
        candidate clears the evidence bar and the normal name-based path should
        continue.
        """
        from core.matching.script_compat import is_cross_script_mismatch

        if not owned_titles:
            return None
        # Every candidate is scored before any is chosen. Taking the first one
        # that clears the bar would be taking MusicBrainz's result ORDER as the
        # tie-break — and that order is decided by name relevance, which is the
        # one signal this whole branch exists because it cannot use. A title as
        # ordinary as "Home" overlaps several catalogues, so the first hit is
        # not the best hit.
        best = None            # (overlap, result, mb_name)
        contested = False
        for _confidence, result in scored:
            mb_name = result.get('name', '') or ''
            if not is_cross_script_mismatch(artist_name, mb_name):
                continue
            if (result.get('score') or 0) < 90:
                continue
            overlap = catalog_overlap_score(
                owned_titles, self._candidate_release_titles(result.get('id')))
            if overlap < 1:
                continue
            if best is None or overlap > best[0]:
                best, contested = (overlap, result, mb_name), False
            elif overlap == best[0]:
                contested = True
        if best is None:
            return None
        if contested:
            # Two entities the library owns records by, and no readable name to
            # separate them. The id would be written onto the artist row and
            # feed the alias bridge from there, so a coin flip here is a wrong
            # identity that outlives this call.
            logger.info(
                "Cross-script match for %r refused: candidates tie on owned-album "
                "overlap (%d), and their names cannot be compared",
                artist_name, best[0],
            )
            return None
        overlap, result, mb_name = best
        mbid = result.get('id')
        confidence = min(100, 60 + overlap * 20)
        self._save_to_cache('artist', artist_name, None, mbid, result, confidence)
        logger.info(
            "Matched artist %r → %r across scripts on %d shared album(s) "
            "(MBID: %s)", artist_name, mb_name, overlap, mbid,
        )
        return {'mbid': mbid, 'name': mb_name,
                'confidence': confidence, 'cached': False}

    def _candidate_release_titles(self, mbid: str) -> list:
        """Release-group titles for a candidate MBID — the catalog side of
        same-name artist disambiguation."""
        if not mbid:
            return []
        try:
            data = self.mb_client.get_artist(mbid, includes=['release-groups'])
        except Exception:
            return []
        groups = (data or {}).get('release-groups') or []
        return [g.get('title') for g in groups if isinstance(g, dict) and g.get('title')]

    def match_artist(self, artist_name: str, owned_titles: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """
        Match an artist by name to MusicBrainz.

        ``owned_titles`` — the library artist's owned album titles. When given and
        more than one strong same-name candidate exists, the one whose release
        groups overlap those owned titles is chosen (disambiguates the ~5 "Rone"s);
        omitted → falls back to the highest-confidence candidate as before.

        Returns:
            Dict with 'mbid', 'name', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('artist', artist_name)
        if cached:
            cached_mbid = cached.get('musicbrainz_id')
            # Don't trust a cached mbid whose catalog has ZERO overlap with the
            # albums this library owns — that's the wrong same-name artist (and a
            # re-match would otherwise be blocked for up to the 90-day cache TTL,
            # #868). Fall through to a fresh, disambiguated resolve in that case.
            stale_wrong_match = bool(
                cached_mbid and owned_titles
                and catalog_overlap_score(owned_titles, self._candidate_release_titles(cached_mbid)) == 0
            )
            if not stale_wrong_match:
                logger.debug(f"Cache hit for artist '{artist_name}'")
                return {
                    'mbid': cached_mbid,
                    'name': artist_name,
                    'confidence': cached['confidence'],
                    'cached': True
                }
            logger.debug(f"Cached MB match for '{artist_name}' has no owned-catalog overlap — re-resolving")
        
        # Search MusicBrainz
        try:
            results = self.mb_client.search_artist(artist_name, limit=5)
            # Issue #586, which was fixed for the alias lookup and not for this:
            # a strict query hits the `artist` field alone and skips the alias
            # and sortname indexes — which is exactly where the romanised
            # spelling of a natively-scripted artist lives. Ask the fuzzy index
            # too when strict comes back empty.
            if not results:
                results = self.mb_client.search_artist(
                    artist_name, limit=5, strict=False)

            if not results:
                logger.info(f"No MusicBrainz results for artist '{artist_name}'")
                self._save_to_cache('artist', artist_name, None, None, None, 0)
                return None
            
            # Score every candidate (name similarity 60% + MB's own relevance 40%).
            scored = []
            for result in results:
                mb_name = result.get('name', '')
                mb_score = result.get('score', 0)  # MusicBrainz search score
                similarity = self._calculate_similarity(artist_name, mb_name)
                # Cap at 100 to prevent edge cases where MB score > 100
                confidence = min(100, int((similarity * 60) + (mb_score / 100 * 40)))
                scored.append((confidence, result))
            scored.sort(key=lambda s: s[0], reverse=True)

            # Among the strong (>=70) candidates, disambiguate same-name artists by
            # which one's release groups overlap the albums this library owns.
            gated = [r for conf, r in scored if conf >= 70]
            best_match = None
            best_confidence = scored[0][0] if scored else 0
            if gated:
                chosen, _overlap = pick_artist_by_catalog(
                    gated, owned_titles or [],
                    lambda r: self._candidate_release_titles(r.get('id')),
                )
                best_match = chosen
                best_confidence = next(conf for conf, r in scored if r is chosen)

            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_name = best_match.get('name')
                
                # Save to cache
                self._save_to_cache('artist', artist_name, None, mbid, best_match, best_confidence)
                
                logger.info(f"Matched artist '{artist_name}' → '{mb_name}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'name': mb_name,
                    'confidence': best_confidence,
                    'cached': False
                }
            # Nothing written in our own script cleared the bar. A name in
            # another script scores 0.0 against ours however certain
            # MusicBrainz is, so the formula above caps such a candidate at 40
            # against a gate of 70 — cross-script artists were structurally
            # unmatchable, and the only way to give one an id was by hand. The
            # name carries no information here, but the CATALOGUE does: album
            # titles survive a script difference far better than names, and an
            # entity holding records this library owns is not somebody else.
            # Deliberately strict — MusicBrainz confident about the name AND at
            # least one owned album in that entity's catalogue. Without owned
            # albums to check against there is no evidence, and it stays
            # unmatched rather than guessing.
            #
            # Runs LAST on purpose. Ahead of the gate it beat a same-script
            # candidate scoring 95 with one scoring 80, on nothing more than a
            # shared album title as common as "Home" — and the id it picked was
            # then written onto the artist row and fed the alias bridge from
            # there on. A fallback cannot do that: it only ever speaks where the
            # name path found nobody.
            cross = self._cross_script_catalogue_match(
                artist_name, scored, owned_titles)
            if cross is not None:
                return cross

            logger.info(f"Low confidence match for artist '{artist_name}' (best: {best_confidence})")
            self._save_to_cache('artist', artist_name, None, None, None, best_confidence)
            return None

        except Exception as e:
            logger.error(f"Error matching artist '{artist_name}': {e}")
            return None
    
    # Version qualifiers that distinguish releases (Deluxe, Remastered, etc.)
    _VERSION_QUALIFIERS = re.compile(
        r'\b(deluxe|expanded|remaster(?:ed)?|anniversary|special|collector|'
        r'limited|bonus|platinum|gold|super\s*deluxe|standard)\b',
        re.IGNORECASE
    )

    def _extract_version_qualifier(self, title: str) -> str:
        """Extract version qualifiers from an album title, normalized and sorted."""
        qualifiers = sorted(set(q.lower() for q in self._VERSION_QUALIFIERS.findall(title)))
        return ' '.join(qualifiers)

    def match_release(self, album_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Match a release (album) by name to MusicBrainz

        Returns:
            Dict with 'mbid', 'title', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('release', album_name, artist_name)
        if cached:
            logger.debug(f"Cache hit for release '{album_name}'")
            return {
                'mbid': cached['musicbrainz_id'],
                'title': album_name,
                'confidence': cached['confidence'],
                'cached': True
            }

        # Search MusicBrainz
        try:
            results = self.mb_client.search_release(album_name, artist_name, limit=5)

            if not results:
                logger.info(f"No MusicBrainz results for release '{album_name}'")
                self._save_to_cache('release', album_name, artist_name, None, None, 0)
                return None

            # Extract version qualifier from search query for preference matching
            query_qualifier = self._extract_version_qualifier(album_name)

            # Find best match
            best_match = None
            best_confidence = 0

            for result in results:
                mb_title = result.get('title', '')
                mb_score = result.get('score', 0)

                # Calculate title similarity
                title_similarity = self._calculate_similarity(album_name, mb_title)

                # Hard floor, mirroring match_recording's: without it the
                # bonuses (artist +20, version +10, mb_score up to +30) could
                # walk a ~0.4-title-similarity release past the 70 gate — and
                # that MBID feeds MBID-keyed cover art with no downstream
                # validation.
                if title_similarity < 0.6:
                    continue

                # If we have artist info, check artist match too
                artist_bonus = 0
                if artist_name and 'artist-credit' in result:
                    artist_credits = result['artist-credit']
                    for credit in artist_credits:
                        if isinstance(credit, dict) and 'artist' in credit:
                            mb_artist = credit['artist'].get('name', '')
                            artist_similarity = self._calculate_similarity(artist_name, mb_artist)
                            if artist_similarity > 0.7:
                                artist_bonus = 20
                                break

                # Version qualifier matching: prefer releases with the same
                # edition qualifier (Deluxe, Remastered, etc.) as the query.
                # This prevents "Playing the Angel (Deluxe)" from matching the
                # standard "Playing the Angel" release.
                version_bonus = 0
                if query_qualifier:
                    mb_qualifier = self._extract_version_qualifier(mb_title)
                    if query_qualifier == mb_qualifier:
                        version_bonus = 10  # Same edition — strong preference
                    elif mb_qualifier and mb_qualifier in query_qualifier:
                        version_bonus = 5   # Partial match (e.g. "deluxe" in "super deluxe")
                    elif not mb_qualifier:
                        version_bonus = -5  # Query has qualifier but result doesn't — penalize

                # Combine scores - cap at 100
                confidence = min(100, int((title_similarity * 50) + (mb_score / 100 * 30) + artist_bonus + version_bonus))

                # Numeric difference = different release. 'Vol.4' vs 'Vol.4.5'
                # scores 0.97 string similarity, so a near-identical wrong
                # volume could win and its MBID then feeds CAA art with NO
                # downstream validation (CAA is MBID-keyed — Sokhi's wrong
                # covers). Halving lands any such candidate below the 70 gate
                # while leaving the exact-volume result untouched.
                from core.text.title_match import numeric_tokens_differ
                if numeric_tokens_differ(album_name, mb_title):
                    confidence = int(confidence * 0.5)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = result
            
            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_title = best_match.get('title')
                
                # Save to cache
                self._save_to_cache('release', album_name, artist_name, mbid, best_match, best_confidence)
                
                logger.info(f"Matched release '{album_name}' → '{mb_title}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'title': mb_title,
                    'confidence': best_confidence,
                    'cached': False
                }
            else:
                logger.info(f"Low confidence match for release '{album_name}' (best: {best_confidence})")
                self._save_to_cache('release', album_name, artist_name, None, None, best_confidence)
                return None
                
        except Exception as e:
            logger.error(f"Error matching release '{album_name}': {e}")
            return None
    
    def match_recording(self, track_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Match a recording (track) by name to MusicBrainz
        
        Returns:
            Dict with 'mbid', 'title', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('recording', track_name, artist_name)
        if cached:
            logger.debug(f"Cache hit for recording '{track_name}'")
            return {
                'mbid': cached['musicbrainz_id'],
                'title': track_name,
                'confidence': cached['confidence'],
                'cached': True
            }
        
        # Search MusicBrainz
        try:
            results = self.mb_client.search_recording(track_name, artist_name, limit=5)
            
            if not results:
                logger.info(f"No MusicBrainz results for recording '{track_name}'")
                self._save_to_cache('recording', track_name, artist_name, None, None, 0)
                return None
            
            # Find best match
            best_match = None
            best_confidence = 0
            
            for result in results:
                mb_title = result.get('title', '')
                mb_score = result.get('score', 0)

                # Calculate title similarity
                title_similarity = self._calculate_similarity(track_name, mb_title)

                # Hard gate: title must be at least 60% similar.
                # Without this, artist bonus + MB score can push totally
                # different titles (e.g. "Sweet Surrender" → "Answers")
                # past the confidence threshold.
                if title_similarity < 0.6:
                    continue

                # If we have artist info, check artist match too
                artist_bonus = 0
                if artist_name and 'artist-credit' in result:
                    artist_credits = result['artist-credit']
                    for credit in artist_credits:
                        if isinstance(credit, dict) and 'artist' in credit:
                            mb_artist = credit['artist'].get('name', '')
                            artist_similarity = self._calculate_similarity(artist_name, mb_artist)
                            if artist_similarity > 0.7:
                                artist_bonus = 20
                                break

                # Combine scores - cap at 100
                confidence = min(100, int((title_similarity * 50) + (mb_score / 100 * 30) + artist_bonus))

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = result

            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_title = best_match.get('title')
                
                # Save to cache
                self._save_to_cache('recording', track_name, artist_name, mbid, best_match, best_confidence)
                
                logger.info(f"Matched recording '{track_name}' → '{mb_title}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'title': mb_title,
                    'confidence': best_confidence,
                    'cached': False
                }
            else:
                logger.info(f"Low confidence match for recording '{track_name}' (best: {best_confidence})")
                self._save_to_cache('recording', track_name, artist_name, None, None, best_confidence)
                return None
                
        except Exception as e:
            logger.error(f"Error matching recording '{track_name}': {e}")
            return None
    
    def lookup_artist_aliases(self, artist_name: str) -> list:
        """Find alternate-spelling aliases for an artist by NAME.

        Multi-tier resolution:
        1. Library DB row (`artists.aliases` populated by the MB
           worker when the artist was enriched). Fast path — no
           network.
        2. Existing musicbrainz_cache entry (entity_type='artist_aliases')
           — caches a prior live MB lookup for this name.
        3. Live MB lookup: search artist → fetch aliases for the best
           MBID → cache the result.

        Always returns a list (possibly empty) — never raises. Empty
        result on any tier means "no alternate spellings found, fall
        back to direct match" which is identical to pre-fix behaviour.

        Used by the AcoustID verifier when an artist comparison fails
        the direct similarity check. Caching means each unique artist
        name only hits MB once per cache TTL even if 100 download
        candidates fail verification with that artist.
        """
        if not artist_name:
            return []

        # Tier 1: library DB
        library = self.get_artist_aliases(artist_name)
        if library:
            return library

        # Tier 1b: the artist's own MusicBrainz identity, when the catalogue
        # already knows it. Everything below this line GUESSES from the name,
        # and for a cross-script artist that guess is exactly what fails: the
        # trust gate's own comment names `Sawano Hiroyuki` as the case where a
        # decoy entity outscores the real `澤野弘之`, and MusicBrainz's
        # relevance scores are not stable enough for the escape hatch to be
        # relied on. That is why the bridge could work one day and not the
        # next. There is nothing to guess once the row carries the MBID.
        cached = self._check_cache('artist_aliases', artist_name)
        row_mbid = self._artist_row_mbid(artist_name)
        if row_mbid:
            # A cache row that was resolved against THIS identity says exactly
            # what a fresh fetch would say, and every fetch spends a second of
            # the process-wide MusicBrainz budget — one per scanned file for an
            # artist MusicBrainz lists no alias for, all of it contending with
            # the enrichment worker on the same lock.
            known = self._cached_aliases(cached, for_mbid=row_mbid)
            if known is not None:
                return known
            aliases = self.resolve_artist_aliases(row_mbid)
            if aliases is not None:
                self._save_to_cache(
                    'artist_aliases', artist_name, None, row_mbid,
                    {'aliases': aliases, 'resolved': True}, 100,
                )
                if aliases:
                    try:
                        self._persist_artist_identity(artist_name, row_mbid, aliases)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("alias write-back for %r failed: %s",
                                     artist_name, e)
                return aliases
            # The fetch did not come back. That settles nothing, so carry on
            # rather than reporting "no aliases" off the back of it.

        # Tier 2: cached live lookup (re-uses musicbrainz_cache table)
        answered = self._cached_aliases(cached)
        if answered is not None:
            return answered

        # Tier 3: live MB lookup. Search → fetch by MBID → cache.
        # Issue #586 — strict search queries `artist:"..."` only and
        # MISSES alias / sortname indexes. When MB's canonical name is
        # the non-Latin form (e.g. `Дмитрий Яблонский`), the user's
        # Latin input ("Dmitry Yablonsky") finds nothing under strict.
        # Fall back to non-strict (bare query, hits alias + sortname
        # indexes) when strict returns empty OR all results fail the
        # trust gate.
        # `None` from a search means MusicBrainz never answered (timeout, rate
        # limit, 503). That is not the same as "no such artist", and caching it
        # as an empty alias list is how a single bulk-scan rate-limit could
        # silence the romaji↔kanji bridge for a month.
        strict_hits = self._search_and_score_artists(artist_name, strict=True)
        lookup_failed = strict_hits is None
        scored = strict_hits or []
        if not scored or self._best_score(scored) < 0.85:
            non_strict = self._search_and_score_artists(artist_name, strict=False)
            if non_strict is None:
                lookup_failed = True
            elif non_strict and (not scored
                                 or self._best_score(non_strict) > self._best_score(scored)):
                scored = non_strict
                lookup_failed = False

        def _remember_no_aliases():
            """Write "no alternate spellings" down — unless a search failed.

            Every gate below can reject the candidates it was given and land
            here, and a rejection is only a verdict if the SEARCH was complete.
            When the strict query returned weak candidates and the non-strict
            one timed out, `scored` is non-empty (so the guard above does not
            fire) while the entity that would have passed may only have existed
            in the query that never answered. Caching then blocks the retry that
            would have found it.
            """
            if lookup_failed:
                logger.debug(
                    "lookup_artist_aliases: a search for %r did not complete — "
                    "not recording an empty result", artist_name,
                )
                return
            self._save_to_cache('artist_aliases', artist_name, None, None, _NO_ALIASES, 0)

        if not scored:
            _remember_no_aliases()
            return []

        scored.sort(key=lambda x: -x[0])
        best_score, best_mbid, best_mb_score = scored[0]

        # The genuine cross-script match (romaji↔kanji, latin↔cyrillic)
        # has near-zero LOCAL similarity, so its COMBINED score sinks
        # below an unrelated same-script decoy — even though MB itself is
        # certain. "Sawano Hiroyuki": a decoy entity led on combined
        # (sim 0.82, mb_score 83, combined 0.82 — just under the 0.85 bar)
        # while the real artist '澤野弘之' had mb_score 100 but combined
        # 0.30, sorted last. So evaluate the MB-SCORE leader independently
        # of the combined ranking for the mb-only escape, not scored[0].
        mb_leader = max(scored, key=lambda x: x[2])  # (combined, mbid, raw_mb)
        mb_scores_desc = sorted((x[2] for x in scored), reverse=True)
        mb_unambiguous = len(mb_scores_desc) < 2 or (mb_scores_desc[0] - mb_scores_desc[1]) >= 5

        # Trust gate. Two ways to pass:
        #   1. Combined score >= 0.85 (the historical strict bar that
        #      catches same-script matches) → trust the combined leader.
        #   2. MB's OWN score is very high (>= 95) AND that MB-score leader
        #      is unambiguous → trust IT. Bridges the cross-script case
        #      where local similarity is near zero ("Dmitry Yablonsky" vs
        #      "Дмитрий Яблонский" sim ~0) but MB's index is confident.
        passes_combined = best_score >= 0.85
        passes_mb_only = mb_leader[2] >= 95 and mb_unambiguous
        if not (passes_combined or passes_mb_only):
            logger.debug(
                "lookup_artist_aliases: best match for %r below trust "
                "threshold (combined=%.2f, best_mb=%d, leader_mb=%d)",
                artist_name, best_score, best_mb_score, mb_leader[2],
            )
            _remember_no_aliases()
            return []

        # Pick the entity to pull aliases from. Combined-strong matches use
        # the combined leader; the mb-only escape uses the MB-score leader
        # (which may differ from scored[0] in the cross-script case above).
        if passes_combined:
            chosen_mbid, chosen_conf = best_mbid, best_score
        else:
            chosen_mbid, chosen_conf = mb_leader[1], mb_leader[2] / 100.0

        # Ambiguity detection: when 2+ results both score high (within
        # 0.1 of the best combined), the search hit multiple distinct
        # artists with similar names. Pulling aliases for one could
        # produce wrong matches. Skip + cache empty. The unambiguous
        # MB-score leader (passes_mb_only) is exempt — its decisiveness
        # was already checked via mb_unambiguous.
        if len(scored) >= 2 and (scored[0][0] - scored[1][0]) < 0.1 and not passes_mb_only:
            logger.debug(
                "lookup_artist_aliases: ambiguous match for %r — top "
                "two results within 0.1 (%.2f / %.2f). Skipping alias lookup.",
                artist_name, scored[0][0], scored[1][0],
            )
            _remember_no_aliases()
            return []

        aliases = self.resolve_artist_aliases(chosen_mbid)
        if aliases is None:
            # The identity resolved, the alias fetch did not come back. Writing
            # that down as "no aliases" is precisely what froze this lookup for
            # a 90-day TTL and took the romaji-kanji bridge with it; leave the
            # question open instead.
            logger.debug(
                "lookup_artist_aliases: alias fetch for %r (%s) did not "
                "complete — not recording a result", artist_name, chosen_mbid,
            )
            return []
        self._save_to_cache(
            'artist_aliases', artist_name, None, chosen_mbid,
            {'aliases': aliases, 'resolved': True}, int(chosen_conf * 100),
        )
        # Keep what we just learned on the ARTIST, not only in a cache keyed by
        # the spelling this caller happened to pass. Without this the knowledge
        # that let a download pass expires with the cache row and is invisible
        # to any later lookup that spells the name differently — which is how a
        # library scan came to disagree with the download about the same file.
        # Best-effort by design: the caller asked for aliases and has them.
        try:
            self._persist_artist_identity(artist_name, chosen_mbid, aliases)
        except Exception as e:  # noqa: BLE001
            logger.debug("alias write-back for %r failed: %s", artist_name, e)
        return aliases

    def _artist_row_mbid(self, artist_name: str) -> Optional[str]:
        """The MusicBrainz id the catalogue already holds for this artist name.

        Best-effort: any failure (no catalogue, no row, no id) returns None and
        the caller falls back to resolving by name, which is what it did before
        this existed.
        """
        try:
            conn = self.db._get_connection()
        except Exception:  # noqa: BLE001
            return None
        try:
            # DISTINCT, not LIMIT 1. A catalogue can hold several rows under
            # one display name — the same artist from two providers, or two
            # genuinely different artists who share it. Taking whichever row
            # sorted first would make an arbitrary pick authoritative for every
            # verification that ever compares against this name, and its aliases
            # could then let a wrong artist pass. Same-name rows agreeing on the
            # id is the normal case and stays free; disagreement means the name
            # does not identify anybody, so fall back to resolving it.
            rows = conn.execute(
                "SELECT DISTINCT musicbrainz_id FROM lib2_artists "
                "WHERE name = ? COLLATE NOCASE "
                "AND COALESCE(musicbrainz_id,'') <> ''",
                (artist_name,),
            ).fetchall()
            mbids = {str(r[0]) for r in rows if r and r[0]}
            if len(mbids) > 1:
                logger.debug(
                    "artist mbid lookup for %r is ambiguous — %d rows with that "
                    "name hold different MusicBrainz ids", artist_name, len(mbids))
                return None
            return next(iter(mbids)) if mbids else None
        except Exception as e:  # noqa: BLE001
            logger.debug("artist mbid lookup failed for %r: %s", artist_name, e)
            return None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110 — best effort
                pass

    def _persist_artist_identity(self, artist_name: str, mbid: Optional[str],
                                 aliases: Optional[list]) -> None:
        """Store a live-resolved MBID + alias list on the catalogue artist row.

        Matched by name because that is all the verifier ever has — it compares
        against a metadata-source string, not a library id. A differently-named
        artist already holding this MBID means the name search landed on the
        wrong entity, so the guard the enrichment worker uses applies here too:
        better no id than one smeared across two artists.

        A name can also address several rows — the same artist reached through
        two providers is the ordinary case, which is why ``provider_id_conflict``
        treats a same-named holder as no conflict. So every row under the name
        is read, and the write only happens when they AGREE about the identity:
        one of them already naming a different MBID means this name does not
        identify anybody, and picking a row would make the choice arbitrary.
        """
        if not artist_name or not mbid:
            return
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import provider_id_conflict

            conn = self.db._get_connection()
            rows = conn.execute(
                "SELECT id, musicbrainz_id FROM lib2_artists "
                "WHERE name = ? COLLATE NOCASE",
                (artist_name,),
            ).fetchall()
            if not rows:
                return
            targets = [int(r[0]) for r in rows]
            stored = {str(r[1]) for r in rows if r[1]}
            # The aliases were fetched FROM this MBID, so they are only this
            # artist's aliases if this MBID is. Writing them without that is
            # how one artist's alternate spellings end up on another's row.
            if stored - {str(mbid)}:
                logger.debug(
                    "alias write-back skipped for %r: rows under that name hold "
                    "MBID(s) %s, the name search resolved %s",
                    artist_name, sorted(stored), mbid)
                return
            if not stored:
                conflict = provider_id_conflict(
                    conn, 'musicbrainz', mbid, targets[0], artist_name)
                if conflict:
                    logger.debug(
                        "alias write-back skipped for %r: MBID %s already held "
                        "by %r", artist_name, mbid, conflict)
                    return
                for artist_id in targets:
                    write_provider_enrichment(
                        conn, entity_type='artist', entity_id=artist_id,
                        service='musicbrainz', provider_id=mbid)
                    record_attempt(conn, entity_type='artist', entity_id=artist_id,
                                   service='musicbrainz', status='matched')
            if aliases:
                for artist_id in targets:
                    write_provider_enrichment(
                        conn, entity_type='artist', entity_id=artist_id,
                        service='musicbrainz',
                        columns={'aliases': json.dumps(aliases)})
            conn.commit()
            logger.info(
                "Stored MusicBrainz identity for artist %r on %d row(s) "
                "(mbid=%s, %d aliases)",
                artist_name, len(targets), mbid, len(aliases or []),
            )
        finally:
            if conn:
                conn.close()

    def _search_and_score_artists(self, artist_name: str, strict: bool):
        """Search MB for an artist and score each result.

        Returns a list of (combined_score, mbid, raw_mb_score) tuples.
        Combined score: 70% local similarity + 30% MB's own relevance
        score (0..1). raw_mb_score preserved separately so the trust
        gate can prefer high-MB-score results in cross-script cases
        where local similarity is near zero.

        Returns ``None`` when the search itself did not complete (timeout,
        rate limit, transport error) and a list otherwise — including the
        empty list, which is MusicBrainz genuinely answering "nobody by that
        name". The caller has to tell those apart: only the second is a
        verdict worth caching.
        """
        try:
            # `raise_on_error` matters more than it looks: without it the client
            # catches a timeout / 429 / 503 and hands back `[]`, which is the
            # exact value it uses for "MusicBrainz knows nobody by that name".
            # Every distinction below would then be decided on a value that
            # cannot carry it, and the outage would be cached as a verdict.
            results = self.mb_client.search_artist(
                artist_name, limit=3, strict=strict, raise_on_error=True)
        except Exception as e:
            logger.debug(
                "lookup_artist_aliases: search_artist(%r, strict=%s) raised: %s",
                artist_name, strict, e,
            )
            return None
        scored = []
        for result in results or []:
            mb_name = result.get('name', '')
            mb_score = result.get('score', 0)
            sim = self._calculate_similarity(artist_name, mb_name)
            combined = (sim * 0.7) + (mb_score / 100 * 0.3)
            mbid = result.get('id')
            if mbid:
                scored.append((combined, mbid, mb_score))
        return scored

    @staticmethod
    def _best_score(scored):
        return max((s[0] for s in scored), default=0.0) if scored else 0.0

    def fetch_artist_aliases(self, mbid: str) -> list:
        """Alias list for an artist, with a failed fetch reported as empty.

        Kept for callers that genuinely cannot act on the difference (the
        enrichment worker, which only ever stores a non-empty list). Anything
        that CACHES the answer must use :meth:`resolve_artist_aliases` — see the
        note there.
        """
        return self.resolve_artist_aliases(mbid) or []

    def resolve_artist_aliases(self, mbid: str) -> Optional[list]:
        """Fetch the alias list for an artist from MusicBrainz.

        Issue #442 — Japanese kanji / Cyrillic / etc. spellings of an
        artist's name are stored as `aliases` on the MusicBrainz
        artist record. Pull them so SoulSync can recognise that
        `澤野弘之` and `Hiroyuki Sawano` refer to the same artist.

        Issue #586 — for some artists MB's CANONICAL `name` is the
        non-Latin spelling (e.g. `Дмитрий Яблонский`) while the
        Latin spelling lives in `aliases` — but the inverse also
        happens, where the Latin canonical name has the Cyrillic in
        aliases. Either way the canonical `name` and `sort-name` are
        themselves valid alternate spellings for matching purposes,
        so include them alongside the explicit alias entries.

        Returns the deduplicated list of alias `name` strings, or **None**
        when MusicBrainz never answered. That distinction is the whole point:
        an empty list means "MusicBrainz lists no alternate spelling", None
        means "we do not know", and only the first may ever be written down as
        a result. Collapsing the two is what let one timeout freeze a working
        cross-script bridge for 90 days.
        """
        if not mbid:
            return None
        try:
            data = self.mb_client.get_artist(
                mbid, includes=['aliases'], raise_on_error=True)
        except Exception as e:
            logger.debug("resolve_artist_aliases: get_artist(%s) raised: %s", mbid, e)
            return None
        if not data:
            return None

        seen = set()
        cleaned = []

        def _add(value):
            if not isinstance(value, str):
                return
            text = value.strip()
            if not text:
                return
            key = text.lower()
            if key in seen:
                return
            seen.add(key)
            cleaned.append(text)

        # Canonical name + sort-name treated as aliases for matching —
        # they're the strongest cross-script bridge when MB's
        # canonical spelling differs from the user's input.
        _add(data.get('name'))
        _add(data.get('sort-name'))

        # MB returns each alias as a dict with `name`, `sort-name`,
        # `locale`, `primary`, `type`, etc. We only care about the
        # display name — that's what `actual` artist strings will
        # match against. Also pull alias sort-name when present
        # (some entries have a different sortable form).
        for entry in data.get('aliases') or []:
            if not isinstance(entry, dict):
                continue
            _add(entry.get('name'))
            _add(entry.get('sort-name'))
        return cleaned

    def update_artist_aliases(self, artist_id: int, aliases: list) -> None:
        """Persist the alias list to ``lib2_artists.aliases`` as a JSON array.

        Idempotent — overwrites any existing value. An empty list clears the column
        (the caller may want this if MB no longer lists aliases for the artist), so
        this is an outright write rather than a backfill.
        """
        if artist_id is None:
            return
        conn = None
        try:
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id,
                service='musicbrainz',
                columns={'aliases': json.dumps(aliases) if aliases else '[]'},
            )
            conn.commit()
            logger.debug("Updated artist %s aliases (%d entries)",
                         artist_id, len(aliases or []))
        except Exception as e:
            logger.error(f"Error updating artist aliases for {artist_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def get_artist_aliases(self, artist_name: str) -> list:
        """Look up cached aliases for an artist by NAME (not id).

        Used by the verifier where the expected artist comes from a
        download's metadata-source data — we don't have a library
        row's `id` to query, just the display name. Returns empty
        list when the artist isn't in the library or has no aliases
        recorded. The verifier falls back to live MB lookup in that
        case.
        """
        if not artist_name:
            return []
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            # Reads lib2, because that is where update_artist_aliases writes now
            # (docs §32.3.1 stage 2). A reader left on legacy would not see the
            # aliases the worker just stored.
            cursor.execute(
                "SELECT aliases FROM lib2_artists WHERE name = ? COLLATE NOCASE "
                "LIMIT 1",
                (artist_name,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return []
            try:
                parsed = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                return []
            if not isinstance(parsed, list):
                return []
            return [str(x).strip() for x in parsed if x]
        except Exception as e:
            logger.debug("get_artist_aliases lookup failed for %r: %s", artist_name, e)
            return []
        finally:
            if conn:
                conn.close()

    def _record_mbid(self, entity_type: str, entity_id, mbid: Optional[str],
                     status: str):
        """Store an MBID and the attempt outcome on a Library-v2 row.

        One method for all three entity types: legacy needed three because the
        column name differed per table (musicbrainz_id / musicbrainz_release_id /
        musicbrainz_recording_id), while lib2 keeps the mbid in one promoted
        ``musicbrainz_id`` column plus ``external_ids`` on every entity.

        A miss records the attempt and leaves any stored id alone. Legacy nulled it
        out, which was a no-op on every path that can reach here — a stored id
        short-circuits into the preserve-manual-match branch long before — and
        keeping it means a transient failure can never erase a good id.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            if mbid:
                write_provider_enrichment(
                    conn, entity_type=entity_type, entity_id=entity_id,
                    service='musicbrainz', provider_id=mbid)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='musicbrainz', status=status)
            conn.commit()

            logger.debug(f"Updated {entity_type} {entity_id} with MBID: {mbid}, "
                         f"status: {status}")

        except Exception as e:
            logger.error(f"Error updating {entity_type} {entity_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def update_artist_mbid(self, artist_id: int, mbid: Optional[str], status: str):
        """Update artist with MusicBrainz ID"""
        self._record_mbid('artist', artist_id, mbid, status)

    def update_album_mbid(self, album_id: int, mbid: Optional[str], status: str):
        """Update album with MusicBrainz release ID"""
        self._record_mbid('album', album_id, mbid, status)

    def update_track_mbid(self, track_id: int, mbid: Optional[str], status: str):
        """Update track with MusicBrainz recording ID"""
        self._record_mbid('track', track_id, mbid, status)



# ── Shared instance ─────────────────────────────────────────────────────────
# The service is stateless apart from its HTTP session and the rate limiter,
# and the rate limiter is what makes sharing matter: MusicBrainz allows one
# request per second per client, so every extra instance is another way to
# exceed it. Callers that only need a lookup should use this rather than
# constructing their own (core.acoustid_verification and
# core.exports.export_sources each grew a private singleton before this
# existed).
_shared_service = None
_shared_service_lock = threading.Lock()


def get_musicbrainz_service():
    """The process-wide MusicBrainzService, created on first use."""
    global _shared_service
    if _shared_service is None:
        with _shared_service_lock:
            if _shared_service is None:
                from database.music_database import get_database
                _shared_service = MusicBrainzService(get_database())
    return _shared_service
