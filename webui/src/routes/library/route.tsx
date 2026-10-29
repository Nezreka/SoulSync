import { createFileRoute, redirect } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
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
  beforeLoad: async ({ context, search }) => {
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
        .then((found) =>
          // only an artist with files here opens in the library. a
          // watchlisted or discography-only row keeps the artist page.
          found.artistId && (source === 'library' || found.owned) ? found.artistId : null,
        )
        .catch(() => null);
      if (!known && source === 'library')
        throw redirect({ to: '/library', search: {}, replace: true });
      if (known)
        throw redirect({
          to: '/library',
          search: {
            ...search,
            artist: known,
            discover: undefined,
            discoverName: undefined,
            view: 'discovery',
            header: 'rich',
            releases: 'all',
            releaseView: 'cards',
          },
          replace: true,
        });
      const albumSplit = search.discoverAlbum?.indexOf(':') ?? -1;
      throw redirect({
        to: '/artist-detail/$source/$id',
        params: { source, id: providerId },
        search: {
          name: search.discoverName ?? '',
          album:
            albumSplit > 0 && search.discoverAlbum?.slice(0, albumSplit) === source
              ? search.discoverAlbum.slice(albumSplit + 1)
              : undefined,
        },
        replace: true,
      });
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
    if (deps.section === 'songs' && !deps.album && !deps.artist) {
      // the songs tab loads its own list (it is keyed by profile)
    } else if (deps.section === 'wanted') {
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
