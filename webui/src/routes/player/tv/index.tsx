import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';

import { showCard } from '../-lib/rails';
import { decadeOf, genresOf, libraryTitles, sortTitles, validateBrowseSearch } from '../-lib/video';
import { videoCatalogQuery } from '../-player.api';
import { BrowseTabs, ShowCollections, VideoCategories, VideoForYou } from '../-ui/browse';
import { CardGrid, Empty, PageTitle, useViewMode, ViewToggle, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BrowseToolbar, DecadeChip } from '../-ui/video-parts';

export const Route = createFileRoute('/player/tv/')({
  validateSearch: validateBrowseSearch,
  component: TvBrowse,
});

function TvBrowse() {
  const sections = useSections();
  const [view, setView] = useViewMode('tv');
  const search = Route.useSearch();
  const { sort = 'added', genre = '', decade, library } = search;
  const tab = search.tab ?? (genre || decade ? 'library' : 'foryou');
  const navigate = useNavigate({ from: '/player/tv/' });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.tv });
  // a library's own page shows only its titles; TV itself, everything not in one
  const here = useMemo(
    () => (catalog.data?.libraries ?? []).find((l) => l.id === library && l.kind === 'show'),
    [catalog.data, library],
  );
  const shows = useMemo(
    () => libraryTitles(catalog.data?.shows ?? [], catalog.data?.libraries ?? [], library),
    [catalog.data, library],
  );
  const genres = useMemo(() => genresOf(shows), [shows]);
  // new episodes of the shows on this page only
  const recentEpisodes = useMemo(() => {
    const mine = new Set(shows.map((x) => x.id));
    return (catalog.data?.recent_episodes ?? []).filter((e) => mine.has(e.show_id));
  }, [catalog.data, shows]);

  const cards: RailCard[] = useMemo(
    () =>
      sortTitles(
        shows.filter(
          (x) => (!genre || x.genres.includes(genre)) && (!decade || decadeOf(x.year) === decade),
        ),
        sort,
      ).map(showCard),
    [shows, genre, decade, sort],
  );

  if (!sections.tv) return <NotPartOfProfile what="TV shows" />;

  return (
    <>
      <PageTitle>{here?.name ?? 'TV'}</PageTitle>
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
        <Empty title="Could not load your shows">Try again in a moment.</Empty>
      ) : null}
      {catalog.data && tab === 'foryou' ? (
        <VideoForYou
          kind="show"
          movies={catalog.data.movies}
          shows={shows}
          recentEpisodes={recentEpisodes}
          library={here ? { id: here.id, name: here.name } : undefined}
        />
      ) : null}
      {catalog.data && tab === 'collections' ? <ShowCollections shows={shows} /> : null}
      {catalog.data && tab === 'categories' ? (
        <VideoCategories kind="show" items={shows} library={here?.id} />
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
            noun={cards.length === 1 ? 'show' : 'shows'}
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
            <Empty title={genre || decade ? 'No shows match' : 'No episodes on disk yet'}>
              {genre || decade
                ? 'Try another genre or decade.'
                : 'Shows appear here once at least one episode is downloaded.'}
            </Empty>
          ) : (
            <CardGrid cards={cards} view={view} />
          )}
        </>
      ) : null}
    </>
  );
}
