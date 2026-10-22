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
} from '../-adl.batch';
import { batchColorIndex, batchEta, statusClass } from '../-adl.helpers';
import { dayKey, dayLabel, parseStamp } from '../-dl.format';
import { AdlRow } from './adl-row';
import { DlAvatar, DlButton, DlIcon, DlMore } from './dl-parts';

function queueOrder(row: AdlDownload): number {
  return typeof row.batch_position === 'number' ? row.batch_position : Number.MAX_SAFE_INTEGER;
}

function sortByBatchPosition(rows: AdlDownload[]): AdlDownload[] {
  return [...rows].sort((a, b) => queueOrder(a) - queueOrder(b) || a.timestamp - b.timestamp);
}

/**
 * The group's cover: the first track's artwork, or the batch's initial on a
 * tint of its batch colour. a failed load just flips state.
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
    return <img className="dlc-gart" src={artwork} alt="" onError={() => setFailed(true)} />;
  }
  return (
    <div
      className="dlc-gart dlc-gart-empty"
      style={
        colorIdx >= 0
          ? {
              background: `rgba(var(--batch-color-${colorIdx}), 0.15)`,
              color: `rgba(var(--batch-color-${colorIdx}), 0.8)`,
            }
          : undefined
      }
    >
      {initial.toUpperCase()}
    </div>
  );
}

/**
 * A playlist batch has no one cover: show four of its covers as a 2x2 tile,
 * the way a playlist looks everywhere else. Null when there aren't four.
 */
function CoverMosaic({ covers }: { covers: string[] }) {
  if (covers.length < 4) return null;
  return (
    <div className="dlc-gart dlc-mosaic" aria-hidden="true">
      {covers.slice(0, 4).map((src) => (
        <img key={src} src={src} alt="" loading="lazy" />
      ))}
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
    <button
      type="button"
      className={`dlc-fold${open ? ' dlc-fold-open' : ''}`}
      aria-expanded={open}
      onClick={onToggle}
    >
      <span className="dlc-fold-chev" aria-hidden="true">
        {DlIcon.fold}
      </span>
      {count} {noun}
      {!open && preview ? <span className="dlc-fold-preview"> · next: {preview}</span> : null}
    </button>
  );
}

interface RowHandlers {
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
  onDownloadNext: (dl: AdlDownload) => void | Promise<void>;
  numbered: boolean;
  canInspect: boolean;
}

/**
 * What a mid-flight batch actually shows: the rows that are MOVING and the
 * rows that BROKE, in full. the queue and the done pile are facts, not
 * events, so they fold to one line each. that's what turned a 12-track sync
 * from a screen and a half of rows into five lines.
 */
function BucketedRows({
  rows,
  openBuckets,
  onToggleBucket,
  onCancelRow,
  onDownloadNext,
  numbered,
  canInspect,
}: RowHandlers & {
  rows: AdlDownload[];
  openBuckets: ReadonlySet<string>;
  onToggleBucket: (key: string) => void;
}) {
  const inFlight = rows.filter((dl) => statusClass(dl.status) === 'active');
  const broken = rows.filter((dl) => ['failed', 'cancelled'].includes(statusClass(dl.status)));
  const queued = sortByBatchPosition(rows.filter((dl) => statusClass(dl.status) === 'queued'));
  const done = sortByBatchPosition(rows.filter((dl) => statusClass(dl.status) === 'completed'));
  const row = (dl: AdlDownload, next?: boolean) => (
    <AdlRow
      key={dl.task_id}
      dl={dl}
      compact
      numbered={numbered}
      canInspect={canInspect}
      onCancel={onCancelRow}
      onDownloadNext={next ? onDownloadNext : undefined}
    />
  );

  return (
    <>
      {inFlight.map((dl) => row(dl))}
      {broken.map((dl) => row(dl))}
      {queued.length > 0 ? (
        <>
          <BucketFold
            count={queued.length}
            noun="queued"
            preview={queued[0]?.title}
            open={openBuckets.has('queued')}
            onToggle={() => onToggleBucket('queued')}
          />
          {openBuckets.has('queued') ? queued.map((dl) => row(dl, true)) : null}
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
          {openBuckets.has('done') ? done.map((dl) => row(dl)) : null}
        </>
      ) : null}
    </>
  );
}

/** "Audiobook" / "From wishlist" / "Album · from Discover". */
function groupEyebrow(batch: AdlBatch, isAlbum: boolean): string {
  const kind =
    batch.batch_type === 'audiobook' || batch.playlist_id === 'audiobooks'
      ? 'Audiobook'
      : batch.batch_type === 'music_video'
        ? 'Music videos'
        : batch.batch_type === 'client_match'
          ? 'Client import'
          : batch.album_bundle || isAlbum
            ? 'Album'
            : 'Tracks';
  const from = batch.source_page ? `from ${batch.source_page.replace(/_/g, ' ')}` : '';
  return [kind, from].filter(Boolean).join(' · ');
}

/** one value when every row agrees on it, else ''. */
function shared(rows: AdlDownload[], pick: (dl: AdlDownload) => string): string {
  const first = rows.length ? pick(rows[0]) : '';
  return first && rows.every((dl) => pick(dl) === first) ? first : '';
}

export interface AdlGroupProps {
  batch: AdlBatch;
  /** The batch's rows AFTER the page's status filter. */
  rows: AdlDownload[];
  /** The batch's FULL row set: artwork lookup and the trimmed count. */
  allBatchRows: AdlDownload[];
  /**
   * When true, queued and done rows fold into one-line summaries and only
   * in-flight + failed rows render in full. off under a status filter (the
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
  onDownloadNext: (dl: AdlDownload) => void | Promise<void>;
  onDownloadBatchNext: (batch: AdlBatch) => void | Promise<void>;
}

/**
 * One batch: a header with the cover, what it is, where it's at, and its
 * rows underneath. an album batch reads like a tracklist.
 *
 * Open state lives here: live batches start open, terminal ones start
 * folded. the override survives the 2s poll because the component stays
 * mounted, and resets when the batch leaves the list.
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
  onDownloadNext,
  onDownloadBatchNext,
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
  const eta = batchEta(batch, samples, Date.now());
  const active = isBatchActive(batch);
  const canDownloadBatchNext = !terminal && (batch.queued || 0) > 0;
  const orderedRows =
    rows.length > 0 && rows.every((dl) => statusClass(dl.status) !== 'active')
      ? sortByBatchPosition(rows)
      : rows;

  const art = allBatchRows.find((t) => t.artwork)?.artwork ?? null;
  const covers = [...new Set(allBatchRows.map((t) => t.artwork).filter(Boolean))];
  const album = shared(allBatchRows, (dl) => dl.album);
  const artist = shared(allBatchRows, (dl) => dl.artist);
  const artistImage = artist ? allBatchRows.find((dl) => dl.artist_image)?.artist_image : undefined;
  // one album across every row: number the rows like a tracklist
  const audiobook = batch.batch_type === 'audiobook' || batch.playlist_id === 'audiobooks';
  const numbered = Boolean(album) && allBatchRows.length > 1 && !audiobook;
  // the inspector searches music sources, so only music batches get it
  const canInspect = !audiobook && !batch.batch_type;

  // Rows hidden by the status filter still exist. say so instead of letting
  // a filtered group read as a smaller batch than it is.
  const trimmed = allBatchRows.length - rows.length;

  const toggle = () => setOpenOverride(!open);
  const menu = [
    ...(canDownloadBatchNext
      ? [
          {
            key: 'next',
            label: 'Download this batch next',
            onSelect: () => void onDownloadBatchNext(batch),
          },
        ]
      : []),
    {
      key: 'filter',
      label: filtered ? 'Show all downloads' : 'Show only this batch',
      onSelect: onFilter,
    },
    // a finished batch has nothing left to cancel
    ...(!terminal
      ? [{ key: 'cancel', label: 'Cancel batch', danger: true, onSelect: onCancel }]
      : []),
  ];

  return (
    <div
      className={`dlc-list dlc-group phase-${batch.phase}${open ? ' dlc-group-open' : ''}${active ? ' dlc-group-live' : ''}${filtered ? ' dlc-group-filtered' : ''}`}
      style={opacity < 1 ? { opacity } : undefined}
      data-batch-id={batch.batch_id}
    >
      <div
        className="dlc-ghead"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggle();
          }
        }}
      >
        {/* the album art, blurred to a wash behind the header */}
        {art ? <div className="dlc-wash" style={{ backgroundImage: `url(${art})` }} /> : null}
        <div className="dlc-gart-wrap">
          {!album && covers.length >= 4 ? (
            <CoverMosaic covers={covers} />
          ) : (
            // no cover anywhere: the artist's photo stands in for it
            <BatchThumb batch={batch} artwork={art || artistImage || null} colorIdx={colorIdx} />
          )}
          {/* one artist across the batch: their photo sits on the cover's corner */}
          {art && artistImage ? (
            <span className="dlc-gart-badge">
              <DlAvatar src={artistImage} size="md" />
            </span>
          ) : null}
        </div>
        <div className="dlc-main">
          <div className="dlc-eyebrow">{groupEyebrow(batch, Boolean(album))}</div>
          <div className="dlc-gtitle">{batch.batch_name || 'Download'}</div>
          <div className="dlc-gsub">
            {artist && artist !== batch.batch_name ? (
              // the photo already sits on the cover's corner, so just the name here
              <span>{artist}</span>
            ) : null}
            <span className="dlc-gphase">
              {phase.icon === 'spinner' ? (
                <span className="dlc-spin dlc-spin-sm" aria-hidden="true" />
              ) : null}
              {phase.text}
            </span>
            {batch.failed ? <span className="dlc-gbad">{batch.failed} failed</span> : null}
            {eta ? <span>{eta}</span> : null}
          </div>
        </div>
        <div className="dlc-gside">
          <DlButton title="Open the download window for this batch" onClick={onOpenModal}>
            Open
          </DlButton>
          <DlMore items={menu} label="Batch actions" />
          <span
            className={`dlc-icon-btn dlc-chev${open ? ' dlc-chev-open' : ''}`}
            aria-hidden="true"
          >
            {DlIcon.chevron}
          </span>
        </div>
      </div>

      <div className="dlc-gbar" aria-hidden="true">
        <i className="dlc-gbar-done" style={{ width: `${segments.done}%` }} />
        <i className="dlc-gbar-fail" style={{ width: `${segments.failed}%` }} />
        <i
          className={`dlc-gbar-active${active ? ' dlc-shimmer' : ''}`}
          style={{ width: `${segments.active}%` }}
        />
      </div>

      {open ? (
        <div className="dlc-group-rows">
          {bucketed ? (
            <BucketedRows
              rows={rows}
              openBuckets={openBuckets}
              onToggleBucket={toggleBucket}
              onCancelRow={onCancelRow}
              onDownloadNext={onDownloadNext}
              numbered={numbered}
              canInspect={canInspect}
            />
          ) : (
            orderedRows.map((dl) => (
              <AdlRow
                key={dl.task_id}
                dl={dl}
                compact
                numbered={numbered}
                canInspect={canInspect}
                onCancel={onCancelRow}
                onDownloadNext={onDownloadNext}
              />
            ))
          )}
          {rows.length === 0 && batch.phase === 'album_downloading' ? (
            <div className="dlc-note">
              Downloading one release first. Track matching starts after staging.
            </div>
          ) : null}
          {rows.length === 0 && batch.phase !== 'album_downloading' ? (
            <div className="dlc-note">
              {trimmed > 0 ? 'No tracks match the current filter.' : 'No tracks loaded.'}
            </div>
          ) : null}
          {rows.length > 0 && trimmed > 0 ? (
            <div className="dlc-note">{trimmed} more hidden by the current filter.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** How many "earlier" items (rows or album folds) show before the fold. */
export const EARLIER_FOLD = 12;

/** Album runs this short start open; longer ones fold to their header. */
const EARLIER_OPEN_MAX = 3;

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
  | { kind: 'row'; row: AdlDownload; day: number | null }
  | {
      kind: 'album';
      key: string;
      name: string;
      artist: string;
      rows: AdlDownload[];
      day: number | null;
    };

/** when a settled row finished: history rows carry created_at, live ones a status stamp. */
function finishedAt(dl: AdlDownload): number | null {
  return dl.is_persistent_history ? parseStamp(dl.created_at) : parseStamp(dl.timestamp);
}

/** One album run inside a day: a small header, its rows under it. */
function EarlierAlbum({
  item,
  onCancelRow,
}: {
  item: Extract<EarlierItem, { kind: 'album' }>;
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(item.rows.length <= EARLIER_OPEN_MAX);
  const podcast = item.rows.every((dl) => dl.event_type === 'podcast');
  const art = item.rows.find((dl) => dl.artwork)?.artwork;
  return (
    <div className="dlc-sub-group">
      <button
        type="button"
        className={'dlc-sub-head'}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="dlc-fold-chev" aria-hidden="true">
          {DlIcon.fold}
        </span>
        {art ? <img className="dlc-sub-art" src={art} alt="" /> : null}
        <span className="dlc-sub-text">
          {podcast ? <span className="dlc-eyebrow">Podcast</span> : null}
          <span className="dlc-sub-name">{item.name}</span>
          {item.artist ? (
            <span className="dlc-sub-artist">
              <DlAvatar src={item.rows.find((dl) => dl.artist_image)?.artist_image} />
              {item.artist}
            </span>
          ) : null}
        </span>
        <span className="dlc-sub-summary">{earlierAlbumSummary(item.rows)}</span>
      </button>
      {open
        ? item.rows.map((dl) => <AdlRow key={dl.task_id} dl={dl} compact onCancel={onCancelRow} />)
        : null}
    </div>
  );
}

/**
 * Rows with no live batch: aged-out history plus anything batchless.
 *
 * Grouped by day, newest first, with real times. same-album runs collapse
 * under one header (twelve identical Romance (Deluxe Edition) rows stacked
 * full height was the Aug 27 complaint), and past EARLIER_FOLD items the
 * rest folds, because this is where 300-row payloads live.
 */
export function AdlEarlierGroup({
  rows,
  onCancelRow,
}: {
  rows: AdlDownload[];
  onCancelRow: (dl: AdlDownload) => void | Promise<void>;
}) {
  const [showAll, setShowAll] = useState(false);
  if (rows.length === 0) return null;

  // a batchless row that is still moving is not history. it stays a plain
  // row on top and never hides inside an album fold
  const live = rows.filter((dl) => ['active', 'queued'].includes(statusClass(dl.status)));
  const settled = rows.filter((dl) => !['active', 'queued'].includes(statusClass(dl.status)));

  const grouped: EarlierItem[] = [];
  const byKey = new Map<string, Extract<EarlierItem, { kind: 'album' }>>();
  for (const row of settled) {
    const day = finishedAt(row);
    // group by the ALBUM on the same day, not the batch name. history rows
    // carry the download source as their batch name, and "Soulseek · 45
    // done" says nothing. the album run is the unit a human recognizes.
    const key = row.album ? `${dayKey(day)}|${row.artist}|${row.album}` : '';
    if (!key) {
      grouped.push({ kind: 'row', row, day });
      continue;
    }
    let album = byKey.get(key);
    if (!album) {
      album = { kind: 'album', key, name: row.album, artist: row.artist || '', rows: [], day };
      byKey.set(key, album);
      grouped.push(album);
    }
    album.rows.push(row);
  }
  // an album of one is just a row
  const items: EarlierItem[] = grouped.map((item) =>
    item.kind === 'album' && item.rows.length === 1
      ? { kind: 'row', row: item.rows[0], day: item.day }
      : item,
  );

  const shown = showAll ? items : items.slice(0, EARLIER_FOLD);
  const hidden = items.length - shown.length;

  const now = Date.now();
  const days: { key: string; label: string; items: EarlierItem[] }[] = [];
  for (const item of shown) {
    const key = dayKey(item.day) || 'earlier';
    let bucket = days[days.length - 1];
    if (!bucket || bucket.key !== key) {
      bucket = { key, label: dayLabel(item.day, now), items: [] };
      days.push(bucket);
    }
    bucket.items.push(item);
  }

  return (
    <div className="dlc-earlier">
      {live.length ? (
        <div className="dlc-list">
          {live.map((dl) => (
            <AdlRow key={dl.task_id} dl={dl} onCancel={onCancelRow} />
          ))}
        </div>
      ) : null}
      {days.map((bucket) => (
        <section key={bucket.key} className="dlc-day">
          <h3 className="dlc-section-label">{bucket.label}</h3>
          <div className="dlc-list">
            {bucket.items.map((item) =>
              item.kind === 'row' ? (
                <AdlRow key={item.row.task_id} dl={item.row} onCancel={onCancelRow} />
              ) : (
                <EarlierAlbum key={item.key} item={item} onCancelRow={onCancelRow} />
              ),
            )}
          </div>
        </section>
      ))}
      {hidden > 0 || showAll ? (
        <button type="button" className="dlc-more-link" onClick={() => setShowAll((v) => !v)}>
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
                {failed > 0 ? (
                  <span style={{ color: 'var(--danger)' }}> {failed} failed</span>
                ) : null}
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
  onDownloadNext: (dl: AdlDownload) => void | Promise<void>;
  onDownloadBatchNext: (batch: AdlBatch) => void | Promise<void>;
  onNavigate: (page: string) => void;
}

/**
 * The Downloads view: batches as groups, then the batchless tail, then the
 * recent-history fold. One rendering of the truth. this replaces both the
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
  onDownloadNext,
  onDownloadBatchNext,
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
      <div className="adl-list dlc-stack" id="adl-list">
        <AdlDownloadsEmpty onNavigate={onNavigate} />
        <AdlRecentHistory history={history} onOpenFullHistory={onOpenFullHistory} />
      </div>
    );
  }

  return (
    <div className="adl-list dlc-stack" id="adl-list">
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
          onDownloadNext={onDownloadNext}
          onDownloadBatchNext={onDownloadBatchNext}
        />
      ))}
      {!filterBatchId ? <AdlEarlierGroup rows={earlier} onCancelRow={onCancelRow} /> : null}
      {!filterBatchId ? (
        <AdlRecentHistory history={history} onOpenFullHistory={onOpenFullHistory} />
      ) : null}
    </div>
  );
}
