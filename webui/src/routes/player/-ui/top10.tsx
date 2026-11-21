/**
 * a ranked row: the first ten with big numerals beside their posters, the
 * way a streaming app shows its top 10. any poster row can be shown this way.
 */

import type { RailCard } from '../-lib/rails';

import { Art, HScroll, PlayerLink, styles as s } from './kit';

export function RankedRail({ title, cards }: { title: string; cards: RailCard[] }) {
  const top = cards.slice(0, 10);
  if (top.length < 3) return null;
  return (
    <section className={s.rail} aria-label={title}>
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>{title}</h2>
      </div>
      <HScroll className={s.top10Row}>
        {top.map((card, i) => (
          <PlayerLink
            key={card.key}
            href={card.href ?? '#'}
            className={s.top10Item}
            title={card.subtitle ? `${card.title} · ${card.subtitle}` : card.title}
            data-nav
          >
            <span className={s.top10Num} aria-hidden="true">
              {i + 1}
            </span>
            <span className={s.top10Poster}>
              <Art
                src={card.art ?? null}
                srcSet={card.artSet}
                sizes={card.artSizes}
                alt={card.title}
              />
            </span>
          </PlayerLink>
        ))}
      </HScroll>
    </section>
  );
}
