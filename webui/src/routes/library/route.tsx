import { createFileRoute, redirect } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
  LIBRARY_V2_QUERY_KEY,
  resolveLibraryV2DiscoveryArtist,
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
    guardPageAccess(context.shell.bridge, 'library');
    // Resolve provider-qualified identities without materializing catalogue rows.
    const discover = search.discover ?? '';
    const split = discover.indexOf(':');
    if (split > 0 && !search.artist && !search.album) {
      const source = discover.slice(0, split);
      const providerId = discover.slice(split + 1);
      const known = await context.queryClient
        .fetchQuery({
          queryKey: [
            ...LIBRARY_V2_QUERY_KEY,
            'discovery-resolve',
            source,
            providerId,
            search.discoverName,
          ],
          queryFn: () =>
            resolveLibraryV2DiscoveryArtist({
              source,
              providerId,
              name: search.discoverName ?? '',
            }),
        })
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
  }),
  loader: async ({ context, deps }) => {
    // Warm the feature-flag check + first page of artists; never block on a
    // transient fetch failure — the page owns its own empty/error/disabled state.
    await context.queryClient
      .ensureQueryData(libraryV2EnabledQueryOptions())
      .catch(() => undefined);
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
