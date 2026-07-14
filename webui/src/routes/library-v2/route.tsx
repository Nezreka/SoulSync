import { createFileRoute } from '@tanstack/react-router';

import {
  libraryV2AlbumQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2PlaylistQueryOptions,
  libraryV2PlaylistsQueryOptions,
} from './-library-v2.api';
import { libraryV2SearchSchema } from './-library-v2.types';
import { LibraryV2Page } from './-ui/library-v2-page';

export const Route = createFileRoute('/library-v2')({
  validateSearch: libraryV2SearchSchema,
  loaderDeps: ({ search }) => ({
    q: search.q,
    sort: search.sort,
    page: search.page,
    monitored: search.monitored,
    album: search.album,
    playlist: search.playlist,
    section: search.section,
  }),
  loader: async ({ context, deps }) => {
    // Warm the feature-flag check + first page of artists; never block on a
    // transient fetch failure — the page owns its own empty/error/disabled state.
    await context.queryClient
      .ensureQueryData(libraryV2EnabledQueryOptions())
      .catch(() => undefined);
    if (deps.playlist) {
      void context.queryClient.prefetchQuery(libraryV2PlaylistQueryOptions(deps.playlist));
    } else if (deps.section === 'playlists') {
      void context.queryClient.prefetchQuery(libraryV2PlaylistsQueryOptions());
    } else if (deps.album) {
      void context.queryClient.prefetchQuery(libraryV2AlbumQueryOptions(deps.album));
    } else {
      void context.queryClient.prefetchQuery(libraryV2ArtistsQueryOptions(deps));
    }
  },
  component: LibraryV2Page,
});
