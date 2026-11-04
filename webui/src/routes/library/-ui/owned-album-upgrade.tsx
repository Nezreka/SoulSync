import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';
import { useState } from 'react';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2EnabledQueryOptions,
  libraryV2EnhanceQueryOptions,
  startLibraryV2Enhance,
} from '../-library-v2.api';
import { EnhanceQualityModal } from './enhance-quality-modal';
import styles from './library-v2-page.module.css';

// dev's album "upgrade n": the album's files below their quality profile,
// picked in the same enhance modal the artist toolbar uses. it reads the
// artist's enhance list (already fetched for the toolbar) so a page of albums
// costs no extra requests.

export function AlbumUpgradeButton({
  albumId,
  albumTitle,
}: {
  albumId: number;
  albumTitle: string;
}) {
  const queryClient = useQueryClient();
  const search = useSearch({ strict: false }) as { artist?: number };
  const artistId = typeof search.artist === 'number' ? search.artist : null;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const canWish = useQuery(libraryV2EnabledQueryOptions()).data?.canWish === true;
  const enhance = useQuery({
    ...libraryV2EnhanceQueryOptions('artists', artistId ?? 0),
    enabled: artistId != null && canWish,
  });
  const tracks = (enhance.data?.tracks ?? []).filter((t) => t.album_id === albumId);
  if (!canWish || tracks.length === 0) return null;
  return (
    <>
      <button
        type="button"
        className={styles.btnGhost}
        title="Files on this album below their quality profile — pick which to upgrade"
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        Upgrade {tracks.length}
      </button>
      {open ? (
        <EnhanceQualityModal
          title={albumTitle}
          tracks={tracks}
          onClose={() => setOpen(false)}
          onEnhance={(trackIds) => {
            setOpen(false);
            setBusy(true);
            void startLibraryV2Enhance('albums', albumId, trackIds)
              .then(() => {
                window.showToast?.(
                  `Upgrading ${trackIds.length} track(s) on ${albumTitle}`,
                  'success',
                );
                void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
              })
              .catch((error: Error) =>
                window.showToast?.(error.message || 'Upgrade failed', 'error'),
              )
              .finally(() => setBusy(false));
          }}
        />
      ) : null}
    </>
  );
}
