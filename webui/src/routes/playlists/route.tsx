import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { kindsQueryOptions, playlistsQueryOptions } from './-playlists.api';
import { playlistsSearchSchema } from './-playlists.types';
import { PlaylistsPage } from './-ui/playlists-page';

export const Route = createFileRoute('/playlists')({
  validateSearch: playlistsSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('playlists')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loader: async ({ context }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(kindsQueryOptions()),
      context.queryClient.ensureQueryData(playlistsQueryOptions()),
    ]);
  },
  component: PlaylistsPage,
});
