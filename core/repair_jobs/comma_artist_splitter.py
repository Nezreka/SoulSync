"""Split Artists (comma artist splitter) — a catalogue healer.

Finds library artists whose name is several artists joined by a separator
("Camellia, Toby Fox", "K/DA; League Of Legends") and splits them in the
catalogue. It is a healer in the Library v2 sense (docs/library-v2-data-flow.md):

- it reads the catalogue, never the files: an artist row credited on tracks
  with files is a candidate, whatever the tags say (a file whose tags disagree
  with the catalogue is the Re-tag job's to find)
- it decides with the one credit kernel (core/matching/artist_credit.py):
  the provider credit lists stored for the artist's tracks first, and when
  those say nothing, what the credit fetcher stores for them on request
  (provider_credits.ensure_track_credits); then the album's own artist as a
  part, the library's identified artists and the user's Keep Whole Names.
  Nothing decides on a name alone, and a name nothing explains stays whole
- the fix heals the catalogue through the one combined-artist heal
  (native_enrich.heal_combined_artist), which keeps watches, aliases, locks
  and hand-set names, and then asks Re-tag and Reorganize to look at the
  files of exactly those tracks. They act by their own settings: findings
  by default, writing only when the user switched that on.

Dry run (default) files a finding per combined artist; "One artist" keeps the
name whole for good, "Edit split" takes the user's own split.

Separators the job splits at: comma (,), semicolon (;), ampersand (&),
forward slash (/), each switchable in its settings.
"""

import json
import re
from typing import Optional

from core.matching.artist_credit import (
    decide_credit, name_key, parse_kept_whole_names, tokens,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, get_scope_artist_ids, get_scope_file_paths,
)
from core.repair_jobs.metadata_gap_filler import _gap_cursor
from utils.logging_config import get_logger

logger = get_logger("repair_job.comma_artist_splitter")

# Cap on combined artists examined per run (each may ask the credit fetcher).
SCAN_ARTIST_LIMIT = 300
# artists per run that may ask the credit fetcher (a few tracks each, every
# configured source): a big library's backlog goes over many runs, never as
# one burst of tens of thousands of provider calls
FETCH_ARTIST_BUDGET = 60


class _FetchBudgetSpent(Exception):
    """this run asked the providers enough; the artist waits for the next"""
# artists read per query while walking the catalogue
_ARTIST_PAGE = 500
# where the last run that hit SCAN_ARTIST_LIMIT stopped (a lib2 artist id)
_CURSOR_KEY = 'repair.comma_artist_splitter.artist_cursor'
# tracks of one artist the credit fetcher is asked about, at most
_FETCH_TRACKS = 3

# Sample of affected tracks stored in the finding for display; the fix
# re-reads the catalogue so it always covers ALL of the artist's tracks.
TRACK_SAMPLE_LIMIT = 40


def normalize_artist_name(name: str, symbols: list = None) -> str:
    """Casefold + whitespace-collapse for exact-name comparison, comma
    spacing canonical ("Tyler,The Creator" equals "Tyler, The Creator")."""
    return name_key(name)


def split_artist_parts(name: str, symbols: list = None) -> list:
    """Split a separator-joined artist string into clean parts.
    Supports: comma (,), semicolon (;), ampersand (&), forward slash (/)."""
    if symbols is None:
        symbols = [',', ';', '/', '&']
    if not symbols:
        return [str(name or '').strip()] if str(name or '').strip() else []
    parts = re.split(r'[{}]+'.format(''.join(symbols)), str(name or ''))
    return [p.strip() for p in parts if p.strip()]


def _piece_spans(name: str, symbols: list) -> list:
    """(start, end) of each non-empty piece of ``name`` between separators."""
    name = str(name or '')
    if not symbols:
        return [(0, len(name))] if name.strip() else []
    spans, pos = [], 0
    for sep in re.finditer(r'[{}]+'.format(re.escape(''.join(symbols))), name):
        spans.append((pos, sep.start()))
        pos = sep.end()
    spans.append((pos, len(name)))
    return [(a, b) for a, b in spans if name[a:b].strip()]


def _letters(text: str) -> str:
    return ''.join(str(text or '').casefold().split())


def custom_split_parts(combined: str, edited: str) -> tuple:
    """the user's own split of ``combined`` (#2150): names joined by ';'.

    every name must be a run of the name's own pieces, so an edit can join
    or part names at the separators that are there but never invents one.
    returns (parts spelled as in the name, the parts that join pieces).
    raises ValueError when the edit doesn't fit the name."""
    combined = str(combined or '')
    spans = _piece_spans(combined, [',', ';', '/', '&'])
    wanted = [w.strip() for w in str(edited or '').split(';') if w.strip()]
    if not wanted:
        raise ValueError('No names given')
    parts, joined, i = [], [], 0
    for want in wanted:
        target = _letters(re.sub(r'[,;/&]', '', want))
        got, j = '', i
        while j < len(spans) and len(got) < len(target):
            got += _letters(re.sub(r'[,;/&]', '', combined[spans[j][0]:spans[j][1]]))
            j += 1
        if j == i or got != target:
            raise ValueError(f'"{want}" is not a name in "{combined}"')
        part = combined[spans[i][0]:spans[j - 1][1]].strip()
        if ';' in part:
            raise ValueError(f'"{want}" runs across a ";", which always parts two names')
        parts.append(part)
        if j - i > 1:
            joined.append(part)
        i = j
    if i != len(spans):
        raise ValueError(f'Every name in "{combined}" has to stay in the split')
    return parts, joined


# every name the kernel's tokenizer can read as a list, for the SQL prefilter;
# the tokenizer itself decides (core/matching/artist_credit.py)
_CANDIDATE_LIKES = ('%,%', '%;%', '%/%', '%&%', '%|%', '%+%', '%×%', '% and %', '% feat%',
                    '% ft.%', '% ft %', '% featuring %', '% x %', '% vs%', '% with %',
                    '% w/ %', '% pres%', '% meets %')


def _separator_switch(kind: str, raw: str) -> str:
    """the setting a kernel separator falls under ('' = always a list): the
    four switches cover the separators that can be part of a name; a guest
    marker (feat., x, vs., with, pres.) and "|" always separate artists"""
    text = raw.strip()
    if kind == 'comma':
        return ','
    if kind in ('amp', 'link'):
        return '&'
    if kind == 'slash':
        return '/'
    if kind == 'hard':
        return ';' if text == ';' else '/' if text == '/' else ''
    return ''


def _distinct(*streams):
    """(id, name) pairs from several walks, each artist once"""
    seen = set()
    for stream in streams:
        for artist_id, name in stream:
            if artist_id not in seen:
                seen.add(artist_id)
                yield artist_id, name


def _reads_as_list(name: str) -> bool:
    return len(tokens(name or '')[0]) >= 2


def _splits_only_at(name: str, parts: list, symbols: list) -> bool:
    """every gap between ``parts`` in ``name`` is one separator the kernel
    reads, and one this job splits at (its settings switch the ones that can
    be part of a name on or off)"""
    text = ' '.join(str(name or '').split())
    lower = text.lower()
    pos = 0
    for index, part in enumerate(parts):
        piece = ' '.join(part.split()).lower()
        at = lower.find(piece, pos)
        if at < 0:
            return False
        if index:
            names, seps = tokens(f"Alpha{text[pos:at]}Omega")
            if names != ['Alpha', 'Omega'] or len(seps) != 1:
                return False
            switch = _separator_switch(*seps[0])
            if switch and switch not in symbols:
                return False
        pos = at + len(piece)
    return not text[pos:].strip()


@register_job
class CommaArtistSplitterJob(RepairJob):
    job_id = 'comma_artist_splitter'
    display_name = 'Comma Artist Splitter'
    description = 'Finds library artists that are really several artists joined by a separator'
    help_text = (
        'An artist named like "Camellia, Toby Fox" is often several artists the media server '
        'or an old import turned into one: it gets no artist image, clutters your artist '
        'grid and keeps the real artists from their credits.\n\n'
        'This job looks at the artists in your library (not at file tags) and decides each '
        'name with what the library knows:\n'
        '1. The artist lists the metadata providers credit its tracks with. When the library '
        'has none yet, it asks the metadata fetcher for them first.\n'
        '2. The album\'s own artist inside the name, the artists the library already '
        'identified, and your Keep Whole Names.\n'
        'A name nothing explains is left as it is.\n\n'
        'A split is confirmed when a provider (Spotify, Deezer, MusicBrainz, Tidal, Discogs, '
        'JioSaavn) credits every part on one of the '
        'artist\'s tracks. A split only the names suggest is filed for you to check: '
        'Fix all and live mode leave it alone, the finding\'s own button applies it.\n\n'
        'In dry run mode (default), each finding shows how the artist would be split. '
        'Applying it splits the artist in your library (albums, credits and watches move to '
        'the real artists) and asks Re-tag and Reorganize to check exactly those files. '
        'They follow their own settings: by default they file findings for you to apply.\n\n'
        'Settings:\n'
        '- Comma Splitter: Enable splitting commas (,)\n'
        '- Semicolon Splitter: Enable splitting semicolons (;)\n'
        '- Forward Slash Splitter: Enable splitting forward slashes (/)\n'
        '- Ampersand Splitter: Enable splitting ampersands (&)\n'
        '- Keep Whole Names: Names that are one artist and never split, separated by ";" '
        '(e.g. "K/DA; Above & Beyond"). A finding\'s "One artist" and "Edit split" add to it\n'
        '- Dry Run: When enabled, only reports splits without changing your library\n'
        '- Heal Ghosts: a combined artist that only stands in credits (owns no album, nobody '
        'watches it) is split on its own as soon as the providers confirm it, dry run or not. '
        'One they do not confirm stays a finding to check\n'
        '- Request Re-tag / Request Reorganize: after every split, ask those jobs to check '
        'exactly the affected files. A job you switched off is not run; the files follow '
        'once it runs'
    )
    icon = 'repair-icon-artist'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    default_settings = {
        'comma_splitter': True,
        'semicolon_splitter': True,
        'forward_slash_splitter': True,
        'ampersand_splitter': True,
        'keep_whole_names': '',
        'dry_run': True,
        'request_retag': True,
        'request_reorganize': True,
        # a confirmed split of a ghost (a credit no album belongs to and
        # nobody watches) is applied on its own, dry run or not
        'heal_ghosts': True,
    }
    auto_fix = True
    writes_library_files = False
    supports_file_scope = True
    # the one job that splits an artist: others hand it their candidates by id
    # (RepairWorker.request_split), a fileless ghost included
    supports_artist_id_scope = True

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            likes = ' OR '.join("a.name LIKE ?" for _ in _CANDIDATE_LIKES)
            return int(conn.execute(
                "SELECT COUNT(*) FROM lib2_artists a WHERE a.canonical_artist_id IS NULL "
                f"AND ({likes})", _CANDIDATE_LIKES).fetchone()[0])
        except Exception:  # noqa: BLE001 - an estimate only
            return 0
        finally:
            if conn:
                conn.close()

    def _get_settings(self, context: JobContext) -> dict:
        """Read job settings from config, falling back to defaults."""
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        if 'dry_run' not in cfg and 'apply_automatically' in cfg:
            merged['dry_run'] = not bool(cfg.get('apply_automatically', False))
        return merged

    def _get_symbols(self, settings: dict) -> list:
        """Return the list of separators enabled in the settings."""
        symbols = []
        if settings.get('comma_splitter', True):
            symbols.append(',')
        if settings.get('semicolon_splitter', True):
            symbols.append(';')
        if settings.get('forward_slash_splitter', True):
            symbols.append('/')
        if settings.get('ampersand_splitter', True):
            symbols.append('&')
        return symbols

    # --- the scan ---------------------------------------------------------------

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        self._identified = None      # read once per scan, not per candidate
        self._fetch_budget = FETCH_ARTIST_BUDGET
        settings = self._get_settings(context)
        dry_run = bool(settings.get('dry_run', True))
        symbols: list = self._get_symbols(settings)
        if not symbols:
            return result
        kept = {name_key(n) for n in parse_kept_whole_names(settings.get('keep_whole_names'))}
        allowed = get_scope_file_paths(context)
        asked_for = get_scope_artist_ids(context)
        from core.library2.provider_credits import credit_services
        services = credit_services() - ({'spotify'} if context.is_spotify_rate_limited() else set())

        cursor_start = _gap_cursor(context, _CURSOR_KEY)
        total = self.estimate_scope(context)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} artist name(s)...', total=total)

        walk = {'last': None, 'error': None}
        found = []
        capped = False
        if asked_for is not None and allowed is not None:
            # two requests in one run: artists asked for by id, and the files
            # a scoped run was asked for (the walk keeps only theirs)
            artists = _distinct(self._asked_artists(context, asked_for, symbols, walk),
                                self._walk_artists(context, 0, symbols, walk))
        elif asked_for is not None:
            artists = self._asked_artists(context, asked_for, symbols, walk)
        else:
            artists = self._walk_artists(context, cursor_start, symbols, walk)
        seen_ids: set = set()
        for artist_id, name in artists:
            if context.check_stop():
                return result
            if context.wait_if_paused():
                return result
            if len(found) >= SCAN_ARTIST_LIMIT:
                capped = True
                break
            previous_last = walk['last']
            seen_ids.add(int(artist_id))
            walk['last'] = int(artist_id)
            result.scanned += 1
            if context.update_progress:
                context.update_progress(result.scanned, total)
            key = name_key(name)
            if key in kept:
                result.skipped += 1
                continue
            try:
                by_id = asked_for is not None and int(artist_id) in asked_for
                info = self._decide(context, int(artist_id), name, symbols, services,
                                    None if by_id else allowed, asked=by_id)
            except _FetchBudgetSpent:
                # this artist and the rest wait for the next run (the walk's
                # cursor, or a request for the rest)
                seen_ids.discard(int(artist_id))
                walk['last'] = previous_last
                result.scanned -= 1
                capped = True
                break
            except Exception as e:  # noqa: BLE001 - one artist never ends the scan
                logger.debug("Split check failed for %r: %s", name, e)
                result.errors += 1
                continue
            if info is None:
                result.skipped += 1
                continue
            found.append(info)

        if walk['error'] is not None:
            result.errors += 1
            if not result.scanned:
                return result
        elif asked_for is not None:
            # a request for named artists leaves the library walk where it
            # was; what one run could not take is asked for again
            rest = sorted(set(asked_for) - seen_ids)
            if capped and rest:
                from core.repair_worker import request_artist_split
                request_artist_split(rest, reason='Split Artists, the rest of a request')
        elif capped:
            if walk['last'] is not None:    # else: stopped at its first artist, stays put
                _gap_cursor(context, _CURSOR_KEY, walk['last'])
        else:
            _gap_cursor(context, _CURSOR_KEY, 0)

        heal_ghosts = settings.get('heal_ghosts', True) is not False
        fixer = None

        def get_fixer():
            nonlocal fixer
            if fixer is None:
                from core.repair_worker import active_repair_worker, RepairWorker
                fixer = active_repair_worker()
                # the app's worker, when it works on this library's database
                if fixer is None or getattr(fixer, 'db', None) is not context.db:
                    fixer = RepairWorker(context.db, transfer_folder=context.transfer_folder)
                    fixer._config_manager = context.config_manager
            return fixer

        for info in sorted(found, key=lambda x: len(x['details']['all_files']), reverse=True):
            if context.check_stop():
                return result
            details = info['details']
            if context.report_progress:
                context.report_progress(
                    scanned=result.scanned,
                    log_line=(f'"{details["combined_name"]}" → {len(details["split_artists"])} '
                              f'artists ({details["track_count"]} track(s))'),
                    log_type='warning')
            entity_id = f'lib2:{info["artist_id"]}'
            unconfirmed = bool(details.get('needs_confirmation'))
            # applied on its own: what the providers confirmed, in live mode,
            # or for a ghost whenever ghosts heal. A split names alone
            # propose waits for the user either way
            auto = not unconfirmed and (not dry_run or (heal_ghosts and details.get('ghost')))
            if auto and self._dismissed(context, entity_id):
                # the user said no to this split: nothing applies it on its own
                result.skipped += 1
                continue
            if not auto:
                self._file_finding(context, result, details, entity_id, unconfirmed)
            else:
                applied = get_fixer()._fix_comma_artist_split('artist', entity_id, None, details)
                if applied.get('success') and applied.get('action') == 'artists_split':
                    result.auto_fixed += applied.get('fixed', 0)
                    self._record_resolved_finding(context, details, int(applied.get('fixed', 0)),
                                                  entity_id)
                elif not applied.get('success'):
                    if dry_run:
                        # a ghost the heal refused (a lock, a name set by hand)
                        self._file_finding(context, result, details, entity_id, unconfirmed)
                    else:
                        result.errors += 1
                        logger.warning("Could not apply artist split for %s: %s",
                                       details['combined_name'], applied.get('error'))
            if context.sleep_or_stop(0.05):
                return result

        if context.update_progress:
            context.update_progress(total, total)
        if context.report_progress:
            context.report_progress(
                phase=(f'Done — {result.findings_created} splittable artist(s) found'
                       if dry_run else f'Done — {result.auto_fixed} track(s) healed'),
                log_line=(f'{result.findings_created} finding(s), '
                          if dry_run else f'{result.auto_fixed} track(s) fixed, ') +
                         f'{result.skipped} skipped, {result.scanned} checked',
                log_type='success')
        return result

    def _file_finding(self, context: JobContext, result: JobResult, details: dict,
                      entity_id: str, unconfirmed: bool) -> None:
        if not context.create_finding:
            return
        count = len(details["split_artists"])
        try:
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='comma_artist_split',
                severity='info' if unconfirmed else 'warning',
                entity_type='artist',
                entity_id=entity_id,
                file_path=None,
                title=f'Combined artist: {details["combined_name"]}',
                description=(
                    f'"{details["combined_name"]}" may be {count} artists. No '
                    f'provider confirms it: check it before you split it'
                    if unconfirmed else
                    f'"{details["combined_name"]}" is {count} '
                    f'artists — the fix splits it in your library for '
                    f'{details["track_count"]} track(s)'),
                details=details,
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("Error creating finding for %s: %s", details['combined_name'], e)
            result.errors += 1

    def _asked_artists(self, context: JobContext, artist_ids, symbols: list, walk: dict):
        """Yield (id, name) of the requested artists whose name holds an
        enabled separator, files or not."""
        if not artist_ids:
            return
        conn = None
        try:
            conn = context.db._get_connection()
            ids = sorted(artist_ids)
            rows = []
            for start in range(0, len(ids), 500):     # SQLite's variable limit
                chunk = ids[start:start + 500]
                marks = ','.join('?' for _ in chunk)
                rows += conn.execute(
                    f"SELECT id, name FROM lib2_artists WHERE canonical_artist_id IS NULL "
                    f"AND id IN ({marks}) ORDER BY id", chunk).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.error("Error reading requested artists: %s", e, exc_info=True)
            walk['error'] = e
            return
        finally:
            if conn is not None:
                conn.close()
        for artist_id, name in rows:
            if _reads_as_list(name):
                yield int(artist_id), name

    def _walk_artists(self, context: JobContext, after: int, symbols: list, walk: dict):
        """Yield (id, name) of every canonical artist whose name holds an
        enabled separator and that has files in the library, in id order from
        ``after``, wrapping round. One page per connection."""
        likes = ' OR '.join("a.name LIKE ?" for _ in _CANDIDATE_LIKES)
        patterns = list(_CANDIDATE_LIKES)
        ranges = [(after, None)] + ([(0, after)] if after else [])
        for low, high in ranges:
            while True:
                conn = None
                try:
                    conn = context.db._get_connection()
                    upper = 'AND a.id <= ?' if high is not None else ''
                    rows = conn.execute(f"""
                        SELECT a.id, a.name FROM lib2_artists a
                         WHERE a.canonical_artist_id IS NULL AND a.id > ? {upper}
                           AND ({likes})
                           AND (EXISTS (SELECT 1 FROM lib2_track_artists ta
                                          JOIN lib2_track_files f ON f.track_id = ta.track_id
                                         WHERE ta.artist_id = a.id
                                           AND COALESCE(f.file_state,'active') = 'active')
                                OR EXISTS (SELECT 1 FROM lib2_albums al
                                             JOIN lib2_tracks t ON t.album_id = al.id
                                             JOIN lib2_track_files f ON f.track_id = t.id
                                            WHERE al.primary_artist_id = a.id
                                              AND COALESCE(f.file_state,'active') = 'active'))
                         ORDER BY a.id LIMIT {_ARTIST_PAGE}
                    """, (low, *((high,) if high is not None else ()), *patterns)).fetchall()
                except Exception as e:  # noqa: BLE001
                    logger.error("Error reading artists: %s", e, exc_info=True)
                    walk['error'] = e
                    return
                finally:
                    if conn:
                        conn.close()
                for row in rows:
                    if _reads_as_list(row[1]):
                        yield int(row[0]), str(row[1] or '')
                if len(rows) < _ARTIST_PAGE:
                    break
                low = int(rows[-1][0])

    def _decide(self, context: JobContext, artist_id: int, name: str, symbols: list,
                services: set, allowed, *, asked: bool = False) -> Optional[dict]:
        """the finding for one combined artist, None to leave it alone.

        Reads the catalogue only. A split is confirmed when the providers
        credit every part on one of its tracks (``split_confirmed``). When the
        stored lists neither confirm a split nor keep the name whole, the
        credit fetcher is asked for a few of the tracks, looking them up under
        the proposed artists too, and the lists are read again. A split names
        alone propose is still filed, as a finding the user confirms. A split
        only ever happens at a separator this job has switched on."""
        from core.library2.native_enrich import (
            _ghost_credit_evidence, _identified_name_keys, combined_artist_scope,
            split_confirmed,
        )
        from core.library2.importer import normalize_name

        names, _seps = tokens(name)
        if len(names) < 2:
            return None
        conn = context.db._get_connection()
        try:
            scope = combined_artist_scope(conn, artist_id)
            files = self._files(conn, scope['track_ids'], allowed)
            if not files and not asked:
                return None  # nothing of it in this run's scope
            anchors = [r[0] for r in conn.execute(
                "SELECT DISTINCT pa.name FROM lib2_track_artists ta "
                "JOIN lib2_tracks t ON t.id = ta.track_id "
                "JOIN lib2_albums al ON al.id = t.album_id "
                "JOIN lib2_artists pa ON pa.id = al.primary_artist_id "
                "WHERE ta.artist_id = ? AND pa.id <> ?", (artist_id, artist_id)) if r[0]]
            identified = self._identified_holders(conn, _identified_name_keys)
            # a ghost: a credit no album belongs to and nobody watches
            watched = conn.execute(
                "SELECT 1 FROM lib2_artists WHERE id=? AND COALESCE(monitored,0)=1 UNION ALL "
                "SELECT 1 FROM lib2_monitor_rules WHERE entity_type='artist' AND entity_id=? "
                "AND COALESCE(monitored,0)=1 LIMIT 1", (artist_id, artist_id)).fetchone()
            if watched is None:
                # a profile's watchlist names it (the watch row would point at nothing)
                try:
                    watched = conn.execute(
                        "SELECT 1 FROM watchlist_artists WHERE LOWER(TRIM(artist_name))="
                        "LOWER(TRIM(?)) LIMIT 1", (name,)).fetchone()
                except Exception:  # noqa: BLE001 - no watchlist table: nobody watches
                    watched = None
            ghost = not scope['primary_album_ids'] and watched is None
            # the artist's own provider identity says the whole name is one
            # act: "Lo & Leduc" stays whole next to identified "Lo" / "Leduc"
            from core.library2.native_enrich import _IDENTIFIED_SQL
            own = conn.execute(f"SELECT {_IDENTIFIED_SQL} FROM lib2_artists a WHERE a.id=?",
                               (artist_id,)).fetchone()
            self_identified = bool(own and own[0])
        finally:
            conn.close()
        whole = normalize_name(name)

        def known(n):
            # identified by a row other than this artist's own, or a name the
            # user keeps whole (no row: an empty set)
            holders = identified.get(normalize_name(n))
            if holders is None:
                return False
            return not holders or bool(holders - {artist_id})

        def act(n):
            return known(n) or (self_identified and normalize_name(n) == whole)

        def decide():
            # what the library holds, read fresh each time
            conn = context.db._get_connection()
            try:
                stored = list(_ghost_credit_evidence(conn, artist_id))
            finally:
                conn.close()
            return decide_credit(name, evidence=stored, known=anchors, is_act=act,
                                 is_artist=known, config=context.config_manager)

        def confirmed(decision):
            if not decision.split:
                return False
            conn = context.db._get_connection()
            try:
                return split_confirmed(conn, artist_id, decision.names)
            finally:
                conn.close()

        decision = decide()
        # settled: a split the providers confirm, or a whole name a provider
        # list, the artist's own identity, the album's artist or the band its
        # lead fronts already accounts for. Only the rest is worth a request
        settled = confirmed(decision) or (not decision.split and (
            decision.via.endswith(' credits') or decision.via in ('act', 'known', 'band')))
        if not settled:
            # the providers asked about the proposed artists: a track the
            # combined name finds nowhere is looked up under each of them
            hints = list(decision.names) if decision.split else names
            if services and scope['track_ids']:
                if getattr(self, '_fetch_budget', FETCH_ARTIST_BUDGET) <= 0:
                    raise _FetchBudgetSpent()
                self._fetch_budget = getattr(self, '_fetch_budget', FETCH_ARTIST_BUDGET) - 1
            if self._fetch_credits(context, scope['track_ids'], services, hints):
                decision = decide()
        if not decision.split or not _splits_only_at(name, list(decision.names), symbols):
            return None
        is_confirmed = confirmed(decision)
        parts = list(decision.names)
        lead = next((p for p in parts if normalize_name(p) in {normalize_name(a) for a in anchors}),
                    parts[0])
        conn = context.db._get_connection()
        try:
            resolution = []
            for part in parts:
                library_id = self._library_artist_id(conn, part)
                resolution.append({'name': part, 'in_library': library_id is not None,
                                   'library_artist_id': library_id,
                                   'verified_via': decision.via})
        finally:
            conn.close()
        details = {
            'combined_name': name,
            'artist_name': name,
            'split_artists': parts,
            'primary_artist': lead,
            'new_display_artist': '; '.join(parts),
            'db_artist_id': artist_id,
            'db_artist_name': name,
            'verified_via': decision.via,
            'checked_sources': [decision.via] if is_confirmed else [],
            # names alone proposed it: never applied without the user
            'confirmed': is_confirmed,
            'needs_confirmation': not is_confirmed,
            'ghost': ghost,
            'parts_resolution': resolution,
            'file_count': len(files),
            'track_count': len(scope['track_ids']),
            'files': files[:TRACK_SAMPLE_LIMIT],
            'all_files': files,
            # the albums whose own artist this is: they move to the lead
            # (library_v2 below is rebuilt for every finding, so not in there)
            'primary_album_ids': scope['primary_album_ids'],
            'library_v2_native': True,
            'library_v2': {
                'artist_ids': [artist_id],
                'track_ids': scope['track_ids'],
                'album_ids': scope['album_ids'],
                'file_ids': sorted({f['lib2_file_id'] for f in files}),
            },
        }
        return {'artist_id': artist_id, 'details': details}

    def _files(self, conn, track_ids: list, allowed) -> list:
        """the active files of these tracks, inside the run's file scope"""
        if not track_ids:
            return []
        from core.repair_jobs.base import file_path_in_scope
        marks = ','.join('?' for _ in track_ids)
        rows = conn.execute(
            f"SELECT f.id, f.track_id, f.path, t.title FROM lib2_track_files f "
            f"JOIN lib2_tracks t ON t.id = f.track_id WHERE f.track_id IN ({marks}) "
            "AND COALESCE(f.file_state,'active') = 'active' AND f.path IS NOT NULL "
            "AND f.path <> '' ORDER BY f.track_id, f.id", list(track_ids)).fetchall()
        return [{'title': r[3] or '', 'file_path': r[2], 'lib2_file_id': int(r[0]),
                 'lib2_track_id': int(r[1])}
                for r in rows if allowed is None or file_path_in_scope(r[2], allowed)]

    def _fetch_credits(self, context: JobContext, track_ids: list, services: set,
                       hints=()) -> bool:
        """ask the credit fetcher for a few of the tracks' credit lists from
        ``services`` (the configured sources that give one: Spotify, Deezer,
        MusicBrainz, Tidal, Discogs, JioSaavn), matching them there first when
        they have no id: under the album's artist, then under each of
        ``hints``"""
        if not services:
            return False
        from core.library2.provider_credits import ensure_track_credits
        stored = False
        for track_id in list(track_ids)[:_FETCH_TRACKS]:
            stored = ensure_track_credits(context.db, int(track_id), services,
                                          artist_hints=hints) or stored
        return stored

    def _library_artist_id(self, conn, name: str):
        row = conn.execute(
            "SELECT id FROM lib2_artists WHERE canonical_artist_id IS NULL "
            "AND LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1", (name,)).fetchone()
        return int(row[0]) if row else None

    def _identified_holders(self, conn, name_keys) -> dict:
        """normalized name -> ids of the identified artists holding it (an
        empty set for a keep-whole name), read once per scan"""
        if getattr(self, '_identified', None) is None:
            from core.library2.importer import normalize_name
            from core.library2.native_enrich import _IDENTIFIED_SQL
            index: dict = {}
            for aid, name in conn.execute(
                    f"SELECT a.id, a.name FROM lib2_artists a WHERE a.canonical_artist_id IS NULL "
                    f"AND {_IDENTIFIED_SQL}"):
                key = normalize_name(name or "")
                if key:
                    index.setdefault(key, set()).add(int(aid))
            for key in name_keys(conn) - set(index):
                index[key] = set()          # a keep-whole name (#2150)
            self._identified = index
        return self._identified

    def _dismissed(self, context: JobContext, entity_id: str) -> bool:
        """whether the user dismissed a split finding of this artist"""
        conn = None
        try:
            conn = context.db._get_connection()
            return conn.execute(
                "SELECT 1 FROM repair_findings WHERE job_id=? AND finding_type='comma_artist_split' "
                "AND entity_type='artist' AND entity_id=? AND status='dismissed' LIMIT 1",
                (self.job_id, entity_id)).fetchone() is not None
        except Exception as e:  # noqa: BLE001 - unreadable: do not apply on our own
            logger.debug("dismissed check for %s failed: %s", entity_id, e)
            return True
        finally:
            if conn is not None:
                conn.close()

    def _record_resolved_finding(self, context: JobContext, details: dict, fixed_count: int,
                                 entity_id: str) -> None:
        """Persist an auto-applied split as a resolved finding entry."""
        combined_name = details['combined_name']
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'resolved',
                    user_action = 'artists_split',
                    resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND finding_type = ? AND entity_type = ? AND entity_id = ?
                  AND status = 'pending'
            """, (self.job_id, 'comma_artist_split', 'artist', entity_id))
            if cursor.rowcount > 0:
                conn.commit()
                return
            cursor.execute("""
                INSERT INTO repair_findings
                    (job_id, finding_type, severity, status, entity_type, entity_id,
                     file_path, title, description, details_json, user_action, resolved_at)
                VALUES (?, ?, ?, 'resolved', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                self.job_id, 'comma_artist_split', 'warning', 'artist', entity_id, None,
                f'Combined artist: {combined_name}',
                (f'"{combined_name}" was split into {len(details["split_artists"])} artists '
                 f'for {fixed_count} track(s)'),
                json.dumps(details), 'artists_split',
            ))
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not record resolved auto-split finding for %s: %s",
                           combined_name, e)
        finally:
            if conn:
                conn.close()
