/**
 * a studio's or a network's page: what you have of theirs up top, then
 * everything they made (tmdb's discover, newest, most popular or top
 * rated), yours marked, the rest one click from a request.
 */

import { useInfiniteQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import type { CatalogMovie, CatalogShow } from '../-player.types';

import { plural } from '../-lib/format';
import { remoteImage } from '../-lib/images';
import { backdropUrl, movieCard, showCard } from '../-lib/rails';
import { makerQuery, type MakerSort } from '../-player.api';
import { extraCard, ownedByTmdb } from './detail';
import { CardGrid, CardRail, Empty, Hero, Tabs, useViewMode, ViewToggle, styles as s } from './kit';

const SORTS: { value: MakerSort; label: string }[] = [
  { value: 'popular', label: 'Most popular' },
  { value: 'newest', label: 'Newest' },
  { value: 'rated', label: 'Top rated' },
];

export function MakerPage({
  kind,
  name,
  mine,
  all,
}: {
  kind: 'studio' | 'network';
  name: string;
  /** your titles from it, from the catalog */
  mine: (CatalogMovie | CatalogShow)[];
  /** the whole catalog, to mark what you own */
  all: (CatalogMovie | CatalogShow)[];
}) {
  const [sort, setSort] = useState<MakerSort>('popular');
  const [view, setView] = useViewMode(`maker:${kind}`);
  const titles = useInfiniteQuery(makerQuery(kind, name, sort));
  const owned = useMemo(() => ownedByTmdb(all), [all]);
  const first = titles.data?.pages[0];
  const card = first?.card;
  const everything = useMemo(() => {
    const seen = new Set<number>();
    return (titles.data?.pages ?? [])
      .flatMap((p) => p.titles)
      .filter((t) => !seen.has(t.tmdb_id) && seen.add(t.tmdb_id))
      .map((t) => extraCard(t, owned));
  }, [titles.data, owned]);
  const noun = kind === 'studio' ? 'film' : 'show';
  const lead = [...mine]
    .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
    .find((x) => x.has_backdrop);
  const yours = mine
    .slice()
    .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
    .map((x) => ('episode_count' in x ? showCard(x) : movieCard(x)));

  return (
    <>
      <Hero
        backdrop={lead ? backdropUrl(kind === 'studio' ? 'movie' : 'show', lead.id) : null}
        kicker={kind === 'studio' ? 'Studio' : 'Network'}
        title={name}
        logo={card?.logo ? remoteImage(card.logo, 'hero') : null}
        meta={
          <>
            <span>{plural(mine.length, noun)} in your library</span>
            {card?.headquarters ? <span>{card.headquarters}</span> : null}
          </>
        }
        overview={card?.description ?? null}
      />

      {yours.length ? <CardRail title="In your library" cards={yours} /> : null}

      <section className={s.rail} aria-label={`Everything from ${name}`}>
        <div className={s.toolbar}>
          <h2 className={s.sectionTitle}>Everything from {name}</h2>
          <div className={s.fieldRow}>
            <Tabs label="Sort" value={sort} options={SORTS} onChange={setSort} />
            <ViewToggle value={view} onChange={setView} />
          </div>
        </div>
        {titles.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
        {titles.isError || (first && first.found === false) ? (
          <Empty title={`Couldn't find ${name} on TMDB`}>
            What you have from them is above. The rest needs TMDB to know the name.
          </Empty>
        ) : null}
        {everything.length ? <CardGrid cards={everything} view={view} /> : null}
        {titles.hasNextPage ? (
          <div className={s.moreRow}>
            <button
              type="button"
              className={s.btn}
              disabled={titles.isFetchingNextPage}
              onClick={() => void titles.fetchNextPage()}
              data-nav
            >
              {titles.isFetchingNextPage ? 'Loading…' : 'Show more'}
            </button>
          </div>
        ) : null}
      </section>
    </>
  );
}
