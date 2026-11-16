import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { libraryV2ArtistRenameCheckQueryOptions } from '../-library-v2.api';
import styles from './library-v2-page.module.css';
import { AlbumReorganizeModal } from './reorganize-modal';

/** #2141: files that aren't where the naming template puts them, counted off
 *  the same plan Preview rename shows. fetched after the page is up, and only
 *  ever shown when organizing is on and the template renders. */
export function useRenameCheck(artistId: number | undefined) {
  const query = useQuery(libraryV2ArtistRenameCheckQueryOptions(artistId ?? 0));
  const data = query.data?.enabled ? query.data : null;
  const total = data ? Object.values(data.albums).reduce((sum, n) => sum + n, 0) : 0;
  return {
    total,
    album: (albumId: number) => data?.albums[String(albumId)] ?? 0,
    tracks: new Set(data?.tracks ?? []),
  };
}

export function renameHintTitle(files: number): string {
  return `${files} ${files === 1 ? 'file is' : 'files are'} not where your naming template puts ${files === 1 ? 'it' : 'them'}. Click to preview the rename.`;
}

/** the count on an album row; a click opens that album's Preview rename */
export function AlbumRenameBadge({
  artistId,
  albumId,
  albumTitle,
}: {
  artistId: number;
  albumId: number;
  albumTitle: string;
}) {
  const [open, setOpen] = useState(false);
  const files = useRenameCheck(artistId).album(albumId);
  if (!files) return null;
  return (
    <>
      <button
        type="button"
        className={styles.albumRenameBadge}
        title={renameHintTitle(files)}
        onClick={(event) => {
          // the row toggles on a click
          event.stopPropagation();
          setOpen(true);
        }}
      >
        {files} to rename
      </button>
      {open ? (
        // the dialog is portaled, but its clicks still bubble up the react
        // tree to the row (#2143)
        <span className={styles.portalHost} onClick={(event) => event.stopPropagation()}>
          <AlbumReorganizeModal
            albumId={albumId}
            albumTitle={albumTitle}
            onClose={() => setOpen(false)}
          />
        </span>
      ) : null}
    </>
  );
}
