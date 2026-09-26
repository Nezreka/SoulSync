import { createFileRoute, redirect } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

import { useShellBridge } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { artistDetailSearchSchema } from '../-artist-detail.types';
import { ArtistDetailPage } from '../-ui/artist-detail-page';
import {
  LIBRARY_V2_QUERY_KEY,
  resolveLibraryV2DiscoveryArtist,
} from '../../library/-library-v2.api';
import { ArtistPageLibraryProvider } from '../../library/-ui/artist-page-release-actions';

/**
 * Whether the shell has handed artist detail over to React yet.
 *
 * TanStack matches from the generated route tree, not the manifest, so this
 * route matches the moment the file exists. Without this guard the React page
 * and the vanilla page would both activate on the same URL. Delete the
 * indirection once the vanilla artist-detail page is gone.
 *
 * The search schema moved to -artist-detail.types.ts, where its preprocess is
 * unit-tested: TanStack JSON-parses search values, so an all-digits artist
 * name ("311", "702") arrives as a NUMBER and a bare z.string() throws
 * SearchParamError, killing the route. That has regressed once already.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('artist-detail')?.kind === 'react';
}

export const Route = createFileRoute('/artist-detail/$source/$id')({
  validateSearch: artistDetailSearchSchema,
  /**
   * Library v2 owns every artist the catalogue knows; this page shows the ones
   * it does not. `library:<n>` is a catalogue row. Any other id is looked up
   * first, with a plain GET so that opening a search result creates nothing:
   * a provider artist the catalogue already holds opens there, with the full
   * discography as cards so it looks like what the user clicked. A failed
   * lookup still shows the provider page, which is the right answer for an
   * artist we cannot place.
   */
  beforeLoad: async ({ params, search, context }) => {
    const source = params.source.toLowerCase();
    const isLibrary = source === 'library';
    if (isLibrary && /^\d+$/.test(params.id)) {
      throw redirect({ to: '/library', search: { artist: Number(params.id) }, replace: true });
    }
    const known = await context.queryClient
      .fetchQuery({
        queryKey: [...LIBRARY_V2_QUERY_KEY, 'discovery-resolve', source, params.id, search.name],
        queryFn: () =>
          resolveLibraryV2DiscoveryArtist({ source, providerId: params.id, name: search.name }),
      })
      .catch(() => null);
    if (known) {
      throw redirect({
        to: '/library',
        search: isLibrary
          ? { artist: known }
          : { artist: known, releases: 'all', releaseView: 'cards', header: 'rich' },
        replace: true,
      });
    }
    // A media-server id nothing in the catalogue carries has no provider to
    // ask either (`/api/artist-detail` needs a source).
    if (isLibrary) throw redirect({ to: '/library', replace: true });
  },
  component: ArtistDetailRouteComponent,
});

function ArtistDetailRouteComponent() {
  const { source, id } = Route.useParams();
  const { name } = Route.useSearch();
  if (!isReactOwned()) {
    return <LegacyArtistDetailHandoff />;
  }
  return (
    <ArtistPageLibraryProvider source={source} id={id} name={name}>
      <ArtistDetailPage />
    </ArtistPageLibraryProvider>
  );
}

/**
 * Thin legacy handoff: TanStack owns the URL shape, but the vanilla JS page
 * still renders the experience. The route owns cancellation so similar-artist
 * loading stops when this page changes.
 */
function LegacyArtistDetailHandoff() {
  const bridge = useShellBridge();
  const { source, id } = Route.useParams();
  const { name } = Route.useSearch();

  useLayoutEffect(() => {
    if (!bridge) return;

    const normalizedSource = source.toLowerCase() === 'library' ? null : source.toLowerCase();
    bridge.navigateToArtistDetail(id, name, normalizedSource, {
      skipRouteChange: true,
    });

    return () => {
      bridge.cancelSimilarArtistsLoad();
    };
  }, [bridge, id, source, name]);

  return null;
}
