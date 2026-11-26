import { createFileRoute } from '@tanstack/react-router';

import { HomeAlbumPage, HomeVideosPage, type HomeView } from './-ui/home-videos';
import { NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';

interface HomeSearch {
  library?: number;
  album?: string;
  view?: HomeView;
}

export const Route = createFileRoute('/player/home-videos')({
  validateSearch: (search: Record<string, unknown>): HomeSearch => {
    const out: HomeSearch = {};
    const lib = Number(search.library);
    if (Number.isInteger(lib) && lib > 0) out.library = lib;
    if (typeof search.album === 'string' && /^[0-9a-f]{6,40}$/.test(search.album))
      out.album = search.album;
    if (search.view === 'folders') out.view = 'folders';
    return out;
  },
  component: HomeVideosRoute,
});

function HomeVideosRoute() {
  const sections = useSections();
  const { library, album, view } = Route.useSearch();
  if (!sections.video) return <NotPartOfProfile what="Home videos" />;
  if (album) return <HomeAlbumPage library={library} album={album} />;
  return <HomeVideosPage library={library} view={view} />;
}
