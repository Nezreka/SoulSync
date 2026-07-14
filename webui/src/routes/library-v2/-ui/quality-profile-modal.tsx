import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { LibraryV2QualityProfile } from '../-library-v2.types';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2QualityProfilesQueryOptions,
  setLibraryV2QualityProfile,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

function policyLabel(p: LibraryV2QualityProfile): string {
  if (p.upgrade_policy === 'until_top') return 'Upgrades until top quality';
  if (p.upgrade_policy === 'until_cutoff') {
    return p.upgrade_cutoff_index > 0
      ? `Upgrades until cutoff (target #${p.upgrade_cutoff_index + 1})`
      : 'Upgrades until top quality';
  }
  return 'No upgrades once acceptable';
}

/** Pick the quality profile for an artist or album. These are the app-wide
 *  profiles (Settings → Quality) — the exact rows the wishlist/download
 *  pipeline enforces, so assigning one here changes what gets searched,
 *  accepted and upgraded for this artist/album. */
export function QualityProfileModal({
  entity,
  id,
  currentProfileId,
  title,
  onClose,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  id: number;
  currentProfileId: number;
  title: string;
  onClose: () => void;
}) {
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const queryClient = useQueryClient();
  // Assigning a profile is a quality decision. Monitoring the tracks for
  // upgrades (queueing downloads) is a separate wanted-action — explicit
  // opt-in, default off (audit P1-15).
  const [monitorExisting, setMonitorExisting] = useState(false);
  const mutation = useMutation({
    mutationFn: (profileId: number) =>
      // A track has no children to cascade to.
      setLibraryV2QualityProfile(entity, id, profileId, entity !== 'tracks', monitorExisting),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
    onSuccess: () => onClose(),
  });
  const profiles = profilesQuery.data ?? [];

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Quality Profile</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className={styles.qpHeadRow}>
          <p className={styles.qpSubtitle}>
            {entity === 'artists' ? 'Artist' : entity === 'albums' ? 'Album' : 'Track'}: {title}
            {entity === 'artists' ? ' — applies to all its albums' : ''}
          </p>
          <span className={styles.qpManagedHint}>Profiles are managed in Settings → Quality</span>
        </div>
        <label className={styles.checkOption}>
          <input
            type="checkbox"
            checked={monitorExisting}
            onChange={(e) => setMonitorExisting(e.target.checked)}
          />
          Also monitor existing tracks for upgrades (adds them to Wanted and may queue downloads)
        </label>
        <div className={styles.qpList}>
          {profilesQuery.isLoading ? (
            <div className={styles.inlineLoading}>Loading profiles…</div>
          ) : (
            profiles.map((p) => {
              const active = p.id === currentProfileId;
              return (
                <button
                  key={p.id}
                  type="button"
                  className={`${styles.qpOption} ${active ? styles.qpOptionActive : ''}`}
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate(p.id)}
                >
                  <span className={styles.qpName}>
                    {p.name}
                    {active ? <span className={styles.qpCurrent}>current</span> : null}
                  </span>
                  {p.description ? <span className={styles.qpDesc}>{p.description}</span> : null}
                  <span className={styles.qpPolicy}>{policyLabel(p)}</span>
                </button>
              );
            })
          )}
        </div>
        {mutation.isError && typeof mutation.variables === 'number' ? (
          <div className={styles.mutationError} role="alert">
            <span>
              {mutation.error instanceof Error && mutation.error.message.trim()
                ? mutation.error.message
                : 'Quality profile could not be saved'}
            </span>
            <button
              type="button"
              className={styles.inlineRetry}
              onClick={() => mutation.mutate(mutation.variables)}
            >
              Retry
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
