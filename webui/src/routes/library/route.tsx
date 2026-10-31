import { createFileRoute, redirect } from '@tanstack/react-router';

import { isArtistView, lastNavigationFrom } from '@/platform/shell/last-navigation';
import { guardPageAccess } from '@/platform/shell/route-guard';

import {
  artistHeaderFromLibrary,
  artistHeaderFromElsewhere,
  LIBRARY_V2_QUERY_KEY,
  resolveLibraryV2DiscoveryArtistOwnership,
  libraryV2AlbumQueryOptions,
  libraryV2AlbumsQueryOptions,
  libraryV2ArtistQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2WantedQueryOptions,
} from './-library-v2.api';
import { libraryV2SearchSchema } from './-library-v2.types';
import { LibraryV2Page } from './-ui/library-v2-page';

export const Route = createFileRoute('/library')({
  validateSearch: libraryV2SearchSchema,
  beforeLoad: async ({ context, search, location, cause }) => {
    // Resolve provider-qualified identities without materializing catalogue rows.
    const discover = search.discover ?? '';
    const split = discover.indexOf(':');
    // an artist link for a profile that may search but not open the library
    // goes to the artist page, the way dev let library OR search open it.
    if (!context.shell.bridge.isPageAllowed('library')) {
      if (split > 0 && !search.artist && !search.album)
        throw redirect({
          to: '/artist-detail/$source/$id',
          params: { source: discover.slice(0, split), id: discover.slice(split + 1) },
          search: { name: search.discoverName ?? '' },
          replace: true,
        });
      if (search.artist && search.view === 'discovery' && search.immersive)
        throw redirect({
          to: '/artist-detail/$source/$id',
          params: { source: 'library', id: String(search.artist) },
          search: {},
          replace: true,
        });
    }
    guardPageAccess(context.shell.bridge, 'library');
    if (split > 0 && !search.artist && !search.album) {
      const source = discover.slice(0, split);
      const providerId = discover.slice(split + 1);
      const known = await context.queryClient
        .fetchQuery({
          queryKey: [
            ...LIBRARY_V2_QUERY_KEY,
            'discovery-resolve-owned',
            source,
            providerId,
            search.discoverName,
          ],
          queryFn: () =>
            resolveLibraryV2DiscoveryArtistOwnership({
              source,
              providerId,
              name: search.discoverName ?? '',
            }),
        })
        .then((found) => (found.artistId ? found : null))
        .catch(() => null);
      if (!known && source === 'library')
        throw redirect({ to: '/library', search: {}, replace: true });
      // every artist is this page, the way dev had one artist page for
      // everyone. one the library has a row for opens it, with My Library
      // only when it has files here; one it has never heard of stays on
      // ?discover= and the page reads it from the provider (no row written)
      if (known)
        throw redirect({
          to: '/library',
          search: {
            ...search,
            artist: known.artistId ?? undefined,
            discover: undefined,
            discoverName: undefined,
            view: 'discovery',
            header: await artistHeaderFromElsewhere(context.queryClient),
            releases: known.owned || source === 'library' ? (search.releases ?? 'all') : 'all',
            releaseView: 'cards',
          },
          replace: true,
        });
    }
    // an artist reached from another page (the wishlist, the watchlist, search,
    // the dashboard) opens in the header this profile picked for arrivals from
    // elsewhere; inside the library ('stay') its own links pick the library's.
    // a header written in the url of a cold load (a bookmark, a reload) stays
    const transitionFrom = lastNavigationFrom();
    const enteredFromOutside = Boolean(transitionFrom && !isArtistView(transitionFrom));
    const startedAtTarget =
      transitionFrom?.pathname === location.pathname &&
      transitionFrom.searchStr === location.searchStr;
    const coldWithoutHeader =
      cause === 'enter' && (!transitionFrom || startedAtTarget) && search.header === undefined;
    if (search.artist && (enteredFromOutside || coldWithoutHeader)) {
      const fromLibrary =
        transitionFrom?.pathname.replace(/\/+$/, '').endsWith('/library') ?? false;
      const header = fromLibrary
        ? await artistHeaderFromLibrary(context.queryClient)
        : await artistHeaderFromElsewhere(context.queryClient);
      if (header !== search.header) {
        throw redirect({ to: '/library', search: { ...search, header }, replace: true });
      }
    }
  },
  loaderDeps: ({ search }) => ({
    q: search.q,
    sort: search.sort,
    albumSort: search.albumSort,
    page: search.page,
    monitored: search.monitored,
    album: search.album,
    artist: search.artist,
    discover: search.discover,
    section: search.section,
    wantedKind: search.wantedKind,
    letter: search.letter,
    source: search.source,
    quality: search.quality,
  }),
  loader: async ({ context, deps }) => {
    // Warm the feature-flag check + first page of artists; never block on a
    // transient fetch failure — the page owns its own empty/error/disabled state.
    await context.queryClient
      .ensureQueryData(libraryV2EnabledQueryOptions())
      .catch(() => undefined);
    // the songs view asks for its own page; an artists prefetch here would be
    // a request nothing on screen reads (upstream #1906)
    if (deps.section === 'songs' && !deps.album && !deps.artist) return;
    if (deps.section === 'wanted') {
      void context.queryClient.prefetchQuery(
        libraryV2WantedQueryOptions({ q: deps.q, page: deps.page, wantedKind: deps.wantedKind }),
      );
    } else if (deps.section === 'albums' && !deps.album && !deps.artist) {
      void context.queryClient.prefetchQuery(libraryV2AlbumsQueryOptions(deps));
    } else if (deps.album) {
      void context.queryClient.prefetchQuery(libraryV2AlbumQueryOptions(deps.album));
    } else if (deps.artist) {
      void context.queryClient.prefetchQuery(libraryV2ArtistQueryOptions(deps.artist));
    } else if (!deps.discover) {
      void context.queryClient.prefetchQuery(libraryV2ArtistsQueryOptions(deps));
    }
  },
  component: LibraryV2Page,
});
