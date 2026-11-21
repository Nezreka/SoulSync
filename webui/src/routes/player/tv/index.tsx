import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';

import { recentEpisodeCards, showCard } from '../-lib/rails';
import { genresOf, sortTitles, validateBrowseSearch } from '../-lib/video';
import { videoCatalogQuery } from '../-player.api';
import {
  CardGrid,
  CardRail,
  Empty,
  PageTitle,
  useViewMode,
  ViewToggle,
  styles as s,
} from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BrowseToolbar } from '../-ui/video-parts';

export const Route = createFileRoute('/player/tv/')({
  validateSearch: validateBrowseSearch,
  component: TvBrowse,
});

function TvBrowse() {
  const sections = useSections();
  const [view, setView] = useViewMode('tv');
  const { sort = 'added', genre = '' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/tv/' });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.tv });
  const shows = catalog.data?.shows ?? [];
  const genres = useMemo(() => genresOf(shows), [shows]);

  const cards: RailCard[] = useMemo(
    () =>
      sortTitles(genre ? shows.filter((x) => x.genres.includes(genre)) : shows, sort).map(showCard),
    [shows, genre, sort],
  );

  if (!sections.tv) return <NotPartOfProfile what="TV shows" />;

  return (
    <>
      <PageTitle>TV</PageTitle>
      {!genre ? (
        <CardRail
          title="New episodes"
          cards={recentEpisodeCards(catalog.data?.recent_episodes ?? [], 16)}
        />
      ) : null}
      <div className={s.rail}>
        <BrowseToolbar
          sort={sort}
          onSort={(next) =>
            void navigate({ search: (prev) => ({ ...prev, sort: next }), replace: true })
          }
          genres={genres}
          genre={genre}
          onGenre={(next) =>
            void navigate({
              search: (prev) => ({ ...prev, genre: next || undefined }),
              replace: true,
            })
          }
          count={cards.length}
          noun={cards.length === 1 ? 'show' : 'shows'}
          extra={<ViewToggle value={view} onChange={setView} />}
        />
      </div>
      {catalog.isLoading ? <div className={s.skeleton} style={{ height: '50vh' }} /> : null}
      {catalog.isError ? (
        <Empty title="Could not load your shows">Try again in a moment.</Empty>
      ) : null}
      {catalog.data && !cards.length ? (
        <Empty title={genre ? `No ${genre.toLowerCase()} shows` : 'No episodes on disk yet'}>
          {genre
            ? 'Try another genre.'
            : 'Shows appear here once at least one episode is downloaded.'}
        </Empty>
      ) : null}
      {cards.length ? <CardGrid cards={cards} view={view} /> : null}
    </>
  );
}
