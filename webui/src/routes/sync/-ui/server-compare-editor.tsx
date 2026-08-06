/**
 * The server tab's compare editor — _openServerCompareView (pages-extra.js
 * 247-354), _updateCompareStats (356-383), _renderCompareColumns (490-583),
 * _setupScrollLinking (585-610), _compareTrackClick (612-628) and, with slice C,
 * _serverSelectTrack (886-980) and _serverRemoveTrack (982-1020).
 *
 * The two columns render in SOURCE order and are paired by index, so row i on
 * the left and row i on the right are the same pair — that is what
 * `data-pair-id` encodes, and what the click highlight and the linked scrolling
 * both rely on.
 *
 * CORRECTION to slice B: swap / find-and-add / remove were declared there as
 * OPTIONAL PROPS, on the guess that a parent would own them. The read says
 * otherwise — all three mutate `_serverEditorState.tracks` and re-render this
 * view and nothing else, so this component owns them and the three props are
 * gone. Leaving them would have been exactly the declared-but-never-supplied
 * defect the standing sweep looks for.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  CompareResponse,
  CompareTrack,
  LibrarySearchTrack,
  MirroredMatch,
  ServerOrderTrack,
  ServerPlaylist,
  ServerSearchMode,
} from '../-sync.server';

import {
  addServerTrack,
  addTrackPosition,
  alignMatchedIds,
  alignServerPlaylist,
  applyPickedTrack,
  applyRemovedTrack,
  downloadM3u,
  compareConfidenceBadge,
  compareFilterLabel,
  compareFooterText,
  compareMetaText,
  compareMissingHint,
  compareServerIcon,
  compareServerLabel,
  compareSourceIcon,
  compareSourceLabel,
  compareStats,
  exportServerM3u,
  fetchComparePlaylist,
  formatDurationMs,
  m3uExportNote,
  m3uFileName,
  removeConfirmOptions,
  removeServerTrack,
  replaceServerTrack,
  serverM3uTracks,
} from '../-sync.server';
import { ServerOrderModal } from './server-order-modal';
import { ServerSearchOverlay } from './server-search-overlay';

const FILTERS = ['all', 'matched', 'missing', 'extra'] as const;
type CompareFilter = (typeof FILTERS)[number];

/* ── Rows ─────────────────────────────────────────────────────────────────── */

function SourceRow({
  track,
  index,
  hidden,
  highlighted,
  onSelect,
}: {
  track: CompareTrack;
  index: number;
  hidden: boolean;
  highlighted: boolean;
  onSelect: () => void;
}) {
  const src = track.source_track;
  // 521-528: an extra server track has no source side at all — a static slot
  // with NO click handler, unlike the missing slot on the right.
  if (!src) {
    return (
      <div
        className={`server-track-item extra-gap${highlighted ? ' highlighted' : ''}`}
        data-pair-id={`pair-${index}`}
        data-index={index}
        data-status={track.match_status}
        style={hidden ? { display: 'none' } : undefined}
      >
        <div className="server-track-empty-slot extra">
          <span className="empty-slot-label">No source track</span>
        </div>
      </div>
    );
  }
  return (
    <div
      className={`server-track-item ${track.match_status}${highlighted ? ' highlighted' : ''}`}
      data-pair-id={`pair-${index}`}
      data-index={index}
      data-status={track.match_status}
      style={hidden ? { display: 'none' } : undefined}
      onClick={onSelect}
    >
      {/* 510: the SOURCE column shows the source's own position when it has
          one — only the server column is always ordinal. */}
      <div className="server-track-num">{src.position != null ? src.position : index + 1}</div>
      <div className="server-track-art">
        {src.image_url ? (
          <img src={src.image_url} alt="" loading="lazy" />
        ) : (
          <div className="server-track-art-empty" />
        )}
      </div>
      <div className="server-track-info">
        <div className="server-track-title">{src.name}</div>
        <div className="server-track-artist">{src.artist || ''}</div>
      </div>
      <div className="server-track-duration">{formatDurationMs(src.duration_ms)}</div>
      <div className="server-track-status-dot" />
    </div>
  );
}

function ServerRow({
  track,
  index,
  hidden,
  highlighted,
  onSelect,
  onSwap,
  onRemove,
  onFindAndAdd,
}: {
  track: CompareTrack;
  index: number;
  hidden: boolean;
  highlighted: boolean;
  onSelect: () => void;
  onSwap: (index: number) => void;
  onRemove: (index: number, serverTrackId: string) => void;
  onFindAndAdd: (index: number) => void;
}) {
  const svr = track.server_track;
  // 564-577: missing on the server — the whole slot is the Find & add button.
  if (!svr) {
    return (
      <div
        className={`server-track-item empty-slot-wrap${highlighted ? ' highlighted' : ''}`}
        data-pair-id={`pair-${index}`}
        data-index={index}
        data-status={track.match_status}
        style={hidden ? { display: 'none' } : undefined}
        onClick={() => onFindAndAdd(index)}
      >
        <div className="server-track-empty-slot missing">
          <div className="empty-slot-icon">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.35-4.35" />
            </svg>
          </div>
          <span className="empty-slot-label">Find &amp; add</span>
          <span className="empty-slot-hint">{compareMissingHint(track)}</span>
        </div>
      </div>
    );
  }

  const badge = compareConfidenceBadge(track);
  return (
    <div
      className={`server-track-item ${track.match_status}${highlighted ? ' highlighted' : ''}`}
      data-pair-id={`pair-${index}`}
      data-index={index}
      data-status={track.match_status}
      style={hidden ? { display: 'none' } : undefined}
      onClick={onSelect}
    >
      {/* 544: ordinal on this side, always. */}
      <div className="server-track-num">{index + 1}</div>
      <div className="server-track-art">
        {svr.thumb ? (
          <img src={svr.thumb} alt="" loading="lazy" />
        ) : (
          <div className="server-track-art-empty" />
        )}
      </div>
      <div className="server-track-info">
        <div className="server-track-title">{svr.title}</div>
        <div className="server-track-artist">{svr.artist || ''}</div>
      </div>
      {badge && (
        <span className={`server-track-conf ${badge.className}`} title="Title similarity">
          {badge.percent}%
        </span>
      )}
      <div className="server-track-duration">{formatDurationMs(svr.duration)}</div>
      <div className="server-track-actions">
        {/* 555: swap is offered for a MATCHED row only — there is nothing to
            swap on an extra. Remove is offered on both. */}
        {track.match_status === 'matched' && (
          <button
            type="button"
            className="server-track-swap-btn"
            title="Swap for different version"
            onClick={(event) => {
              event.stopPropagation();
              onSwap(index);
            }}
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <path d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4" />
            </svg>
          </button>
        )}
        <button
          type="button"
          className="server-track-remove-btn"
          title="Remove from playlist"
          onClick={(event) => {
            event.stopPropagation();
            onRemove(index, svr.id ?? '');
          }}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
          >
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="server-track-status-dot" />
    </div>
  );
}

/* ── The editor ───────────────────────────────────────────────────────────── */

export interface ServerCompareEditorProps {
  playlist: ServerPlaylist;
  mirrored: MirroredMatch | null;
  onBack: () => void;
}

export function ServerCompareEditor({ playlist, mirrored, onBack }: ServerCompareEditorProps) {
  const [data, setData] = useState<CompareResponse | null>(null);
  /**
   * The tracks are state of their own, apart from `data`, because that is the
   * split the vanilla has: the in-place patches rewrite
   * `_serverEditorState.tracks` and _rerenderCompare repaints the stats and the
   * columns from it — while the header line, the column counts and the
   * out-of-order badge keep whatever the FETCH said and go stale until the next
   * full open (732-742 touches none of them).
   *
   * _rerenderCompare's other two jobs need no code here. It saves and restores
   * both columns' scrollTop because it rebuilds them with innerHTML; React
   * reconciles the same elements, so the scroll position was never lost. And it
   * re-applies the active filter (#1005: a reload once left the columns showing
   * everything while the 'Missing' pill stayed lit) because its rows are built
   * fresh and visible; here the filter is state that every render already
   * honours, so the two cannot disagree.
   */
  const [tracks, setTracks] = useState<CompareTrack[]>([]);
  const [meta, setMeta] = useState('Loading comparison...');
  const [filter, setFilter] = useState<CompareFilter>('all');
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [search, setSearch] = useState<{ index: number; mode: ServerSearchMode } | null>(null);
  const [showOrder, setShowOrder] = useState(false);
  const [exporting, setExporting] = useState(false);

  const sourceScroll = useRef<HTMLDivElement>(null);
  const serverScroll = useRef<HTMLDivElement>(null);

  /**
   * Plex deletes and recreates a playlist on every write, so the id it answers
   * with replaces ours (939, 1003). It is a ref, not state: no render reads it,
   * and making it state would re-run the loader below and undo the very
   * in-place patch the write just made.
   */
  const playlistIdRef = useRef(playlist.id);
  const runId = useRef(0);

  const loadCompare = useCallback(async () => {
    const id = ++runId.current;
    setData(null);
    setTracks([]);
    setMeta('Loading comparison...');
    try {
      const response = await fetchComparePlaylist(
        playlistIdRef.current,
        playlist.name,
        mirrored?.id,
      );
      if (runId.current !== id) return;
      if (!response.success) {
        setMeta(response.error || 'Failed to load');
        return;
      }
      setData(response);
      setTracks(response.tracks ?? []);
      setMeta(compareMetaText(response));
    } catch (error) {
      if (runId.current !== id) return;
      setMeta(`Error: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  }, [playlist.name, mirrored?.id]);

  useEffect(() => {
    playlistIdRef.current = playlist.id;
    void loadCompare();
    return () => {
      runId.current++;
    };
  }, [playlist.id, loadCompare]);

  /**
   * Linked scrolling (597-606) — PROPORTIONAL, not absolute, because the two
   * columns can differ in height. The `syncing` guard stops the echo, and the
   * vanilla clears it on the next frame; an AbortController is unnecessary here
   * because React removes the listeners with the element.
   */
  const syncing = useRef(false);
  const syncScroll = useCallback((from: HTMLDivElement | null, to: HTMLDivElement | null) => {
    if (!from || !to || syncing.current) return;
    syncing.current = true;
    const maxFrom = from.scrollHeight - from.clientHeight;
    const maxTo = to.scrollHeight - to.clientHeight;
    if (maxFrom > 0 && maxTo > 0) to.scrollTop = (from.scrollTop / maxFrom) * maxTo;
    requestAnimationFrame(() => {
      syncing.current = false;
    });
  }, []);

  /** _compareTrackClick (612-628): highlight the pair, scroll the OTHER side. */
  const selectPair = useCallback((index: number, side: 'source' | 'server') => {
    setHighlighted(index);
    const other = side === 'source' ? serverScroll.current : sourceScroll.current;
    other
      ?.querySelector(`[data-pair-id="pair-${index}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  /* ── The three writes (886-1020) ────────────────────────────────────────── */

  // 747-748: both entry points bail on an index with no track behind it.
  const openSearch = useCallback(
    (index: number, mode: ServerSearchMode) => {
      if (!tracks[index]) return;
      setSearch({ index, mode });
    },
    [tracks],
  );

  /**
   * _serverSelectTrack (886-980). Returns false to leave the overlay open with
   * its Select button restored, which is what the vanilla does at 974/978.
   */
  const selectSearchResult = useCallback(
    async (
      newTrackId: string,
      resolvePicked: () => LibrarySearchTrack | undefined,
    ): Promise<boolean> => {
      if (!search) return false;
      const { index, mode } = search;
      const track = tracks[index];
      if (!track) return false;
      try {
        const response =
          mode === 'replace'
            ? await replaceServerTrack(
                playlistIdRef.current,
                playlist.name,
                track.server_track?.id,
                newTrackId,
              )
            : await addServerTrack(
                playlistIdRef.current,
                playlist.name,
                newTrackId,
                addTrackPosition(tracks, index),
                track.source_track,
                mirrored?.source,
              );
        if (!response.success) {
          window.showToast?.(response.error || 'Failed to update track', 'error');
          return false;
        }
        window.showToast?.(response.message || 'Track updated', 'success');
        setSearch(null);
        if (response.new_playlist_id) playlistIdRef.current = response.new_playlist_id;
        // 946: resolved only NOW, after the write — see the seam's doc comment.
        const picked = resolvePicked();
        if (picked) {
          setTracks((current) => applyPickedTrack(current, index, newTrackId, picked));
        } else {
          // 968-971: the pick could not be identified locally, so there is
          // nothing to patch with — fall back to the full reload the patch
          // exists to avoid.
          void loadCompare();
        }
        return true;
      } catch (error) {
        window.showToast?.(
          `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
        return false;
      }
    },
    [loadCompare, mirrored?.source, playlist.name, search, tracks],
  );

  /** _serverRemoveTrack (982-1020). */
  const removeTrack = useCallback(
    async (index: number, serverTrackId: string) => {
      // 983: an id-less row is not removable, and the confirm never opens.
      if (!serverTrackId) return;
      const confirmed = await window.showConfirmDialog?.(removeConfirmOptions(tracks[index]));
      if (!confirmed) return;
      try {
        const response = await removeServerTrack(
          playlistIdRef.current,
          playlist.name,
          serverTrackId,
        );
        if (!response.success) {
          window.showToast?.(response.error || 'Failed to remove track', 'error');
          return;
        }
        window.showToast?.(response.message || 'Track removed', 'success');
        playlistIdRef.current = response.new_playlist_id || playlistIdRef.current;
        setTracks((current) => applyRemovedTrack(current, index));
      } catch (error) {
        window.showToast?.(
          `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [playlist.name, tracks],
  );

  /**
   * _alignPlaylist (448-482). Order-only: the matched server ids are sent in
   * SOURCE order and the backend rewrites the playlist into exactly that
   * sequence, keeping or dropping the extras. Missing tracks are never added —
   * that is a normal sync's job.
   */
  const alignPlaylist = useCallback(
    async (keepExtras: boolean) => {
      // 453: no playlist, nothing to align.
      if (!playlistIdRef.current) return;
      const matchedIds = alignMatchedIds(tracks);
      if (matchedIds.length === 0) {
        // 458: 'warning', not 'error' — nothing went wrong, there is just
        // nothing an order-only rewrite could act on.
        window.showToast?.('Nothing to align', 'warning');
        return;
      }
      try {
        const response = await alignServerPlaylist(
          playlistIdRef.current,
          playlist.name,
          matchedIds,
          keepExtras,
        );
        if (!response.success) {
          // 477: the modal STAYS open on failure, so the user can retry.
          window.showToast?.(response.error || 'Align failed', 'error');
          return;
        }
        window.showToast?.(`Playlist order aligned (${response.track_count} tracks)`, 'success');
        setShowOrder(false);
        // 475 _serverEditorRefresh: a reorder invalidates order_status and the
        // server column's numbering, so this one really does reload — unlike
        // the row writes, there is nothing to patch in place.
        void loadCompare();
      } catch (error) {
        window.showToast?.(
          `Align failed: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [loadCompare, playlist.name, tracks],
  );

  /** exportServerPlaylistM3U (632-696). */
  const exportM3u = useCallback(async () => {
    const m3uTracks = serverM3uTracks(tracks);
    // 652-655: the guard runs BEFORE the button is touched, so an empty
    // playlist never shows the exporting state at all.
    if (m3uTracks.length === 0) {
      window.showToast?.('No server tracks to export', 'warning');
      return;
    }
    setExporting(true);
    try {
      const data = await exportServerM3u(playlist.name, m3uTracks);
      downloadM3u(data.m3u_content || '', m3uFileName(playlist.name));
      const found = data.stats?.found != null ? data.stats.found : m3uTracks.length;
      // 690: the toast names the playlist WITHOUT the 'Playlist' fallback the
      // body and the filename both use — a nameless playlist really does read
      // 'Exported M3U: (3 tracks)'. Transcribed, not tidied.
      window.showToast?.(
        `Exported M3U: ${playlist.name}${m3uExportNote(found, m3uTracks.length)}`,
        'success',
      );
    } catch (error) {
      window.showToast?.(
        `M3U export failed: ${error instanceof Error ? error.message : 'unknown error'}`,
        'error',
      );
    } finally {
      setExporting(false);
    }
  }, [playlist.name, tracks]);

  const stats = compareStats(tracks);
  const outOfOrder = Boolean(data?.order_status?.out_of_order);
  const serverLabel = compareServerLabel(data?.server_type);

  /**
   * 715-721: the filter HIDES rows, it never drops them. That matters — the
   * two columns are paired by index, so removing a row from one side would
   * slide the other side out of alignment.
   */
  const isHidden = (track: CompareTrack) => filter !== 'all' && track.match_status !== filter;

  return (
    <div id="server-editor">
      <div className="server-editor-header">
        <button type="button" className="server-editor-back" onClick={onBack}>
          ← Back
        </button>
        <div id="server-editor-name">{playlist.name}</div>
        <div id="server-editor-meta">{meta}</div>
        {/* 657/694: the label IS the button state. The vanilla restores
            whatever text it captured, falling back to this same string — a
            fallback that can only ever produce the string it already had. */}
        <button
          type="button"
          id="server-editor-export-btn"
          disabled={exporting}
          onClick={() => void exportM3u()}
        >
          {exporting ? '⏳ Exporting…' : '📋 Export M3U'}
        </button>
      </div>

      {/* 304-306: only when there is no mirrored source at all. */}
      {data && !mirrored && <div id="server-no-source-banner" />}

      <div id="server-editor-stats">
        <div className="server-editor-stat">
          <div className="server-editor-stat-num matched">{stats.matched}</div>
          <div className="server-editor-stat-label">Matched</div>
        </div>
        <div className="server-editor-stat">
          <div className="server-editor-stat-num missing">{stats.missing}</div>
          <div className="server-editor-stat-label">Missing</div>
        </div>
        {/* 366: the Extra tile appears only when there are any. */}
        {stats.extra > 0 && (
          <div className="server-editor-stat">
            <div className="server-editor-stat-num extra">{stats.extra}</div>
            <div className="server-editor-stat-label">Extra</div>
          </div>
        )}
      </div>

      <div className="server-editor-filters">
        {FILTERS.map((f) => (
          <button
            type="button"
            key={f}
            className={`discog-filter${filter === f ? ' active' : ''}`}
            data-filter={f}
            onClick={() => setFilter(f)}
          >
            {compareFilterLabel(f, stats)}
          </button>
        ))}
      </div>

      <div className="server-compare-columns">
        <div id="server-col-source">
          <div className="server-col-header">
            <span id="server-col-source-icon">
              {mirrored ? compareSourceIcon(mirrored.source) : '📋'}
            </span>
            <span id="server-col-source-label">
              {compareSourceLabel(mirrored?.source, Boolean(mirrored))}
            </span>
            <span id="server-col-source-count">{data?.source_track_count || 0} tracks</span>
          </div>
          <div
            id="server-col-source-scroll"
            ref={sourceScroll}
            onScroll={() => syncScroll(sourceScroll.current, serverScroll.current)}
          >
            {data ? (
              tracks.map((track, index) => (
                <SourceRow
                  key={index}
                  track={track}
                  index={index}
                  hidden={isHidden(track)}
                  highlighted={highlighted === index}
                  onSelect={() => selectPair(index, 'source')}
                />
              ))
            ) : (
              <div
                style={{
                  textAlign: 'center',
                  padding: 30,
                  color: 'rgba(255,255,255,0.2)',
                  fontSize: 12,
                }}
              >
                Loading...
              </div>
            )}
          </div>
        </div>

        <div id="server-col-server">
          <div className="server-col-header">
            <span id="server-col-server-icon">{compareServerIcon(data?.server_type)}</span>
            <span id="server-col-server-label">{serverLabel}</span>
            <span id="server-col-server-count">
              {data?.server_track_count || 0} tracks{' '}
              {outOfOrder && (
                <button
                  type="button"
                  className="server-order-badge"
                  title={`These tracks match the source, but the playlist is in a different order on ${serverLabel}. Click to view the actual server order.`}
                  onClick={() => setShowOrder(true)}
                >
                  ⚠ out of order
                </button>
              )}
            </span>
          </div>
          <div
            id="server-col-server-scroll"
            ref={serverScroll}
            onScroll={() => syncScroll(serverScroll.current, sourceScroll.current)}
          >
            {data ? (
              tracks.map((track, index) => (
                <ServerRow
                  key={index}
                  track={track}
                  index={index}
                  hidden={isHidden(track)}
                  highlighted={highlighted === index}
                  onSelect={() => selectPair(index, 'server')}
                  onSwap={(i) => openSearch(i, 'replace')}
                  onRemove={(i, id) => void removeTrack(i, id)}
                  onFindAndAdd={(i) => openSearch(i, 'add')}
                />
              ))
            ) : (
              <div
                style={{
                  textAlign: 'center',
                  padding: 30,
                  color: 'rgba(255,255,255,0.2)',
                  fontSize: 12,
                }}
              >
                Loading...
              </div>
            )}
          </div>
        </div>
      </div>

      <div id="server-editor-footer">{compareFooterText(stats)}</div>

      {showOrder && (
        <ServerOrderModal
          order={(data?.server_order ?? []) as ServerOrderTrack[]}
          serverType={data?.server_type}
          onClose={() => setShowOrder(false)}
          onAlign={(keepExtras) => void alignPlaylist(keepExtras)}
        />
      )}

      {/* 761-762: each open builds a fresh overlay, so the key remounts it
          rather than re-seeding a live one. */}
      {search && tracks[search.index] && (
        <ServerSearchOverlay
          key={`${search.index}-${search.mode}`}
          track={tracks[search.index]}
          mode={search.mode}
          onClose={() => setSearch(null)}
          onSelect={selectSearchResult}
        />
      )}
    </div>
  );
}
