/**
 * The server tab's compare editor — _openServerCompareView (pages-extra.js
 * 247-354), _updateCompareStats (356-383), _renderCompareColumns (490-583),
 * _setupScrollLinking (585-610) and _compareTrackClick (612-628).
 *
 * Slice B. The two columns render in SOURCE order and are paired by index, so
 * row i on the left and row i on the right are the same pair — that is what
 * `data-pair-id` encodes, and what the click highlight and the linked scrolling
 * both rely on.
 *
 * Slices C-E are not here yet: the swap / find-and-add / remove buttons and the
 * '⚠ out of order' badge raise their intent through props, so the row markup is
 * already final and only the handlers arrive later.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { CompareResponse, CompareTrack, MirroredMatch, ServerPlaylist } from '../-sync.server';

import {
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
  fetchComparePlaylist,
  formatDurationMs,
} from '../-sync.server';

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
  /** Slice D — the '⚠ out of order' badge. */
  onShowOrder?: () => void;
  /** Slice C — serverSearchReplace(index, 'replace' | 'add') and remove. */
  onSwap?: (index: number) => void;
  onFindAndAdd?: (index: number) => void;
  onRemove?: (index: number, serverTrackId: string) => void;
  /** Slice E — exportServerPlaylistM3U. */
  onExportM3u?: () => void;
}

export function ServerCompareEditor({
  playlist,
  mirrored,
  onBack,
  onShowOrder,
  onSwap,
  onFindAndAdd,
  onRemove,
  onExportM3u,
}: ServerCompareEditorProps) {
  const [data, setData] = useState<CompareResponse | null>(null);
  const [meta, setMeta] = useState('Loading comparison...');
  const [filter, setFilter] = useState<CompareFilter>('all');
  const [highlighted, setHighlighted] = useState<number | null>(null);

  const sourceScroll = useRef<HTMLDivElement>(null);
  const serverScroll = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setMeta('Loading comparison...');
    void (async () => {
      try {
        const response = await fetchComparePlaylist(playlist.id, playlist.name, mirrored?.id);
        if (cancelled) return;
        if (!response.success) {
          setMeta(response.error || 'Failed to load');
          return;
        }
        setData(response);
        setMeta(compareMetaText(response));
      } catch (error) {
        if (!cancelled) {
          setMeta(`Error: ${error instanceof Error ? error.message : 'unknown error'}`);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [playlist.id, playlist.name, mirrored?.id]);

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

  const tracks = data?.tracks ?? [];
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
        <button type="button" id="server-editor-export-btn" onClick={onExportM3u}>
          📋 Export M3U
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
                  onClick={onShowOrder}
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
                  onSwap={(i) => onSwap?.(i)}
                  onRemove={(i, id) => onRemove?.(i, id)}
                  onFindAndAdd={(i) => onFindAndAdd?.(i)}
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
    </div>
  );
}
