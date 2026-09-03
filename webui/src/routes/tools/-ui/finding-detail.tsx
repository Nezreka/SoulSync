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

import { Fragment } from 'react';

import type { FindingDetailRow } from '../-tools.core';
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
  pickDuplicateToKeep,
  scoreBar,
} from '../-tools.core';
// tracks.duration is stored in MILLISECONDS (music_database.py:424); this
// panel used to print it raw with an "s" suffix, so a 3:58 song read
// "238000s" (#1210).
// The artist-detail `enhanced` family is gone on this branch (Library v2
// replaced it); the same helper still lives beside the sync server view.
import { formatDurationMs } from '../../sync/-sync.server';

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
export function DetailGrid({ rows }: { rows: FindingDetailRow[] }) {
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
}

export function FindingDetail({ finding, onKeepDuplicate, onApplyCoverArt }: FindingDetailProps) {
  const d = (finding.details || {}) as Details;
  const rows: FindingDetailRow[] = [];
  const media = <FindingMedia details={d} />;

  switch (finding.finding_type) {
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
      rows.push(['Current artist tag', text(d.combined_name)]);
      rows.push(['New artist tag', text(d.new_display_artist) || parts.join('; ')]);
      if (d.primary_artist) {
        rows.push([
          'Album artist',
          `becomes "${text(d.primary_artist)}" (only where it was the combined name)`,
        ]);
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
                  📄 {track.filename}
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
      rows.push(['Expected Title', text(d.expected_title) || '-']);
      rows.push(['Expected Artist', text(d.expected_artist) || '-']);
      rows.push(['AcoustID Title', text(d.acoustid_title) || '-', 'highlight']);
      rows.push(['AcoustID Artist', text(d.acoustid_artist) || '-', 'highlight']);
      pushIf(rows, finding.file_path, 'File', 'path');
      return (
        <>
          {media}
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
                <div
                  className={`repair-detail-subitem ${isBest ? 'best' : 'removable'}${
                    track.file_path ? ' playable' : ''
                  }`}
                  style={{ cursor: 'pointer' }}
                  title="Click to keep this version"
                  key={`${trackId ?? index}`}
                  onClick={() => onKeepDuplicate(finding.id, String(trackId))}
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
          <div style={{ color: 'rgba(255,255,255,0.3)', fontSize: '11px', padding: '4px 0' }}>
            Play to compare, click a version to keep it, or use &quot;Keep Best&quot; for
            auto-selection
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
                  {track.duration_ms ? (
                    <span>Duration: {Math.round(track.duration_ms / 1000)}s</span>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </>
      );
    }

    case 'path_mismatch':
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
      if (found && typeof found === 'object') {
        Object.entries(found as Record<string, unknown>).forEach(([key, value]) => {
          rows.push([`Found: ${key}`, String(value), 'success']);
        });
      }
      return (
        <>
          {media}
          <DetailGrid rows={rows} />
        </>
      );
    }

    case 'missing_cover_art': {
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
      if (d.current_track_num !== undefined) {
        rows.push(['Current Track #', text(d.current_track_num)]);
      }
      if (d.correct_track_num !== undefined) {
        rows.push(['Correct Track #', text(d.correct_track_num), 'success']);
      }
      pushIf(rows, finding.file_path, 'File', 'path');
      const changes = list<string>(d.changes);
      return (
        <>
          {media}
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
            <span style={{ color: 'rgba(255,255,255,0.3)', fontSize: '12px' }}>
              No additional details available
            </span>
          )}
        </>
      );
    }
  }
}

const CHIP_BASE = {
  padding: '3px 10px',
  borderRadius: '12px',
  background: 'rgba(255,255,255,.07)',
  fontSize: '12px',
} as const;
