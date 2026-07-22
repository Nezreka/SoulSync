import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import {
  libraryV2AlbumQueryOptions,
  libraryV2AcquisitionImportsQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2PlaylistQueryOptions,
  libraryV2PlaylistsQueryOptions,
  libraryV2WantedQueryOptions,
} from './-library-v2.api';
import { libraryV2SearchSchema } from './-library-v2.types';
import { LibraryV2Page } from './-ui/library-v2-page';

export const Route = createFileRoute('/library-v2')({
  validateSearch: libraryV2SearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;
    if (!bridge.isPageAllowed('library-v2')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => ({
    q: search.q,
    sort: search.sort,
    page: search.page,
    monitored: search.monitored,
    album: search.album,
    playlist: search.playlist,
    section: search.section,
    wantedKind: search.wantedKind,
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
    } else if (deps.section === 'wanted') {
      void context.queryClient.prefetchQuery(
        libraryV2WantedQueryOptions({ q: deps.q, page: deps.page, wantedKind: deps.wantedKind }),
      );
    } else if (deps.section === 'import-review') {
      void context.queryClient.prefetchQuery(libraryV2AcquisitionImportsQueryOptions());
    } else if (deps.album) {
      void context.queryClient.prefetchQuery(libraryV2AlbumQueryOptions(deps.album));
    } else {
      void context.queryClient.prefetchQuery(libraryV2ArtistsQueryOptions(deps));
    }
  },
  component: LibraryV2Page,
});
