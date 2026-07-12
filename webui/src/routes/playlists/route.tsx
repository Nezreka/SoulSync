import { createFileRoute } from '@tanstack/react-router';

import { playlistsSearchSchema } from './-playlists.types';
import { PlaylistsPage } from './-ui/playlists-page';

export const Route = createFileRoute('/playlists')({
  validateSearch: playlistsSearchSchema,
  component: PlaylistsPage,
});
