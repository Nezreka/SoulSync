import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { getShellProfileContext } from '@/platform/shell/bridge';

import type { LibraryV2Track } from '../-library-v2.types';

import { LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import { albumQueueRows } from '../-library-v2.play';
import { ActionMenu, type ActionMenuItem } from '../../artist-detail/-ui/action-menu';
import { HaveTrackModal } from '../../artist-detail/-ui/missing-track-modals';
import { ReidentifyModal } from '../../artist-detail/-ui/reidentify-modal';
import styles from './library-v2-page.module.css';

// the per-track extras dev's artist page had and the owned view lost:
// play next, add to queue, re-identify, and "i have this" on a missing slot.

function isAdmin(): boolean {
  return getShellProfileContext()?.isAdmin === true;
}

export function ownedTrackQueueRow(
  track: LibraryV2Track,
  albumId: number | undefined,
  albumTitle: string,
  artistName: string,
) {
  return albumQueueRows(
    { id: albumId ?? 0, title: albumTitle, image_url: null, tracks: [track] },
    artistName,
  )[0];
}

export function OwnedTrackMoreMenu({
  track,
  albumId,
  albumTitle,
  artistName,
}: {
  track: LibraryV2Track;
  albumId: number | undefined;
  albumTitle: string;
  artistName: string;
}) {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<'reidentify' | 'have' | null>(null);
  const hasFile = Boolean(track.file?.path);
  const admin = isAdmin();
  const label = track.title || 'Unknown Track';
  const refresh = () => void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });

  const items: ActionMenuItem[] = [];
  if (hasFile) {
    const row = ownedTrackQueueRow(track, albumId, albumTitle, artistName);
    items.push(
      { key: 'next', label: 'Play next', onSelect: () => window.playNext?.(row) },
      { key: 'queue', label: 'Add to queue', onSelect: () => window.addToQueue?.(row) },
    );
    if (admin && track.id != null)
      items.push({
        key: 'reidentify',
        label: 'Re-identify…',
        title: 'Re-file this track under a different release',
        onSelect: () => setDialog('reidentify'),
      });
  } else if (admin && albumId) {
    items.push({
      key: 'have',
      label: 'I Have This…',
      title: 'Use a file already in your library for this missing track',
      onSelect: () => setDialog('have'),
    });
  }
  if (!items.length) return null;

  return (
    <>
      <ActionMenu
        heading={<strong>{label}</strong>}
        items={items}
        trigger={(props) => (
          <button
            type="button"
            {...props}
            className={styles.iconAction}
            aria-label={`More for ${label}`}
            title="More"
            onClick={(e) => {
              e.stopPropagation();
              props.onClick(e);
            }}
          >
            ⋯
          </button>
        )}
      />
      {dialog === 'reidentify' ? (
        <ReidentifyModal
          trackId={track.id}
          trackTitle={label}
          artistName={artistName}
          albumTitle={albumTitle}
          imageUrl=""
          onApplied={(message) => {
            window.showToast?.(message, 'success');
            refresh();
          }}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === 'have' && albumId ? (
        <HaveTrackModal
          track={{
            id: track.id,
            title: track.title ?? '',
            track_number: track.track_number,
            disc_number: track.disc_number ?? 1,
            duration: track.duration ?? undefined,
            artists: track.artists?.map((a) => ({ name: a.name })) ?? [],
          }}
          album={{ id: albumId, title: albumTitle }}
          artist={{ id: null, name: artistName, imageUrl: '' }}
          onImported={() => {
            window.showToast?.('Imported the existing file into this album', 'success');
            refresh();
          }}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  );
}

/** "unlock hand-tagged details" for the album menu. renders only for an
 *  album that was tagged by hand from basic search. */
export function AlbumUnlockMenuItem({
  albumId,
  className,
  onDone,
}: {
  albumId: number;
  className?: string;
  onDone?: () => void;
}) {
  const queryClient = useQueryClient();
  const lockQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'manual-lock', albumId],
    queryFn: () =>
      readJson<{ metadata_locked?: boolean }>(apiClient.get(`album/${albumId}/metadata-lock`)),
    enabled: isAdmin(),
    staleTime: 60_000,
  });
  if (!lockQuery.data?.metadata_locked) return null;
  return (
    <button
      type="button"
      className={className}
      title="It was tagged by hand. Unlocking lets enrichment and the maintenance jobs rematch it."
      onClick={async () => {
        try {
          await readJson(apiClient.delete(`album/${albumId}/metadata-lock`));
          window.showToast?.('Album unlocked, enrichment may rematch it', 'success');
          void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
        } catch (error) {
          window.showToast?.((error as Error).message || 'Unlock failed', 'error');
        }
        onDone?.();
      }}
    >
      Unlock hand-tagged details
    </button>
  );
}
