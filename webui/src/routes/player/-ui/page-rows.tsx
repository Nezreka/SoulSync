/**
 * the rows of a browse page, from the server's contract
 * (core/player/page_layout.py): which rows, in what order, in what style.
 * the page builds each row's cards; this reads the layout, opens the
 * editor and saves it, for this screen or for all of them.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';

import { deviceId } from '../-lib/device';
import { type HomeLayout, type LayoutRow, type RowStyle } from '../-lib/home-layout';
import { backdropSet, WIDE_SIZES } from '../-lib/images';
import { pageLayoutQuery, resetPageLayout, savePageLayout } from '../-player.api';
import { HomeEditor } from './home-editor';
import { CardRail, styles as s, type CardAction, type CardOpen } from './kit';
import { RankedRail } from './top10';

/** a poster card as a wide still: the title's backdrop when it has one */
export function toWide(card: RailCard): RailCard {
  if (card.shape === 'wide' || !card.preview) return card;
  const set = backdropSet(card.preview.kind, card.preview.id);
  return { ...card, shape: 'wide', art: set.src, artSet: set.srcSet, artSizes: WIDE_SIZES };
}

/** one row of cards in the style its layout row asks for */
export function StyledRow({
  style,
  title,
  cards,
  more,
  round,
  actionsFor,
  onOpen,
}: {
  style: RowStyle;
  title: string;
  cards: RailCard[];
  more?: { to: string; label?: string };
  round?: boolean;
  actionsFor?: (card: RailCard) => CardAction[];
  /** a card that does something here (plays a clip) instead of opening a page */
  onOpen?: CardOpen;
}) {
  if (!cards.length) return null;
  if (style === 'ranked') return <RankedRail title={title} cards={cards.slice(0, 10)} />;
  const shown = style === 'wide' ? cards.map(toWide) : cards;
  const rail = (
    <CardRail
      title={title}
      cards={shown}
      more={more}
      round={round}
      actionsFor={actionsFor}
      onOpen={onOpen}
    />
  );
  return style === 'large' ? <div className={s.rowLarge}>{rail}</div> : rail;
}

/**
 * a page's rows and its editor. `rows` is the layout's rows that are on, in
 * order; while the layout loads (or can't be read) it is `fallback`, the
 * page's default rows, so the page never waits on it.
 */
export function usePageRows({
  page,
  name,
  fallback,
  movieGenres,
  showGenres,
}: {
  /** the page key: movies, tv, music, youtube, or "<page>:lib:<id>" */
  page: string;
  /** what the editor calls the page: "Movies" */
  name: string;
  fallback: readonly string[];
  movieGenres?: string[];
  showGenres?: string[];
}): { rows: LayoutRow[]; editor: ReactNode; customize: ReactNode } {
  const queryClient = useQueryClient();
  const device = useMemo(() => deviceId(), []);
  const saved = useQuery(pageLayoutQuery(page, device));
  const [draft, setDraft] = useState<HomeLayout | null>(null);
  const [editing, setEditing] = useState(false);
  const layout = useMemo<HomeLayout>(
    () =>
      draft ?? {
        billboard: true,
        rows:
          saved.data?.layout?.rows ?? fallback.map((id) => ({ id, style: 'standard', on: true })),
      },
    // fallback is a constant of the page
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [draft, saved.data],
  );
  const close = useCallback(() => {
    setEditing(false);
    setDraft(null);
  }, []);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['player', 'page-layout', page] });

  const editor = editing ? (
    <HomeEditor
      title={`Customize ${name}`}
      noBillboard
      layout={layout}
      catalog={saved.data?.catalog}
      scope={saved.data?.scope ?? null}
      movieGenres={movieGenres ?? []}
      showGenres={showGenres ?? []}
      onChange={setDraft}
      onSave={async (where) => {
        await savePageLayout(
          page,
          { rows: layout.rows },
          where === 'all' ? '*' : device,
          where === 'all' ? device : undefined,
        );
        await refresh();
      }}
      onReset={async (what) => {
        await resetPageLayout(page, what === 'device' ? device : undefined);
        await refresh();
        close();
      }}
      onClose={close}
    />
  ) : null;

  const customize = (
    <div className={s.customizeRow}>
      <button type="button" className={s.btn} onClick={() => setEditing(true)} data-nav>
        Customize this page
      </button>
    </div>
  );

  return { rows: layout.rows.filter((r) => r.on), editor, customize };
}
