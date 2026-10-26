"""
SoulID Worker — generates deterministic soul IDs for artists, albums, and tracks.

Runs as a background worker that processes library entries without soul IDs,
computes a deterministic hash from normalized metadata, and stores the result.
Everything is computed locally from the library's own strings — no API calls —
so every install holding the same metadata derives the same ID.

Algorithm "kinded_v2" (SOULID_VERSION). The hash input is positional and
fixed per entity kind, joined with a unit separator and prefixed with the
algorithm version:

    kinded_v2 <US> kind <US> part1 [<US> part2 [<US> part3]]

  - artist:      (artist_name)
  - album:       (artist_name, album_title)
  - track:       (artist_name, track_title)              — the "song" ID
  - album_track: (artist_name, album_title, track_title) — release-specific

The song ID links different versions of the same song (single vs album).
The album-track ID is specific to a track on a particular release.

The kind is part of the input on purpose: before kinded_v2 an album and a
song with the same artist + title hashed to the SAME id, and the parts were
concatenated with no separator, so ("AB", "C") and ("A", "BC") collided.

Artists are minted from the name alone. The pre-v2 artist derivations are
gone: the provider-canonical one hashed a Deezer/iTunes artist id found by
a fuzzy, track-verified search (two installs routinely derived different
ids for the same artist, and the fallback tiers depended on what the local
library happened to own). A soul_id is a shared NAME key, not proof of
identity — two different artists who share a name share an artist soul_id,
exactly as their albums already collided through the artist-name part.
Consumers that need true identity use the provider IDs stored alongside.

Rule for empty parts: if any part of a kind normalizes to '', no real ID
is minted — the row gets the soul_unnamed_<pk> placeholder (or, for a
track's album_soul_id when the track has no album, NULL). An ID is never
hashed from a partial input.

One-time cutover: at worker start, version-flagged migrations copy every
pre-v2 id into the *_legacy columns (so peers can bridge old -> new) and
NULL the live columns; the worker then re-mints everything under kinded_v2.
"""

import hashlib
import re
import threading
import time
import unicodedata
from typing import Dict, Any, List

from utils.logging_config import get_logger
from core.worker_utils import interruptible_sleep

logger = get_logger("soulid_worker")

# The soul_id algorithm version. Stamped into the hash input, recorded in
# the metadata table by the cutover migrations, and served by the MetaSync
# export so a peer knows which scheme an install mints.
SOULID_VERSION = "kinded_v2"

_SEP = "\x1f"

# Fixed positional parts per entity kind. The arity is part of the scheme:
# a kind's input always has exactly this many parts.
_KIND_ARITY = {"artist": 1, "album": 2, "track": 2, "album_track": 3}


# Scripts whose combining marks are optional decoration: they come off,
# so Beyoncé and Beyonce — and שלום with and without niqqud, محمد with
# and without harakat — are the same artist. In most other scripts the
# "marks" ARE part of the letter — a kana dakuten (が vs か), a
# Devanagari vowel sign (हिन्दी mangled to हनद without it), a Thai tone
# mark — and stripping them collides different words onto one id.
_MARK_STRIPPED_SCRIPTS = (
    (0x0041, 0x024F),  # Latin, incl. Extended-A/B
    (0x1E00, 0x1EFF),  # Latin Extended Additional (Vietnamese)
    (0x0370, 0x03FF),  # Greek
    (0x1F00, 0x1FFF),  # Greek Extended
    (0x0400, 0x052F),  # Cyrillic + Supplement
    (0x0590, 0x05FF),  # Hebrew (niqqud are optional vowel points)
    (0x0600, 0x06FF),  # Arabic (harakat are optional vowel marks)
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
)


def _marks_stripped_after(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _MARK_STRIPPED_SCRIPTS)


def _filter_for_hash(s: str) -> str:
    out = []
    base = ''
    for ch in s:
        cat = unicodedata.category(ch)
        if cat == 'Cf':
            # Invisible format chars (RLM/LRM, soft hyphen, ZWJ/ZWNJ,
            # BOM) come out unconditionally: sources disagree on them,
            # so keeping them splits one name into two ids.
            continue
        if cat in ('Mn', 'Mc', 'Me'):
            # A combining mark lives or dies with its base letter.
            if base and not _marks_stripped_after(base):
                out.append(ch)
            continue
        if cat[0] in ('L', 'N'):
            out.append(ch)
            base = ch
        else:
            base = ''
    return unicodedata.normalize('NFC', ''.join(out))


def normalize_for_soul_id(text: str) -> str:
    """Normalize a string for deterministic hashing, in any script.

    - Compatibility-decompose and casefold (fullwidth forms fold to ASCII,
      ß -> ss, ﬁ -> fi)
    - Remove parentheticals: (feat. X), (Deluxe), (Remastered), [Live] —
      unless that empties the string, in which case they stay ("(Intro)"
      is a title, not a suffix)
    - Keep letters and digits in any script. Combining marks come off
      after Latin/Greek/Cyrillic bases (accents) and Hebrew/Arabic bases
      (niqqud/harakat are optional vowel marks); after any other base
      they are kept and the result is recomposed, because there the mark
      is part of the letter (kana dakuten, Indic vowel signs, Thai
      tones). A deliberate side effect of the Arabic strip: hamza folds
      onto its carrier (أحمد == احمد), the standard Arabic search
      normalization — sources disagree on hamza spelling more than on
      anything else. Invisible format characters always come out.
      Punctuation, symbols and whitespace are dropped.
    - If nothing letter-like survives (the band "!!!", the album "( )"),
      fall back to the raw text — compatibility-folded, casefolded,
      whitespace removed — so symbol-only names still mint ONE
      deterministic id on every install instead of a placeholder that
      the export then hides forever.

    The pre-v2 normalizer deleted every character outside [a-z0-9], which
    hashed non-Latin names to '' or fragments (and made every Cyrillic
    album by one artist share a single id).

    This function is a frozen network contract: kinded_v2 ids on other
    installs are computed with exactly this code, so any change here is
    a new SOULID_VERSION and a new cutover, never a quiet edit.
    """
    if not text:
        return ''
    raw = str(text)
    s = unicodedata.normalize('NFKD', raw).casefold()
    # Remove parenthetical/bracket suffixes (NFKD folded fullwidth brackets
    # to ASCII, so one pattern covers both)
    no_parens = re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', s)
    candidate = no_parens if no_parens.strip() else s
    # Format chars come out of the candidate itself, so the symbol
    # fallback below can't resurrect them ("!!!" + RLM is still "!!!").
    candidate = ''.join(
        c for c in candidate if unicodedata.category(c) != 'Cf')
    result = _filter_for_hash(candidate)
    if result:
        return result
    # Fall back to the same candidate the filter saw (parens already
    # stripped when there was anything outside them), whitespace removed.
    return ''.join(candidate.split())


def generate_soul_id(kind: str, *parts: str) -> str:
    """Generate a kinded_v2 soul ID for one entity.

    `kind` is one of _KIND_ARITY and `parts` must match its fixed arity.
    Returns '' when any part normalizes to '' — the caller decides between
    the soul_unnamed_<pk> placeholder and leaving the column NULL, so an ID
    is never minted from a partial input.
    """
    if kind not in _KIND_ARITY:
        raise ValueError(f"unknown soul_id kind: {kind!r}")
    if len(parts) != _KIND_ARITY[kind]:
        raise ValueError(
            f"soul_id kind {kind!r} takes {_KIND_ARITY[kind]} part(s), "
            f"got {len(parts)}")
    normalized = [normalize_for_soul_id(p) for p in parts]
    if any(not p for p in normalized):
        return ''
    combined = _SEP.join([SOULID_VERSION, kind, *normalized])
    digest = hashlib.sha256(combined.encode('utf-8')).hexdigest()[:16]
    return f'soul_{digest}'


class SoulIDWorker:
    """Background worker that generates soul IDs for all library entities.

    All three entity kinds are processed in batches from the local DB —
    kinded_v2 needs no API calls for any of them.
    """

    def __init__(self, database):
        self.db = database

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self.current_item = None
        self._stop_event = threading.Event()

        # Statistics
        self.stats = {
            'artists_processed': 0,
            'albums_processed': 0,
            'tracks_processed': 0,
            'errors': 0,
            'pending': 0,
        }

        # Processing config
        self.batch_size = 100         # Entities per batch (local DB only)
        # Rows per committed chunk of the one-time cutover. One statement
        # over the whole table held the write lock for 7 minutes on a
        # 311k-track library (measured, WSL): every scan, download and
        # import write in the app waited or failed meanwhile.
        self.cutover_chunk = 1000
        self.inter_batch_sleep = 0.5  # Between batches
        self.idle_sleep = 30

        logger.info("SoulID worker initialized")

    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("SoulID worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("SoulID worker stopped")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending()
        is_running = self.running and self.thread is not None and self.thread.is_alive()
        is_idle = is_running and not self.paused and self.stats['pending'] == 0
        return {
            'enabled': True,
            'running': is_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
        }

    # ── Main loop ──

    def _run(self):
        logger.info("SoulID worker thread started")

        # One-time cutover: park pre-v2 ids in the *_legacy columns and
        # reset the live columns so everything re-mints under kinded_v2.
        # A failed table retries on the next start (its flag is only
        # stamped on success) — say so loudly, because until it lands
        # that table keeps pre-v2 ids while the rest has moved on.
        self._cut_over = {
            "artists": self._migrate_artist_soul_ids(),
            "albums": self._migrate_album_soul_ids(),
            "tracks": self._migrate_track_soul_ids(),
        }
        failed = [name for name, ok in self._cut_over.items() if not ok]
        if failed:
            self.stats['errors'] += len(failed)
            logger.error(
                f"SoulID {SOULID_VERSION} cutover incomplete for: "
                f"{', '.join(failed)} — those tables keep pre-v2 ids "
                f"until the cutover succeeds on a later start (no new ids "
                f"are minted there meanwhile)")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                processed = self._mint_round()

                if processed == 0:
                    self.current_item = None
                    interruptible_sleep(self._stop_event, self.idle_sleep)
                else:
                    interruptible_sleep(self._stop_event, self.inter_batch_sleep)

            except Exception as e:
                logger.error(f"Error in SoulID worker loop: {e}", exc_info=True)
                self.stats['errors'] += 1
                interruptible_sleep(self._stop_event, 5)

        self.current_item = None
        logger.info("SoulID worker thread finished")

    def _mint_round(self) -> int:
        """One batch per table. A table whose cutover didn't land mints
        nothing: a v2 id minted now would be parked as "legacy" by the
        retry on the next start."""
        cut = getattr(self, "_cut_over", {})
        processed = 0
        if cut.get("artists"):
            processed += self._process_artists()
        if cut.get("albums"):
            processed += self._process_albums()
        if cut.get("tracks"):
            processed += self._process_tracks()
        return processed

    # ── Artist processing (batch, local DB only) ──

    def _process_artists(self) -> int:
        """Mint artist soul IDs from the artist name alone.

        kinded_v2 derives the artist ID from nothing but the normalized
        name — no provider lookups, no library-dependent fallbacks — so
        every install derives the same ID for the same name string. The
        derivation is recorded in soul_id_path as 'name'; pre-v2 values
        ('canonical', 'album') can only exist on rows that predate the
        cutover, and the cutover resets those rows.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE (soul_id IS NULL OR soul_id = '')
                  AND name IS NOT NULL AND name != ''
                  AND id IS NOT NULL
                LIMIT ?
            """, (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for artist_id, name in rows:
                if self.should_stop:
                    break
                soul_id = generate_soul_id("artist", name)
                if soul_id:
                    soul_id_path = 'name'
                else:
                    # A placeholder is not a derivation — path stays NULL
                    # so nothing downstream reads it as reproducible.
                    soul_id = f'soul_unnamed_{artist_id}'
                    soul_id_path = None
                cursor.execute(
                    "UPDATE artists SET soul_id = ?, soul_id_path = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                    (soul_id, soul_id_path, artist_id)
                )
                count += 1
                self.current_item = f"Artist: {name}"

            if count > 0:
                conn.commit()
                self.stats['artists_processed'] += count
                logger.info(f"Generated soul IDs for {count} artists")
            return count
        except Exception as e:
            logger.error(f"Error processing artists: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return 0
        finally:
            if conn:
                conn.close()

    def _process_albums(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT al.id, al.title, ar.name as artist_name
                FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.soul_id IS NULL OR al.soul_id = '')
                  AND al.title IS NOT NULL AND al.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND al.id IS NOT NULL
                LIMIT ?
            """, (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for album_id, title, artist_name in rows:
                if self.should_stop:
                    break
                soul_id = generate_soul_id("album", artist_name, title)
                if not soul_id:
                    soul_id = f'soul_unnamed_{album_id}'
                cursor.execute(
                    "UPDATE albums SET soul_id = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                    (soul_id, album_id)
                )
                count += 1
                self.current_item = f"Album: {artist_name} - {title}"

            if count > 0:
                conn.commit()
                self.stats['albums_processed'] += count
                logger.info(f"Generated soul IDs for {count} albums")
            return count
        except Exception as e:
            logger.error(f"Error processing albums: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return 0
        finally:
            if conn:
                conn.close()

    # ── Track processing (batch, local DB only) ──

    def _process_tracks(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name as artist_name, al.title as album_title
                FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE (t.soul_id IS NULL OR t.soul_id = '')
                  AND t.title IS NOT NULL AND t.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND t.id IS NOT NULL
                LIMIT ?
            """, (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for track_id, title, artist_name, album_title in rows:
                if self.should_stop:
                    break

                # Song soul ID: artist + track (links singles to album versions)
                song_soul_id = generate_soul_id("track", artist_name, title)

                # Album track soul ID: artist + album + track (this release)
                album_soul_id = ''
                if album_title:
                    album_soul_id = generate_soul_id(
                        "album_track", artist_name, album_title, title)

                if not song_soul_id:
                    song_soul_id = f'soul_unnamed_{track_id}'
                # updated_at moves with every soul_id write (L2-011): a row
                # with no soul_id is filtered OUT of the MetaSync export
                # entirely, so minting one is the moment that row starts
                # existing for consumers. Without the touch, a full walk
                # that ran before this worker meant the row never appeared
                # in any later incremental either.
                cursor.execute(
                    "UPDATE tracks SET soul_id = ?, album_soul_id = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                    (song_soul_id, album_soul_id or None, track_id)
                )
                count += 1
                self.current_item = f"Track: {artist_name} - {title}"

            if count > 0:
                conn.commit()
                self.stats['tracks_processed'] += count
                logger.info(f"Generated soul IDs for {count} tracks")
            return count
        except Exception as e:
            logger.error(f"Error processing tracks: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return 0
        finally:
            if conn:
                conn.close()

    # ── Cutover migrations (kinded_v2) ──
    #
    # One per entity kind, each gated by its own metadata flag and run
    # once at worker start. Each parks the pre-v2 ids in the *_legacy
    # columns — only where the legacy column is still empty, so a re-run
    # after a partial failure can never overwrite parked originals — and
    # NULLs the live columns so the worker re-mints under kinded_v2.
    # The old "prove each artist id's derivation" path migration is gone:
    # kinded_v2 has exactly one derivation per kind, so there is nothing
    # left to prove.

    def _cutover_done(self, cursor, key: str) -> bool:
        cursor.execute(
            "SELECT value FROM metadata WHERE key = ? LIMIT 1", (key,))
        row = cursor.fetchone()
        return bool(row and row[0] == SOULID_VERSION)

    def _park_and_reset(self, conn, table: str,
                        pairs: List[tuple]) -> int:
        """Copy live ids into their legacy columns, then NULL the live
        ones. `pairs` is [(live_column, legacy_column), ...]. Returns the
        number of rows reset.

        Chunked by rowid and committed per chunk, so the write lock is
        held for one chunk at a time and the rest of the app gets in
        between. Each chunk parks then resets the same rows, so an
        interrupted run leaves every reset row parked, and the rerun
        (the flag is only stamped once the whole table is done) picks up
        where it stopped. Parking never overwrites a filled legacy
        column, so re-running a chunk is harmless.

        Fail-closed: if any legacy column is missing, raise WITHOUT
        touching the live ids. The reset is destructive and the parked
        copy is the only one — a partial schema (the columns are added by
        a best-effort init block) must retry on a later start, never
        "cut over" by deletion.

        Placeholders (soul_unnamed_<pk>) are reset but NOT parked: the pk
        is install-local, so a parked placeholder would poison a peer's
        old->new bridge with an id that names a different row there."""
        cursor = conn.cursor()
        columns = {r[1] for r in cursor.execute(f"PRAGMA table_info({table})")}
        missing = [legacy for _, legacy in pairs if legacy not in columns]
        if missing:
            raise RuntimeError(
                f"{table} is missing legacy columns {missing}; refusing "
                "to reset soul ids without parking them first")
        lo, hi = cursor.execute(f"SELECT MIN(rowid), MAX(rowid) FROM {table}").fetchone()
        if lo is None:
            return 0
        chunk = max(1, int(getattr(self, "cutover_chunk", 1000) or 1000))
        set_clause = ", ".join(f"{live} = NULL" for live, _ in pairs)
        reset_where = " OR ".join(
            f"({live} IS NOT NULL AND {live} != '')" for live, _ in pairs)
        reset = 0
        start = lo
        while start <= hi:
            if getattr(self, "should_stop", False):
                raise RuntimeError(f"{table} cutover stopped part-way; it resumes next start")
            end = start + chunk - 1
            for live, legacy in pairs:
                cursor.execute(
                    f"UPDATE {table} SET {legacy} = {live} "
                    f"WHERE rowid BETWEEN ? AND ? "
                    f"AND {live} IS NOT NULL AND {live} != '' "
                    f"AND {live} NOT LIKE 'soul\\_unnamed\\_%' ESCAPE '\\' "
                    f"AND ({legacy} IS NULL OR {legacy} = '')", (start, end))
            cursor.execute(
                f"UPDATE {table} SET {set_clause}, "
                "updated_at = CURRENT_TIMESTAMP "
                f"WHERE rowid BETWEEN ? AND ? AND ({reset_where})", (start, end))
            reset += cursor.rowcount
            conn.commit()
            start = end + 1
            # let a writer that queued behind this chunk take the lock
            time.sleep(0.02)
        return reset

    def _migrate_artist_soul_ids(self):
        """kinded_v2 cutover for artists: park pre-v2 ids, clear soul_id
        and soul_id_path (a v1 derivation label must not survive onto a
        re-minted row), regenerate from the name alone."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if self._cutover_done(cursor, 'soulid_artist_version'):
                return True

            reset_count = self._park_and_reset(
                conn, "artists", [("soul_id", "soul_id_legacy")])
            cursor.execute(
                "UPDATE artists SET soul_id_path = NULL "
                "WHERE soul_id_path IS NOT NULL")
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) "
                "VALUES ('soulid_artist_version', ?)", (SOULID_VERSION,))
            # Clean up superseded flags (incl. the retired path migration)
            cursor.execute(
                "DELETE FROM metadata WHERE key IN "
                "('soulid_artist_v2', 'soulid_artist_v3', "
                "'soulid_artist_path_version')")
            conn.commit()

            if reset_count > 0:
                logger.info(
                    f"SoulID migration: parked + reset {reset_count} artist "
                    f"soul IDs for {SOULID_VERSION} regeneration")
            return True
        except Exception as e:
            logger.error(f"SoulID artist migration failed: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return False
        finally:
            if conn:
                conn.close()

    def _migrate_album_soul_ids(self):
        """kinded_v2 cutover for albums: park pre-v2 ids, clear soul_id."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if self._cutover_done(cursor, 'soulid_album_version'):
                return True

            reset_count = self._park_and_reset(
                conn, "albums", [("soul_id", "soul_id_legacy")])
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) "
                "VALUES ('soulid_album_version', ?)", (SOULID_VERSION,))
            conn.commit()

            if reset_count > 0:
                logger.info(
                    f"SoulID migration: parked + reset {reset_count} album "
                    f"soul IDs for {SOULID_VERSION} regeneration")
            return True
        except Exception as e:
            logger.error(f"SoulID album migration failed: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return False
        finally:
            if conn:
                conn.close()

    def _migrate_track_soul_ids(self):
        """kinded_v2 cutover for tracks: park pre-v2 ids, clear soul_id
        and album_soul_id. track_downloads.soul_id is deliberately NOT
        touched: it is download history (incl. ids read from files'
        embedded tags and the watchlist's already-downloaded lookups),
        and nothing copies it back into tracks any more — the provenance
        backfill map has no soul_id entry."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            if self._cutover_done(cursor, 'soulid_track_version'):
                return True

            reset_count = self._park_and_reset(
                conn, "tracks",
                [("soul_id", "soul_id_legacy"),
                 ("album_soul_id", "album_soul_id_legacy")])
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) "
                "VALUES ('soulid_track_version', ?)", (SOULID_VERSION,))
            conn.commit()

            if reset_count > 0:
                logger.info(
                    f"SoulID migration: parked + reset {reset_count} track "
                    f"soul IDs for {SOULID_VERSION} regeneration")
            return True
        except Exception as e:
            logger.error(f"SoulID track migration failed: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return False
        finally:
            if conn:
                conn.close()

    # ── Helpers ──

    def _count_pending(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            total = 0

            # Must match the WHERE clauses in _process_* methods exactly
            cursor.execute("""
                SELECT COUNT(*) FROM artists
                WHERE (soul_id IS NULL OR soul_id = '')
                  AND name IS NOT NULL AND name != ''
                  AND id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            cursor.execute("""
                SELECT COUNT(*) FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.soul_id IS NULL OR al.soul_id = '')
                  AND al.title IS NOT NULL AND al.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND al.id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            cursor.execute("""
                SELECT COUNT(*) FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                WHERE (t.soul_id IS NULL OR t.soul_id = '')
                  AND t.title IS NOT NULL AND t.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND t.id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            return total
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
