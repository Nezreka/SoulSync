import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { LibraryV2ArtCandidate } from '../-library-v2.types';

import {
  applyLibraryV2AlbumArt,
  applyLibraryV2ArtistArt,
  fetchLibraryV2AlbumArtOptions,
  fetchLibraryV2ArtistArtOptions,
  LIBRARY_V2_QUERY_KEY,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

/** Legacy "Change cover" parity (docs §49): candidate covers from Cover Art
 *  Archive + Deezer/iTunes/Spotify/AudioDB, click one to apply. The choice is
 *  pinned server-side so a later refresh won't revert it. */
export function AlbumArtPickerModal({
  albumId,
  albumTitle,
  onClose,
}: {
  albumId: number;
  albumTitle: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  // iss27-03: a 5-minute server cache can pin a partial result from a
  // transient provider hiccup — bumping this forces `?refresh=1`, a fresh
  // cache slot (so it can't collide with the still-displayed stale one).
  const [refreshNonce, setRefreshNonce] = useState(0);
  const optionsQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'art-options', albumId, refreshNonce],
    queryFn: () => fetchLibraryV2AlbumArtOptions(albumId, { refresh: refreshNonce > 0 }),
    staleTime: 0,
  });
  const [busyUrl, setBusyUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function apply(url: string) {
    setBusyUrl(url);
    setError(null);
    try {
      await applyLibraryV2AlbumArt(albumId, url);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to apply cover');
      setBusyUrl(null);
    }
  }

  const candidates = optionsQuery.data ?? [];

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${styles.modalWide}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Change Cover — {albumTitle}</h3>
          <button
            type="button"
            className={styles.iconAction}
            title="Refresh — re-query every provider instead of the cached result"
            disabled={optionsQuery.isFetching}
            onClick={() => setRefreshNonce((n) => n + 1)}
          >
            ⟳
          </button>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        {error ? <div className={styles.searchError}>{error}</div> : null}

        <div className={styles.resultsWrap}>
          {optionsQuery.isLoading ? (
            <div className={styles.inlineLoading}>Fetching candidate covers…</div>
          ) : optionsQuery.isError ? (
            <div className={styles.searchError}>
              {optionsQuery.error instanceof Error
                ? optionsQuery.error.message
                : 'Failed to load covers'}
            </div>
          ) : candidates.length === 0 ? (
            <div className={styles.inlineLoading}>No alternate covers found.</div>
          ) : (
            <div className={styles.artPickerGrid}>
              {candidates.map((c, i) => (
                <ArtPickerCard
                  key={`${c.source}:${c.url}:${i}`}
                  candidate={c}
                  busy={busyUrl === c.url}
                  disabled={busyUrl !== null}
                  onPick={() => void apply(c.url)}
                />
              ))}
            </div>
          )}
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

/** Artist image picker (deep-dive A9): one candidate photo per configured
 *  source (Spotify/Deezer/iTunes/Discogs), click one to apply. Same pick/pin
 *  mechanism as the album cover picker (§49) — no cover-embed retag needed. */
export function ArtistImagePickerModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: number;
  artistName: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  // iss27-03: see the matching comment in AlbumArtPickerModal above.
  const [refreshNonce, setRefreshNonce] = useState(0);
  const optionsQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-art-options', artistId, refreshNonce],
    queryFn: () => fetchLibraryV2ArtistArtOptions(artistId, { refresh: refreshNonce > 0 }),
    staleTime: 0,
  });
  const [busyUrl, setBusyUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function apply(url: string) {
    setBusyUrl(url);
    setError(null);
    try {
      await applyLibraryV2ArtistArt(artistId, url);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to apply photo');
      setBusyUrl(null);
    }
  }

  const candidates = optionsQuery.data ?? [];

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${styles.modalWide}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Change Photo — {artistName}</h3>
          <button
            type="button"
            className={styles.iconAction}
            title="Refresh — re-query every provider instead of the cached result"
            disabled={optionsQuery.isFetching}
            onClick={() => setRefreshNonce((n) => n + 1)}
          >
            ⟳
          </button>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        {error ? <div className={styles.searchError}>{error}</div> : null}

        <div className={styles.resultsWrap}>
          {optionsQuery.isLoading ? (
            <div className={styles.inlineLoading}>Fetching candidate photos…</div>
          ) : optionsQuery.isError ? (
            <div className={styles.searchError}>
              {optionsQuery.error instanceof Error
                ? optionsQuery.error.message
                : 'Failed to load photos'}
            </div>
          ) : candidates.length === 0 ? (
            <div className={styles.inlineLoading}>No alternate photos found.</div>
          ) : (
            <div className={styles.artPickerGrid}>
              {candidates.map((c, i) => (
                <ArtPickerCard
                  key={`${c.source}:${c.url}:${i}`}
                  candidate={c}
                  busy={busyUrl === c.url}
                  disabled={busyUrl !== null}
                  onPick={() => void apply(c.url)}
                />
              ))}
            </div>
          )}
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function ArtPickerCard({
  candidate,
  busy,
  disabled,
  onPick,
}: {
  candidate: LibraryV2ArtCandidate;
  busy: boolean;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      className={styles.artPickerCard}
      disabled={disabled}
      title={`Use this cover from ${candidate.source}`}
      onClick={onPick}
    >
      <img
        className={styles.artPickerImg}
        src={candidate.url}
        alt={`Cover option from ${candidate.source}`}
        loading="lazy"
      />
      <span className={styles.artPickerBadge}>{busy ? 'Applying…' : candidate.source}</span>
    </button>
  );
}
