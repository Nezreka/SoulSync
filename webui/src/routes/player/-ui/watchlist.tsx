/**
 * the watchlist: things a profile saved to watch later. one hook holds the
 * list (and flips a title on or off at once, before the server answers);
 * the button sits on a title's page and in its preview.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';
import type { ProgressMeta } from '../-player.types';
import type { CardAction } from './kit';

import { youtubeThumb } from '../-lib/images';
import { movieCard, showCard } from '../-lib/rails';
import {
  addToWatchlist,
  removeFromWatchlist,
  watchlistQuery,
  type WatchlistItem,
  type WatchlistKind,
} from '../-player.api';
import { BookmarkIcon } from './icons';
import { styles as s } from './kit';
import { notifyError } from './notice';

const keyOf = (kind: string, id: string | number) => `${kind}:${id}`;

export function useWatchlist() {
  const queryClient = useQueryClient();
  const list = useQuery(watchlistQuery());
  const keys = useMemo(
    () => new Set((list.data ?? []).map((i) => keyOf(i.item_type, i.item_id))),
    [list.data],
  );
  const flip = useMutation({
    mutationFn: async (v: { kind: WatchlistKind; id: string; meta?: ProgressMeta; on: boolean }) =>
      v.on ? addToWatchlist(v.kind, v.id, v.meta) : removeFromWatchlist(v.kind, v.id),
    onMutate: async (v) => {
      await queryClient.cancelQueries({ queryKey: ['player', 'watchlist'] });
      const before = queryClient.getQueryData<WatchlistItem[]>(['player', 'watchlist']);
      queryClient.setQueryData<WatchlistItem[]>(['player', 'watchlist'], (old = []) =>
        v.on
          ? [
              {
                item_type: v.kind,
                item_id: v.id,
                meta: v.meta ?? null,
                added_at: Date.now() / 1000,
              },
              ...old.filter((i) => keyOf(i.item_type, i.item_id) !== keyOf(v.kind, v.id)),
            ]
          : old.filter((i) => keyOf(i.item_type, i.item_id) !== keyOf(v.kind, v.id)),
      );
      return { before };
    },
    onError: (e, _v, ctx) => {
      queryClient.setQueryData(['player', 'watchlist'], ctx?.before);
      notifyError("Couldn't update your watchlist")(e);
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['player', 'watchlist'] }),
  });
  return {
    items: list.data ?? [],
    loading: list.isLoading,
    has: (kind: WatchlistKind, id: string | number) => keys.has(keyOf(kind, id)),
    toggle: (kind: WatchlistKind, id: string | number, meta?: ProgressMeta) =>
      flip.mutate({ kind, id: String(id), meta, on: !keys.has(keyOf(kind, id)) }),
  };
}

export function WatchlistButton({
  kind,
  id,
  meta,
  compact,
}: {
  kind: WatchlistKind;
  id: string | number;
  meta?: ProgressMeta;
  /** just the icon (a preview's row of buttons) */
  compact?: boolean;
}) {
  const watchlist = useWatchlist();
  const on = watchlist.has(kind, id);
  const label = on ? 'On your watchlist' : 'Add to watchlist';
  return (
    <button
      type="button"
      className={compact ? s.iconRound : s.btn}
      aria-pressed={on}
      aria-label={label}
      title={on ? 'On your watchlist. Click to take it off.' : 'Save it to watch later'}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        watchlist.toggle(kind, id, meta);
      }}
      data-nav
    >
      <BookmarkIcon filled={on} />
      {compact ? null : on ? 'On your watchlist' : 'Watchlist'}
    </button>
  );
}

/** a saved item as a card: movies and shows from the catalog, youtube from what was saved with it */
export function watchlistCard(
  item: WatchlistItem,
  movies: Map<number, Parameters<typeof movieCard>[0]>,
  shows: Map<number, Parameters<typeof showCard>[0]>,
): RailCard | null {
  if (item.item_type === 'movie') {
    const m = movies.get(Number(item.item_id));
    return m ? movieCard(m) : null;
  }
  if (item.item_type === 'show') {
    const x = shows.get(Number(item.item_id));
    return x ? showCard(x) : null;
  }
  const meta = item.meta ?? {};
  return {
    key: `youtube:${item.item_id}`,
    title: meta.title || 'YouTube video',
    subtitle: meta.subtitle,
    art: youtubeThumb(meta.artwork ?? null, 'card', item.item_id),
    href: meta.href,
    shape: 'wide',
  };
}

/** the watchlist entry in a youtube video card's menu */
export function useYoutubeWatchlistActions(): (card: RailCard) => CardAction[] {
  const watchlist = useWatchlist();
  return (card) => {
    if (!card.key.startsWith('yt:')) return [];
    const id = card.key.slice(3);
    const on = watchlist.has('youtube', id);
    return [
      {
        label: on ? 'Remove from watchlist' : 'Add to watchlist',
        onSelect: () =>
          watchlist.toggle('youtube', id, {
            title: card.title,
            subtitle: card.subtitle,
            href: card.href,
          }),
      },
    ];
  };
}
