import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';

import { movieCard } from '../-lib/rails';
import { decadeOf, genresOf, libraryTitles, sortTitles, validateBrowseSearch } from '../-lib/video';
import { videoCatalogQuery } from '../-player.api';
import { BrowseTabs, MovieCollections, VideoCategories, VideoForYou } from '../-ui/browse';
import { CardGrid, Empty, PageTitle, useViewMode, ViewToggle, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BrowseToolbar, DecadeChip } from '../-ui/video-parts';

export const Route = createFileRoute('/player/movies/')({
  validateSearch: validateBrowseSearch,
  component: MoviesBrowse,
});

function MoviesBrowse() {
  const sections = useSections();
  const [view, setView] = useViewMode('movies');
  const search = Route.useSearch();
  const { sort = 'added', genre = '', decade, library } = search;
  // a filter link (a genre, a decade) lands on the library
  const tab = search.tab ?? (genre || decade ? 'library' : 'foryou');
  const navigate = useNavigate({ from: '/player/movies/' });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.movies });
  // a library's own page shows only its titles; Movies itself, everything not in one
  const here = useMemo(
    () => (catalog.data?.libraries ?? []).find((l) => l.id === library && l.kind === 'movie'),
    [catalog.data, library],
  );
  const movies = useMemo(
    () => libraryTitles(catalog.data?.movies ?? [], catalog.data?.libraries ?? [], library),
    [catalog.data, library],
  );
  const genres = useMemo(() => genresOf(movies), [movies]);

  const cards: RailCard[] = useMemo(
    () =>
      sortTitles(
        movies.filter(
          (m) => (!genre || m.genres.includes(genre)) && (!decade || decadeOf(m.year) === decade),
        ),
        sort,
      ).map(movieCard),
    [movies, genre, decade, sort],
  );

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;

  return (
    <>
      <PageTitle>{here?.name ?? 'Movies'}</PageTitle>
      <BrowseTabs
        value={tab}
        onChange={(next) =>
          void navigate({
            search: next === 'library' ? { tab: next, sort, library } : { tab: next, library },
            replace: true,
          })
        }
      />
      {catalog.isLoading ? <div className={s.skeleton} style={{ height: '50vh' }} /> : null}
      {catalog.isError ? (
        <Empty title="Could not load your movies">Try again in a moment.</Empty>
      ) : null}
      {catalog.data && tab === 'foryou' ? (
        <VideoForYou
          kind="movie"
          movies={movies}
          shows={catalog.data.shows}
          library={here ? { id: here.id, name: here.name } : undefined}
        />
      ) : null}
      {catalog.data && tab === 'collections' ? <MovieCollections movies={movies} /> : null}
      {catalog.data && tab === 'categories' ? (
        <VideoCategories kind="movie" items={movies} library={here?.id} />
      ) : null}
      {catalog.data && tab === 'library' ? (
        <>
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
            extra={
              <>
                {decade ? (
                  <DecadeChip
                    decade={decade}
                    onClear={() =>
                      void navigate({
                        search: (prev) => ({ ...prev, decade: undefined }),
                        replace: true,
                      })
                    }
                  />
                ) : null}
                <ViewToggle value={view} onChange={setView} />
              </>
            }
          />
          {!cards.length ? (
            <Empty title={genre || decade ? 'No films match' : 'No movies on disk yet'}>
              {genre || decade
                ? 'Try another genre or decade.'
                : 'Films you add in SoulSync show up here once they are downloaded.'}
            </Empty>
          ) : (
            <CardGrid cards={cards} view={view} />
          )}
        </>
      ) : null}
    </>
  );
}
