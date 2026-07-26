import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';

import { useShellBridge } from '@/platform/shell/route-controllers';

import type { WatchlistLabel } from '../-watchlist.types';

import {
  removeWatchlistLabel,
  setWatchlistLabelBacklog,
  watchlistLabelsQueryOptions,
} from '../-watchlist.api';
import { formatRelativeScanTime } from '../-watchlist.helpers';
import styles from './watchlist-page.module.css';

export function WatchlistLabelsTab({ profileId }: { profileId: number }) {
  const queryClient = useQueryClient();
  const bridge = useShellBridge();
  const navigate = useNavigate();
  const labelsQuery = useQuery(watchlistLabelsQueryOptions(profileId));
  const labels = labelsQuery.data ?? [];

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: watchlistLabelsQueryOptions(profileId).queryKey });

  const backlogMutation = useMutation({
    mutationFn: ({ mbid, backlog }: { mbid: string; backlog: boolean }) =>
      setWatchlistLabelBacklog(mbid, backlog),
    onSuccess: invalidate,
    onError: () => window.showToast?.('Could not update label setting', 'error'),
  });

  const unfollowMutation = useMutation({
    mutationFn: (mbid: string) => removeWatchlistLabel(mbid),
    onSuccess: () => {
      void invalidate();
      // The nav badge and hero button count labels too; the vanilla flow
      // refreshed them here and swallowed any failure as non-fatal.
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    },
    onError: () => window.showToast?.('Could not unfollow label', 'error'),
  });

  const onUnfollow = async (label: WatchlistLabel) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Unfollow Label',
      message: `Stop monitoring ${label.label_name || 'this label'} for new releases?`,
      confirmText: 'Unfollow',
      destructive: true,
    });
    if (confirmed === false) return;
    unfollowMutation.mutate(label.musicbrainz_label_id);
  };

  if (labels.length === 0) {
    return (
      <div className="watchlist-page-empty">
        <div className="watchlist-page-empty-icon">
          <svg
            width="64"
            height="64"
            viewBox="0 0 24 24"
            fill="none"
            stroke="rgba(255,255,255,0.15)"
            strokeWidth="1.5"
          >
            <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
            <line x1="7" y1="7" x2="7.01" y2="7" />
          </svg>
        </div>
        <h3>No labels followed yet</h3>
        <p>Search a record label, open it, and hit Follow to monitor its new releases here.</p>
        <button
          className="btn btn--primary"
          type="button"
          onClick={() => void navigate({ href: '/search' })}
        >
          Open Search
        </button>
      </div>
    );
  }

  return (
    <div className={styles.labelsGrid}>
      {labels.map((label) => (
        <div
          key={label.musicbrainz_label_id || label.id}
          className={styles.labelCard}
          onClick={() =>
            bridge?.navigateToLabelDetail(label.musicbrainz_label_id, label.label_name)
          }
        >
          <div className={styles.labelActions}>
            <button
              type="button"
              title={
                label.backlog
                  ? 'Monitoring full backlog — click for new-releases-only'
                  : 'Monitoring new releases only — click for full backlog'
              }
              onClick={(event) => {
                event.stopPropagation();
                backlogMutation.mutate({
                  mbid: label.musicbrainz_label_id,
                  backlog: !label.backlog,
                });
              }}
            >
              {label.backlog ? '📚' : '🆕'}
            </button>
            <button
              type="button"
              title="Unfollow label"
              onClick={(event) => {
                event.stopPropagation();
                void onUnfollow(label);
              }}
            >
              ✕
            </button>
          </div>
          <div className={styles.labelIcon}>🏷️</div>
          <div className={styles.labelName}>{label.label_name || 'Label'}</div>
          <div className={styles.labelMeta}>{labelScanText(label)}</div>
          {label.backlog ? <span className={styles.labelBacklogBadge}>Full backlog</span> : null}
        </div>
      ))}
    </div>
  );
}

/**
 * The label card's scan line.
 *
 * Reproduced exactly, doubled word and all: `formatRelativeScanTime` already
 * returns "Scanned 3d ago", and the vanilla template prefixes another
 * "Scanned ", so a scanned label reads "Scanned Scanned 3d ago" today. That is
 * a real (cosmetic) bug in the live page, kept here so this migration changes
 * nothing. Drop the prefix to fix it — deliberately not done as part of a port.
 */
export function labelScanText(label: Pick<WatchlistLabel, 'last_scan_timestamp'>): string {
  return label.last_scan_timestamp
    ? `Scanned ${formatRelativeScanTime(label.last_scan_timestamp)}`
    : 'Not scanned yet';
}
