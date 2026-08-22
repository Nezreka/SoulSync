/**
 * Activity — one place for "what has this page been doing".
 *
 * It replaces two separate surfaces that answered the same question from
 * different angles and lived behind different buttons: the Sync History modal
 * (a manual sync you ran) and the Auto-Sync modal's Run History tab (a
 * scheduled run the pipeline ran). Someone asking "did my playlist update?" had
 * to know which of those two ran it before they knew where to look.
 *
 * Download Origins deliberately stays OUT. The other two answer "what
 * happened"; that one answers "what is on my disk and should some of it go
 * away", with select-all and a delete that removes files. Filing a destructive
 * file manager behind a tab next to two read-only logs makes it easier to reach
 * by accident, and it is also the Watchlist page's only way in — it is not a
 * sync-page surface that happens to be shared.
 */

import { useEffect, useState } from 'react';

import type { AutoSyncHistoryEntry, AutoSyncHistoryFilter } from '../-sync.autosync';
import type { SyncHistoryPanelProps } from './sync-history-panel';

import { AutoSyncHistoryPanel } from './autosync-history';
import { SyncHistoryPanel } from './sync-history-panel';

export type ActivityTab = 'syncs' | 'runs';

/** "Last sync" and "Scheduled runs" say who ran it, which is the real split. */
export const ACTIVITY_TAB_LABELS: Record<ActivityTab, string> = {
  syncs: 'Syncs you ran',
  runs: 'Scheduled runs',
};

export interface ActivityModalProps {
  open: boolean;
  tab: ActivityTab;
  onTab: (tab: ActivityTab) => void;
  onClose: () => void;
  /** Everything the Syncs tab needs, passed straight through. */
  syncs: Omit<SyncHistoryPanelProps, 'now'>;
  runs: {
    history: AutoSyncHistoryEntry[];
    total: number;
    filter: AutoSyncHistoryFilter;
    onFilterChange: (filter: AutoSyncHistoryFilter) => void;
    onLoadMore: () => void;
    onRunAgain: (playlistId: number, playlistName: string) => void;
  };
  /** Badge on the Scheduled runs tab; hidden at zero. */
  failedRuns?: number;
  now: number;
}

export function ActivityModal({
  open,
  tab,
  onTab,
  onClose,
  syncs,
  runs,
  failedRuns = 0,
  now,
}: ActivityModalProps) {
  const [mounted, setMounted] = useState(open);

  useEffect(() => {
    if (open) setMounted(true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open && !mounted) return null;

  return (
    <div
      className={`sync-activity-overlay${open ? '' : ' hidden'}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="sync-activity-modal" role="dialog" aria-modal="true" aria-label="Activity">
        <div className="sync-activity-head">
          <h3>Activity</h3>
          <button type="button" className="sync-activity-close" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="sync-activity-tabs" role="tablist" aria-label="Activity">
          {(Object.keys(ACTIVITY_TAB_LABELS) as ActivityTab[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`sync-activity-tab${tab === key ? ' active' : ''}`}
              onClick={() => onTab(key)}
            >
              {ACTIVITY_TAB_LABELS[key]}
              {/* Only on the runs tab, and only when it is not zero: a badge
                  reading 0 is a badge saying nothing. */}
              {key === 'runs' && failedRuns > 0 && (
                <span className="sync-activity-tab-badge">{failedRuns}</span>
              )}
            </button>
          ))}
        </div>

        <div className="sync-activity-body">
          {tab === 'syncs' ? (
            <SyncHistoryPanel {...syncs} now={now} />
          ) : (
            <AutoSyncHistoryPanel
              history={runs.history}
              total={runs.total}
              filter={runs.filter}
              onFilterChange={runs.onFilterChange}
              onLoadMore={runs.onLoadMore}
              onRunAgain={runs.onRunAgain}
              now={now}
            />
          )}
        </div>
      </div>
    </div>
  );
}
