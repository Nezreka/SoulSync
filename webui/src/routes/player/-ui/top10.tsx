/**
 * a top 10 row: tmdb's chart for today, ranked with big numerals beside the
 * posters. yours open their page; the rest are dimmed and open a page to
 * ask for them.
 */

import { useQuery } from '@tanstack/react-query';

import type { CatalogMovie, CatalogShow } from '../-player.types';

import { discoverListQuery } from '../-player.api';
import { extraCard, ownedByTmdb } from './detail';
import { Art, HScroll, PlayerLink, styles as s } from './kit';

export function Top10Rail({
  title,
  listKey,
  kind,
  owned,
}: {
  title: string;
  listKey: string;
  kind: 'movie' | 'show';
  owned: (CatalogMovie | CatalogShow)[];
}) {
  const list = useQuery(discoverListQuery(listKey, 1));
  const map = ownedByTmdb(owned);
  const cards = (list.data ?? [])
    .filter((t) => t.kind === kind)
    .slice(0, 10)
    .map((t) => extraCard(t, map));
  if (cards.length < 5) return null;
  return (
    <section className={s.rail} aria-label={title}>
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>{title}</h2>
      </div>
      <HScroll className={s.top10Row}>
        {cards.map((card, i) => (
          <PlayerLink
            key={card.key}
            href={card.href ?? '#'}
            className={s.top10Item}
            data-muted={card.muted ? 'true' : undefined}
            title={card.muted ? `${card.title} · not in your library` : card.title}
            data-nav
          >
            <span className={s.top10Num} aria-hidden="true">
              {i + 1}
            </span>
            <span className={s.top10Poster}>
              <Art src={card.art ?? null} alt={card.title} />
            </span>
          </PlayerLink>
        ))}
      </HScroll>
    </section>
  );
}
