import { useState } from 'react';

import type { RateSample } from '../-adl.helpers';
import type { AdlBatch, AdlBatchHistoryEntry, AdlDownload } from '../-adl.types';

import {
  batchSummary,
  isBatchActive,
  isTerminalPhase,
  nowTrack,
  phaseDisplay,
  progressSegments,
  showTrackProgressBar,
  statChips,
  trackRowState,
  historyAgo,
  historyDotColor,
} from '../-adl.batch';
import { batchColorIndex, batchEta, statusClass } from '../-adl.helpers';

function PhaseIcon({ icon }: { icon: 'spinner' | 'check' | 'hourglass' | null }) {
  if (icon === 'spinner') return <span className="adl-spinner" style={{ marginRight: '4px' }} />;
  if (icon === 'check') return <span style={{ color: '#22c55e', marginRight: '4px' }}>✓</span>;
  if (icon === 'hourglass') return <span style={{ marginRight: '4px', opacity: 0.6 }}>⏳</span>;
  return null;
}

/**
 * The batch thumbnail: the first track's artwork, or the batch's initial.
 *
 * The vanilla built the fallback as an `onerror` string with nested escaped
 * quotes — fragile enough that a name containing a quote could break it. Here
 * a failed load just flips state.
 */
function BatchThumb({
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
    return (
      <img className="adl-batch-card-thumb" src={artwork} alt="" onError={() => setFailed(true)} />
    );
  }
  return (
    <div
      className="adl-batch-card-thumb adl-batch-card-thumb-fallback"
      style={
        colorIdx >= 0
          ? {
              background: `rgba(var(--batch-color-${colorIdx}), 0.15)`,
              color: `rgba(var(--batch-color-${colorIdx}), 0.7)`,
            }
          : { background: 'rgba(255,255,255,0.05)', color: 'rgba(255,255,255,0.4)' }
      }
    >
      {/* The image fallback keeps the raw initial; the no-art one upper-cases. */}
      {artwork ? initial : initial.toUpperCase()}
    </div>
  );
}

function BatchTrackRow({ track, index }: { track: AdlDownload; index: number }) {
  const cls = statusClass(track.status);
  const state = trackRowState(track, cls);
  const downloading = showTrackProgressBar(track);
  const progress = track.progress || 0;

  return (
    <div className={`adl-batch-track-row ${cls}${downloading ? ' downloading' : ''}`}>
      <span className="adl-batch-track-idx">{index}</span>
      <span className="adl-batch-track-text">
        <span className="adl-batch-track-title">{track.title || 'Unknown'}</span>
        {track.artist ? <span className="adl-batch-track-sub">{track.artist}</span> : null}
      </span>
      <span className="adl-batch-track-state" title={state.title}>
        {state.spinner ? (
          <span className="adl-spinner" style={{ width: '9px', height: '9px' }} />
        ) : (
          state.text
        )}
      </span>
      {downloading ? (
        <div className="adl-batch-track-progress">
          <div className="adl-batch-track-progress-fill" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
    </div>
  );
}

export interface AdlBatchCardProps {
  batch: AdlBatch;
  tracks: AdlDownload[];
  expanded: boolean;
  filtered: boolean;
  opacity: number;
  samples: RateSample[];
  onToggle: () => void;
  onFilter: () => void;
  onCancel: () => void;
  onOpenModal: () => void;
}

export function AdlBatchCard({
  batch,
  tracks,
  expanded,
  filtered,
  opacity,
  samples,
  onToggle,
  onFilter,
  onCancel,
  onOpenModal,
}: AdlBatchCardProps) {
  const colorIdx = batchColorIndex(batch.batch_id);
  const phase = phaseDisplay(batch);
  const segments = progressSegments(batch);
  const chips = statChips(batch);
  const eta = batchEta(batch, samples, Date.now());
  const active = isBatchActive(batch);
  const terminal = isTerminalPhase(batch.phase);
  const live = nowTrack(batch, tracks);
  const artwork = tracks.find((t) => t.artwork)?.artwork ?? null;

  const classes = ['adl-batch-card', `phase-${batch.phase}`];
  if (expanded) classes.push('expanded');
  if (active) classes.push('active-glow');
  if (filtered) classes.push('filtered');

  return (
    <div
      className={classes.join(' ')}
      style={{
        ...(colorIdx >= 0 ? { borderLeftColor: `rgba(var(--batch-color-${colorIdx}), 0.6)` } : {}),
        ...(opacity < 1 ? { opacity } : {}),
      }}
      data-batch-id={batch.batch_id}
      onClick={onToggle}
    >
      <div className="adl-batch-card-top">
        <BatchThumb batch={batch} artwork={artwork} colorIdx={colorIdx} />
        <div className="adl-batch-card-info">
          <div
            className="adl-batch-card-name adl-batch-card-link"
            title="Open download modal"
            onClick={(event) => {
              event.stopPropagation();
              onOpenModal();
            }}
          >
            {batch.batch_name || 'Download'}
          </div>
          <div className="adl-batch-card-meta">
            <PhaseIcon icon={phase.icon} />
            {phase.text}
          </div>
          {live?.title ? (
            <div className="adl-batch-card-now">
              <span className="adl-batch-now-icon">↓</span> {live.title}
            </div>
          ) : null}
        </div>
        {batch.source_page ? (
          <span className="adl-batch-card-source">{batch.source_page}</span>
        ) : null}
        <div className="adl-batch-card-actions">
          <button
            type="button"
            className={`adl-batch-card-filter ${filtered ? 'active' : ''}`}
            title={filtered ? 'Show all downloads' : 'Filter to this batch'}
            onClick={(event) => {
              event.stopPropagation();
              onFilter();
            }}
          >
            <svg
              width="13"
              height="13"
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
              className="adl-batch-card-cancel"
              title="Cancel batch"
              onClick={(event) => {
                event.stopPropagation();
                onCancel();
              }}
            >
              <svg
                width="13"
                height="13"
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
          <span className="adl-batch-card-chevron" aria-hidden="true">
            <svg
              width="13"
              height="13"
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

      <div className="adl-batch-segbar">
        <div className="adl-batch-seg seg-done" style={{ width: `${segments.done}%` }} />
        <div className="adl-batch-seg seg-fail" style={{ width: `${segments.failed}%` }} />
        <div
          className={`adl-batch-seg seg-active${active ? ' shimmer' : ''}`}
          style={{ width: `${segments.active}%` }}
        />
      </div>

      {chips.length || eta ? (
        <div className="adl-batch-statline">
          <div className="adl-batch-chips">
            {chips.map((chip) => (
              <span key={chip.text} className={chip.className}>
                {chip.text}
              </span>
            ))}
          </div>
          {eta ? <span className="adl-batch-eta">{eta}</span> : null}
        </div>
      ) : null}

      <div className="adl-batch-tracks">
        {expanded ? (
          tracks.length > 0 ? (
            tracks.map((track, i) => (
              <BatchTrackRow
                key={track.task_id}
                track={track}
                index={track.track_index != null ? track.track_index + 1 : i + 1}
              />
            ))
          ) : batch.phase === 'album_downloading' ? (
            <div className="adl-batch-release-note">
              Downloading one release first. Track matching starts after staging.
            </div>
          ) : (
            <div style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.3)', padding: '4px 0' }}>
              No tracks loaded
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}

export function AdlBatchEmpty({ onNavigate }: { onNavigate: (page: string) => void }) {
  return (
    <div className="adl-batch-empty">
      <div className="adl-batch-empty-icon">
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
      <div className="adl-batch-empty-title">Nothing downloading</div>
      <div className="adl-batch-empty-sub">Batches show up here as they run.</div>
      <div className="adl-batch-empty-links">
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

export function AdlBatchHistory({
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
                  <span
                    className="adl-batch-card-source"
                    style={{ fontSize: '0.6rem', padding: '0 4px' }}
                  >
                    {entry.source_page}
                  </span>
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

export interface AdlBatchPanelProps {
  batches: AdlBatch[];
  downloads: AdlDownload[];
  history: AdlBatchHistoryEntry[];
  expandedBatches: ReadonlySet<string>;
  filterBatchId: string | null;
  batchOpacity: (batchId: string, phase: string) => number;
  samplesFor: (batchId: string) => RateSample[];
  onToggleBatch: (batchId: string) => void;
  onFilterBatch: (batchId: string) => void;
  onCancelBatch: (batch: AdlBatch) => void;
  onOpenBatchModal: (batch: AdlBatch) => void;
  onOpenFullHistory: () => void;
  onNavigate: (page: string) => void;
}

export function AdlBatchPanel({
  batches,
  downloads,
  history,
  expandedBatches,
  filterBatchId,
  batchOpacity,
  samplesFor,
  onToggleBatch,
  onFilterBatch,
  onCancelBatch,
  onOpenBatchModal,
  onOpenFullHistory,
  onNavigate,
}: AdlBatchPanelProps) {
  const [collapsed, setCollapsed] = useState(false);

  const activeBatches = batches.filter((b) => !isTerminalPhase(b.phase));
  const summary = batchSummary(activeBatches, samplesFor, Date.now());

  return (
    <div className={`adl-batch-panel${collapsed ? ' collapsed' : ''}`} id="adl-batch-panel">
      <div className="adl-batch-panel-header">
        <h3 className="adl-batch-panel-title">
          {activeBatches.length > 0 ? `Batches (${activeBatches.length})` : 'Batches'}
        </h3>
        <button
          type="button"
          className="adl-batch-panel-collapse"
          id="adl-batch-collapse"
          title="Toggle batch panel"
          onClick={() => setCollapsed((open) => !open)}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      </div>

      {summary ? (
        <div className="adl-batch-summary" id="adl-batch-summary">
          <span className="adl-batch-summary-main">{summary.main}</span>
          {summary.eta ? <span className="adl-batch-summary-eta">{summary.eta}</span> : null}
        </div>
      ) : null}

      <div className="adl-batch-active" id="adl-batch-active">
        {batches.length === 0 ? (
          <AdlBatchEmpty onNavigate={onNavigate} />
        ) : (
          batches.map((batch) => (
            <AdlBatchCard
              key={batch.batch_id}
              batch={batch}
              tracks={downloads.filter((d) => d.batch_id === batch.batch_id)}
              expanded={expandedBatches.has(batch.batch_id)}
              filtered={filterBatchId === batch.batch_id}
              opacity={batchOpacity(batch.batch_id, batch.phase)}
              samples={samplesFor(batch.batch_id)}
              onToggle={() => onToggleBatch(batch.batch_id)}
              onFilter={() => onFilterBatch(batch.batch_id)}
              onCancel={() => onCancelBatch(batch)}
              onOpenModal={() => onOpenBatchModal(batch)}
            />
          ))
        )}
      </div>

      <AdlBatchHistory history={history} onOpenFullHistory={onOpenFullHistory} />
    </div>
  );
}
