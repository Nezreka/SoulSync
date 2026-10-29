import { createFileRoute, redirect } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

import { useShellBridge } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { artistDetailSearchSchema } from '../-artist-detail.types';
import { ArtistDetailPage } from '../-ui/artist-detail-page';
import { fetchLibraryArtistIdentity } from '../-artist-detail.api';
import {
  LIBRARY_V2_QUERY_KEY,
  resolveLibraryV2DiscoveryArtistOwnership,
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
  beforeLoad: async ({ params, search, context }) => {
    const source = params.source.toLowerCase();
    const isLibrary = source === 'library';
    // dev let a profile with library OR search open artist pages. a profile
    // without the library page keeps the original page instead of being
    // bounced home by the library guard.
    const libraryAllowed = context.shell.bridge.isPageAllowed('library');
    if (!libraryAllowed) {
      if (isLibrary && /^\d+$/.test(params.id)) {
        const identity = await fetchLibraryArtistIdentity(Number(params.id)).catch(() => null);
        if (identity?.source && identity.providerId) {
          throw redirect({
            to: '/artist-detail/$source/$id',
            params: { source: identity.source, id: identity.providerId },
            search: { name: identity.name || search.name },
            replace: true,
          });
        }
      }
      return;
    }
    let known: number | null = null;
    if (isLibrary && /^\d+$/.test(params.id)) {
      known = Number(params.id);
    } else {
      const found = await context.queryClient
        .fetchQuery({
          queryKey: [
            ...LIBRARY_V2_QUERY_KEY,
            'discovery-resolve-owned',
            source,
            params.id,
            search.name,
          ],
          queryFn: () =>
            resolveLibraryV2DiscoveryArtistOwnership({
              source,
              providerId: params.id,
              name: search.name,
            }),
        })
        .catch(() => null);
      // only an artist with files here leaves the artist page. a watchlisted,
      // bookmarked or discography-only row keeps the original page. a
      // media-server id has no provider page, so any catalogue row will do.
      if (found?.artistId && (isLibrary || found.owned)) known = found.artistId;
    }
    if (known) {
      throw redirect({
        to: '/library',
        search: {
          artist: known,
          album:
            isLibrary && search.album && /^\d+$/.test(search.album)
              ? Number(search.album)
              : undefined,
          view: 'discovery',
          immersive: true,
        },
        replace: true,
      });
    }
    // Unknown provider artists retain the original page. Media-server ids
    // without a catalogue match have no provider page to fall back to.
    if (isLibrary) throw redirect({ to: '/library', search: {}, replace: true });
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
