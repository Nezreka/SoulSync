import { createFileRoute, redirect } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

import { useShellBridge } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { fetchLibraryArtistIdentity } from '../-artist-detail.api';
import { artistDetailSearchSchema } from '../-artist-detail.types';
import { ArtistDetailPage } from '../-ui/artist-detail-page';
import {
  artistHeaderFromElsewhere,
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
    let owned = true;
    let focusAlbum: number | undefined =
      isLibrary && search.album && /^\d+$/.test(search.album) ? Number(search.album) : undefined;
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
            search.album,
          ],
          queryFn: () =>
            resolveLibraryV2DiscoveryArtistOwnership({
              source,
              providerId: params.id,
              name: search.name,
              albumProviderId: isLibrary ? undefined : search.album,
            }),
        })
        .catch(() => null);
      // only an artist with files here leaves the artist page. a watchlisted,
      // bookmarked or discography-only row keeps the original page. a
      // media-server id has no provider page, so any catalogue row will do.
      if (found?.artistId) known = found.artistId;
      owned = isLibrary || found?.owned === true;
      // a provider ?album= keeps its focus when the release is catalogued here
      if (found?.albumId) focusAlbum = found.albumId;
    }
    // every artist opens on the library's artist page, the way dev had one
    // artist page for everyone: My Library only when there are files here
    if (known) {
      throw redirect({
        to: '/library',
        search: {
          artist: known,
          album: focusAlbum,
          view: 'discovery',
          immersive: true,
          header: await artistHeaderFromElsewhere(context.queryClient),
          ...(owned ? {} : { releases: 'all' as const }),
        },
        replace: true,
      });
    }
    // Media-server ids without a catalogue match have no provider page.
    if (isLibrary) throw redirect({ to: '/library', search: {}, replace: true });
    // one the library has never heard of: the same page, read from the
    // provider. nothing is written until you watch, monitor or download
    throw redirect({
      to: '/library',
      search: {
        discover: `${source}:${params.id}`,
        discoverName: search.name || undefined,
        discoverAlbum: search.album ? `${source}:${search.album}` : undefined,
        view: 'discovery',
        immersive: true,
        releases: 'all',
      },
      replace: true,
    });
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
