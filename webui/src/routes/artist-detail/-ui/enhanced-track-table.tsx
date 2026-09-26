import { useRef, useState } from 'react';

import { getShellBridge } from '@/platform/shell/bridge';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';

import { extractFormat, formatDurationMs } from '../-artist-detail.enhanced';
import {
  bitrateClass,
  formatTrackBitrate,
  getAlbumTrackRows,
  trackBitrateTitle,
  queueTrackPayload,
  sortedTrackRows,
  sortIndicator,
  type TrackSort,
  trackColumns,
  trackFileName,
  trackMatchChips,
  trackMatchQuery,
} from '../-artist-detail.enhanced-album';
import { foldUpdatedData } from '../-artist-detail.enrich-match';
import {
  deleteLibraryTrackRequest,
  type DeleteTrackChoice,
} from '../-artist-detail.manage-actions';
import { analyzeTrackReplayGainRequest } from '../-artist-detail.tags-rg';
import { ActionMenu } from './action-menu';
import { EditableCell } from './editable-cell';
import {
  DownloadIcon,
  FlagIcon,
  InfoIcon,
  MoreIcon,
  PlayIcon,
  PlayNextIcon,
  PlusIcon,
  SwapIcon,
  TagIcon,
  TrashIcon,
  WaveIcon,
} from './lib-icons';
import { ManualMatchModal } from './manual-match-modal';
import { MissingTrackManageModal } from './missing-track-modals';
import { MobileTrackActions } from './mobile-track-actions';
import { RedownloadModal } from './redownload-modal';
import { ReidentifyModal } from './reidentify-modal';
import { SmartDeleteDialog, TRACK_DELETE_COPY } from './smart-delete-dialog';
import { SourceMeter } from './source-health';
import { SourceInfoPopover } from './source-info-popover';
import { TagPreviewModal } from './tag-preview-modal';

interface Props {
  album: EnhancedAlbum;
  isAdmin: boolean;
  /** Supplies the artist name and id every track action needs. */
  artist: Record<string, unknown> | undefined;
  /** Applies a saved inline edit to the album in the panel's state. */
  onTrackEdited: (trackId: unknown, field: string, value: string | number | null) => void;
  /** Removes a deleted track from the album in the panel's state. */
  onTrackDeleted: (trackId: unknown) => void;
  /** A fresh server copy of the album (after a track match) re-renders the panel. */
  onAlbumPatched: (album: Record<string, unknown>) => void;
  /** Track ids currently ticked, owned by the panel so the bulk bar can read them. */
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  /** Full payload re-fetch (a finished redownload / a fallback-path import). */
  onReload: () => void;
}

/**
 * The per-album track table (renderTrackTable / _buildTrackRow, library.js:4776).
 *
 * The column sort WORKS here, which it never did in the vanilla: that stored a
 * per-album sort, called sortEnhancedTracks on album.tracks, and then rendered
 * from _getEnhancedAlbumTrackRows — which always re-sorts by disc, then track,
 * then title. The custom sort was applied and immediately thrown away, so a
 * header click moved the arrow and nothing else. A deliberate fix, not a port.
 */
export function EnhancedTrackTable({
  album,
  isAdmin,
  artist,
  selected,
  onSelectedChange,
  onTrackEdited,
  onTrackDeleted,
  onAlbumPatched,
  onReload,
}: Props) {
  const [sort, setSort] = useState<TrackSort | undefined>(undefined);
  const [matching, setMatching] = useState<{ track: EnhancedTrack; service: string } | null>(null);
  const [tagPreview, setTagPreview] = useState<EnhancedTrack | null>(null);
  /** One popover / one dialog for the whole table, keyed to the acting row. */
  const [sourceInfo, setSourceInfo] = useState<{
    track: EnhancedTrack;
    anchor: HTMLElement | null;
  } | null>(null);
  const [deleting, setDeleting] = useState<EnhancedTrack | null>(null);
  const [redownloading, setRedownloading] = useState<EnhancedTrack | null>(null);
  const [managingMissing, setManagingMissing] = useState<EnhancedTrack | null>(null);
  const [reidentifying, setReidentifying] = useState<EnhancedTrack | null>(null);
  const rows = sortedTrackRows(getAlbumTrackRows(album), sort);

  if (rows.length === 0) {
    return <div className="enhanced-no-tracks">No tracks in database</div>;
  }

  const columns = trackColumns(isAdmin);
  // the disc column only earns its width on a multi-disc release
  const multiDisc = rows.some((row) => Number(row.disc_number || 1) > 1);
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

  /**
   * The confirmed delete (deleteLibraryTrack, library.js:3096): request, the
   * vanilla's toasts (a file error gets its own longer-lived toast), then the
   * row leaves the panel's state and the selection.
   */
  const performDelete = async (choice: DeleteTrackChoice) => {
    const track = deleting;
    setDeleting(null);
    if (!track) return;
    try {
      const toast = await deleteLibraryTrackRequest(track.id, choice);
      window.showToast?.(toast.message, toast.tone);
      if (toast.extra) window.showToast?.(toast.extra, 'error', 8000);
      onTrackDeleted(track.id);
      const next = new Set(selected);
      next.delete(String(track.id));
      onSelectedChange(next);
    } catch (error) {
      window.showToast?.(`Delete failed: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <>
      <table
        className="enhanced-track-table"
        data-album-id={String(album.id)}
        data-multidisc={multiDisc ? '' : undefined}
      >
        <thead>
          <tr>
            {isAdmin ? (
              <th>
                <input
                  type="checkbox"
                  className="enhanced-track-checkbox"
                  aria-label="Select all tracks"
                  checked={allSelected}
                  onChange={toggleAll}
                />
              </th>
            ) : null}
            {columns.map((column) => (
              <th
                key={column.cls}
                className={`${column.cls}${sort && sort.field === column.sortField ? ' sorted' : ''}`}
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
              artist={artist}
              selected={selected.has(String(track.id))}
              onToggle={() => toggleOne(String(track.id))}
              onEdited={(field, value) => onTrackEdited(track.id, field, value)}
              onSourceInfo={(anchor) => setSourceInfo({ track, anchor })}
              onDelete={() => setDeleting(track)}
              onMatch={(service) => setMatching({ track, service })}
              onTagPreview={() => setTagPreview(track)}
              onRedownload={() => setRedownloading(track)}
              onMissingManage={() => setManagingMissing(track)}
              onReidentify={() => setReidentifying(track)}
            />
          ))}
        </tbody>
      </table>
      {sourceInfo ? (
        <SourceInfoPopover
          trackId={sourceInfo.track.id}
          trackTitle={String(sourceInfo.track.title || '')}
          anchor={sourceInfo.anchor}
          onClose={() => setSourceInfo(null)}
        />
      ) : null}
      {deleting ? (
        <SmartDeleteDialog
          copy={TRACK_DELETE_COPY}
          onChoose={(choice) => void performDelete(choice as DeleteTrackChoice)}
          onClose={() => setDeleting(null)}
        />
      ) : null}
      {tagPreview ? (
        <TagPreviewModal trackId={tagPreview.id} onClose={() => setTagPreview(null)} />
      ) : null}
      {matching ? (
        <ManualMatchModal
          entityType="track"
          entityId={matching.track.id}
          service={matching.service}
          defaultQuery={trackMatchQuery(matching.service, matching.track, album)}
          artistId={artist?.id ?? null}
          onUpdated={(outcome) => {
            if (!outcome.updatedData) return;
            const fresh = foldUpdatedData(
              (window.artistDetailPageState?.enhancedData ?? null) as
                | import('../-artist-detail.enhanced').EnhancedData
                | null,
              outcome.updatedData,
              album.id,
            );
            if (fresh) onAlbumPatched(fresh);
          }}
          onClose={() => setMatching(null)}
        />
      ) : null}
      {reidentifying ? (
        <ReidentifyModal
          trackId={reidentifying.id}
          trackTitle={String(reidentifying.title || 'Unknown')}
          artistName={String(artist?.name || '')}
          albumTitle={String(album.title || '')}
          imageUrl={String(album.thumb_url || '')}
          onClose={() => setReidentifying(null)}
        />
      ) : null}
      {redownloading ? (
        <RedownloadModal
          track={redownloading}
          album={album}
          artistName={String(artist?.name || '')}
          onReload={onReload}
          onClose={() => setRedownloading(null)}
        />
      ) : null}
      {managingMissing ? (
        <MissingTrackManageModal
          track={managingMissing}
          album={album}
          artist={{
            id: artist?.id,
            name: String(artist?.name || ''),
            imageUrl: String(artist?.thumb_url || ''),
          }}
          onImported={(updatedData) => {
            // The importer hands back the whole refreshed payload; fold it in
            // like a match does, else fall back to a full re-fetch (5203-5209).
            if (updatedData) {
              const fresh = foldUpdatedData(
                (window.artistDetailPageState?.enhancedData ?? null) as
                  | import('../-artist-detail.enhanced').EnhancedData
                  | null,
                updatedData as never,
                album.id,
              );
              if (fresh) onAlbumPatched(fresh);
            } else {
              onReload();
            }
          }}
          onClose={() => setManagingMissing(null)}
        />
      ) : null}
    </>
  );
}

function TrackRow({
  track,
  album,
  isAdmin,
  artist,
  selected,
  onToggle,
  onEdited,
  onSourceInfo,
  onDelete,
  onMatch,
  onTagPreview,
  onRedownload,
  onMissingManage,
  onReidentify,
}: {
  track: EnhancedTrack;
  album: EnhancedAlbum;
  isAdmin: boolean;
  artist: Record<string, unknown> | undefined;
  selected: boolean;
  onToggle: () => void;
  onEdited: (field: string, value: string | number | null) => void;
  onSourceInfo: (anchor: HTMLElement | null) => void;
  onDelete: () => void;
  onMatch: (service: string) => void;
  onTagPreview: () => void;
  onRedownload: () => void;
  onMissingManage: () => void;
  onReidentify: () => void;
}) {
  const [rgBusy, setRgBusy] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  /** the source-info popover anchors beside the row's menu button. */
  const moreRef = useRef<HTMLElement | null>(null);
  const missing = Boolean((track as { _missingExpected?: boolean })._missingExpected);
  const editable = isAdmin && !missing ? ' editable' : '';
  const format = extractFormat(track.file_path);
  const artistName = String(artist?.name || '');

  /**
   * Every action stops propagation, exactly as the delegated handler did: the
   * row sits inside the album panel, and a bubbling click would toggle the
   * album shut under the button that was just pressed.
   */
  const act =
    (handler: (e: React.MouseEvent<HTMLElement>) => void) => (e: React.MouseEvent<HTMLElement>) => {
      e.stopPropagation();
      handler(e);
    };

  const enqueue = (playNext: boolean) => {
    const payload = queueTrackPayload(track, album, artist);
    // Play-next falls back to a plain enqueue when the player does not expose
    // it, which is what the vanilla's typeof check did.
    if (playNext && typeof window.playNext === 'function') window.playNext(payload);
    else window.addToQueue?.(payload);
  };

  const trackTitle = String(track.title || 'Unknown');
  const play = () => {
    if (track.file_path) {
      getShellBridge()?.playLibraryTrack(track as never, String(album.title || ''), artistName);
    }
  };

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
              aria-label={`Select ${trackTitle}`}
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
          aria-label={track.file_path ? `Play ${trackTitle}` : 'No file available'}
          disabled={!track.file_path}
          onClick={act(play)}
        >
          {missing ? <span aria-hidden="true">—</span> : <PlayIcon size={12} />}
        </button>
      </td>

      {/* The track NUMBER stays editable on a missing row — it is the slot the
          row claims. The disc and title describe a real file's tags, so those
          two lose `editable` (#1051). */}
      <EditableCell
        className={`col-num${isAdmin ? ' editable' : ''}`}
        editable={isAdmin}
        entityType="track"
        entityId={track.id}
        field="track_number"
        value={track.track_number as string | number | null}
        onSaved={onEdited}
      >
        {String(track.track_number || '-')}
      </EditableCell>
      <EditableCell
        className={`col-disc${editable}`}
        editable={Boolean(editable)}
        entityType="track"
        entityId={track.id}
        field="disc_number"
        value={track.disc_number as string | number | null}
        onSaved={onEdited}
      >
        {String(track.disc_number || '-')}
      </EditableCell>
      <EditableCell
        className={`col-title${editable}`}
        editable={Boolean(editable)}
        entityType="track"
        entityId={track.id}
        field="title"
        value={track.title as string | null}
        onSaved={onEdited}
      >
        <span className="lib-track-title">{trackTitle}</span>
        {missing ? <span className="enhanced-missing-track-badge">Missing</span> : null}
      </EditableCell>

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
        <span
          className={`enhanced-bitrate ${bitrateClass(track.bitrate)}`}
          title={trackBitrateTitle(track)}
        >
          {formatTrackBitrate(track)}
        </span>
      </td>

      <EditableCell
        className={`col-bpm${isAdmin ? ' editable' : ''}`}
        editable={isAdmin}
        entityType="track"
        entityId={track.id}
        field="bpm"
        value={track.bpm as string | number | null}
        onSaved={onEdited}
      >
        {String(track.bpm || '-')}
      </EditableCell>

      <td className="col-path" title={String(track.file_path || '-')}>
        {trackFileName(track)}
      </td>

      <td className="col-match">
        {/* Re-matching is admin-only; for everyone else the meter is just a
            status marker. */}
        <SourceMeter entries={trackMatchChips(track)} onMatch={isAdmin ? onMatch : undefined} />
      </td>

      <td className="col-queue">
        {track.file_path ||
        (missing && (track as { _hasActionableContext?: boolean })._hasActionableContext) ? (
          <span className="lib-row-queue">
            <button
              type="button"
              className="enhanced-playnext-btn lib-row-btn"
              title={missing ? 'Download automatically and play next' : 'Play next'}
              aria-label={missing ? 'Download automatically and play next' : 'Play next'}
              onClick={act(() => enqueue(true))}
            >
              <PlayNextIcon size={15} />
            </button>
            <button
              type="button"
              className="enhanced-queue-btn lib-row-btn"
              title={missing ? 'Add to queue and download automatically' : 'Add to queue'}
              aria-label={missing ? 'Add to queue and download automatically' : 'Add to queue'}
              onClick={act(() => enqueue(false))}
            >
              <PlusIcon size={15} />
            </button>
          </span>
        ) : null}
      </td>

      {isAdmin ? (
        <td className="col-track-actions">
          {missing ? (
            <button
              type="button"
              className="enhanced-missing-manage-btn"
              data-action="manage-missing"
              title="Manage this missing album track"
              onClick={act(() => onMissingManage())}
            >
              Manage
            </button>
          ) : (
            <ActionMenu
              heading={<strong>{trackTitle}</strong>}
              items={[
                ...(track.file_path
                  ? [
                      {
                        key: 'tags',
                        className: 'enhanced-write-tag-btn',
                        icon: <TagIcon />,
                        label: 'Write tags to file',
                        onSelect: onTagPreview,
                      },
                      {
                        key: 'rg',
                        className: 'enhanced-rg-btn',
                        icon: <WaveIcon />,
                        label: rgBusy ? 'Analyzing ReplayGain…' : 'Analyze ReplayGain',
                        disabled: rgBusy,
                        onSelect: () => {
                          // Synchronous on the server (~1-3s); the row shows a
                          // spinner meanwhile, as the vanilla's did (5710-5712).
                          setRgBusy(true);
                          void analyzeTrackReplayGainRequest(track.id).finally(() =>
                            setRgBusy(false),
                          );
                        },
                      },
                    ]
                  : []),
                {
                  key: 'source',
                  className: 'enhanced-source-info-btn',
                  icon: <InfoIcon />,
                  label: 'Where this file came from',
                  onSelect: () => onSourceInfo(moreRef.current),
                },
                {
                  key: 'reidentify',
                  className: 'enhanced-reidentify-btn',
                  icon: <SwapIcon />,
                  label: 'Re-identify…',
                  title: 'File this track under a different release',
                  onSelect: onReidentify,
                },
                {
                  key: 'redownload',
                  className: 'enhanced-redownload-btn',
                  icon: <DownloadIcon />,
                  label: 'Redownload…',
                  onSelect: onRedownload,
                },
                {
                  key: 'delete',
                  className: 'enhanced-delete-btn',
                  icon: <TrashIcon />,
                  label: 'Delete from library…',
                  danger: true,
                  onSelect: onDelete,
                },
              ]}
              trigger={(t) => (
                <button
                  type="button"
                  className={`lib-row-btn lib-row-more${rgBusy ? ' busy' : ''}`}
                  title="More actions"
                  aria-label={`More actions for ${trackTitle}`}
                  {...t}
                  ref={(el) => {
                    t.ref(el);
                    moreRef.current = el;
                  }}
                >
                  <MoreIcon size={16} />
                </button>
              )}
            />
          )}
        </td>
      ) : (
        <td className="col-report">
          {missing ? (
            <button
              type="button"
              className="enhanced-missing-manage-btn"
              data-action="manage-missing"
              onClick={act(() => onMissingManage())}
            >
              Manage
            </button>
          ) : (
            <button
              type="button"
              className="enhanced-track-report-btn lib-row-btn"
              title="Report issue with this track"
              aria-label={`Report an issue with ${trackTitle}`}
              onClick={act(() =>
                window.showReportIssueModal?.(
                  'track',
                  track.id,
                  String(track.title || 'Unknown'),
                  artistName,
                  String(album.title || ''),
                ),
              )}
            >
              <FlagIcon size={14} />
            </button>
          )}
        </td>
      )}

      {/* Shown only on mobile, via CSS. */}
      <td className="col-mobile-actions">
        <button
          type="button"
          className="enhanced-mobile-actions-btn"
          title="Actions"
          aria-label={`Actions for ${trackTitle}`}
          onClick={act(() => setMobileOpen(true))}
        >
          <MoreIcon size={16} />
        </button>
        {mobileOpen ? (
          <MobileTrackActions
            track={track}
            isAdmin={isAdmin}
            onPlay={play}
            onQueue={() => enqueue(false)}
            onTagPreview={onTagPreview}
            onSourceInfo={() => onSourceInfo(null)}
            onRedownload={onRedownload}
            onDelete={onDelete}
            onMissingManage={onMissingManage}
            onClose={() => setMobileOpen(false)}
          />
        ) : null}
      </td>
    </tr>
  );
}
