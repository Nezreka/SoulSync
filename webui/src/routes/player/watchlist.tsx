import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from './-lib/rails';
import type { WatchlistItem, WatchlistKind } from './-player.api';

import { sortKey } from './-lib/video';
import { videoCatalogQuery } from './-player.api';
import { CardGrid, Empty, PageTitle, Tabs, useViewMode, ViewToggle, styles as s } from './-ui/kit';
import { NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';
import { useWatchlist, watchlistCard } from './-ui/watchlist';

type Kind = 'all' | WatchlistKind;
type Sort = 'added' | 'title';

export const Route = createFileRoute('/player/watchlist')({
  validateSearch: (search: Record<string, unknown>): { kind?: Kind; sort?: Sort } => ({
    ...(search.kind === 'movie' || search.kind === 'show' || search.kind === 'youtube'
      ? { kind: search.kind }
      : {}),
    ...(search.sort === 'title' ? { sort: 'title' as const } : {}),
  }),
  component: WatchlistPage,
});

/**
 * everything this profile saved to watch later, newest first or by title.
 * a title the profile can't see right now (it left the library, or it's
 * over a kids cap) stays on the list but isn't drawn.
 */
function WatchlistPage() {
  const sections = useSections();
  const { kind = 'all', sort = 'added' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/watchlist' });
  const [view, setView] = useViewMode('watchlist');
  const watchlist = useWatchlist();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });

  const { cards, counts, hidden } = useMemo(() => {
    const movies = new Map(
      (sections.movies ? (catalog.data?.movies ?? []) : []).map((m) => [m.id, m]),
    );
    const shows = new Map((sections.tv ? (catalog.data?.shows ?? []) : []).map((x) => [x.id, x]));
    const all = watchlist.items
      .map((item) => ({ item, card: catalog.data ? watchlistCard(item, movies, shows) : null }))
      .filter((x): x is { item: WatchlistItem; card: RailCard } => !!x.card);
    const counts = { all: all.length, movie: 0, show: 0, youtube: 0 };
    for (const x of all) counts[x.item.item_type] += 1;
    const picked = all.filter((x) => kind === 'all' || x.item.item_type === kind);
    if (sort === 'title')
      picked.sort((a, b) => sortKey(a.card.title).localeCompare(sortKey(b.card.title)));
    return {
      cards: picked.map((x) => ({ ...x.card, key: `${x.item.item_type}:${x.item.item_id}` })),
      counts,
      hidden: catalog.data ? watchlist.items.length - all.length : 0,
    };
  }, [watchlist.items, catalog.data, kind, sort, sections.movies, sections.tv]);

  if (!sections.video) return <NotPartOfProfile what="Movies and TV" />;
  const loading = watchlist.loading || catalog.isLoading;
  const tabs: { value: Kind; label: string }[] = [
    { value: 'all', label: `All ${counts.all}` },
    ...(counts.movie ? [{ value: 'movie' as const, label: `Movies ${counts.movie}` }] : []),
    ...(counts.show ? [{ value: 'show' as const, label: `TV ${counts.show}` }] : []),
    ...(counts.youtube ? [{ value: 'youtube' as const, label: `YouTube ${counts.youtube}` }] : []),
  ];

  return (
    <>
      <PageTitle>Watchlist</PageTitle>
      {counts.all ? (
        <div className={s.toolbar}>
          <Tabs
            label="Show"
            value={kind}
            options={tabs}
            onChange={(next) =>
              void navigate({
                search: (prev) => ({ ...prev, kind: next === 'all' ? undefined : next }),
                replace: true,
              })
            }
          />
          <div className={s.fieldRow}>
            <Tabs
              label="Sort"
              value={sort}
              options={[
                { value: 'added', label: 'Recently added' },
                { value: 'title', label: 'A to Z' },
              ]}
              onChange={(next) =>
                void navigate({
                  search: (prev) => ({ ...prev, sort: next === 'added' ? undefined : next }),
                  replace: true,
                })
              }
            />
            <ViewToggle value={view} onChange={setView} />
          </div>
        </div>
      ) : null}
      {loading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {!loading && !counts.all ? (
        <Empty title="Nothing saved yet">
          Press Watchlist on a movie or show, or + on its preview, and it waits here for you on
          every screen you use.
        </Empty>
      ) : null}
      {cards.length ? (
        <CardGrid
          cards={cards}
          view={view}
          shape={kind === 'youtube' ? 'wide' : 'poster'}
          actionsFor={(card) => {
            const [type, ...rest] = card.key.split(':');
            return [
              {
                label: 'Remove from watchlist',
                onSelect: () => watchlist.toggle(type as WatchlistKind, rest.join(':')),
              },
            ];
          }}
        />
      ) : null}
      {hidden > 0 ? (
        <p className={s.notYet}>
          {hidden} saved {hidden === 1 ? 'title isn’t' : 'titles aren’t'} in your library right now.
        </p>
      ) : null}
    </>
  );
}
