import type { AdlFilter } from '../-adl.types';
import type { ActionMenuItem } from '../../artist-detail/-ui/action-menu';
import type { DlLive } from './dl-header';

import { ADL_FILTERS } from '../-adl.types';
import { DlHeader } from './dl-header';

/** The Review tab's copy when the unverified queue can exist. */
const REVIEW_TAB = {
  label: 'Review',
  title:
    'Review queue: imported-but-unconfirmed downloads (unverified / force-imported) and quarantined files that were never imported.',
};

/**
 * ...and when it cannot.
 *
 * Without an AcoustID key (or with require-verified on) nothing ever lands
 * unverified, so the tab is honest about being quarantine-only.
 */
const QUARANTINE_ONLY_TAB = {
  label: 'Quarantine',
  title:
    'Files that failed import checks and were NOT imported. (AcoustID is not configured or require-verified is on, so there is no unverified review queue.)',
};

/** Which of the three top-level views a filter value belongs to. */
export function viewOf(filter: AdlFilter): 'downloads' | 'review' | 'clients' {
  if (filter === 'unverified') return 'review';
  if (filter === 'clients') return 'clients';
  return 'downloads';
}

export interface AdlHeaderProps {
  filter: AdlFilter;
  counts: {
    active: number;
    queued: number;
    failed: number;
    total: number;
    completedOrFailed: number;
  };
  /** how many rows each filter chip would show, keyed by filter value. */
  chipCounts: Record<string, number>;
  hasRunningWork: boolean;
  /** False when the review queue is quarantine-only. */
  acoustidEnabled: boolean;
  /**
   * How many files are waiting on a human, from the server. Null until the
   * first count lands, and the badge stays hidden until then rather than
   * flashing a 0 at someone who has 72 waiting.
   */
  reviewCount: number | null;
  /** the live strip's numbers, or null when nothing moves. */
  live: DlLive | null;
  /** the one line shown when nothing moves. */
  quiet: string;
  historyCount: number | null;
  onOpenHistory: () => void;
  onFilter: (filter: AdlFilter) => void;
  onCancelAll: () => void;
  onClearCompleted: () => void;
  cancelAllPending: boolean;
}

/**
 * The music side of the shared header (dl-header.tsx). The view tabs map
 * onto the old filter values, so "Review" is still filter 'unverified' and
 * every caller and automation selector keeps working.
 */
export function AdlHeader({
  filter,
  counts,
  chipCounts,
  hasRunningWork,
  acoustidEnabled,
  reviewCount,
  live,
  quiet,
  historyCount,
  onOpenHistory,
  onFilter,
  onCancelAll,
  onClearCompleted,
  cancelAllPending,
}: AdlHeaderProps) {
  const view = viewOf(filter);
  const reviewTab = acoustidEnabled ? REVIEW_TAB : QUARANTINE_ONLY_TAB;
  const menu: ActionMenuItem[] = [];
  if (counts.completedOrFailed > 0) {
    menu.push({
      key: 'clear',
      label: `Clear finished (${counts.completedOrFailed})`,
      className: 'dlh-act-clear',
      onSelect: onClearCompleted,
    });
  }
  if (hasRunningWork) {
    menu.push({
      key: 'cancel',
      label: `Cancel all (${counts.active + counts.queued})`,
      className: 'dlh-act-cancel-all',
      danger: true,
      disabled: cancelAllPending,
      title: 'Cancel all active and queued downloads',
      onSelect: onCancelAll,
    });
  }

  return (
    <DlHeader
      tabs={[
        // data-filter="all" so "back to the list" automation keeps one selector
        { value: 'downloads', label: 'Downloads', dataFilter: 'all' },
        {
          value: 'review',
          label: reviewTab.label,
          title: reviewTab.title,
          badge: reviewCount,
          dataFilter: 'unverified',
        },
        {
          value: 'clients',
          label: 'Clients',
          title: 'Your external download clients (slskd, torrent, usenet) in one pane',
          dataFilter: 'clients',
        },
      ]}
      view={view}
      onView={(v) => onFilter(v === 'review' ? 'unverified' : v === 'clients' ? 'clients' : 'all')}
      chips={
        view === 'downloads'
          ? ADL_FILTERS.map((option) => ({
              value: option.value,
              label: option.label,
              count: chipCounts[option.value] ?? 0,
              bad: option.value === 'failed',
            }))
          : null
      }
      filter={filter}
      onFilter={(v) => onFilter(v as AdlFilter)}
      historyCount={historyCount}
      onOpenHistory={onOpenHistory}
      menu={menu}
      live={live}
      quiet={quiet}
    />
  );
}
