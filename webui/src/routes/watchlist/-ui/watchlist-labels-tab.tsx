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
import home from './watchlist-home.module.css';
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
      <div className={home.empty}>
        <span className={home.emptyIcon} aria-hidden="true">
          <svg
            width="64"
            height="64"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
            <line x1="7" y1="7" x2="7.01" y2="7" />
          </svg>
        </span>
        <h3 className={home.emptyTitle}>No labels followed yet</h3>
        <p className={home.emptyText}>
          Search a record label, open it, and hit Follow to monitor its new releases here.
        </p>
        <div className={home.emptyActions}>
          <button
            className={home.cta}
            type="button"
            onClick={() => void navigate({ href: '/search' })}
          >
            Open Search
          </button>
        </div>
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
          <div className={styles.labelArt} aria-hidden="true">
            <svg
              width="40"
              height="40"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
              <circle cx="7" cy="7" r="1.5" />
            </svg>
          </div>
          <div className={styles.labelName}>{label.label_name || 'Label'}</div>
          <div className={styles.labelMeta}>{labelScanText(label)}</div>
          <div className={styles.labelFoot}>
            {/* what a scan watches: only what comes out next, or everything
                the label has ever put out */}
            <button
              type="button"
              className={`${styles.labelToggle}${label.backlog ? ` ${styles.labelToggleOn}` : ''}`}
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
              {label.backlog ? 'Full backlog' : 'New releases'}
            </button>
            <button
              type="button"
              className={styles.labelRemove}
              title="Unfollow label"
              aria-label={`Unfollow ${label.label_name || 'label'}`}
              onClick={(event) => {
                event.stopPropagation();
                void onUnfollow(label);
              }}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.6"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M6 6l12 12" />
                <path d="M18 6L6 18" />
              </svg>
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * The label card's scan line.
 *
 * `formatRelativeScanTime` already returns "Scanned 3d ago", so the extra
 * "Scanned " prefix the label card carried produced "Scanned Scanned 3d ago".
 * Introduced in 7bcfde777 (labels P3) by prefixing a function that already
 * prefixes; the prefix is dropped here rather than the function changed,
 * because every other caller relies on it supplying the word.
 */
export function labelScanText(label: Pick<WatchlistLabel, 'last_scan_timestamp'>): string {
  return label.last_scan_timestamp
    ? formatRelativeScanTime(label.last_scan_timestamp)
    : 'Not scanned yet';
}
