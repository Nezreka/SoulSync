/**
 * The Auto-Sync run-history panel — auto-sync.js 1200-1253 and 1394-1882.
 *
 * TEN OF THE REGION'S 27 FUNCTIONS ARE DEAD and are not ported. Seven have no
 * call site at all — `autoSyncHistoryStatHtml` (1693), `autoSyncHistoryPreviewPill`
 * (1705), `autoSyncHistoryResultPill` (1716), `autoSyncHistorySnapshotHtml`
 * (1805), `autoSyncHistoryObjectHtml` (1824), `autoSyncHistoryLogsHtml` (1845)
 * and `autoSyncHistoryFallbackSummary` (1644) — and three more are reachable
 * only from those: `autoSyncHistoryPreviewText`, `autoSyncHistoryFactHtml` and
 * `autoSyncHumanizeKey`. They are earlier drafts of the detail panel that the
 * current one replaced; `autoSyncHistoryLogsHtml` in particular is a 12-line
 * twin of the 20-line `autoSyncHistoryLogsCompactHtml` that actually runs.
 * Verified by locating every reference to each name in the file.
 *
 * The two-phase render dissolves. The vanilla emits a placeholder marked
 * `data-renderer="pending"` (1233), then a separate pass walks the DOM and
 * builds cards with createElement (1394-1436), then a third binds click and
 * keydown handlers to them (1617-1630). That exists because you cannot put
 * live listeners in an innerHTML string. React renders once, so the
 * placeholder, the `data-renderer` / `data-renderedCount` markers, the
 * post-hoc binding pass and `createAutoSyncHistoryListFallback` — a safety net
 * for "the renderer did not complete" — all have no counterpart.
 *
 * The PER-ROW error isolation does NOT dissolve, and is kept. The vanilla
 * wraps each card build in try/catch and substitutes an error card (1428-1432)
 * so one malformed row cannot blank the whole list. In React a throwing child
 * takes its parent down, so that guarantee needs a real error boundary.
 */

import { Component, useState, type ErrorInfo, type ReactNode } from 'react';

import {
  autoSyncDelta,
  autoSyncDeltaClass,
  autoSyncDeltaLabel,
  autoSyncDurationLabel,
  autoSyncFormatDateTime,
  autoSyncHistoryLogLines,
  autoSyncHistoryMatchesFilter,
  autoSyncHistoryResultPills,
  autoSyncHistoryStats,
  autoSyncHistoryStatusClass,
  autoSyncHistoryStatusLabel,
  autoSyncHistoryTabs,
  autoSyncNormalizeHistoryEntry,
  autoSyncTimeAgo,
  autoSyncValueLabel,
  type AutoSyncHistoryEntry,
  type AutoSyncHistoryFilter,
  type AutoSyncNormalizedEntry,
} from '../-sync.autosync';

/** 1450-1476. The card a row degrades to when its own render throws. */
function HistoryErrorCard({
  entry,
  index,
  message,
}: {
  entry: AutoSyncHistoryEntry | null | undefined;
  index: number;
  message: string;
}) {
  return (
    <article className="auto-sync-history-entry auto-sync-history-entry-error">
      <div className="auto-sync-history-row">
        <div className="auto-sync-history-card-head">
          <div className="auto-sync-history-title-block">
            <div className="auto-sync-history-title-row">
              <span className="auto-sync-card-status-dot disabled" />
              <strong>{entry?.playlist_name || `Run #${entry?.id || index + 1}`}</strong>
              <span className="auto-sync-history-status error">Render error</span>
            </div>
            <small>{message || 'This run history row could not be rendered.'}</small>
          </div>
        </div>
      </div>
    </article>
  );
}

/**
 * The React equivalent of the vanilla's per-row try/catch (1428-1432). Without
 * it a single malformed row would unmount the entire history list, which is
 * strictly worse than what the vanilla does.
 */
class HistoryRowBoundary extends Component<
  { entry: AutoSyncHistoryEntry | null | undefined; index: number; children: ReactNode },
  { message: string | null }
> {
  state: { message: string | null } = { message: null };

  static getDerivedStateFromError(error: unknown) {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keeping the row's identity in the log, since the card itself is gone.
    console.error('Auto-Sync history row failed to render', this.props.entry?.id, error, info);
  }

  render() {
    if (this.state.message !== null) {
      return (
        <HistoryErrorCard
          entry={this.props.entry}
          index={this.props.index}
          message={this.state.message}
        />
      );
    }
    return this.props.children;
  }
}

/** 1768-1784. */
function HistoryStatCard({
  label,
  before,
  after,
  delta,
}: {
  label: string;
  before: number;
  after: number;
  delta: number;
}) {
  return (
    <div className="auto-sync-history-stat">
      <div className="auto-sync-history-stat-label">{label}</div>
      <div className="auto-sync-history-stat-value">
        <span className="stat-before">{before}</span>
        <span className="stat-arrow">&rarr;</span>
        <span className="stat-after">{after}</span>
        {delta ? (
          <span className={`stat-delta ${autoSyncDeltaClass(delta)}`}>
            {delta > 0 ? '+' : ''}
            {delta}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** 1721-1766. */
function HistoryDetail({
  entry,
  onRunAgain,
}: {
  entry: AutoSyncNormalizedEntry;
  onRunAgain: (playlistId: number, playlistName: string) => void;
}) {
  const before = entry.before_json;
  const after = entry.after_json;
  const result = entry.result_json;
  const stats = autoSyncHistoryStats(before, after);
  const pills = autoSyncHistoryResultPills(result);
  const logs = autoSyncHistoryLogLines(entry.log_lines);
  const playlistId = entry.playlist_id || after.playlist_id || before.playlist_id || '';
  const playlistName = entry.playlist_name || after.name || before.name || '';
  const facts: [string, unknown][] = [
    ['Started', autoSyncFormatDateTime(entry.started_at)],
    ['Finished', autoSyncFormatDateTime(entry.finished_at)],
    ['Duration', entry.duration_seconds ? autoSyncDurationLabel(entry.duration_seconds) : '—'],
    ['Source', entry.source || after.source || before.source || '—'],
  ];
  return (
    <>
      <div className="auto-sync-history-stats-grid">
        {stats.map((s) => (
          <HistoryStatCard key={s.label} {...s} />
        ))}
      </div>
      <div className="auto-sync-history-facts-row">
        {facts.map(([label, value]) => (
          <div key={label} className="auto-sync-history-fact">
            <span>{label}</span>
            <strong>{autoSyncValueLabel(value)}</strong>
          </div>
        ))}
      </div>
      {pills.length ? (
        <div className="auto-sync-history-result-row">
          {pills.map((p) => (
            <span key={p.label} className="auto-sync-history-result-pill">
              <em>{p.label}</em>
              {p.value}
            </span>
          ))}
        </div>
      ) : null}
      {playlistId ? (
        <div className="auto-sync-history-detail-actions">
          <button
            type="button"
            className="auto-sync-history-run-again"
            onClick={(e) => {
              // Inherited from 1754 and INERT in both codebases: the detail
              // panel is a sibling of the clickable row, not a child, so the
              // click never had a row handler to bubble into. Kept because it
              // costs nothing and would matter the moment the two are nested.
              e.stopPropagation();
              onRunAgain(parseInt(String(playlistId), 10), playlistName);
            }}
          >
            Run pipeline again
          </button>
        </div>
      ) : null}
      {result.error ? <div className="auto-sync-history-error">{String(result.error)}</div> : null}
      {logs.length ? (
        <div className="auto-sync-history-log-section">
          {logs.map((line, i) => (
            <div
              key={i}
              className={`auto-sync-history-log-line auto-sync-history-log-${line.type}`}
            >
              {line.text}
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

/** 1478-1572. */
function AutoSyncHistoryEntryCard({
  entry: rawEntry,
  index,
  now,
  expanded,
  onToggle,
  onRunAgain,
}: {
  entry: AutoSyncHistoryEntry;
  index: number;
  now: number;
  expanded: boolean;
  onToggle: () => void;
  onRunAgain: (playlistId: number, playlistName: string) => void;
}) {
  const entry = autoSyncNormalizeHistoryEntry(rawEntry, index);
  const status = entry.status || 'completed';
  const before = entry.before_json;
  const after = entry.after_json;
  const started = entry.started_at ? autoSyncTimeAgo(entry.started_at, now) : '';
  const duration = entry.duration_seconds ? autoSyncDurationLabel(entry.duration_seconds) : '';
  const trackDelta = autoSyncDelta(after.track_count, before.track_count);
  const entryId = `auto-sync-history-${entry.id}`;
  const playlistName =
    entry.playlist_name ||
    after.name ||
    before.name ||
    `Playlist #${entry.playlist_id || 'unknown'}`;
  // 1520: a completed run gets no status modifier class at all.
  const statusClass =
    status === 'completed' || status === 'finished' ? '' : `auto-sync-history-entry-${status}`;
  return (
    <article
      className={`auto-sync-history-entry ${statusClass} ${expanded ? 'expanded' : ''}`.trim()}
      id={`${entryId}-card`}
      data-history-entry={entryId}
    >
      <div
        className="auto-sync-history-row"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls={entryId}
        onClick={onToggle}
        onKeyDown={(e) => {
          // 1660-1664: Enter and Space only, and the page must not scroll.
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          onToggle();
        }}
      >
        <span className={`auto-sync-card-status-dot ${autoSyncHistoryStatusClass(status)}`} />
        <div className="auto-sync-history-info">
          <div className="auto-sync-history-name">{playlistName}</div>
          <div className="auto-sync-history-flow">
            <span className="flow-trigger">{entry.trigger_source || 'pipeline'}</span>
            <span className="flow-arrow">-&gt;</span>
            <span className="flow-action">Refresh</span>
            <span className="flow-arrow">-&gt;</span>
            <span className="flow-action">Discover</span>
            <span className="flow-arrow">-&gt;</span>
            <span className="flow-notify">Sync + wishlist</span>
          </div>
          <div className="auto-sync-history-meta-inline">
            <span className={`auto-sync-history-status ${status}`}>
              {autoSyncHistoryStatusLabel(status)}
            </span>
            {started ? <span className="auto-sync-history-time">{started}</span> : null}
            {duration ? <span className="auto-sync-history-duration">{duration}</span> : null}
            <span className={`auto-sync-history-delta ${autoSyncDeltaClass(trackDelta)}`}>
              {autoSyncDeltaLabel(after.track_count, trackDelta, 'tracks')}
            </span>
          </div>
        </div>
        <div className="auto-sync-history-actions">
          <button
            type="button"
            className="auto-sync-history-expand-btn"
            data-history-toggle-button={entryId}
            aria-label="Toggle details"
            onClick={(e) => {
              // 1624: the button must not double-fire through the row.
              e.stopPropagation();
              onToggle();
            }}
          >
            <span className="auto-sync-history-expand-icon">&#9662;</span>
          </button>
        </div>
      </div>
      <div id={entryId} className={`auto-sync-history-detail ${expanded ? 'expanded' : ''}`.trim()}>
        <HistoryDetail entry={entry} onRunAgain={onRunAgain} />
      </div>
    </article>
  );
}

export interface AutoSyncHistoryPanelProps {
  history: AutoSyncHistoryEntry[];
  total: number;
  filter: AutoSyncHistoryFilter;
  onFilterChange: (filter: AutoSyncHistoryFilter) => void;
  onLoadMore: () => void;
  onRunAgain: (playlistId: number, playlistName: string) => void;
  now: number;
}

/** 1200-1243 + 1394-1436. */
export function AutoSyncHistoryPanel({
  history,
  total,
  filter,
  onFilterChange,
  onLoadMore,
  onRunAgain,
  now,
}: AutoSyncHistoryPanelProps) {
  // 1201-1208: an empty WINDOW short-circuits before any chrome renders.
  if (!history.length) {
    return (
      <div className="auto-sync-history-empty">
        <strong>No playlist pipeline runs yet</strong>
        <span>
          Future Auto-Sync and playlist pipeline runs will record before/after playlist snapshots
          here.
        </span>
      </div>
    );
  }

  const visible = history.filter((h) => autoSyncHistoryMatchesFilter(h, filter));
  return (
    <>
      <div className="auto-sync-history-intro">
        <div>
          <strong>Playlist pipeline run history</strong>
          <span>
            Each run records what changed on the mirrored playlist before and after refresh,
            discovery, sync, and wishlist processing.
          </span>
        </div>
        <div className="auto-sync-history-intro-controls">
          <div className="auto-sync-history-filters">
            {autoSyncHistoryTabs(history).map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`auto-sync-history-filter-btn ${filter === tab.key ? 'active' : ''} ${
                  tab.hasErrors ? 'has-errors' : ''
                }`}
                onClick={() => {
                  onFilterChange(tab.key);
                }}
              >
                {tab.label} <em>{tab.count}</em>
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="auto-sync-history-list">
        {/* 1407-1418: a filter that matches nothing says so, but only a
            filter — the unfiltered empty case was handled above. */}
        {!visible.length ? (
          <div className="auto-sync-history-empty">
            <strong>
              {filter === 'error'
                ? 'No failed runs in the loaded window'
                : 'No completed runs in the loaded window'}
            </strong>
            <span>Switch filters or load more history.</span>
          </div>
        ) : (
          visible.map((entry, i) => (
            <HistoryRowBoundary key={String(entry?.id ?? i)} entry={entry} index={i}>
              <HistoryRow entry={entry} index={i} now={now} onRunAgain={onRunAgain} />
            </HistoryRowBoundary>
          ))
        )}
        {/* 1433-1438: the running total sits INSIDE the list. */}
        {total > visible.length ? (
          <div className="auto-sync-history-total">
            Showing {visible.length} of {total} runs
          </div>
        ) : null}
      </div>
      {/* 1235-1241: load-more compares against the UNFILTERED window. */}
      {total > history.length ? (
        <div className="auto-sync-history-load-more-row">
          <button type="button" className="auto-sync-history-load-more" onClick={onLoadMore}>
            Load more ({history.length} of {total})
          </button>
        </div>
      ) : null}
    </>
  );
}

/** Each row owns its own expanded state, as the vanilla's DOM class did. */
function HistoryRow({
  entry,
  index,
  now,
  onRunAgain,
}: {
  entry: AutoSyncHistoryEntry;
  index: number;
  now: number;
  onRunAgain: (playlistId: number, playlistName: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <AutoSyncHistoryEntryCard
      entry={entry}
      index={index}
      now={now}
      expanded={expanded}
      onToggle={() => {
        setExpanded((v) => !v);
      }}
      onRunAgain={onRunAgain}
    />
  );
}
