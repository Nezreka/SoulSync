/**
 * The video side of the shared header (dl-header.tsx): same tiers, same
 * actions, the video view's own tabs and filters.
 */

import type { VdlDownload, VdlFilter, VdlView } from '../-vdl.types';
import type { ActionMenuItem } from '../../artist-detail/-ui/action-menu';

import { videoChipCounts, videoLive, videoQuiet } from '../-dl.header-data';
import { VDL_FILTERS } from '../-vdl.types';
import { DlHeader } from './dl-header';

export interface VdlHeaderProps {
  view: VdlView;
  onView: (view: VdlView) => void;
  filter: VdlFilter;
  onFilter: (filter: VdlFilter) => void;
  downloads: VdlDownload[];
  counts: { active: number; completed: number; failed: number; removable: number; total: number };
  liveSpeedBps: number;
  hasRunningWork: boolean;
  retryableCount: number;
  reviewCount: number | null;
  historyCount: number | null;
  onOpenHistory: () => void;
  onCancelAll: () => void;
  onRetryAll: () => void;
  onClearFinished: () => void;
  onClearFailed: () => void;
  retryAllPending: boolean;
}

const REVIEW_TITLE = 'Recycle bin + release blocklist';
const CLIENTS_TITLE = 'Your download clients (torrent, usenet, slskd) in one pane';

export function VdlHeader({
  view,
  onView,
  filter,
  onFilter,
  downloads,
  counts,
  liveSpeedBps,
  hasRunningWork,
  retryableCount,
  reviewCount,
  historyCount,
  onOpenHistory,
  onCancelAll,
  onRetryAll,
  onClearFinished,
  onClearFailed,
  retryAllPending,
}: VdlHeaderProps) {
  const chipCounts = videoChipCounts(downloads);
  const finished = counts.completed + counts.failed;
  const menu: ActionMenuItem[] = [];
  if (retryableCount > 0) {
    menu.push({
      key: 'retry',
      label: `Retry failed (${retryableCount})`,
      title: 'Re-grab every failed download (same releases)',
      disabled: retryAllPending,
      onSelect: onRetryAll,
    });
  }
  if (counts.removable > 0) {
    menu.push({
      key: 'clear-failed',
      label: `Clear failed (${counts.removable})`,
      title: 'Remove failed and cancelled downloads from the list without retrying',
      onSelect: onClearFailed,
    });
  }
  if (finished > 0) {
    menu.push({ key: 'clear', label: `Clear finished (${finished})`, onSelect: onClearFinished });
  }
  if (hasRunningWork) {
    menu.push({
      key: 'cancel',
      label: `Cancel all (${counts.active})`,
      danger: true,
      onSelect: onCancelAll,
    });
  }

  return (
    <DlHeader
      tabs={[
        { value: 'downloads', label: 'Downloads' },
        { value: 'review', label: 'Review', title: REVIEW_TITLE, badge: reviewCount },
        { value: 'clients', label: 'Clients', title: CLIENTS_TITLE },
      ]}
      view={view}
      onView={(v) => onView(v as VdlView)}
      chips={
        view === 'downloads'
          ? VDL_FILTERS.map((option) => ({
              value: option.value,
              label: option.label,
              count: chipCounts[option.value] ?? 0,
              bad: option.value === 'failed',
            }))
          : null
      }
      filter={filter}
      onFilter={(v) => onFilter(v as VdlFilter)}
      historyCount={historyCount}
      onOpenHistory={onOpenHistory}
      menu={menu}
      live={videoLive(downloads, liveSpeedBps)}
      quiet={videoQuiet(downloads, Date.now())}
    />
  );
}
