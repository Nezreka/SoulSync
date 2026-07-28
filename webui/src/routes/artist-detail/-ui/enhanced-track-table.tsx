import { useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import { extractFormat, formatDurationMs } from '../-artist-detail.enhanced';
import {
  bitrateClass,
  getAlbumTrackRows,
  sortIndicator,
  type TrackSort,
  trackColumns,
  trackFileName,
  trackMatchChips,
} from '../-artist-detail.enhanced-album';

interface Props {
  album: EnhancedAlbum;
  isAdmin: boolean;
  /** Track ids currently ticked, owned by the panel so the bulk bar can read them. */
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
}

/**
 * The per-album track table (renderTrackTable / _buildTrackRow, library.js:4776).
 *
 * A note on the column sort, reproduced rather than fixed: the vanilla stored a
 * per-album sort, called sortEnhancedTracks on album.tracks, and then rendered
 * from _getEnhancedAlbumTrackRows — which ALWAYS re-sorts by disc, then track,
 * then title. So clicking a header moves the arrow and leaves the rows where
 * they were. Making the columns actually sort would be a behaviour change, not
 * a port, so the arrow behaviour is kept and the row order still comes from
 * getAlbumTrackRows.
 */
export function EnhancedTrackTable({ album, isAdmin, selected, onSelectedChange }: Props) {
  const [sort, setSort] = useState<TrackSort | undefined>(undefined);
  const rows = getAlbumTrackRows(album);

  if (rows.length === 0) {
    return <div className="enhanced-no-tracks">No tracks in database</div>;
  }

  const columns = trackColumns(isAdmin);
  // Only owned rows are selectable; a missing row has no file to act on.
  const selectableIds = rows
    .filter((row) => !(row as { _missingExpected?: boolean })._missingExpected)
    .map((row) => String(row.id));
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));

  const toggleAll = () => {
    const next = new Set(selected);
    if (allSelected) for (const id of selectableIds) next.delete(id);
    else for (const id of selectableIds) next.add(id);
    onSelectedChange(next);
  };

  const toggleOne = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange(next);
  };

  const clickHeader = (field: string | undefined) => {
    if (!field) return;
    setSort((current) =>
      current && current.field === field
        ? { field, ascending: !current.ascending }
        : { field, ascending: true },
    );
  };

  return (
    <table className="enhanced-track-table" data-album-id={String(album.id)}>
      <thead>
        <tr>
          {isAdmin ? (
            <th>
              <input
                type="checkbox"
                className="enhanced-track-checkbox"
                checked={allSelected}
                onChange={toggleAll}
              />
            </th>
          ) : null}
          {columns.map((column) => (
            <th
              key={column.cls}
              className={column.cls}
              style={column.sortField ? { cursor: 'pointer' } : undefined}
              data-sort-field={column.sortField}
              data-label={column.sortField ? column.label : undefined}
              onClick={() => clickHeader(column.sortField)}
            >
              {sortIndicator(column, sort)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((track) => (
          <TrackRow
            key={String(track.id)}
            track={track}
            album={album}
            isAdmin={isAdmin}
            selected={selected.has(String(track.id))}
            onToggle={() => toggleOne(String(track.id))}
          />
        ))}
      </tbody>
    </table>
  );
}

function TrackRow({
  track,
  album,
  isAdmin,
  selected,
  onToggle,
}: {
  track: EnhancedTrack;
  album: EnhancedAlbum;
  isAdmin: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const missing = Boolean((track as { _missingExpected?: boolean })._missingExpected);
  const editable = isAdmin && !missing ? ' editable' : '';
  const format = extractFormat(track.file_path);

  return (
    <tr
      data-track-id={String(track.id)}
      data-album-id={String(album.id)}
      className={`${missing ? 'enhanced-missing-track-row' : ''}${selected ? ' selected' : ''}`.trim()}
    >
      {isAdmin ? (
        <td>
          {/* A missing row gets an EMPTY cell, not a disabled box: there is no
              file for a bulk action to touch. */}
          {missing ? null : (
            <input
              type="checkbox"
              className="enhanced-track-checkbox"
              checked={selected}
              onChange={onToggle}
            />
          )}
        </td>
      ) : null}

      <td className="col-play">
        <button
          type="button"
          className="enhanced-play-btn"
          title={track.file_path ? 'Play track' : 'No file available'}
          disabled={!track.file_path}
        >
          {missing ? '—' : '▶'}
        </button>
      </td>

      {/* The track NUMBER stays editable on a missing row — it is the slot the
          row claims. The disc and title describe a real file's tags, so those
          two lose `editable` (#1051). */}
      <td className={`col-num${isAdmin ? ' editable' : ''}`}>
        {String(track.track_number || '-')}
      </td>
      <td className={`col-disc${editable}`}>{String(track.disc_number || '-')}</td>
      <td className={`col-title${editable}`}>
        {String(track.title || 'Unknown')}
        {missing ? <span className="enhanced-missing-track-badge">Missing</span> : null}
      </td>

      <td className="col-duration">{formatDurationMs(track.duration)}</td>

      <td className="col-format">
        {missing ? (
          '-'
        ) : (
          <span
            className={`enhanced-format-badge ${
              format === 'FLAC' ? 'flac' : format === 'MP3' ? 'mp3' : 'other'
            }`}
          >
            {format}
          </span>
        )}
      </td>

      <td className="col-bitrate">
        <span className={`enhanced-bitrate ${bitrateClass(track.bitrate)}`}>
          {track.bitrate ? `${track.bitrate} kbps` : '-'}
        </span>
      </td>

      <td className={`col-bpm${isAdmin ? ' editable' : ''}`}>{String(track.bpm || '-')}</td>

      <td className="col-path" title={String(track.file_path || '-')}>
        {trackFileName(track)}
      </td>

      <td className="col-match">
        <div className="enhanced-track-match-cell">
          {trackMatchChips(track).map((chip) => (
            <span
              key={chip.service}
              className={chip.className}
              title={chip.title}
              data-service={chip.service}
            >
              {chip.label}
            </span>
          ))}
        </div>
      </td>

      {/* `!missing` is redundant — normalizeExpectedMissingTrack never sets a
          file_path, so a missing row cannot reach the truthy branch. Kept
          because the vanilla wrote it, and it survives mutation for that
          reason rather than for want of a test. */}
      <td className="col-queue">
        {!missing && track.file_path ? (
          <>
            <button type="button" className="enhanced-playnext-btn" title="Play next">
              ⇥
            </button>
            <button type="button" className="enhanced-queue-btn" title="Add to queue">
              +
            </button>
          </>
        ) : null}
      </td>

      {isAdmin ? (
        <>
          <td className="col-writetag">
            {track.file_path && !missing ? (
              <>
                <button type="button" className="enhanced-write-tag-btn" title="Write tags to file">
                  ✎
                </button>
                <button
                  type="button"
                  className="enhanced-rg-btn"
                  title="Analyze &amp; write ReplayGain (track gain)"
                >
                  RG
                </button>
              </>
            ) : null}
          </td>
          <td className="col-track-actions">
            {missing ? (
              <div className="enhanced-track-actions-group visible">
                <button
                  type="button"
                  className="enhanced-missing-manage-btn"
                  data-action="manage-missing"
                  title="Manage this missing album track"
                >
                  Manage
                </button>
              </div>
            ) : (
              <div className="enhanced-track-actions-group">
                <button
                  type="button"
                  className="enhanced-source-info-btn"
                  title="View download source info"
                >
                  ℹ
                </button>
                <button
                  type="button"
                  className="enhanced-reidentify-btn"
                  title="Re-identify — file this track under a different release"
                >
                  ⇄
                </button>
                <button
                  type="button"
                  className="enhanced-redownload-btn"
                  title="Redownload this track"
                >
                  ↻
                </button>
                <button
                  type="button"
                  className="enhanced-delete-btn"
                  title="Delete track from library"
                >
                  ✕
                </button>
              </div>
            )}
          </td>
        </>
      ) : (
        <td className="col-report">
          {missing ? (
            <button
              type="button"
              className="enhanced-missing-manage-btn"
              data-action="manage-missing"
            >
              Manage
            </button>
          ) : (
            <button
              type="button"
              className="enhanced-track-report-btn"
              title="Report issue with this track"
            >
              ⚑
            </button>
          )}
        </td>
      )}

      {/* Shown only on mobile, via CSS. */}
      <td className="col-mobile-actions">
        <button type="button" className="enhanced-mobile-actions-btn" title="Actions">
          ⋯
        </button>
      </td>
    </tr>
  );
}
