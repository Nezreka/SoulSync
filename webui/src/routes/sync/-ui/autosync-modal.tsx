/**
 * The Auto-Sync Manager modal shell — auto-sync.js 571-740, plus the bulk
 * popover at 1256-1313.
 *
 * The vanilla builds this by appending a div to document.body and rewriting
 * its innerHTML on every change (571-593, 652-731). React renders it in place,
 * so the overlay lifecycle, the `#auto-sync-schedule-modal` lookup that every
 * handler starts with, and the manual `overlay.remove()` all dissolve.
 *
 * SCROLL PRESERVATION DISSOLVES TOO, and that is worth spelling out because it
 * looks like a feature being dropped. The vanilla reads `.scrollTop` off the
 * active tab's lane board before the re-render and writes it back afterwards
 * (657-661, 736-739) — because innerHTML destroys and rebuilds every node, so
 * dropping a playlist used to snap the board back to the top. React keeps the
 * same DOM nodes across a state change, so the scroll position is never lost
 * and there is nothing to restore.
 *
 * THE WINDOW.PROMPT IS REPLACED, not ported. `promptAutoSyncBulkCustom` (1305)
 * calls `window.prompt` for the custom interval, which this repo forbids
 * outright. It becomes an inline field in the bulk popover; the validation
 * itself is unchanged and lives in `autoSyncParseCustomInterval`.
 */

import { useEffect, useRef, useState } from 'react';

import {
  AUTO_SYNC_BUCKETS,
  autoSyncIntervalLabel,
  autoSyncNormalizeTab,
  autoSyncParseCustomInterval,
  autoSyncSourceLabel,
  autoSyncSummary,
  type AutoSyncHistoryEntry,
  type AutoSyncHistoryFilter,
  type AutoSyncHourlyEntry,
  type AutoSyncScheduleState,
  type AutoSyncTab,
} from '../-sync.autosync';
import { AutoSyncBoard, type AutoSyncBoardActions } from './autosync-board';
import { AutoSyncHistoryPanel } from './autosync-history';
import { AutoSyncAutomationPanel, AutoSyncMonitorPanel } from './autosync-panels';
import { AutoSyncWeeklyBoard, type AutoSyncWeeklyBoardActions } from './autosync-weekly';

const TAB_LABELS: Record<AutoSyncTab, string> = {
  schedule: 'Hourly Board',
  weekly: 'Weekly Board',
  automations: 'Automation Pipelines',
  history: 'Run History',
};

export interface AutoSyncBulkMenuProps {
  source: string;
  /** Where the anchor button sits, so the popover lands under it (1285-1287). */
  anchor: { top: number; left: number };
  onSchedule: (hours: number) => void;
  onUnschedule: () => void;
  onClose: () => void;
}

/** 1256-1301, with the custom-interval prompt folded in. */
export function AutoSyncBulkMenu({
  source,
  anchor,
  onSchedule,
  onUnschedule,
  onClose,
}: AutoSyncBulkMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [custom, setCustom] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    // 1289-1292: an outside click closes it. The vanilla defers the listener
    // by a tick so the click that OPENED the menu does not immediately close
    // it; the same hazard exists here, so the same defer does.
    const onDocClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => {
      document.addEventListener('click', onDocClick);
    }, 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', onDocClick);
    };
  }, [onClose]);

  const submitCustom = () => {
    const parsed = autoSyncParseCustomInterval(custom ?? '');
    if ('error' in parsed) {
      setError(parsed.error);
      return;
    }
    onSchedule(parsed.hours);
  };

  return (
    <div
      ref={ref}
      className="auto-sync-bulk-menu"
      id="auto-sync-bulk-menu"
      style={{ top: `${anchor.top}px`, left: `${anchor.left}px` }}
    >
      <div className="auto-sync-bulk-menu-title">Schedule all {autoSyncSourceLabel(source)}</div>
      <div className="auto-sync-bulk-menu-buckets">
        {AUTO_SYNC_BUCKETS.map((h) => (
          <button
            key={h}
            type="button"
            onClick={() => {
              onSchedule(h);
            }}
          >
            {autoSyncIntervalLabel(h)}
          </button>
        ))}
      </div>
      {custom === null ? (
        <button
          type="button"
          className="auto-sync-bulk-menu-custom"
          onClick={() => {
            setCustom('6');
          }}
        >
          Custom interval…
        </button>
      ) : (
        <div className="auto-sync-bulk-menu-custom-row">
          <input
            type="number"
            min={1}
            aria-label="Custom interval in hours"
            placeholder="e.g. 6, 36, 96"
            value={custom}
            autoFocus
            onChange={(e) => {
              setCustom(e.target.value);
              setError('');
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitCustom();
            }}
          />
          <button type="button" onClick={submitCustom}>
            Schedule
          </button>
          {error ? <small className="auto-sync-bulk-menu-error">{error}</small> : null}
        </div>
      )}
      <button type="button" className="auto-sync-bulk-menu-unschedule" onClick={onUnschedule}>
        Unschedule all
      </button>
    </div>
  );
}

export interface AutoSyncModalProps {
  state: AutoSyncScheduleState;
  /** Non-null while the first load or a refresh is in flight (588). */
  loading: boolean;
  /** Set when the load failed outright — replaces the whole body (640-649). */
  loadError: string | null;
  now: number;
  historyFilter: AutoSyncHistoryFilter;
  onHistoryFilterChange: (filter: AutoSyncHistoryFilter) => void;
  onLoadMoreHistory: () => void;
  onRefresh: () => void;
  onClose: () => void;
  boardActions: Omit<AutoSyncBoardActions, 'onRefresh' | 'onBulkMenu'>;
  weeklyActions: Omit<AutoSyncWeeklyBoardActions, 'onRefresh'>;
  onBulkSchedule: (source: string, hours: number) => void;
  onBulkUnschedule: (source: string) => void;
  onOpenDetails: (playlistId: number) => void;
  onRunAgain: (playlistId: number, playlistName: string) => void;
}

export function AutoSyncModal({
  state,
  loading,
  loadError,
  now,
  historyFilter,
  onHistoryFilterChange,
  onLoadMoreHistory,
  onRefresh,
  onClose,
  boardActions,
  weeklyActions,
  onBulkSchedule,
  onBulkUnschedule,
  onOpenDetails,
  onRunAgain,
}: AutoSyncModalProps) {
  const [tab, setTab] = useState<AutoSyncTab>('schedule');
  const [bulk, setBulk] = useState<{ source: string; top: number; left: number } | null>(null);

  const summary = autoSyncSummary(state);

  /**
   * Escape closes, like every other overlay on this page.
   *
   * The vanilla had no key handler here, and the port inherited that — but it
   * is not a house style worth matching: the genre browser, the server search
   * overlay and the server disambiguation modal all wire Escape. This one was
   * simply the odd one out, and it is the largest surface of the four.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  /**
   * ONE description of what this modal is.
   *
   * The vanilla gave the error, loading and ready states three different
   * blurbs — so the explanation of the feature changed depending on whether its
   * data had arrived. This is the accurate one; the state-specific message
   * belongs in the body, where it already is.
   */
  const BLURB =
    'Schedule mirrored playlists through the same playlist-pipeline engine used by Automations.';

  // 594: a click on the overlay itself — never on the modal — closes it.
  const overlay = (children: React.ReactNode) => (
    <div
      id="auto-sync-schedule-modal"
      className="auto-sync-overlay"
      style={{ display: 'flex' }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {children}
    </div>
  );

  const header = (blurb: string) => (
    <div className="auto-sync-header">
      <div>
        <div className="auto-sync-eyebrow">Playlist automation</div>
        <h3>Auto-Sync Manager</h3>
        <p>{blurb}</p>
      </div>
      {/* ONE Refresh. The vanilla grew four — monitor, hourly board, weekly
          board and history — all calling this same handler, so which one you
          reached for depended only on where you happened to be looking. */}
      <div className="auto-sync-header-actions">
        <button type="button" className="auto-sync-refresh" onClick={onRefresh}>
          Refresh
        </button>
        <button type="button" className="auto-sync-close" onClick={onClose}>
          &times;
        </button>
      </div>
    </div>
  );

  if (loadError) {
    return overlay(
      <div className="auto-sync-modal">
        {header(BLURB)}
        <div className="auto-sync-error">
          <p>{loadError}</p>
          {/* Without this the only way out of a failed load was to close the
              modal and reopen it — which the user has to guess at. */}
          <button type="button" className="auto-sync-error-retry" onClick={onRefresh}>
            Try again
          </button>
        </div>
      </div>,
    );
  }

  if (loading) {
    return overlay(
      <div className="auto-sync-modal">
        {header(BLURB)}
        <div className="auto-sync-loading">Loading schedule...</div>
      </div>,
    );
  }

  return overlay(
    <div className="auto-sync-modal">
      {header(BLURB)}
      {/* One fact per slot, and the better fact.
          - "scheduled playlists" and "active schedules" were the same number
            until something was paused. Paused is the interesting half, so it
            rides along and only appears when it is not zero.
          - "mirrored tracks" was never about scheduling at all. Failed runs
            are, and they are the one number here you would act on. */}
      <div className="auto-sync-summary">
        <div>
          <span>{summary.scheduledCount}</span>
          <small>
            scheduled {summary.scheduledCount === 1 ? 'playlist' : 'playlists'}
            {summary.pausedCount > 0 ? ` · ${summary.pausedCount} paused` : ''}
          </small>
        </div>
        <div>
          <span>{summary.pipelineCount}</span>
          <small>automation pipelines</small>
        </div>
        {summary.historyErrorCount > 0 && (
          <div className="auto-sync-summary-bad">
            <span>{summary.historyErrorCount}</span>
            <small>failed {summary.historyErrorCount === 1 ? 'run' : 'runs'}</small>
          </div>
        )}
      </div>

      <AutoSyncMonitorPanel playlists={state.playlists} onDetails={onOpenDetails} />

      <div className="auto-sync-tabs">
        {(Object.keys(TAB_LABELS) as AutoSyncTab[]).map((key) => (
          <button
            key={key}
            type="button"
            className={tab === key ? 'active' : ''}
            onClick={() => {
              setTab(autoSyncNormalizeTab(key));
            }}
          >
            {TAB_LABELS[key]}
            {key === 'history' && summary.historyErrorCount ? (
              <span className="auto-sync-tab-badge error">{summary.historyErrorCount}</span>
            ) : null}
          </button>
        ))}
      </div>

      {/* The vanilla renders all four panels and toggles `.active` (727-730).
          Doing the same keeps each board's filter and expanded state alive
          across a tab switch, which an unmount would throw away. */}
      <div
        className={`auto-sync-tab-panel ${tab === 'schedule' ? 'active' : ''}`}
        id="auto-sync-schedule-panel"
      >
        <AutoSyncBoard
          playlists={state.playlists}
          playlistSchedules={state.playlistSchedules}
          runHistory={state.runHistory}
          now={now}
          actions={{
            ...boardActions,
            onRefresh,
            onBulkMenu: (e, source) => {
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              // 1286-1287: below the button, right-aligned, clamped off the edge.
              setBulk({ source, top: rect.bottom + 4, left: Math.max(8, rect.right - 220) });
            },
          }}
        />
      </div>

      <div
        className={`auto-sync-tab-panel ${tab === 'weekly' ? 'active' : ''}`}
        id="auto-sync-weekly-panel"
      >
        <AutoSyncWeeklyBoard
          playlists={state.playlists}
          weeklySchedules={state.weeklySchedules}
          playlistSchedules={state.playlistSchedules as Record<string, AutoSyncHourlyEntry>}
          runHistory={state.runHistory}
          now={now}
          actions={{ ...weeklyActions, onRefresh }}
        />
      </div>

      <div
        className={`auto-sync-tab-panel ${tab === 'automations' ? 'active' : ''}`}
        id="auto-sync-automation-panel"
      >
        <AutoSyncAutomationPanel
          automationPipelines={state.automationPipelines}
          playlists={state.playlists}
          now={now}
        />
      </div>

      <div
        className={`auto-sync-tab-panel ${tab === 'history' ? 'active' : ''}`}
        id="auto-sync-history-panel"
      >
        <AutoSyncHistoryPanel
          history={state.runHistory as AutoSyncHistoryEntry[]}
          total={state.runHistoryTotal}
          filter={historyFilter}
          onFilterChange={onHistoryFilterChange}
          onLoadMore={onLoadMoreHistory}
          onRunAgain={onRunAgain}
          now={now}
        />
      </div>

      {bulk ? (
        <AutoSyncBulkMenu
          source={bulk.source}
          anchor={{ top: bulk.top, left: bulk.left }}
          onSchedule={(hours) => {
            setBulk(null);
            onBulkSchedule(bulk.source, hours);
          }}
          onUnschedule={() => {
            setBulk(null);
            onBulkUnschedule(bulk.source);
          }}
          onClose={() => {
            setBulk(null);
          }}
        />
      ) : null}
    </div>,
  );
}
