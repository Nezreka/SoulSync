import { useQuery } from '@tanstack/react-query';
import { useRouterState } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

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
import { backButtonLabel, pushArtistOrigin, pushPageOrigin } from '../-artist-detail.back-label';
import {
  readEnhancedViewMode,
  showsEnhancedToggle,
  writeEnhancedViewMode,
} from '../-artist-detail.enhanced';
import {
  applyMusicBrainzDeclutter,
  defaultFilterState,
  type DiscographyFilterState,
  isMusicBrainzDiscography,
} from '../-artist-detail.filters';
import { mergeGapReleases } from '../-artist-detail.gap-fill';
import { heroImage } from '../-artist-detail.hero-stats';
import {
  albumTracksParams,
  isReleaseClickable,
  openReleaseArtist,
  releaseToAlbumData,
  stillCheckingMessage,
} from '../-artist-detail.open-release';
import { scrollArtistDetailToTop } from '../-artist-detail.scroll';
import { useCompletionStream } from '../-artist-detail.use-completion';
import { useEnhancedData } from '../-artist-detail.use-enhanced';
import { useGapFill } from '../-artist-detail.use-gap-fill';
import { clearVanillaArtist, syncVanillaArtist } from '../-artist-detail.vanilla-state';
import { ArtistDetailBackButton } from './artist-detail-back-button';
import { ArtistHero } from './artist-hero';
import { ArtistVideosSection } from './artist-videos-section';
import { DiscographyFilters } from './discography-filters';
import { DiscographySection } from './discography-section';
import { EnhancedView } from './enhanced-view';
import { SimilarArtistsSection } from './similar-artists-section';

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

  /**
   * Gap-fill (#1067) merges AFTER the ownership stream, never before: gap
   * releases belong to other sources and must not ride the base artist's
   * completion-stream payload, so they come in with their own ownership
   * already resolved.
   */
  const gapFill = useGapFill(
    payload?.artist?.id,
    payload?.artist?.name,
    discography.source ?? undefined,
    streamed,
  );
  const displayed = useMemo(
    () => mergeGapReleases(streamed, gapFill.releases),
    [streamed, gapFill.releases],
  );

  const isMusicBrainz = isMusicBrainzDiscography(displayed.source);

  /**
   * Enhanced Management view. Offered to an admin on a library artist only, and
   * the choice is persisted per profile — a source artist has no DB record to
   * edit, and forcing the view on one showed an empty pane with the discography
   * hidden behind it.
   */
  const profile = useProfile();
  const canEnhance = showsEnhancedToggle(Boolean(profile?.isAdmin), sourceOnly);
  const [enhanced, setEnhanced] = useState(() => readEnhancedViewMode(profile?.profileId));
  const showEnhanced = canEnhance && enhanced;
  const enhancedState = useEnhancedData(payload?.artist?.id, showEnhanced);

  const toggleEnhanced = (next: boolean) => {
    setEnhanced(next);
    writeEnhancedViewMode(profile?.profileId, next);
  };

  /**
   * Similar Artists belongs to the Standard view and is rendered OUTSIDE React,
   * by loadSimilarArtists into the shell's own section — so hiding it has to be
   * a DOM effect rather than a branch.
   */
  useEffect(() => {
    const section = document.getElementById('ad-similar-artists-section');
    if (!section) return;
    section.style.display = showEnhanced ? 'none' : '';
    return () => {
      section.style.display = '';
    };
  }, [showEnhanced]);

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

  /**
   * Every artist opens at the top — arriving, chaining to a similar artist, or
   * coming back. The router is told to keep its hands off scroll for this
   * route, so this is the only thing touching it and there is no race.
   *
   * Keyed on the ROUTE params rather than the payload: it has to fire before
   * the fetch resolves, or you would sit at the previous artist's scroll
   * position while the new one loads.
   */
  useEffect(() => {
    scrollArtistDetailToTop();
  }, [source, id]);

  /**
   * Record an artist → artist hop for the Back label, the way
   * navigateToArtistDetail did. Recomputed on every artist change so the label
   * is read AFTER the push, not before.
   */
  const previousArtistName = useRef<string | null>(null);
  const [backLabel, setBackLabel] = useState('← Back');

  /**
   * Where you came FROM, for arrivals that never touched
   * navigateToArtistDetail — every React page, since those navigate through
   * the router. `resolvedLocation` is the router's previous location (it is
   * what getLocationChangeInfo reports as fromLocation); on a cold load
   * straight onto an artist url it is undefined, and the label stays "← Back".
   */
  const cameFrom = useRouterState({ select: (state) => state.resolvedLocation?.pathname });
  useEffect(() => {
    pushPageOrigin(cameFrom);
    setBackLabel(backButtonLabel());
    // Only on arrival: re-running as the chain grows would rewrite it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const name = payload?.artist?.name ?? null;
    if (!name) return;
    if (previousArtistName.current && previousArtistName.current !== name) {
      pushArtistOrigin(previousArtistName.current);
    }
    previousArtistName.current = name;
    setBackLabel(backButtonLabel());
  }, [payload?.artist?.name]);

  /**
   * Hand the artist down to the vanilla page state.
   *
   * Artist Radio, the artist art picker and the Download Discography modal all
   * read currentArtistId/currentArtistName rather than taking arguments — with
   * these unset they bail out with "No artist selected" and do nothing.
   *
   * The id is the one the PAYLOAD returned, not the one in the URL: when the
   * backend resolves a source-artist click to an existing library record it
   * hands back the library primary key, and the library-only endpoints behind
   * these buttons need that.
   */
  useEffect(() => {
    if (!payload) return;
    syncVanillaArtist({
      id: payload.artist?.id ?? null,
      name: payload.artist?.name ?? null,
      source: payload.discography?.source ?? payload.artist?.source ?? null,
    });
    // Repaint the sidebar breadcrumb: shell-bridge paints it on the page
    // CHANGE, which happens before this payload lands, so "Library / <Artist>"
    // would never appear on a React-native arrival (a library card is a plain
    // <a href> through the router — nothing calls navigateToArtistDetail,
    // which is what used to set the name early enough).
    window._updateSidebarLibraryBreadcrumb?.();
    return () => {
      clearVanillaArtist();
      // ...and back to a plain "Library" on the way out.
      window._updateSidebarLibraryBreadcrumb?.();
    };
  }, [payload]);

  /** Non-fatal: the page still renders, but the vanilla warned about it. */
  useEffect(() => {
    const providerError = payload?.provider_error?.error;
    if (providerError) {
      window.showToast?.(`Discography provider warning: ${providerError}`, 'error');
    }
  }, [payload]);

  /**
   * Fire-and-forget, exactly as the vanilla did — but only once the section
   * this renders is in the DOM. loadSimilarArtists resolves its four elements
   * by id and returns silently when they are missing, so ordering here is the
   * difference between a populated section and an empty one.
   */
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

    const image = heroImage(payload.artist ?? {}, displayed);
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
      <div className="artist-detail-page">
        <ArtistDetailBackButton label={backLabel} />
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
      </div>
    );
  }

  if (query.isPending || !payload) {
    return (
      <div className="artist-detail-page">
        <ArtistDetailBackButton label={backLabel} />
        <div className="artist-detail-content">
          <div className="artist-detail-loading" id="artist-detail-loading">
            <div className="loading-spinner" />
            <p>Loading artist discography...</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    /**
     * The scoping hook the stylesheet needs. 17 rules are written against
     * #artist-detail-page — the release cards' big-photo treatment, the hero,
     * and the source-artist hides for Artist Radio / Enhance Quality /
     * enrichment coverage. React renders inside #webui-react-root, so without
     * this class every one of them silently stops applying.
     */
    <div className="artist-detail-page">
      <ArtistDetailBackButton label={backLabel} />

      <ArtistHero
        artist={payload.artist ?? {}}
        discography={displayed}
        isSourceArtist={sourceOnly}
        streamCounts={stream.counts}
        streamCompleted={stream.completed}
        enrichment={payload.enrichment_coverage}
        watchlist={watchlistIdentity(payload)}
      />

      <div className="artist-detail-content">
        <div className="artist-detail-main" id="artist-detail-main">
          <DiscographyFilters
            filters={filters}
            onChange={setFilters}
            isSourceArtist={sourceOnly}
            gapFillEnabled={gapFill.enabled}
            onToggleGapFill={gapFill.toggle}
            showViewToggle={canEnhance}
            enhanced={showEnhanced}
            onToggleEnhanced={toggleEnhanced}
          />
          {showEnhanced ? (
            <div id="enhanced-view-container">
              <EnhancedView
                data={enhancedState.data}
                status={enhancedState.status}
                isAdmin={Boolean(profile?.isAdmin)}
                onReload={enhancedState.reload}
              />
            </div>
          ) : (
            <div className="discography-sections">
              {BUCKETS.map((bucket) => (
                <DiscographySection
                  key={bucket}
                  bucket={bucket}
                  releases={displayed[bucket] ?? []}
                  filters={filters}
                  isMusicBrainz={isMusicBrainz}
                  isSourceArtist={sourceOnly}
                  onOpen={openRelease}
                />
              ))}
              <ArtistVideosSection artistName={payload.artist?.name} />
            </div>
          )}

          {/* Standard view only — the vanilla hid it in Enhanced. Mounted
              BEFORE loadSimilarArtists can run, or the loader finds no section
              and bails. */}
          <SimilarArtistsSection />
        </div>
      </div>
    </div>
  );
}
