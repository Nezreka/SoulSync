import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2QualityProfilesQueryOptions,
  setLibraryV2QualityProfile,
  syncLibraryV2QualityProfiles,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

/** Pick the quality profile for an artist or album. Profiles drive what quality
 *  counts as "good enough" and whether upgrades are proposed (see core/quality). */
export function QualityProfileModal({
  entity,
  id,
  currentProfileId,
  title,
  onClose,
}: {
  entity: 'artists' | 'albums';
  id: number;
  currentProfileId: number;
  title: string;
  onClose: () => void;
}) {
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (profileId: number) => setLibraryV2QualityProfile(entity, id, profileId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
    onSuccess: () => onClose(),
  });
  const syncMutation = useMutation({
    mutationFn: syncLibraryV2QualityProfiles,
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });

  const profiles = profilesQuery.data ?? [];

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div className={styles.modal} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h3>Quality Profile</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className={styles.qpHeadRow}>
          <p className={styles.qpSubtitle}>
            {entity === 'artists' ? 'Artist' : 'Album'}: {title}
            {entity === 'artists' ? ' — applies to all its albums' : ''}
          </p>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={syncMutation.isPending}
            title="Import the profiles you defined in Settings → Quality"
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending
              ? 'Syncing…'
              : syncMutation.isSuccess
                ? `Synced ${syncMutation.data}`
                : 'Sync from Settings'}
          </button>
        </div>
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
                  <span className={styles.qpPolicy}>
                    {p.upgrade_policy === 'until_top'
                      ? 'Upgrades until top quality'
                      : 'No upgrades once acceptable'}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
