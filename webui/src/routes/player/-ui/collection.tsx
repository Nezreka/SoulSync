/**
 * one page shape for every "everything you own from X" page: a studio, a
 * network, a music genre, a label. the hero takes the best-looking title's
 * backdrop, the grid is the library's own titles.
 */

import type { ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';

import { CardGrid, Empty, Hero, useViewMode, ViewToggle, styles as s } from './kit';

export function CollectionPage({
  kicker,
  title,
  meta,
  backdrop,
  cards,
  shape = 'poster',
  loading,
  empty,
  above,
}: {
  kicker: string;
  title: string;
  meta?: ReactNode;
  backdrop?: string | null;
  cards: RailCard[];
  shape?: RailCard['shape'];
  loading?: boolean;
  empty: string;
  /** rails between the hero and the grid (a genre's artists) */
  above?: ReactNode;
}) {
  const [view, setView] = useViewMode(`collection:${kicker}`);
  return (
    <>
      <Hero backdrop={backdrop} kicker={kicker} title={title} meta={meta} />
      {above}
      <div className={s.rail}>
        {cards.length ? (
          <div className={s.toolbar}>
            <span />
            <ViewToggle value={view} onChange={setView} />
          </div>
        ) : null}
        {loading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
        {!loading && !cards.length ? <Empty title="Nothing here yet">{empty}</Empty> : null}
        {cards.length ? <CardGrid cards={cards} shape={shape} view={view} /> : null}
      </div>
    </>
  );
}
