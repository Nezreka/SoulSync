import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
  libraryV2AlbumQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2WantedQueryOptions,
} from './-library-v2.api';
import { libraryV2SearchSchema } from './-library-v2.types';
import { LibraryV2Page } from './-ui/library-v2-page';

export const Route = createFileRoute('/library')({
  validateSearch: libraryV2SearchSchema,
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'library');
  },
  loaderDeps: ({ search }) => ({
    q: search.q,
    sort: search.sort,
    page: search.page,
    monitored: search.monitored,
    album: search.album,
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
    } else if (deps.album) {
      void context.queryClient.prefetchQuery(libraryV2AlbumQueryOptions(deps.album));
    } else {
      void context.queryClient.prefetchQuery(libraryV2ArtistsQueryOptions(deps));
    }
  },
  component: LibraryV2Page,
});
