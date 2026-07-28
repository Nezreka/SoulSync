import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { Discography, DiscographyBucket, DiscographyRelease } from '../-artist-detail.types';

import { Route } from '../$source/$id';
import {
  artistDetailQueryOptions,
  isSourceOnlyArtist,
  needsCompletionStream,
  readArtistDetail,
  settleOwnershipForSourceArtist,
  watchlistIdentity,
} from '../-artist-detail.api';
import {
  applyMusicBrainzDeclutter,
  defaultFilterState,
  type DiscographyFilterState,
  isMusicBrainzDiscography,
} from '../-artist-detail.filters';
import { heroImage } from '../-artist-detail.hero-stats';
import {
  albumTracksParams,
  isReleaseClickable,
  openReleaseArtist,
  releaseToAlbumData,
  stillCheckingMessage,
} from '../-artist-detail.open-release';
import { useCompletionStream } from '../-artist-detail.use-completion';
import { ArtistHero } from './artist-hero';
import { DiscographyFilters } from './discography-filters';
import { DiscographySection } from './discography-section';

const BUCKETS: DiscographyBucket[] = ['albums', 'eps', 'singles'];

export function ArtistDetailPage() {
  useReactPageShell('artist-detail');

  // Deliberately NOT profile-scoped: /api/artist-detail is not, and the
  // vanilla never keyed this page on a profile either.
  const { source, id } = Route.useParams();
  const { name } = Route.useSearch();

  const query = useQuery(artistDetailQueryOptions(source, id, name));

  const payload = useMemo(() => {
    try {
      return readArtistDetail(query.data);
    } catch {
      return null;
    }
  }, [query.data]);

  const sourceOnly = payload ? isSourceOnlyArtist(payload) : false;

  /**
   * A source artist has no library to check ownership against, so every
   * unresolved `owned` is settled to false before render — otherwise those
   * cards sit in "checking" forever, since nothing will ever stream a result.
   */
  const discography: Discography = useMemo(() => {
    if (!payload?.discography) return {};
    return sourceOnly ? settleOwnershipForSourceArtist(payload.discography) : payload.discography;
  }, [payload, sourceOnly]);

  /**
   * Ownership streaming. The gate is the vanilla's: library artists only, and
   * only when something is actually unresolved — a fully-resolved discography
   * would open a stream that can never report anything.
   *
   * Everything below renders from `streamed`, not `discography`, so the section
   * counts and the release cards move with the bars.
   */
  const stream = useCompletionStream(
    payload?.artist?.name,
    discography,
    payload ? needsCompletionStream(payload) : false,
  );
  const streamed = stream.discography;

  const isMusicBrainz = isMusicBrainzDiscography(streamed.source);

  /**
   * Filters reset on every artist change (resetDiscographyFilters ran BEFORE
   * the fetch in the vanilla), then the MusicBrainz declutter is applied once
   * the discography's real source is known.
   */
  const [filters, setFilters] = useState<DiscographyFilterState>(defaultFilterState);
  useEffect(() => {
    setFilters(applyMusicBrainzDeclutter(defaultFilterState(), discography.source));
  }, [source, id, discography.source]);

  /**
   * CSS keys off this to hide library-only UI. Set on the BODY because that is
   * where the vanilla put it and the stylesheets select on it; cleared on
   * unmount so a later page does not inherit an artist's flag.
   */
  useEffect(() => {
    if (!payload) return;
    document.body.dataset.artistSource = sourceOnly ? 'source' : 'library';
    return () => {
      delete document.body.dataset.artistSource;
    };
  }, [payload, sourceOnly]);

  /** Non-fatal: the page still renders, but the vanilla warned about it. */
  useEffect(() => {
    const providerError = payload?.provider_error?.error;
    if (providerError) {
      window.showToast?.(`Discography provider warning: ${providerError}`, 'error');
    }
  }, [payload]);

  /** Fire-and-forget, exactly as the vanilla did — never awaited. */
  useEffect(() => {
    if (!payload?.artist?.name) return;
    window.cancelSimilarArtistsLoad?.();
    window.loadSimilarArtists?.(payload.artist.name);
    return () => window.cancelSimilarArtistsLoad?.();
  }, [payload?.artist?.name]);

  useEffect(() => {
    if (!payload || sourceOnly) return;
    // Library artists only — the endpoint works on library primary keys.
    window.checkArtistEnhanceEligibility?.(payload.artist?.id);
  }, [payload, sourceOnly]);

  /** The watchlist is keyed on the CANONICAL Spotify identity where one exists. */
  useEffect(() => {
    if (!payload) return;
    const identity = watchlistIdentity(payload);
    if (identity) window.initializeLibraryWatchlistButton?.(identity.id, identity.name);
  }, [payload]);

  const failed = query.isError || query.data?.success === false;
  const errorMessage =
    (query.error as Error | undefined)?.message ||
    query.data?.error ||
    'Failed to load artist data';

  useEffect(() => {
    if (failed) window.showToast?.(`Failed to load artist details: ${errorMessage}`, 'error');
  }, [failed, errorMessage]);

  const openRelease = async (release: DiscographyRelease) => {
    if (!isReleaseClickable(release)) {
      window.showToast?.(stillCheckingMessage(release), 'info');
      return;
    }
    if (!payload) return;

    const image = heroImage(payload.artist ?? {}, streamed);
    const artist = openReleaseArtist(payload, payload.artist?.id, image.primary);
    if (!artist) {
      window.showToast?.('Error: No artist information available', 'error');
      return;
    }

    window.showLoadingOverlay?.('Loading album...');
    try {
      const album = releaseToAlbumData(release);
      const params = new URLSearchParams(albumTracksParams(release, artist));
      const response = await fetch(`/api/album/${album.id}/tracks?${params}`);
      if (!response.ok) throw new Error(`Failed to load album tracks: ${response.status}`);

      const data = await response.json();
      if (!data.success || !data.tracks?.length)
        throw new Error('No tracks found for this release');

      // The modal opens immediately; ownership backfills behind it.
      window.hideLoadingOverlay?.();
      await window.openAddToWishlistModal?.(album, artist, data.tracks, album.album_type);
      window.lazyLoadTrackOwnership?.(artist.name, data.tracks, null, album.name);
    } catch (error) {
      window.hideLoadingOverlay?.();
      window.showToast?.(`Error opening wishlist modal: ${(error as Error).message}`, 'error');
    }
  };

  // The hero stays hidden on failure — the vanilla kept it hidden rather than
  // showing an empty shell over an error.
  if (failed) {
    return (
      <div className="artist-detail-content">
        <div className="artist-detail-error" id="artist-detail-error">
          <div className="error-icon">⚠️</div>
          <h3>Failed to load artist details</h3>
          <p id="artist-detail-error-message">{errorMessage}</p>
          <button type="button" className="retry-btn" onClick={() => void query.refetch()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (query.isPending || !payload) {
    return (
      <div className="artist-detail-content">
        <div className="artist-detail-loading" id="artist-detail-loading">
          <div className="loading-spinner" />
          <p>Loading artist discography...</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <ArtistHero
        artist={payload.artist ?? {}}
        discography={streamed}
        isSourceArtist={sourceOnly}
        streamCounts={stream.counts}
        streamCompleted={stream.completed}
      />

      <div className="artist-detail-content">
        <div className="artist-detail-main" id="artist-detail-main">
          <DiscographyFilters filters={filters} onChange={setFilters} isSourceArtist={sourceOnly} />
          {BUCKETS.map((bucket) => (
            <DiscographySection
              key={bucket}
              bucket={bucket}
              releases={streamed[bucket] ?? []}
              filters={filters}
              isMusicBrainz={isMusicBrainz}
              isSourceArtist={sourceOnly}
              onOpen={openRelease}
            />
          ))}
        </div>
      </div>
    </>
  );
}
