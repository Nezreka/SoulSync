import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { fetchMirroredPlaylists } from './-explorer.api';
import { EXPLORER_PLAYLISTS_QUERY_KEY, ExplorerPage } from './-ui/explorer-page';

export const Route = createFileRoute('/playlist-explorer')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'playlist-explorer');
  },
  loader: async ({ context }) => {
    // Warms the picker for the first paint; it does NOT gate the route.
    // allSettled, so a backend hiccup leaves an empty picker rather than
    // handing the whole page to the router's error component.
    await Promise.allSettled([
      context.queryClient.ensureQueryData({
        queryKey: EXPLORER_PLAYLISTS_QUERY_KEY,
        queryFn: fetchMirroredPlaylists,
      }),
    ]);
  },
  component: ExplorerPage,
});
