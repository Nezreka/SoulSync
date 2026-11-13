/**
 * `_renderFindingDetail` — the per-type expanded body of a finding card.
 *
 * NINETEEN explicit cases plus a generic fallback — twenty branches in all, which
 * is where "the 20-type renderer" comes from. Note FINDING_TYPE_LABELS carries 22
 * entries: `missing_lossy_copy`, `quality_upgrade` and `replaygain_retag` have
 * labels and fix buttons but no case of their own, so they render through the
 * generic arm. That asymmetry is the vanilla's.
 *
 * Each case reads a different slice of the finding's `details` payload, and every
 * one is transcribed from the vanilla
 * switch in enrichment.js; the ordering of rows, the exact labels, and which
 * blocks are conditional are all load-bearing for the artefact diff.
 *
 * Two things worth knowing before editing:
 *
 * - The vanilla escapes everything through `_escFinding` because it builds HTML
 *   strings. React escapes by default, so `_escFinding` has no port — but that
 *   also means any place the vanilla deliberately did NOT escape would be a
 *   behaviour change here. There is no such place in this switch.
 *
 * - `details` is `Record<string, unknown>`, so every read goes through the small
 *   coercion helpers below rather than a cast. A finding payload is server data
 *   that has changed shape before.
 */

import { createContext, Fragment, useContext, useState } from 'react';

import type { FindingDetailRow } from '../-tools.core';
import type { TagChange } from '../-tools.finding-copy';
import type { RepairFinding } from '../-tools.types';

import { findExactArtist, searchLibraryArtists } from '../-tools.api';
import {
  COMMA_SPLIT_TRACK_CAP,
  commaSplitChips,
  fakeLosslessSpectrum,
  formatFileSize,
  genericDetailRows,
  incompleteAlbumCompletion,
  libraryRetagDetail,
  nativeRetagChanges,
  pickDuplicateToKeep,
  scoreBar,
} from '../-tools.core';
import { timeAgo } from '../-tools.core';
import {
  describeFinding,
  readApplied,
  readTagChanges,
  readUndone,
  tagCheckLabel,
} from '../-tools.finding-copy';
// tracks.duration is stored in MILLISECONDS (music_database.py:424); this
// panel used to print it raw with an "s" suffix, so a 3:58 song read
// "238000s" (#1210).
// The artist-detail `enhanced` family is gone on this branch (Library v2
// replaced it); the same helper still lives beside the sync server view.
import { formatDurationMs } from '../../sync/-sync.server';
import { TagChangeTable } from './tag-changes';

type Details = Record<string, unknown>;

/** Detail values are scalars in practice; the cast tells the linter so. An
 *  unexpected object still stringifies exactly as the vanilla's `String(v)` did. */
type Scalar = string | number | boolean | bigint | null | undefined;
const text = (value: unknown): string => (value == null ? '' : String(value as Scalar));
const list = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);

/** Truthy-guard matching the vanilla's `if (d.x) rows.push(...)` — 0 and '' are
 *  both skipped, which is what the source does. */
function pushIf(rows: FindingDetailRow[], value: unknown, key: string, cls?: string): void {
  if (value) rows.push([key, text(value), cls]);
}

// ── Shared pieces ────────────────────────────────────────────────────────────

/** `_gridRows` — renders nothing at all when there are no rows. */
/** the diff row for a check key. the checks say 'albumartist' and 'cover', the
 *  diff says 'album_artist' and 'cover_art' */
function retagDiffFor(
  diff: unknown,
  key: string,
): { file_value?: unknown; db_value?: unknown } | null {
  if (!Array.isArray(diff)) return null;
  const want = key === 'albumartist' ? 'album_artist' : key === 'cover' ? 'cover_art' : key;
  const row = diff.find(
    (r) => r && typeof r === 'object' && (r as { file_key?: unknown }).file_key === want,
  );
  return row && typeof row === 'object'
    ? (row as { file_value?: unknown; db_value?: unknown })
    : null;
}

/** "mismatch" alone left you wondering how. a mismatch or a missing tag says
 *  what the file has and what SoulSync would write (Storm) */
function checkWithValues(
  status: string,
  diff: { file_value?: unknown; db_value?: unknown } | null,
): string {
  if (!diff || (status !== 'mismatch' && status !== 'missing')) return status;
  const show = (v: unknown) =>
    v === null || v === undefined || v === '' ? 'empty' : `"${text(v)}"`;
  return `${status}: file ${show(diff.file_value)}, SoulSync ${show(diff.db_value)}`;
}

/** what the library holds for a tag check, when the finding carries it */
function retagLibraryValue(reference: Details, key: string): string {
  const pick: Record<string, unknown> = {
    title: reference.title,
    artist: reference.track_artist || reference.artist_name,
    album: reference.album_title,
    albumartist: reference.artist_name,
    genre: Array.isArray(reference.genres) ? reference.genres.join(', ') : reference.genres,
    year: reference.year,
    track_number: reference.track_number,
    disc_number: reference.disc_number,
    total_tracks: reference.album_track_total,
  };
  return text(pick[key]);
}

/**
 * The tag checks of a re-tag finding, as rows a person reads.
 *
 * It used to be every check as `key: status` in a column of fourteen, twelve
 * of them "correct". The ones that are wrong come first, each saying what the
 * library has (and what the file has, when the scan recorded it); the ones
 * that match fold into one line.
 */
function retagCheckRows(
  checks: Record<string, unknown>,
  d: Details,
  shown: ReadonlySet<string> = new Set(),
): FindingDetailRow[] {
  const reference =
    d.validation && typeof d.validation === 'object' && 'reference' in d.validation
      ? ((d.validation.reference || {}) as Details)
      : {};
  const rows: FindingDetailRow[] = [];
  const matching: string[] = [];
  const unchecked: string[] = [];
  for (const [key, raw] of Object.entries(checks)) {
    const status = text(raw);
    const label = tagCheckLabel(key);
    if (status === 'correct') matching.push(label);
    else if (status === 'unknown') unchecked.push(label);
    else if (shown.has(key)) continue;
    else if (status === 'mismatch' || status === 'missing') {
      const diff = retagDiffFor(d.diff, key);
      const library = retagLibraryValue(reference, key);
      const said = diff
        ? checkWithValues(status, diff)
        : status === 'missing'
          ? `missing from the file${library ? `. The library has ${library}` : ''}`
          : `differs from the library${library ? `, which has ${library}` : ''}`;
      rows.push([label, said, 'highlight']);
    }
  }
  if (matching.length) {
    rows.push([
      'Matching',
      matching.length > 4
        ? `${matching.slice(0, 4).join(', ')} and ${matching.length - 4} more`
        : matching.join(', '),
    ]);
  }
  if (unchecked.length) rows.push(['Could not check', unchecked.join(', ')]);
  return rows;
}

/**
 * What the row above the panel has already said: the finding's title, artist
 * and album, and (below the panel) its file path. A grid that repeats them is
 * three lines of nothing, so those rows are left out.
 */
const SaidAlready = createContext<{ path: string; names: ReadonlySet<string> }>({
  path: '',
  names: new Set(),
});
const IDENTITY_KEYS = new Set(['Track', 'Title', 'Artist', 'Album', 'File', 'Full Path', 'Path']);

export function DetailGrid({ rows: allRows }: { rows: FindingDetailRow[] }) {
  const said = useContext(SaidAlready);
  const rows = allRows.filter(([key, value, cls]) => {
    if (!IDENTITY_KEYS.has(key)) return true;
    if (cls === 'path') return value !== said.path;
    return !said.names.has(value);
  });
  if (!rows.length) return null;
  return (
    <div className="repair-detail-grid">
      {rows.map(([key, value, cls], index) => (
        // Row labels repeat across findings (two "Artist" rows in one grid is
        // legal), so the index is the only stable key here.
        <Fragment key={`${key}-${index}`}>
          <span className="repair-detail-key">{key}</span>
          <span className={`repair-detail-val ${cls || ''}`}>{value}</span>
        </Fragment>
      ))}
    </div>
  );
}

/** `_renderScoreBar`. */
function ScoreBar({ value, label }: { value: unknown; label: string }) {
  const { percent, band } = scoreBar(typeof value === 'number' ? value : Number(value) || 0);
  return (
    <div className="repair-score-bar">
      <span className="repair-detail-key">{label}</span>
      <div className="repair-score-bar-track">
        <div className={`repair-score-bar-fill ${band}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="repair-detail-val">{percent}%</span>
    </div>
  );
}

/**
 * `_renderPlayButton` + `playFindingTrack`. The vanilla stashes the track on data
 * attributes and reads them back in the click handler purely because the button
 * is inside an HTML string; here the values close over directly.
 *
 * The title/artist fallback chains are the vanilla's and differ per field.
 */
function PlayButton({ finding }: { finding: RepairFinding }) {
  const d = (finding.details || {}) as Details;
  const filePath = finding.file_path || text(d.file_path) || text(d.original_path);
  if (!filePath) return null;

  const title = text(d.expected_title || d.title || d.file_title || d.matched_title);
  const artist = text(d.expected_artist || d.artist || d.artist_name);
  const album = text(d.album || d.album_title);

  return (
    <button
      className="repair-finding-play-btn"
      type="button"
      title="Play this track"
      onClick={(event) => {
        event.stopPropagation();
        void window.SoulSyncWebShellBridge?.playLibraryTrack(
          {
            file_path: filePath,
            // The vanilla's `btn.dataset.title || 'Unknown Track'`.
            title: title || 'Unknown Track',
            id: finding.entity_id ?? '',
          },
          album,
          artist,
        );
      }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <polygon points="5,3 19,12 5,21" />
      </svg>{' '}
      Play
    </button>
  );
}

/**
 * Play ONE listed copy (#1214). The duplicate rows are the only place in the app
 * that shows two files claiming to be the same song, and picking between them by
 * filename alone is guesswork - so each row can be auditioned.
 *
 * `exact_path` is the whole trick: playLibraryTrack normally re-resolves
 * title+artist through /api/stats/resolve-track, which is LIMIT 1, so both copies
 * of a duplicate would come back with the SAME file and play identical audio.
 * Here the file path IS the thing being compared, so it must be played verbatim.
 */
function SubitemPlayButton({
  filePath,
  trackId,
  title,
  artist,
  album,
}: {
  filePath?: string;
  trackId?: string | number;
  title?: string;
  artist?: string;
  album?: string;
}) {
  if (!filePath) return null;
  return (
    <button
      className="repair-subitem-play"
      type="button"
      title={`Play this copy: ${filePath}`}
      aria-label={`Play this copy of ${title || 'this track'}`}
      onClick={(event) => {
        // The row itself means "keep this version" - auditioning must not choose.
        event.stopPropagation();
        void window.SoulSyncWebShellBridge?.playLibraryTrack(
          {
            file_path: filePath,
            title: title || 'Unknown Track',
            id: trackId ?? '',
            exact_path: true,
          },
          album || '',
          artist || '',
        );
      }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <polygon points="5,3 19,12 5,21" />
      </svg>{' '}
      Play
    </button>
  );
}

/**
 * `openFindingArtist`. New findings carry the library artist id and navigate
 * without a lookup; older ones resolve by EXACT name, and refuse to guess when
 * there is no exact hit.
 *
 * Ids are opaque server keys — numeric on Plex, alphanumeric on
 * Navidrome/Jellyfin — so only a fully-numeric id is parsed. `Number()` on a
 * Navidrome id would navigate to "NaN".
 */
async function openFindingArtist(artistId: string | null, name: string): Promise<void> {
  if (!name) return;
  const bridge = window.SoulSyncWebShellBridge;
  if (artistId && bridge) {
    bridge.navigateToArtistDetail(
      /^\d+$/.test(artistId) ? Number.parseInt(artistId, 10) : artistId,
      name,
    );
    return;
  }
  try {
    const artists = await searchLibraryArtists(name);
    const exact = findExactArtist(artists, name);
    if (exact && bridge) {
      bridge.navigateToArtistDetail(exact.id, exact.name);
    } else {
      window.showToast?.(`"${name}" isn't in your library`, 'info');
    }
  } catch {
    window.showToast?.('Could not open artist page', 'error');
  }
}

/**
 * `_renderFindingMedia` — the album/artist thumbnail strip. The artist card is
 * clickable only when a name is present; an image that fails to load hides its
 * whole card, exactly as the vanilla's inline `onerror` did.
 */
function FindingMedia({ details }: { details: Details }) {
  const albumUrl = text(details.album_thumb_url);
  const artistUrl = text(details.artist_thumb_url);
  if (!albumUrl && !artistUrl) return null;

  const artistLabel = text(details.artist_name || details.artist) || 'Artist';
  const hasName = Boolean(details.artist_name || details.artist);
  const artistId =
    details.artist_id != null && details.artist_id !== '' ? text(details.artist_id) : null;

  return (
    <div className="repair-finding-media">
      {albumUrl ? (
        <div className="repair-finding-media-card">
          <img
            className="repair-finding-media-img"
            src={albumUrl}
            alt="Album art"
            onError={hideParent}
          />
          <span className="repair-finding-media-label">{text(details.album_title) || 'Album'}</span>
        </div>
      ) : null}
      {artistUrl ? (
        <div
          className={`repair-finding-media-card${hasName ? ' repair-finding-media-card--link' : ''}`}
          {...(hasName
            ? {
                role: 'link',
                title: `Open ${artistLabel}'s page`,
                onClick: (event: React.MouseEvent) => {
                  event.stopPropagation();
                  void openFindingArtist(artistId, artistLabel);
                },
              }
            : {})}
        >
          <img
            className="repair-finding-media-img artist"
            src={artistUrl}
            alt="Artist"
            onError={hideParent}
          />
          <span className="repair-finding-media-label">{artistLabel}</span>
        </div>
      ) : null}
    </div>
  );
}

/** The ported `onerror="this.parentElement.style.display='none'"`. */
function hideParent(event: React.SyntheticEvent<HTMLImageElement>): void {
  const parent = event.currentTarget.parentElement;
  if (parent) parent.style.display = 'none';
}

// ── The switch ───────────────────────────────────────────────────────────────

export interface FindingDetailProps {
  finding: RepairFinding;
  /** `selectDuplicateToKeep` — click a duplicate to keep that version. */
  onKeepDuplicate: (findingId: number, trackId: string) => void;
  /** `applyCoverArtTarget` — per-image apply on a cover-art finding. */
  onApplyCoverArt: (findingId: number, target: 'album' | 'artist') => void;
  /** put back what this finding's fix changed */
  onUndo?: (finding: RepairFinding) => void;
}

const DUPLICATE_PROTECTION_LABELS: Record<string, string> = {
  manual_primary: 'Your chosen primary file',
  hand_tagged: 'Hand-tagged file',
  intentional_format: 'Intentional additional format',
  derivative_source: 'Source of a retained companion',
  retention_policy: 'Retained by your quality settings',
  manual_metadata: 'Metadata edited by hand',
  pinned_release: 'Pinned release',
  canonical_reference: 'Referenced by another recording entry',
  shared_file_reference: 'Shared file reference',
  playlist_reference: 'Used in a playlist',
  media_server_reference: 'Referenced by a media server',
  file_unavailable: 'File currently unavailable',
};

function NativeDuplicateReview({
  finding,
  onKeepDuplicate,
}: Pick<FindingDetailProps, 'finding' | 'onKeepDuplicate'>) {
  const [confirmedRecording, setConfirmedRecording] = useState(false);
  const details = finding.details || {};
  const tracks = list<{
    file_id: number;
    track_id: number;
    title?: string;
    artist?: string;
    album?: string;
    file_path?: string;
    format?: string;
    bitrate?: number;
    duration?: number;
    playlists?: string[];
    protected_reasons?: string[];
  }>(details.tracks);
  const needsConfirmation = Boolean(details.requires_recording_confirmation);
  const approvedRecording = !needsConfirmation || confirmedRecording;
  const choose = (action: string) =>
    onKeepDuplicate(
      finding.id,
      `${action}${needsConfirmation && confirmedRecording ? ':confirmed' : ''}`,
    );
  if (details.schema !== 'native_duplicate_review/v1' || !tracks.length) {
    return <p>This duplicate review needs a fresh native scan before it can be applied.</p>;
  }
  return (
    <>
      <FindingMedia details={details} />
      <p>
        Compare the copies, then keep a file. Redundant copies move to recoverable quarantine;
        protected files stay.
      </p>
      {needsConfirmation ? (
        <label>
          <input
            type="checkbox"
            checked={confirmedRecording}
            onChange={(event) => setConfirmedRecording(event.target.checked)}
          />{' '}
          I confirm these files contain the same recording
        </label>
      ) : null}
      <div className="repair-detail-sublist">
        {tracks.map((track) => {
          const isBest = track.file_id === details.recommended_file_id;
          const protectedReasons = track.protected_reasons || [];
          const protectedCopy = protectedReasons.length > 0;
          return (
            <div
              className={`repair-detail-subitem ${isBest ? 'best' : protectedCopy ? 'protected' : 'removable'}`}
              key={track.file_id}
            >
              <div className="repair-subitem-body">
                <strong>
                  <span
                    className={
                      isBest || protectedCopy ? 'repair-keep-badge' : 'repair-remove-badge'
                    }
                  >
                    {isBest ? 'KEEP' : protectedCopy ? 'PROTECTED' : 'QUARANTINE'}
                  </span>{' '}
                  {text(track.title)} by {text(track.artist)}
                </strong>
                <span>
                  Album: {text(track.album) || 'Unknown'}
                  {track.format ? ` · ${track.format.toUpperCase()}` : ''}
                  {track.bitrate ? ` · ${track.bitrate} kbps` : ''}
                  {track.duration ? ` · ${formatDurationMs(track.duration)}` : ''}
                </span>
                {track.file_path ? <span className="mono">{track.file_path}</span> : null}
                {protectedReasons.length ? (
                  <span>
                    Preserved:{' '}
                    {protectedReasons
                      .map((reason) => DUPLICATE_PROTECTION_LABELS[reason] || 'Protected copy')
                      .join(', ')}
                  </span>
                ) : null}
                {track.playlists?.length ? (
                  <span className="repair-in-playlist">
                    In playlist: {track.playlists.join(', ')}
                  </span>
                ) : null}
                <button
                  type="button"
                  className="repair-btn repair-btn-fix"
                  disabled={!approvedRecording || protectedReasons.includes('file_unavailable')}
                  onClick={() => choose(`file-${track.file_id}`)}
                >
                  Keep this file
                </button>
              </div>
              <SubitemPlayButton
                filePath={track.file_path}
                trackId={`lib2:${track.track_id}`}
                title={track.title}
                artist={track.artist}
                album={track.album}
              />
            </div>
          );
        })}
      </div>
      <button
        type="button"
        className="repair-btn repair-btn-fix"
        disabled={!approvedRecording}
        onClick={() => choose('keep_best')}
      >
        Keep Best
      </button>
    </>
  );
}

function CatalogueDuplicateReview({
  finding,
  onKeepDuplicate,
}: Pick<FindingDetailProps, 'finding' | 'onKeepDuplicate'>) {
  const details = finding.details || {};
  const albums = list<{
    album_id: number;
    title: string;
    artist_name: string;
    release_date?: string;
    file_count: number;
    provider_ids?: Record<string, string>;
    tracks?: {
      id: number;
      title: string;
      duration?: number;
      isrc?: string;
      evidence_source?: string;
    }[];
  }>(details.albums);
  if (details.schema !== 'native_duplicate_releases/v1' || albums.length !== 2) {
    return <p>This release review needs a fresh scan before it can be applied.</p>;
  }
  return (
    <>
      <p>
        Select the release group to keep. Both provider editions, all audio files and Wishlist
        entries are retained under that release.
      </p>
      {list<string>(details.blocked_reasons).map((reason) => (
        <p key={reason}>{reason}</p>
      ))}
      {albums.map((album) => (
        <section className="repair-detail-subitem" key={album.album_id}>
          <strong>
            {album.title} · {album.artist_name} · #{album.album_id}
          </strong>
          <p>
            {album.release_date || 'Date unknown'} · {album.file_count} files
          </p>
          {Object.entries(album.provider_ids || {}).map(([provider, id]) => (
            <p key={provider}>
              {provider}: {id}
            </p>
          ))}
          {list<NonNullable<typeof album.tracks>[number]>(album.tracks).map((track) => (
            <p key={track.id}>
              {track.title}
              {track.duration ? ' · ' + formatDurationMs(track.duration) : ''}
              {track.isrc ? ' · ISRC ' + track.isrc : ' · Recording identity unavailable'}
              {track.evidence_source?.startsWith('cache:') ? ' · Confirmed provider cache' : ''}
            </p>
          ))}
          <button
            type="button"
            className="repair-btn"
            aria-label={'Merge into release #' + album.album_id}
            disabled={details.merge_eligible !== true}
            onClick={() => onKeepDuplicate(finding.id, 'merge-' + album.album_id)}
          >
            Merge into release #{album.album_id}
          </button>
        </section>
      ))}
    </>
  );
}

export function FindingDetail(props: FindingDetailProps) {
  const { finding } = props;
  const copy = describeFinding(finding);
  const names = new Set([copy.title, ...copy.sub.split(' · ')].filter(Boolean));
  const path = finding.file_path || '';
  return (
    <SaidAlready.Provider value={{ path, names }}>
      <FindingDetailBody {...props} />
    </SaidAlready.Provider>
  );
}

function FindingDetailBody({
  finding,
  onKeepDuplicate,
  onApplyCoverArt,
  onUndo,
}: FindingDetailProps) {
  const d = (finding.details || {}) as Details;
  const rows: FindingDetailRow[] = [];
  const media = <FindingMedia details={d} />;

  switch (finding.finding_type) {
    case 'native_duplicate_releases':
      return <CatalogueDuplicateReview finding={finding} onKeepDuplicate={onKeepDuplicate} />;
    case 'native_duplicate_tracks':
      return (
        <NativeDuplicateReview
          key={finding.id}
          finding={finding}
          onKeepDuplicate={onKeepDuplicate}
        />
      );
    case 'genre_cleanup': {
      // #1057 — show exactly what stays and what goes; the fix applies
      // kept_genres verbatim, so this IS the contract.
      const kept = list<string>(d.kept_genres);
      const removed = list<string>(d.removed_genres);
      rows.push([
        'Kept',
        kept.length ? kept.join(', ') : '— none (all genres are off your whitelist)',
      ]);
      rows.push(['Removed', removed.join(', ')]);
      if (d.entity) {
        rows.push(['Applies to', d.entity === 'artist' ? 'Artist genres' : 'Album genres']);
      }
      return (
        <>
          {media}
          <TagChangeTable
            rows={[
              {
                key: 'genre',
                label: 'Genre',
                before: [...kept, ...removed].join(', '),
                after: kept.join(', '),
              },
            ]}
            beforeLabel="Now"
            afterLabel="After Clean Genres"
          />
          <DetailGrid rows={rows} />
        </>
      );
    }

    case 'genre_enrichment': {
      const proposed = list<string>(d.proposed_genres);
      const added = list<string>(d.added_genres);
      const ambiguous = list<{
        raw_genre?: unknown;
        raw?: unknown;
        candidates?: unknown[];
        score?: unknown;
      }>(d.ambiguous_genres);
      const omitted = list<string>(d.omitted_due_to_cap);
      const rejected = list<string>(d.rejected_genres);
      rows.push(['Current genres', list<string>(d.original_genres).join(', ') || '— none']);
      rows.push(['Proposed genres', proposed.join(', ') || '— none']);
      rows.push(['Added', added.join(', ') || '— none']);
      rows.push(['Omitted at cap', omitted.join(', ') || '— none']);
      rows.push(['Rejected', rejected.join(', ') || '— none']);
      if (ambiguous.length) {
        rows.push([
          'Ambiguous',
          ambiguous
            .map((item) => {
              const candidates = list<string>(item.candidates).join(', ');
              const score = Math.round((Number(item.score) || 0) * 100);
              return `${text(item.raw_genre || item.raw)}: ${candidates} (${score}%)`;
            })
            .join('; '),
        ]);
      }
      if (d.sources && typeof d.sources === 'object') {
        rows.push([
          'Sources',
          Object.entries(d.sources as Record<string, unknown>)
            .map(([genre, sources]) => `${genre}: ${list<string>(sources).join(', ')}`)
            .join('; '),
        ]);
      }
      if (d.cache_stats && typeof d.cache_stats === 'object') {
        const stats = d.cache_stats as Record<string, unknown>;
        rows.push([
          'Cache / external calls',
          `metadata ${text(stats.metadata_cache_hits) || '0'}, live ${text(stats.live_calls) || '0'}`,
        ]);
      }
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
        </>
      );
    }

    case 'comma_artist_split': {
      // jadux — the contract: exactly what the artist tag becomes.
      const parts = list<string>(d.split_artists);
      const artistChanges: TagChange[] = [
        {
          key: 'artist',
          label: 'Artist',
          before: text(d.combined_name),
          after: text(d.new_display_artist) || parts.join('; '),
        },
      ];
      if (d.primary_artist) {
        artistChanges.push({
          key: 'albumartist',
          label: 'Album artist',
          before: text(d.combined_name),
          after: text(d.primary_artist),
        });
        rows.push(['Album artist', 'Only changes where it was the combined name']);
      }
      const checked = list<string>(d.checked_sources);
      if (checked.length) rows.push(['Checked against', checked.join(', ')]);
      if (d.track_count != null) rows.push(['Tracks affected', text(d.track_count)]);

      const chips = commaSplitChips(d);
      const tracks = list<{ title?: string; album?: string; file_path?: string }>(d.tracks);
      const trackCount = Number(d.track_count) || 0;

      return (
        <>
          {media}
          <TagChangeTable
            rows={artistChanges}
            beforeLabel="In the files now"
            afterLabel="After Split Artists"
          />
          <DetailGrid rows={rows} />
          <div style={{ margin: '8px 0 2px', fontWeight: 600 }}>Splits into</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '4px' }}>
            {chips.map((chip, index) =>
              chip.inLibrary ? (
                <span
                  key={`${chip.name}-${index}`}
                  role="link"
                  title={`Open ${chip.name}'s page`}
                  style={{ ...CHIP_BASE, cursor: 'pointer', textDecoration: 'underline' }}
                  onClick={(event) => {
                    event.stopPropagation();
                    void openFindingArtist(chip.libraryArtistId, chip.name);
                  }}
                >
                  {chip.name}
                  {chip.mark}
                </span>
              ) : (
                <span key={`${chip.name}-${index}`} style={CHIP_BASE}>
                  {chip.name}
                  {chip.mark}
                </span>
              ),
            )}
          </div>
          {tracks.length ? (
            <>
              <div style={{ margin: '8px 0 2px', fontWeight: 600 }}>Tracks</div>
              {tracks.slice(0, COMMA_SPLIT_TRACK_CAP).map((track, index) => {
                const label = [track.title, track.album].filter(Boolean).join(' — ');
                return (
                  <div
                    className="repair-finding-meta"
                    style={{ margin: '0 0 2px' }}
                    key={`${track.file_path || label}-${index}`}
                  >
                    {label || (track.file_path || '').split(/[\\/]/).pop() || 'Unknown'}
                  </div>
                );
              })}
              {trackCount > tracks.length ? (
                <div className="repair-finding-meta" style={{ marginTop: '6px' }}>
                  …and {trackCount - tracks.length} more track(s)
                </div>
              ) : null}
            </>
          ) : null}
          <div className="repair-finding-meta" style={{ marginTop: '8px' }}>
            Your media server picks up the split on its next scan.
          </div>
        </>
      );
    }

    case 'dead_file':
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album, 'Album');
      pushIf(rows, d.title, 'Title');
      pushIf(rows, d.track_id, 'Track ID');
      pushIf(rows, d.original_path, 'Original Path', 'path');
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );

    case 'orphan_file':
      // No media strip on this one — an orphan has no matched album or artist.
      pushIf(rows, d.folder, 'Folder', 'path');
      if (d.format) rows.push(['Format', text(d.format).toUpperCase()]);
      if (d.file_size) rows.push(['File Size', formatFileSize(Number(d.file_size))]);
      pushIf(rows, d.modified, 'Last Modified');
      pushIf(rows, finding.file_path, 'Full Path', 'path');
      return (
        <>
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );

    case 'library_retag': {
      const checks =
        d.validation && typeof d.validation === 'object' && 'checks' in d.validation
          ? d.validation.checks
          : null;
      // a fix that ran: every tag as it was and as it is now, and the way back
      const applied = finding.status !== 'pending' ? readApplied(d) : null;
      if (applied) {
        return (
          <div className="tag-history">
            <p className="repair-finding-fixline">
              Apply Tags changed this file{applied.at ? ` ${timeAgo(applied.at)}` : ''}.
            </p>
            {applied.files.map((file, index) => (
              <Fragment key={`${file.path}-${index}`}>
                {applied.files.length > 1 ? (
                  <div className="repair-finding-path">{file.path}</div>
                ) : null}
                <TagChangeTable rows={file.fields} beforeLabel="Was" afterLabel="Now" />
                {file.cover ? (
                  <p className="tag-history-note">
                    {file.cover === 'added'
                      ? "The library's cover was embedded. The file had none."
                      : "The embedded cover was replaced with the library's."}
                  </p>
                ) : null}
              </Fragment>
            ))}
            {onUndo ? (
              <div className="tag-history-actions">
                <button
                  className="repair-finding-btn undo"
                  type="button"
                  onClick={() => onUndo(finding)}
                >
                  Undo changes
                </button>
                <span className="tag-history-note">
                  Writes the old values back and reopens this finding.
                </span>
              </div>
            ) : null}
          </div>
        );
      }
      const preview = readTagChanges(finding.preview);
      // the table says these already; the grid keeps what it does not
      const shown = new Set<string>();
      for (const row of preview) {
        shown.add(
          row.key === 'album_artist' ? 'albumartist' : row.key === 'cover_art' ? 'cover' : row.key,
        );
        if (row.key === 'cover_sidecar') shown.add('artwork_sidecar');
      }
      const undone = readUndone(d);
      const undoneNote = undone ? (
        <p className="tag-history-note">
          Undone{undone.at ? ` ${timeAgo(undone.at)}` : ''}. The old tags are back in the file.
          {undone.coverLeft ? ' The cover that was embedded is still there.' : ''}
          {undone.kept.length
            ? ` ${undone.kept.join(', ')} had no value before and ${undone.kept.length === 1 ? 'was' : 'were'} left in place.`
            : ''}
        </p>
      ) : null;
      const checksGrid =
        checks && typeof checks === 'object' ? (
          <DetailGrid rows={retagCheckRows(checks as Record<string, unknown>, d, shown)} />
        ) : null;
      if (preview.length)
        return (
          <>
            <TagChangeTable
              rows={preview}
              beforeLabel="In the file now"
              afterLabel="After Apply Tags"
            />
            {checksGrid}
            {undoneNote}
          </>
        );
      // G16-2: what Apply Tags writes comes first, every field of it
      const changes = nativeRetagChanges(d.diff);
      if (changes.length)
        return (
          <>
            <div className="repair-finding-meta" style={{ marginBottom: '8px' }}>
              Apply Tags will write
            </div>
            <DetailGrid rows={changes} />
            {checksGrid ? (
              <>
                <div className="repair-finding-meta" style={{ margin: '12px 0 8px' }}>
                  Tag checks
                </div>
                {checksGrid}
              </>
            ) : null}
          </>
        );
      if (checksGrid) return checksGrid;
      const retag = libraryRetagDetail(d);
      if (!retag.meta && !retag.coverOnly && !retag.tracks.length && !retag.unmatched.length) {
        return <div className="repair-finding-desc">No changes.</div>;
      }
      return (
        <>
          {retag.meta ? (
            <div className="repair-finding-meta" style={{ marginBottom: '8px' }}>
              {retag.meta}
            </div>
          ) : null}
          {retag.coverOnly ? (
            <div className="repair-finding-desc">
              Tags already correct — this would refresh cover art only.
            </div>
          ) : null}
          {retag.tracks.map((track, index) => (
            <Fragment key={`${track.label}-${index}`}>
              <div style={{ margin: '8px 0 2px', fontWeight: 600 }}>{track.label}</div>
              {/* The physical filename, so a wrong match is easy to spot before
                  applying. Skipped when the label already IS the filename. */}
              {track.filename ? (
                <div
                  className="repair-finding-meta"
                  style={{ margin: '0 0 4px', opacity: 0.7, wordBreak: 'break-all' }}
                >
                  {track.filename}
                </div>
              ) : null}
              <DetailGrid rows={track.rows} />
            </Fragment>
          ))}
          {retag.overflow > 0 ? (
            <div className="repair-finding-meta" style={{ marginTop: '6px' }}>
              …and {retag.overflow} more track(s)
            </div>
          ) : null}
          {retag.unmatched.length ? (
            <div className="repair-finding-meta" style={{ marginTop: '8px' }}>
              Unmatched (left untouched): {retag.unmatched.join(', ')}
            </div>
          ) : null}
        </>
      );
    }

    case 'acoustid_mismatch':
      pushIf(rows, finding.file_path, 'File', 'path');
      return (
        <>
          {media}
          <TagChangeTable
            rows={[
              {
                key: 'title',
                label: 'Title',
                before: text(d.expected_title),
                after: text(d.acoustid_title),
              },
              {
                key: 'artist',
                label: 'Artist',
                before: text(d.expected_artist),
                after: text(d.acoustid_artist),
              },
            ]}
            beforeLabel="Tagged as"
            afterLabel="The audio matches"
          />
          <div style={{ marginBottom: '8px' }}>
            <ScoreBar value={d.fingerprint_score} label="Fingerprint" />
            <ScoreBar value={d.title_similarity} label="Title Match" />
            <ScoreBar value={d.artist_similarity} label="Artist Match" />
          </div>
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );

    case 'acoustid_no_match':
      pushIf(rows, d.expected_title, 'Expected Title');
      pushIf(rows, d.expected_artist, 'Expected Artist');
      pushIf(rows, finding.file_path, 'File', 'path');
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );

    case 'fake_lossless': {
      const spectrum = fakeLosslessSpectrum(d);
      if (d.format) rows.push(['Format', text(d.format).toUpperCase()]);
      if (d.sample_rate) rows.push(['Sample Rate', `${text(d.sample_rate)} Hz`]);
      if (d.bit_depth) rows.push(['Bit Depth', `${text(d.bit_depth)}-bit`]);
      if (d.bitrate) rows.push(['Bitrate', `${text(d.bitrate)} kbps`]);
      if (d.file_size) rows.push(['File Size', formatFileSize(Number(d.file_size))]);
      pushIf(rows, finding.file_path, 'File', 'path');
      // No media strip here — the vanilla builds this one from flHtml only.
      return (
        <>
          {spectrum ? (
            <div className="repair-spectrum-bar">
              <div className="repair-spectrum-label">Spectral Analysis</div>
              <div className="repair-spectrum-track">
                <div
                  className="repair-spectrum-detected"
                  style={{ width: `${spectrum.cutoffPct}%` }}
                />
                <div
                  className="repair-spectrum-expected"
                  style={{ left: `${spectrum.expectedPct}%` }}
                />
              </div>
              <div className="repair-spectrum-legend">
                <span className="repair-spectrum-legend-detected">
                  {spectrum.cutoff} kHz detected
                </span>
                <span className="repair-spectrum-legend-expected">
                  {spectrum.expectedMin} kHz expected min
                </span>
              </div>
            </div>
          ) : null}
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );
    }

    case 'duplicate_tracks': {
      const tracks = list<{
        id?: string | number;
        track_id?: string | number;
        title?: string;
        artist?: string;
        album?: string;
        file_path?: string;
        bitrate?: number;
        duration?: number;
        track_number?: number;
        playlists?: string[];
      }>(d.tracks);
      if (!tracks.length) {
        return <DetailGrid rows={[['Count', text(d.count) || '?']]} />;
      }
      // Best copy by the same ranking as the backend (core/library/duplicate_keep.py):
      // lossless format first, so a FLAC beats an MP3 even when the FLAC's bitrate
      // is missing, then bitrate, duration, track number.
      const best = pickDuplicateToKeep(tracks);
      return (
        <>
          {media}
          <div className="repair-detail-sublist">
            {tracks.map((track, index) => {
              const trackId = track.track_id ?? track.id;
              const isBest = track.id === best?.id;
              return (
                // G16-3: read-only. 3.6.1's fixer for these is gone (the
                // native Duplicate Detector replaced it), so a click asked for
                // a destructive choice the server could only refuse
                <div
                  className={`repair-detail-subitem ${isBest ? 'best' : 'removable'}${
                    track.file_path ? ' playable' : ''
                  }`}
                  key={`${trackId ?? index}`}
                >
                  <div className="repair-subitem-body">
                    <strong>
                      {isBest ? (
                        <span className="repair-keep-badge">KEEP</span>
                      ) : (
                        <span className="repair-remove-badge">REMOVE</span>
                      )}{' '}
                      {text(track.title)} by {text(track.artist)}
                    </strong>
                    <span>
                      Album: {text(track.album) || 'Unknown'}
                      {track.bitrate ? ` · ${track.bitrate} kbps` : ''}
                      {track.duration ? ` · ${formatDurationMs(track.duration)}` : ''}
                      {track.track_number ? ` · Track #${track.track_number}` : ''}
                    </span>
                    {track.file_path ? <span className="mono">{track.file_path}</span> : null}
                    {track.playlists?.length ? (
                      <span className="repair-in-playlist">
                        In playlist: {track.playlists.join(', ')}
                      </span>
                    ) : null}
                  </div>
                  <SubitemPlayButton
                    filePath={track.file_path}
                    trackId={trackId}
                    title={text(track.title)}
                    artist={text(track.artist)}
                    album={text(track.album)}
                  />
                </div>
              );
            })}
          </div>
          <div style={{ color: 'var(--white-a30)', fontSize: '11px', padding: '4px 0' }}>
            Found before the library upgrade. Run the Duplicate Detector on this page to review
            these copies with today&apos;s checks (it moves extras to quarantine, never deletes);
            then dismiss this one.
          </div>
        </>
      );
    }

    case 'incomplete_album': {
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album_title, 'Album');
      if (d.primary_source && d.primary_album_id) {
        const source = text(d.primary_source);
        rows.push([
          `${source.charAt(0).toUpperCase()}${source.slice(1)} ID`,
          text(d.primary_album_id),
        ]);
        if (d.spotify_album_id && d.primary_source !== 'spotify') {
          rows.push(['Spotify ID', text(d.spotify_album_id)]);
        }
      } else if (d.spotify_album_id) {
        rows.push(['Spotify ID', text(d.spotify_album_id)]);
      }
      const completion = incompleteAlbumCompletion(d);
      const missing = list<{
        track_number?: number;
        name?: string;
        title?: string;
        source?: string;
        source_track_id?: string;
        duration_ms?: number;
      }>(d.missing_tracks);
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          {completion ? (
            <div className="repair-completion-bar">
              <div className="repair-completion-label">
                {completion.actual} of {completion.expected} tracks ({completion.percent}%)
              </div>
              <div className="repair-completion-track">
                <div
                  className="repair-completion-fill"
                  style={{ width: `${completion.percent}%` }}
                />
              </div>
            </div>
          ) : null}
          {missing.length ? (
            <div className="repair-detail-sublist">
              {missing.map((track, index) => (
                <div className="repair-detail-subitem" key={`${track.name || index}-${index}`}>
                  <strong>
                    #{track.track_number || '?'} {track.name || track.title || 'Unknown'}
                  </strong>
                  {track.source && track.source !== 'spotify' ? (
                    <span>
                      Source: {track.source}
                      {track.source_track_id ? ` · ID: ${track.source_track_id}` : ''}
                    </span>
                  ) : null}
                  {track.duration_ms ? <span>{formatDurationMs(track.duration_ms)}</span> : null}
                </div>
              ))}
            </div>
          ) : null}
        </>
      );
    }

    case 'path_mismatch':
      if (d.from && d.to)
        return (
          <TagChangeTable
            rows={[{ key: 'location', label: 'Location', before: text(d.from), after: text(d.to) }]}
            beforeLabel="Where it is"
            afterLabel="After Reorganize"
          />
        );
      pushIf(rows, d.from, 'Current Path', 'path');
      pushIf(rows, d.to, 'Expected Path', 'success');
      return <DetailGrid rows={rows} />;

    case 'metadata_gap': {
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album, 'Album');
      pushIf(rows, d.title, 'Title');
      pushIf(rows, d.spotify_track_id, 'Spotify ID');
      pushIf(rows, d.resolved_source, 'Resolved Source');
      pushIf(rows, d.resolved_track_id, 'Resolved Track ID');
      const found = d.found_fields;
      const filled: TagChange[] =
        found && typeof found === 'object'
          ? Object.entries(found as Record<string, unknown>).map(([key, value]) => ({
              key: `found_${key}`,
              label: /^(isrc|bpm|upc)$/.test(key) ? key.toUpperCase() : tagCheckLabel(key),
              before: '',
              after: text(value),
            }))
          : [];
      return (
        <>
          {media}
          <TagChangeTable
            rows={filled}
            beforeLabel="In the library now"
            afterLabel="After Auto-Fill"
          />
          <DetailGrid rows={rows} />
        </>
      );
    }

    case 'missing_cover_art': {
      for (const [key, label] of [
        ['db_missing', 'In the library'],
        ['embed_missing', 'In the audio files'],
        ['sidecar_missing', 'In the album folder'],
      ]) {
        if (typeof d[key] === 'boolean')
          rows.push([label, d[key] ? 'No cover' : 'Has a cover', d[key] ? 'warning' : undefined]);
      }
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album_title, 'Album');
      pushIf(rows, d.spotify_album_id, 'Spotify ID');
      const foundArtwork = text(d.found_artwork_url);
      const currentArtist = text(d.artist_thumb_url);
      const foundArtist = text(d.found_artist_url);
      // Each found image is independently applyable (Pache711: "fix one, dismiss
      // the other") — the right album art can be taken while a wrong artist image
      // is skipped, or the reverse.
      return (
        <>
          {currentArtist || foundArtwork || foundArtist ? (
            <div className="repair-finding-media">
              {foundArtwork ? (
                <div className="repair-finding-media-card">
                  <img
                    className="repair-finding-media-img"
                    src={foundArtwork}
                    alt="Found album art"
                    onError={hideParent}
                  />
                  <span className="repair-finding-media-label">Found Album Art</span>
                  <button
                    className="repair-art-apply-btn"
                    type="button"
                    onClick={() => onApplyCoverArt(finding.id, 'album')}
                  >
                    Use for album
                  </button>
                </div>
              ) : null}
              {/* Current artist image — context only, no apply: it's what's there now. */}
              {currentArtist ? (
                <div className="repair-finding-media-card">
                  <img
                    className="repair-finding-media-img artist"
                    src={currentArtist}
                    alt="Current artist art"
                    onError={hideParent}
                  />
                  <span className="repair-finding-media-label">
                    {text(d.artist) || 'Artist'} (current)
                  </span>
                </div>
              ) : null}
              {foundArtist ? (
                <div className="repair-finding-media-card">
                  <img
                    className="repair-finding-media-img artist"
                    src={foundArtist}
                    alt="Found artist art"
                    onError={hideParent}
                  />
                  <span className="repair-finding-media-label">Found Artist Art</span>
                  <button
                    className="repair-art-apply-btn"
                    type="button"
                    onClick={() => onApplyCoverArt(finding.id, 'artist')}
                  >
                    Use for artist
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
          <DetailGrid rows={rows} />
        </>
      );
    }

    case 'missing_lyrics':
      pushIf(rows, d.track_title, 'Track');
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album_title, 'Album');
      return <DetailGrid rows={rows} />;

    case 'missing_replaygain':
      pushIf(rows, d.track_title, 'Track');
      pushIf(rows, d.artist, 'Artist');
      return <DetailGrid rows={rows} />;

    case 'zero_length_mp4':
      pushIf(rows, d.track_title, 'Track');
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.file_path, 'File', 'path');
      return <DetailGrid rows={rows} />;

    case 'untagged_download':
      pushIf(rows, d.track_title, 'Track');
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album_title, 'Album');
      if (list<string>(d.missing).length)
        rows.push(['Missing tags', list<string>(d.missing).join(', ')]);
      pushIf(rows, d.file_path, 'File', 'path');
      return <DetailGrid rows={rows} />;

    case 'library_unreachable': {
      const outage = (d.outage || {}) as Record<string, unknown>;
      pushIf(rows, outage.unreachable, 'Files not reached');
      pushIf(rows, outage.checked, 'Files checked');
      pushIf(rows, outage.example_path, 'Example', 'path');
      return <DetailGrid rows={rows} />;
    }

    case 'empty_folder': {
      pushIf(rows, d.folder_path, 'Folder', 'path');
      const junk = list<string>(d.junk_files);
      if (junk.length) rows.push(['Junk files', junk.join(', ')]);
      return <DetailGrid rows={rows} />;
    }

    case 'expired_download':
      pushIf(rows, d.title, 'Track');
      pushIf(rows, d.artist, 'Artist');
      if (d.origin) {
        rows.push([
          'Source',
          `${text(d.origin)}${d.origin_context ? ` — ${text(d.origin_context)}` : ''}`,
        ]);
      }
      if (d.file_path) rows.push(['File', text(d.file_path).split(/[\\/]/).pop() || '']);
      return <DetailGrid rows={rows} />;

    case 'track_number_mismatch': {
      pushIf(rows, d.album_title, 'Album');
      pushIf(rows, d.artist_name, 'Artist');
      pushIf(rows, d.matched_title, 'Matched To');
      pushIf(rows, d.file_title, 'File Title');
      // These two are `!== undefined`, not truthy — track 0 must still show.
      const numberChange: TagChange[] =
        d.current_track_num !== undefined && d.correct_track_num !== undefined
          ? [
              {
                key: 'track_number',
                label: 'Track number',
                before: text(d.current_track_num),
                after: text(d.correct_track_num),
              },
            ]
          : [];
      pushIf(rows, finding.file_path, 'File', 'path');
      const changes = list<string>(d.changes);
      return (
        <>
          {media}
          <TagChangeTable
            rows={numberChange}
            beforeLabel="In the file now"
            afterLabel="After Apply"
          />
          {d.match_score ? (
            <div style={{ marginBottom: '8px' }}>
              <ScoreBar value={d.match_score} label="Title Match" />
            </div>
          ) : null}
          <DetailGrid rows={rows} />
          {changes.length ? (
            <div className="repair-detail-sublist">
              {changes.map((change, index) => (
                <div className="repair-detail-subitem" key={`${change}-${index}`}>
                  <strong>{change}</strong>
                </div>
              ))}
            </div>
          ) : null}
          <PlayButton finding={finding} />
        </>
      );
    }

    case 'short_preview_track':
      pushIf(rows, d.artist, 'Artist');
      pushIf(rows, d.album, 'Album');
      pushIf(rows, d.title, 'Title');
      // `!= null`, not truthy — a 0s file length is the whole point of the finding.
      if (d.file_duration_s != null) {
        rows.push(['File Length', `${text(d.file_duration_s)}s`, 'warning']);
      }
      if (d.expected_duration_s != null) {
        rows.push(['Real Length', `${text(d.expected_duration_s)}s`, 'success']);
      }
      pushIf(rows, d.original_path, 'Path', 'path');
      // The play button lets the user hear the clip and confirm it really is a
      // busted ~30s preview before approving the delete + re-download.
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );

    case 'suspect_album_tag': {
      pushIf(rows, d.track_title, 'Track');
      pushIf(rows, d.artist_name, 'Artist');
      pushIf(rows, d.album_title, 'Current Album');
      const reasons = list<string>(d.reasons);
      if (reasons.length) {
        rows.push(['Reasons', reasons.join('; '), 'warning']);
      }
      if (d.full_track_count) {
        rows.push(['Full Release Tracks', text(d.full_track_count)]);
      }
      // an album finding has no single search, its fix is per track
      if (finding.entity_type !== 'album') pushIf(rows, d.reidentify_query, 'Search Query');
      if (finding.file_path) {
        rows.push(['File', finding.file_path, 'path']);
      }
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          <PlayButton finding={finding} />
        </>
      );
    }

    case 'canonical_version': {
      // the editions the review weighed, best first, in place of the raw
      // payload (fingerprints, row ids, fit ratios) this used to print
      const candidates = list<{ source?: unknown; score?: unknown; track_count?: unknown }>(
        d.candidates,
      );
      const owned = Number(d.file_track_count) || 0;
      if (owned) rows.push(['You have', `${owned} track${owned === 1 ? '' : 's'}`]);
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
          {candidates.length ? (
            <div className="repair-detail-sublist">
              {candidates.map((candidate, index) => {
                const percent = Math.round((Number(candidate.score) || 0) * 100);
                const count = Number(candidate.track_count) || 0;
                return (
                  <div className="repair-detail-subitem" key={`${text(candidate.source)}-${index}`}>
                    <div className="repair-subitem-body">
                      <strong>
                        {index === 0 ? <span className="repair-keep-badge">BEST FIT</span> : null}
                        {providerName(text(candidate.source))} release
                      </strong>
                      <span>
                        {percent}% match
                        {count ? ` · lists ${count} track${count === 1 ? '' : 's'}` : ''}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
        </>
      );
    }

    default: {
      // Generic: every scalar detail key, then the file path.
      const generic = genericDetailRows(d);
      if (finding.file_path) generic.push(['File', finding.file_path, 'path']);
      return (
        <>
          {media}
          {generic.length ? (
            <DetailGrid rows={generic} />
          ) : (
            <span style={{ color: 'var(--white-a30)', fontSize: '12px' }}>
              No additional details available
            </span>
          )}
        </>
      );
    }
  }
}

const PROVIDERS: Record<string, string> = {
  musicbrainz: 'MusicBrainz',
  discogs: 'Discogs',
  deezer: 'Deezer',
  spotify: 'Spotify',
  itunes: 'Apple Music',
  qobuz: 'Qobuz',
  tidal: 'Tidal',
  lastfm: 'Last.fm',
};
const providerName = (id: string) => PROVIDERS[id.toLowerCase()] || id || 'Unknown';

const CHIP_BASE = {
  padding: '3px 10px',
  borderRadius: '12px',
  background: 'rgba(255,255,255,.07)',
  fontSize: '12px',
} as const;
