import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { ArtApplyOutcome } from '../-library-v2.api';
import type { LibraryV2ArtCandidate } from '../-library-v2.types';

import {
  applyLibraryV2AlbumArt,
  applyLibraryV2ArtistArt,
  fetchLibraryV2AlbumArtOptions,
  fetchLibraryV2ArtistArtOptions,
  LIBRARY_V2_QUERY_KEY,
  releaseLibraryV2AlbumArt,
  releaseLibraryV2ArtistArt,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

/** dd28-23: a timed-out apply is not a rejected apply. ky raises TimeoutError
 *  once its own budget runs out, but the request keeps running server-side and
 *  usually completes, so telling the user it "failed" is actively wrong. Name
 *  that case explicitly and point at the refresh the modal has already
 *  triggered. */
function applyErrorMessage(caught: unknown, subject: 'cover' | 'photo'): string {
  if (caught instanceof Error && caught.name === 'TimeoutError') {
    return `The server is taking unusually long to apply this ${subject}. It may still complete — reopen this dialog in a moment to check before retrying.`;
  }
  return caught instanceof Error ? caught.message : `Failed to apply ${subject}`;
}

/** What the pick reached, in words (3.6.1's picker said so). */
export function artAppliedMessage(outcome: ArtApplyOutcome, subject: 'cover' | 'photo'): string {
  const reached = [`${subject === 'cover' ? 'Cover' : 'Photo'} updated in SoulSync`];
  if (outcome.server_updated) reached.push('on your media server');
  if (outcome.cover_written) reached.push('as cover.jpg in the album folder');
  if (outcome.disk_written) reached.push('as artist.jpg in the artist folder');
  let message = reached.join(', ');
  if (outcome.disk_skipped) message += `. ${outcome.disk_skipped}`;
  return message;
}

function announce(outcome: ArtApplyOutcome, subject: 'cover' | 'photo') {
  window.showToast?.(artAppliedMessage(outcome, subject), 'success');
  // the embed retag could not start: the pick stands, the files keep old art
  if (outcome.embed_warning) window.showToast?.(outcome.embed_warning, 'warning');
}

/** Paste any image link: 3.6.1's picker had it, for the cover no provider
 *  found. The server downloads and checks it like any candidate. */
function PasteImageUrl({
  subject,
  disabled,
  onApply,
}: {
  subject: 'cover' | 'photo';
  disabled: boolean;
  onApply: (url: string) => void;
}) {
  const [url, setUrl] = useState('');
  const valid = /^https?:\/\/\S+$/i.test(url.trim());
  return (
    <form
      className={styles.previewToolbar}
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !disabled) onApply(url.trim());
      }}
    >
      <input
        className={styles.searchInput}
        type="url"
        placeholder={`…or paste an image URL for the ${subject}`}
        aria-label={`Image URL for the ${subject}`}
        value={url}
        onChange={(event) => setUrl(event.target.value)}
      />
      <button type="submit" className={styles.btnGhost} disabled={!valid || disabled}>
        Use this URL
      </button>
    </form>
  );
}

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
  const [releasing, setReleasing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function apply(url: string) {
    setBusyUrl(url);
    setError(null);
    try {
      const outcome = await applyLibraryV2AlbumArt(albumId, url);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      announce(outcome, 'cover');
      onClose();
    } catch (caught) {
      // dd28-23: a client-side abort does not cancel the server-side apply —
      // it commits the override and rewrites the cache file regardless. Always
      // re-read after a failure so the UI shows what the server actually has,
      // instead of leaving a stale thumbnail next to a "failed" message.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      setError(applyErrorMessage(caught, 'cover'));
      setBusyUrl(null);
    }
  }

  async function release() {
    if (busyUrl || releasing) return;
    setReleasing(true);
    setError(null);
    try {
      await releaseLibraryV2AlbumArt(albumId);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to release cover art');
      setReleasing(false);
    }
  }

  // a candidate whose image never loads drops out (3.6.1 did) instead of
  // sitting there as an empty tile
  const [broken, setBroken] = useState<Set<string>>(new Set());
  const candidates = (optionsQuery.data ?? []).filter((c) => !broken.has(c.url));

  return (
    <LibraryToolDialog
      title={`Change Cover — ${albumTitle}`}
      description="Select an image to apply it. Your choice is kept on refresh."
      onClose={onClose}
      fitContent
      footer={
        <div className={styles.modalActions}>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busyUrl !== null || releasing}
            title="Stop overriding this cover and follow automatic/server artwork again"
            onClick={() => void release()}
          >
            {releasing ? 'Releasing…' : 'Use server art'}
          </button>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            Cancel
          </button>
        </div>
      }
    >
      <div className={styles.previewToolbar}>
        <span className={styles.previewQuiet}>{candidates.length} image options</span>
        <button
          type="button"
          className={styles.btnGhost}
          title="Refresh — re-query every provider instead of the cached result"
          disabled={optionsQuery.isFetching}
          onClick={() => setRefreshNonce((n) => n + 1)}
        >
          {optionsQuery.isFetching ? 'Refreshing…' : 'Refresh images'}
        </button>
      </div>
      <PasteImageUrl
        subject="cover"
        disabled={busyUrl !== null || releasing}
        onApply={(url) => void apply(url)}
      />
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
                disabled={busyUrl !== null || releasing}
                onPick={() => void apply(c.url)}
                onBroken={() => setBroken((prev) => new Set(prev).add(c.url))}
              />
            ))}
          </div>
        )}
      </div>
    </LibraryToolDialog>
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
  const [releasing, setReleasing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function apply(url: string) {
    setBusyUrl(url);
    setError(null);
    try {
      const outcome = await applyLibraryV2ArtistArt(artistId, url);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      announce(outcome, 'photo');
      onClose();
    } catch (caught) {
      // dd28-23: see the matching comment in AlbumArtPickerModal above.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      setError(applyErrorMessage(caught, 'photo'));
      setBusyUrl(null);
    }
  }

  async function release() {
    if (busyUrl || releasing) return;
    setReleasing(true);
    setError(null);
    try {
      await releaseLibraryV2ArtistArt(artistId);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to release artist photo');
      setReleasing(false);
    }
  }

  const [broken, setBroken] = useState<Set<string>>(new Set());
  const candidates = (optionsQuery.data ?? []).filter((c) => !broken.has(c.url));

  return (
    <LibraryToolDialog
      title={`Change Photo — ${artistName}`}
      description="Select an image to apply it. Your choice is kept on refresh."
      onClose={onClose}
      fitContent
      footer={
        <div className={styles.modalActions}>
          <a
            className={styles.btnGhost}
            href={`/api/library/v2/artwork/artist/${artistId}`}
            download={`${artistName}.jpg`}
          >
            Download current photo
          </a>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busyUrl !== null || releasing}
            title="Stop overriding this photo and follow automatic/server artwork again"
            onClick={() => void release()}
          >
            {releasing ? 'Releasing…' : 'Use server art'}
          </button>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            Cancel
          </button>
        </div>
      }
    >
      <div className={styles.previewToolbar}>
        <span className={styles.previewQuiet}>{candidates.length} image options</span>
        <button
          type="button"
          className={styles.btnGhost}
          title="Refresh — re-query every provider instead of the cached result"
          disabled={optionsQuery.isFetching}
          onClick={() => setRefreshNonce((n) => n + 1)}
        >
          {optionsQuery.isFetching ? 'Refreshing…' : 'Refresh images'}
        </button>
      </div>
      <PasteImageUrl
        subject="photo"
        disabled={busyUrl !== null || releasing}
        onApply={(url) => void apply(url)}
      />
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
                subject="photo"
                busy={busyUrl === c.url}
                disabled={busyUrl !== null || releasing}
                onPick={() => void apply(c.url)}
                onBroken={() => setBroken((prev) => new Set(prev).add(c.url))}
              />
            ))}
          </div>
        )}
      </div>
    </LibraryToolDialog>
  );
}

function ArtPickerCard({
  candidate,
  subject = 'cover',
  busy,
  disabled,
  onPick,
  onBroken,
}: {
  candidate: LibraryV2ArtCandidate;
  subject?: 'cover' | 'photo';
  busy: boolean;
  disabled: boolean;
  onPick: () => void;
  onBroken?: () => void;
}) {
  return (
    <button
      type="button"
      className={styles.artPickerCard}
      disabled={disabled}
      title={`Use this ${subject} from ${candidate.source}`}
      onClick={onPick}
    >
      <img
        className={styles.artPickerImg}
        src={candidate.url}
        alt={`${subject === 'photo' ? 'Photo' : 'Cover'} option from ${candidate.source}`}
        loading="lazy"
        onError={onBroken}
      />
      <span className={styles.artPickerBadge}>{busy ? 'Applying…' : candidate.source}</span>
    </button>
  );
}
