import { useState } from 'react';

import type { RateSample } from '../-adl.helpers';
import type { AdlBatch, AdlBatchHistoryEntry, AdlDownload } from '../-adl.types';

import {
  historyAgo,
  historyDotColor,
  isBatchActive,
  isTerminalPhase,
  phaseDisplay,
  progressSegments,
  statLine,
} from '../-adl.batch';
import { batchColorIndex, batchEta, statusClass } from '../-adl.helpers';
import { AdlRow } from './adl-row';

function PhaseIcon({ icon }: { icon: 'spinner' | 'check' | 'hourglass' | null }) {
  if (icon === 'spinner') return <span className="adl-spinner" style={{ marginRight: '4px' }} />;
  if (icon === 'check') return <span style={{ color: '#22c55e', marginRight: '4px' }}>✓</span>;
  if (icon === 'hourglass') return <span style={{ marginRight: '4px', opacity: 0.6 }}>⏳</span>;
  return null;
}

/**
 * The group thumbnail: the first track's artwork, or the batch's initial.
 *
 * The vanilla built the fallback as an `onerror` string with nested escaped
 * quotes — fragile enough that a name containing a quote could break it. Here
 * a failed load just flips state.
 */
export function BatchThumb({
  batch,
  artwork,
  colorIdx,
}: {
  batch: AdlBatch;
  artwork: string | null;
  colorIdx: number;
}) {
  const [failed, setFailed] = useState(false);
  const initial = (batch.batch_name || 'D')[0];

  if (artwork && !failed) {
    return <img className="adl-group-thumb" src={artwork} alt="" onError={() => setFailed(true)} />;
  }
  return (
    <div
      className="adl-group-thumb adl-group-thumb-fallback"
      style={
        colorIdx >= 0
          ? {
              background: `rgba(var(--batch-color-${colorIdx}), 0.15)`,
              color: `rgba(var(--batch-color-${colorIdx}), 0.7)`,
            }
          : { background: 'rgba(255,255,255,0.05)', color: 'rgba(255,255,255,0.55)' }
      }
    >
      {/* The image fallback keeps the raw initial; the no-art one upper-cases. */}
      {artwork ? initial : initial.toUpperCase()}
    </div>
  );
}

/** One folded bucket line: "6 queued · next: BLUE" / "3 done". */
function BucketFold({
  count,
  noun,
  preview,
  open,
  onToggle,
}: {
  count: number;
  noun: string;
  preview?: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" className={`adl-bucket-fold${open ? ' open' : ''}`} onClick={onToggle}>
      <span className="adl-bucket-fold-chevron" aria-hidden="true">
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </span>
      {count} {noun}
      {!open && preview ? (
        <span className="adl-bucket-fold-preview"> · next: {preview}</span>
      ) : null}
    </button>
  );
}

/**
 * What a mid-flight batch actually shows: the rows that are MOVING and the
 * rows that BROKE, in full. The queue and the done pile are facts, not
 * events — they fold to one line each. This is what turned a 12-track sync
 * from a screen and a half of rows into five lines (Boulder, Aug 27:
 * "so much scrolling").
 */
function BucketedRows({
  rows,
  openBuckets,
  onToggleBucket,
  onCancelRow,
}: {
  rows: AdlDownload[];
  openBuckets: ReadonlySet<string>;
  onToggleBucket: (key: string) => void;
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
}) {
  const inFlight = rows.filter((dl) => statusClass(dl.status) === 'active');
  const broken = rows.filter((dl) => ['failed', 'cancelled'].includes(statusClass(dl.status)));
  const queued = rows.filter((dl) => statusClass(dl.status) === 'queued');
  const done = rows.filter((dl) => statusClass(dl.status) === 'completed');

  return (
    <>
      {inFlight.map((dl) => (
        <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />
      ))}
      {broken.map((dl) => (
        <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />
      ))}
      {queued.length > 0 ? (
        <>
          <BucketFold
            count={queued.length}
            noun="queued"
            preview={queued[0]?.title}
            open={openBuckets.has('queued')}
            onToggle={() => onToggleBucket('queued')}
          />
          {openBuckets.has('queued')
            ? queued.map((dl) => <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />)
            : null}
        </>
      ) : null}
      {done.length > 0 ? (
        <>
          <BucketFold
            count={done.length}
            noun="done"
            open={openBuckets.has('done')}
            onToggle={() => onToggleBucket('done')}
          />
          {openBuckets.has('done')
            ? done.map((dl) => <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />)
            : null}
        </>
      ) : null}
    </>
  );
}

export interface AdlGroupProps {
  batch: AdlBatch;
  /** The batch's rows AFTER the page's status filter. */
  rows: AdlDownload[];
  /** The batch's FULL row set — artwork lookup and the trimmed count. */
  allBatchRows: AdlDownload[];
  /**
   * When true, queued and done rows fold into one-line summaries and only
   * in-flight + failed rows render in full. Off under a status filter (the
   * chip asked for exactly those rows) and under a batch filter (the user
   * asked for the whole batch).
   */
  bucketed: boolean;
  filtered: boolean;
  opacity: number;
  samples: RateSample[];
  onFilter: () => void;
  onCancel: () => void;
  onOpenModal: () => void;
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
}

/**
 * One batch as a list group: the old side-panel card promoted to a full-width
 * header, with the batch's real download rows under it instead of the panel's
 * micro track rows.
 *
 * Open state lives here: live batches start open, terminal ones start folded.
 * The override survives the 2s poll because the component stays mounted, and
 * deliberately resets when the batch leaves the list.
 */
export function AdlGroup({
  batch,
  rows,
  allBatchRows,
  bucketed,
  filtered,
  opacity,
  samples,
  onFilter,
  onCancel,
  onOpenModal,
  onCancelRow,
}: AdlGroupProps) {
  const terminal = isTerminalPhase(batch.phase);
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const open = openOverride ?? !terminal;
  /** Which folded buckets ('queued' / 'done') the user has expanded. */
  const [openBuckets, setOpenBuckets] = useState<ReadonlySet<string>>(new Set());
  const toggleBucket = (key: string) =>
    setOpenBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const colorIdx = batchColorIndex(batch.batch_id);
  const phase = phaseDisplay(batch);
  const segments = progressSegments(batch);
  const stats = statLine(batch);
  const eta = batchEta(batch, samples, Date.now());
  const active = isBatchActive(batch);

  const art = allBatchRows.find((t) => t.artwork)?.artwork ?? null;

  const classes = ['adl-group', `phase-${batch.phase}`];
  if (open) classes.push('open');
  if (active) classes.push('active-glow');
  if (filtered) classes.push('filtered');

  // Rows hidden by the status filter still exist — say so instead of letting
  // a filtered group read as a smaller batch than it is.
  const trimmed = allBatchRows.length - rows.length;

  return (
    <div
      className={classes.join(' ')}
      style={opacity < 1 ? { opacity } : undefined}
      data-batch-id={batch.batch_id}
    >
      {/* the album art, blurred to a wash behind the card — the page owns
          real artwork, so the chrome doesn't have to invent color */}
      {art ? (
        <div className="adl-group-backdrop" style={{ backgroundImage: `url(${art})` }} />
      ) : null}
      <div
        className="adl-group-header"
        role="button"
        tabIndex={0}
        title={open ? 'Collapse this batch' : 'Expand this batch'}
        onClick={() => setOpenOverride(!open)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpenOverride(!open);
          }
        }}
      >
        {colorIdx >= 0 ? (
          <div
            className="adl-group-rail"
            style={{ background: `rgba(var(--batch-color-${colorIdx}), 0.6)` }}
          />
        ) : null}
        <BatchThumb batch={batch} artwork={art} colorIdx={colorIdx} />
        <div className="adl-group-info">
          <div className="adl-group-name-line">
            <span
              className="adl-group-name"
              title="Open download modal"
              onClick={(event) => {
                event.stopPropagation();
                onOpenModal();
              }}
            >
              {batch.batch_name || 'Download'}
            </span>
            {batch.source_page ? (
              <span className="adl-group-source">{batch.source_page}</span>
            ) : null}
          </div>
          <div className="adl-group-phase">
            <PhaseIcon icon={phase.icon} />
            {phase.text}
            {eta ? <span className="adl-group-eta"> · {eta}</span> : null}
          </div>
        </div>
        {stats ? <span className="adl-group-stats">{stats}</span> : null}
        <div className="adl-group-actions">
          <button
            type="button"
            className={`adl-group-act adl-group-filter${filtered ? ' active' : ''}`}
            title={filtered ? 'Show all downloads' : 'Show only this batch'}
            onClick={(event) => {
              event.stopPropagation();
              onFilter();
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
            </svg>
          </button>
          {/* A finished batch has nothing left to cancel. */}
          {!terminal ? (
            <button
              type="button"
              className="adl-group-act adl-group-cancel"
              title="Cancel batch"
              onClick={(event) => {
                event.stopPropagation();
                onCancel();
              }}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          ) : null}
          <span className={`adl-group-chevron${open ? ' open' : ''}`} aria-hidden="true">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </span>
        </div>
      </div>

      <div className="adl-group-segbar">
        <div className="adl-batch-seg seg-done" style={{ width: `${segments.done}%` }} />
        <div className="adl-batch-seg seg-fail" style={{ width: `${segments.failed}%` }} />
        <div
          className={`adl-batch-seg seg-active${active ? ' shimmer' : ''}`}
          style={{ width: `${segments.active}%` }}
        />
      </div>

      {open ? (
        <div className="adl-group-rows">
          {bucketed ? (
            <BucketedRows
              rows={rows}
              openBuckets={openBuckets}
              onToggleBucket={toggleBucket}
              onCancelRow={onCancelRow}
            />
          ) : (
            rows.map((dl) => <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />)
          )}
          {rows.length === 0 && batch.phase === 'album_downloading' ? (
            <div className="adl-group-note">
              Downloading one release first. Track matching starts after staging.
            </div>
          ) : null}
          {rows.length === 0 && batch.phase !== 'album_downloading' ? (
            <div className="adl-group-note">
              {trimmed > 0 ? 'No tracks match the current filter.' : 'No tracks loaded.'}
            </div>
          ) : null}
          {rows.length > 0 && trimmed > 0 ? (
            <div className="adl-group-note">{trimmed} more hidden by the current filter.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** How many "earlier" items (rows or album folds) show before the fold. */
export const EARLIER_FOLD = 12;

/** "12 done" / "10 done · 2 failed" for one folded album line. */
function earlierAlbumSummary(rows: AdlDownload[]): string {
  const done = rows.filter((dl) => statusClass(dl.status) === 'completed').length;
  const failed = rows.length - done;
  const parts: string[] = [];
  if (done) parts.push(`${done} done`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(' · ');
}

type EarlierItem =
  | { kind: 'row'; row: AdlDownload }
  | { kind: 'album'; key: string; name: string; artist: string; rows: AdlDownload[] };

/**
 * Rows with no live batch: aged-out history plus anything batchless.
 *
 * Two folds keep this tail short. Same-album history runs collapse to one
 * line each — Boulder's real install had twelve identical Romance (Deluxe
 * Edition) rows stacked full height (Aug 27 screenshot). And past
 * EARLIER_FOLD items the whole section folds, because this is where
 * 300-row payloads live.
 */
export function AdlEarlierGroup({
  rows,
  onCancelRow,
}: {
  rows: AdlDownload[];
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
}) {
  const [showAll, setShowAll] = useState(false);
  const [openAlbums, setOpenAlbums] = useState<ReadonlySet<string>>(new Set());
  if (rows.length === 0) return null;

  const toggleAlbum = (name: string) =>
    setOpenAlbums((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  // a batchless row that is still moving is not history — it stays a plain
  // row on top and never hides inside an album fold
  const live = rows.filter((dl) => ['active', 'queued'].includes(statusClass(dl.status)));
  const settled = rows.filter((dl) => !['active', 'queued'].includes(statusClass(dl.status)));

  const grouped: EarlierItem[] = [];
  const byKey = new Map<string, Extract<EarlierItem, { kind: 'album' }>>();
  for (const row of settled) {
    // group by the ALBUM, not the batch name — history rows carry the
    // download source as their batch name, and "Soulseek · 45 done" says
    // nothing. the album run is the unit a human recognizes.
    const key = row.album ? `${row.artist}|${row.album}` : '';
    if (!key) {
      grouped.push({ kind: 'row', row });
      continue;
    }
    let album = byKey.get(key);
    if (!album) {
      album = { kind: 'album', key, name: row.album, artist: row.artist || '', rows: [] };
      byKey.set(key, album);
      grouped.push(album);
    }
    album.rows.push(row);
  }
  // an album of one is just a row
  const items: EarlierItem[] = grouped.map((item) =>
    item.kind === 'album' && item.rows.length === 1 ? { kind: 'row', row: item.rows[0] } : item,
  );

  const shown = showAll ? items : items.slice(0, EARLIER_FOLD);
  const hidden = items.length - shown.length;

  return (
    <div className="adl-earlier">
      <div className="adl-section-header">Earlier ({rows.length})</div>
      {live.map((dl) => (
        <AdlRow key={dl.task_id} dl={dl} onCancel={onCancelRow} />
      ))}
      {shown.map((item) =>
        item.kind === 'row' ? (
          <AdlRow key={item.row.task_id} dl={item.row} onCancel={onCancelRow} />
        ) : (
          <div className="adl-earlier-album" key={item.key}>
            <button
              type="button"
              className={`adl-earlier-album-header${openAlbums.has(item.key) ? ' open' : ''}`}
              onClick={() => toggleAlbum(item.key)}
            >
              <span className="adl-bucket-fold-chevron" aria-hidden="true">
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                >
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </span>
              {item.rows[0].artwork ? (
                <img className="adl-earlier-album-thumb" src={item.rows[0].artwork} alt="" />
              ) : null}
              <span className="adl-earlier-album-name">{item.name}</span>
              {item.artist ? <span className="adl-earlier-album-artist">{item.artist}</span> : null}
              <span className="adl-earlier-album-summary">{earlierAlbumSummary(item.rows)}</span>
            </button>
            {openAlbums.has(item.key)
              ? item.rows.map((dl) => (
                  <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />
                ))
              : null}
          </div>
        ),
      )}
      {hidden > 0 || showAll ? (
        <button type="button" className="adl-earlier-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Show fewer' : `Show ${hidden} more`}
        </button>
      ) : null}
    </div>
  );
}

/**
 * The completed-batches tail, moved down from the dead side panel.
 */
export function AdlRecentHistory({
  history,
  onOpenFullHistory,
}: {
  history: AdlBatchHistoryEntry[];
  onOpenFullHistory: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // The whole section is hidden until there is something to show.
  if (history.length === 0) return null;

  const now = Date.now();
  return (
    <div
      className={`adl-batch-history-section${expanded ? ' expanded' : ''}`}
      id="adl-batch-history-section"
    >
      <div className="adl-batch-history-header" onClick={() => setExpanded((open) => !open)}>
        <span>Recent History</span>
        <div className="adl-batch-history-header-actions">
          <button
            type="button"
            className="library-history-btn"
            title="View full download + import history"
            onClick={(event) => {
              event.stopPropagation();
              onOpenFullHistory();
            }}
          >
            Download History
          </button>
          <svg
            className="adl-batch-history-chevron"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>
      </div>
      <div className="adl-batch-history-list" id="adl-batch-history-list">
        {history.map((entry, index) => {
          const downloaded = entry.tracks_downloaded || 0;
          const failed = entry.tracks_failed || 0;
          const total = entry.total_tracks || 0;
          return (
            <div className="adl-batch-history-item" key={`${entry.playlist_name}-${index}`}>
              <span
                className="adl-batch-history-dot"
                style={{ background: `rgba(${historyDotColor(entry.source_page)}, 0.6)` }}
              />
              <div className="adl-batch-history-name">
                {entry.playlist_name || 'Unknown'}{' '}
                {entry.source_page ? (
                  <span className="adl-group-source">{entry.source_page}</span>
                ) : null}
              </div>
              <div className="adl-batch-history-stats">
                {downloaded}/{total}
                {failed > 0 ? <span style={{ color: '#ef4444' }}> {failed} failed</span> : null}
              </div>
              <div className="adl-batch-history-date">{historyAgo(entry.completed_at, now)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The nothing-at-all empty state, with somewhere to go. */
export function AdlDownloadsEmpty({ onNavigate }: { onNavigate: (page: string) => void }) {
  return (
    <div className="adl-empty adl-empty-hero" id="adl-empty">
      <div className="adl-empty-icon">
        <svg
          width="34"
          height="34"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
      </div>
      <div className="adl-empty-title">Nothing downloading</div>
      <div className="adl-empty-sub">Start something from one of these and it lands here.</div>
      <div className="adl-empty-links">
        {['search', 'sync', 'wishlist'].map((page) => (
          <a
            key={page}
            href="#"
            onClick={(event) => {
              event.preventDefault();
              onNavigate(page);
            }}
          >
            {page[0].toUpperCase() + page.slice(1)}
          </a>
        ))}
      </div>
    </div>
  );
}

export interface AdlGroupedListProps {
  /** Rows AFTER the status + batch filters — what the chips decided. */
  rows: AdlDownload[];
  /** All rows, for per-batch unfiltered counts. */
  allRows: AdlDownload[];
  /** Live batches, already fade-filtered by the hook. */
  batches: AdlBatch[];
  history: AdlBatchHistoryEntry[];
  filterBatchId: string | null;
  /** True when a status chip other than All is active. */
  statusFiltered: boolean;
  batchOpacity: (batchId: string, phase: string) => number;
  samplesFor: (batchId: string) => RateSample[];
  onFilterBatch: (batchId: string) => void;
  onCancelBatch: (batch: AdlBatch) => void;
  onOpenBatchModal: (batch: AdlBatch) => void;
  onOpenFullHistory: () => void;
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
  onNavigate: (page: string) => void;
}

/**
 * The Downloads view: batches as groups, then the batchless tail, then the
 * recent-history fold. One rendering of the truth — this replaces both the
 * old flat list AND the 366px side panel.
 */
export function AdlGroupedList({
  rows,
  allRows,
  batches,
  history,
  filterBatchId,
  statusFiltered,
  batchOpacity,
  samplesFor,
  onFilterBatch,
  onCancelBatch,
  onOpenBatchModal,
  onOpenFullHistory,
  onCancelRow,
  onNavigate,
}: AdlGroupedListProps) {
  const batchIds = new Set(batches.map((b) => b.batch_id));
  let shownBatches = filterBatchId ? batches.filter((b) => b.batch_id === filterBatchId) : batches;
  // Under a status chip, a group with zero matching rows is pure noise — the
  // chip asked for a kind of row, not a tour of every batch.
  if (statusFiltered && !filterBatchId) {
    shownBatches = shownBatches.filter((b) => rows.some((dl) => dl.batch_id === b.batch_id));
  }
  const earlier = rows.filter((dl) => !dl.batch_id || !batchIds.has(dl.batch_id));

  if (batches.length === 0 && rows.length === 0) {
    return (
      <div className="adl-list adl-group-grid" id="adl-list">
        <AdlDownloadsEmpty onNavigate={onNavigate} />
        <AdlRecentHistory history={history} onOpenFullHistory={onOpenFullHistory} />
      </div>
    );
  }

  return (
    <div className="adl-list adl-group-grid" id="adl-list">
      {shownBatches.map((batch) => (
        <AdlGroup
          key={batch.batch_id}
          batch={batch}
          rows={rows.filter((dl) => dl.batch_id === batch.batch_id)}
          allBatchRows={allRows.filter((dl) => dl.batch_id === batch.batch_id)}
          bucketed={!statusFiltered && !filterBatchId}
          filtered={filterBatchId === batch.batch_id}
          opacity={batchOpacity(batch.batch_id, batch.phase)}
          samples={samplesFor(batch.batch_id)}
          onFilter={() => onFilterBatch(batch.batch_id)}
          onCancel={() => onCancelBatch(batch)}
          onOpenModal={() => onOpenBatchModal(batch)}
          onCancelRow={onCancelRow}
        />
      ))}
      {!filterBatchId ? <AdlEarlierGroup rows={earlier} onCancelRow={onCancelRow} /> : null}
      {!filterBatchId ? (
        <AdlRecentHistory history={history} onOpenFullHistory={onOpenFullHistory} />
      ) : null}
    </div>
  );
}
