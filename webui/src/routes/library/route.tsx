import { createFileRoute, redirect } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
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
  beforeLoad: ({ context, search }) => {
    guardPageAccess(context.shell.bridge, 'library');
    // An artist the catalogue does not hold opens on the artist-detail page.
    // `discover=<source>:<id>` is what that used to be here, and it still
    // arrives from bookmarks and history. Split on the FIRST colon: provider
    // ids can contain one.
    const discover = search.discover ?? '';
    const split = discover.indexOf(':');
    if (split > 0 && !search.artist && !search.album) {
      throw redirect({
        to: '/artist-detail/$source/$id',
        params: { source: discover.slice(0, split), id: discover.slice(split + 1) },
        search: { name: search.discoverName ?? '' },
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
