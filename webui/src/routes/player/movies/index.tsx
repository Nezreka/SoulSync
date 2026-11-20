import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';

import { posterUrl } from '../-lib/rails';
import { genresOf, sortTitles, validateBrowseSearch } from '../-lib/video';
import { videoCatalogQuery } from '../-player.api';
import { CardGrid, Empty, PageTitle, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BrowseToolbar } from '../-ui/video-parts';

export const Route = createFileRoute('/player/movies/')({
  validateSearch: validateBrowseSearch,
  component: MoviesBrowse,
});

function MoviesBrowse() {
  const sections = useSections();
  const { sort = 'added', genre = '' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/movies/' });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.movies });
  const movies = catalog.data?.movies ?? [];
  const genres = useMemo(() => genresOf(movies), [movies]);

  const cards: RailCard[] = useMemo(
    () =>
      sortTitles(genre ? movies.filter((m) => m.genres.includes(genre)) : movies, sort).map(
        (m) => ({
          key: `m${m.id}`,
          title: m.title,
          subtitle: m.year ? String(m.year) : undefined,
          art: m.has_poster ? posterUrl('movie', m.id) : null,
          href: `/player/movies/${m.id}`,
          shape: 'poster',
        }),
      ),
    [movies, genre, sort],
  );

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;

  return (
    <>
      <PageTitle>Movies</PageTitle>
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
        noun={cards.length === 1 ? 'film' : 'films'}
      />
      {catalog.isLoading ? <div className={s.skeleton} style={{ height: '50vh' }} /> : null}
      {catalog.isError ? (
        <Empty title="Could not load your movies">Try again in a moment.</Empty>
      ) : null}
      {catalog.data && !cards.length ? (
        <Empty title={genre ? `No ${genre.toLowerCase()} films` : 'No movies on disk yet'}>
          {genre
            ? 'Try another genre.'
            : 'Films you add in SoulSync show up here once they are downloaded.'}
        </Empty>
      ) : null}
      {cards.length ? <CardGrid cards={cards} /> : null}
    </>
  );
}
