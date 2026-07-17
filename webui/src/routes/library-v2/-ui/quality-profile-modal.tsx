import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { LibraryV2QualityProfile, LibraryV2QualityProfileSource } from '../-library-v2.types';

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

/** Pick + assign a quality profile for an artist/album/track. Pure content —
 *  no modal chrome — so it can be embedded standalone (QualityProfileModal
 *  below) or as a tab inside a bigger modal (the per-track detail modal). */
export function QualityProfilePicker({
  entity,
  id,
  currentProfileId,
  currentProfileSource = 'global',
  currentProfileExplicit = false,
  onSaved,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  id: number;
  currentProfileId: number;
  currentProfileSource?: LibraryV2QualityProfileSource;
  currentProfileExplicit?: boolean;
  onSaved?: () => void;
}) {
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (profileId: number | null) =>
      // A track has no children to cascade to.
      // §52.3: profile choice is orthogonal to wanted/monitoring intent.
      setLibraryV2QualityProfile(entity, id, profileId, entity !== 'tracks', false),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
    onSuccess: () => onSaved?.(),
  });
  const profiles = profilesQuery.data ?? [];
  const currentProfile = profiles.find((profile) => profile.id === currentProfileId);
  const sourceLabel =
    currentProfileSource === 'track'
      ? 'Track override'
      : currentProfileSource === 'album'
        ? entity === 'albums'
          ? 'Album override'
          : 'Inherited from album'
        : currentProfileSource === 'artist'
          ? entity === 'artists'
            ? 'Artist override'
            : 'Inherited from artist'
          : currentProfileSource === 'playlist'
            ? 'Inherited from playlist'
            : 'App default';

  return (
    <>
      <div className={styles.qpHeadRow}>
        <span className={styles.qpManagedHint}>
          Effective: {currentProfile?.name ?? `Profile ${currentProfileId}`}
          {sourceLabel !== 'App default' ? ` (${sourceLabel})` : ''}
        </span>
        <button
          type="button"
          className={styles.btnGhost}
          disabled={!currentProfileExplicit || mutation.isPending}
          onClick={() => mutation.mutate(null)}
        >
          Use inherited profile
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
                <span className={styles.qpPolicy}>{policyLabel(p)}</span>
              </button>
            );
          })
        )}
      </div>
      {mutation.isError ? (
        <div className={styles.mutationError} role="alert">
          <span>
            {mutation.error instanceof Error && mutation.error.message.trim()
              ? mutation.error.message
              : 'Quality profile could not be saved'}
          </span>
          <button
            type="button"
            className={styles.inlineRetry}
            onClick={() => mutation.mutate(mutation.variables ?? null)}
          >
            Retry
          </button>
        </div>
      ) : null}
    </>
  );
}

/** Pick the quality profile for an artist or album (standalone modal). These
 *  are the app-wide profiles (Settings → Quality) — the exact rows the
 *  wishlist/download pipeline enforces, so assigning one here changes what
 *  gets searched, accepted and upgraded for this artist/album. Per-track
 *  assignment is embedded as a tab in the track detail modal instead. */
export function QualityProfileModal({
  entity,
  id,
  currentProfileId,
  currentProfileSource,
  currentProfileExplicit,
  title,
  onClose,
}: {
  entity: 'artists' | 'albums';
  id: number;
  currentProfileId: number;
  currentProfileSource?: LibraryV2QualityProfileSource;
  currentProfileExplicit?: boolean;
  title: string;
  onClose: () => void;
}) {
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
            {entity === 'artists' ? 'Artist' : 'Album'}: {title}
            {entity === 'artists' ? ' — applies to all its albums' : ''}
          </p>
          <span className={styles.qpManagedHint}>Profiles are managed in Settings → Quality</span>
        </div>
        <QualityProfilePicker
          entity={entity}
          id={id}
          currentProfileId={currentProfileId}
          currentProfileSource={currentProfileSource}
          currentProfileExplicit={currentProfileExplicit}
          onSaved={onClose}
        />
      </div>
    </div>
  );
}
