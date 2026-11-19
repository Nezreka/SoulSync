import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { getShellProfileContext } from '@/platform/shell/bridge';

import type { LibraryV2Track } from '../-library-v2.types';

import { LIBRARY_V2_QUERY_KEY, materializeLibraryV2MissingTrack } from '../-library-v2.api';
import { albumQueueRows } from '../-library-v2.play';
import { ActionMenu, type ActionMenuItem } from '../../artist-detail/-ui/action-menu';
import { HaveTrackModal } from '../../artist-detail/-ui/missing-track-modals';
import { ReidentifyModal } from '../../artist-detail/-ui/reidentify-modal';
import styles from './library-v2-page.module.css';
import { MoveTrackHereModal } from './move-track-here-modal';

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

/** #2094: each row with no file whose position is held by exactly one row
 *  that has the file. that row is a leftover (a retag or re-identify moved
 *  its file over) and can be folded into the one that kept it. */
export function leftoverSlotKeepers(
  tracks: LibraryV2Track[],
): Map<number, { id: number; title: string }> {
  const slot = (t: LibraryV2Track) => `${t.disc_number ?? 1}:${t.track_number}`;
  const filed = new Map<string, LibraryV2Track[]>();
  for (const t of tracks) {
    if (t.id == null || t.track_number == null) continue;
    if (t.file_status === 'missing' || !t.file?.path) continue;
    filed.set(slot(t), [...(filed.get(slot(t)) ?? []), t]);
  }
  const keepers = new Map<number, { id: number; title: string }>();
  for (const t of tracks) {
    if (t.id == null || t.track_number == null || t.file_status !== 'missing') continue;
    const there = filed.get(slot(t)) ?? [];
    if (there.length === 1)
      keepers.set(t.id, { id: there[0].id as number, title: there[0].title ?? '' });
  }
  return keepers;
}

export function OwnedTrackMoreMenu({
  track,
  albumId,
  albumTitle,
  artistName,
  slotKeeper,
}: {
  track: LibraryV2Track;
  albumId: number | undefined;
  albumTitle: string;
  artistName: string;
  slotKeeper?: { id: number; title: string };
}) {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<'reidentify' | 'have' | 'move' | null>(null);
  // the missing row's catalogue id; a slot only the tracklist knows gets one
  // when the move opens (the monitor toggle does the same)
  const [slotId, setSlotId] = useState<number | null>(track.id ?? null);
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
      key: 'move',
      label: 'Move a track here…',
      title: 'Move a track you already have into this slot: the file moves, nothing is copied',
      onSelect: () => void openMove(),
    });
    items.push({
      key: 'have',
      label: 'I Have This…',
      title: 'Use a file already in your library for this missing track',
      onSelect: () => setDialog('have'),
    });
    if (slotKeeper && track.id != null) {
      const trackId = track.id;
      items.push({
        key: 'remove-leftover',
        label: 'Remove This Row…',
        title: `Fold this row into "${slotKeeper.title}", which has the file at this position`,
        onSelect: () => void removeLeftover(trackId, label, slotKeeper.title, refresh),
      });
    }
  }
  if (!items.length) return null;

  async function openMove() {
    if (slotId != null) {
      setDialog('move');
      return;
    }
    if (!albumId || track.track_number == null) {
      window.showToast?.('This slot has no track number to move a track into', 'error');
      return;
    }
    try {
      const created = await materializeLibraryV2MissingTrack(albumId, {
        track_number: Number(track.track_number),
        disc_number: track.disc_number ?? 1,
        title: track.title ?? undefined,
      });
      setSlotId(created.track_id);
      setDialog('move');
    } catch (error) {
      window.showToast?.((error as Error).message || 'Could not open the move', 'error');
    }
  }

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
      {dialog === 'move' && slotId != null ? (
        <MoveTrackHereModal
          missingTrackId={slotId}
          missingTitle={track.title ?? ''}
          missingNumber={track.track_number}
          albumTitle={albumTitle}
          artistName={artistName}
          onMoved={refresh}
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

async function removeLeftover(
  trackId: number,
  label: string,
  keptTitle: string,
  refresh: () => void,
): Promise<void> {
  if (typeof window.showConfirmDialog !== 'function') return;
  const ok = await window.showConfirmDialog({
    title: 'Remove this row',
    message:
      `"${label}" has no file, and "${keptTitle}" has the file at the same position. ` +
      `The row is removed and its plays and history move to "${keptTitle}". No files are touched.`,
    confirmText: 'Remove',
    destructive: true,
  });
  if (!ok) return;
  try {
    await readJson(apiClient.post(`library/v2/tracks/${trackId}/remove-leftover`));
    window.showToast?.(`Removed "${label}"`, 'success');
    refresh();
  } catch (error) {
    window.showToast?.((error as Error).message || 'Could not remove the row', 'error');
  }
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
