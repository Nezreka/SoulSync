/**
 * the play queue, as plain data and pure functions: what plays now, what's
 * next, shuffle and repeat. the engine holds one and replaces it; nothing
 * here touches a media element, so it's tested on its own.
 *
 * shuffle reorders what's still to come and keeps the order it had, so
 * turning shuffle off puts it back (the way music apps behave).
 */

import type { PlayItem, RepeatMode } from './types';

export interface Queue {
  items: PlayItem[];
  index: number;
  repeat: RepeatMode;
  shuffle: boolean;
  /** the order before shuffle, to restore it */
  unshuffled: PlayItem[] | null;
}

export const EMPTY_QUEUE: Queue = {
  items: [],
  index: -1,
  repeat: 'off',
  shuffle: false,
  unshuffled: null,
};

export const keyOf = (item: Pick<PlayItem, 'type' | 'id'>) => `${item.type}:${item.id}`;

export function current(q: Queue): PlayItem | null {
  return q.items[q.index] ?? null;
}

/** a new queue of these, starting at ``start``; repeat carries over, shuffle doesn't */
export function play(q: Queue, items: PlayItem[], start = 0): Queue {
  if (!items.length) return { ...EMPTY_QUEUE, repeat: q.repeat };
  return {
    items: items.slice(),
    index: Math.min(Math.max(0, start), items.length - 1),
    repeat: q.repeat,
    shuffle: false,
    unshuffled: null,
  };
}

/**
 * where to go after the current item. ``auto`` is the item ending by itself:
 * repeat one replays it then; a person pressing next always moves on.
 * null: nothing left.
 */
export function nextIndex(q: Queue, auto: boolean): number | null {
  if (!q.items.length) return null;
  if (auto && q.repeat === 'one') return q.index;
  if (q.index + 1 < q.items.length) return q.index + 1;
  // past the end: any repeat goes round (repeat one only holds an item that ends by itself)
  return q.repeat === 'off' ? null : 0;
}

export function previousIndex(q: Queue): number | null {
  if (!q.items.length) return null;
  if (q.index > 0) return q.index - 1;
  return q.repeat === 'off' ? null : q.items.length - 1;
}

export function goTo(q: Queue, index: number): Queue {
  return index >= 0 && index < q.items.length ? { ...q, index } : q;
}

/** right after the current one, in this order */
export function playNext(q: Queue, items: PlayItem[]): Queue {
  if (!q.items.length) return play(q, items);
  const at = q.index + 1;
  return {
    ...q,
    items: [...q.items.slice(0, at), ...items, ...q.items.slice(at)],
    unshuffled: q.unshuffled ? [...q.unshuffled, ...items] : null,
  };
}

export function append(q: Queue, items: PlayItem[]): Queue {
  if (!q.items.length) return play(q, items);
  return {
    ...q,
    items: [...q.items, ...items],
    unshuffled: q.unshuffled ? [...q.unshuffled, ...items] : null,
  };
}

export function remove(q: Queue, index: number): Queue {
  if (index < 0 || index >= q.items.length || index === q.index) return q;
  const gone = q.items[index];
  return {
    ...q,
    items: q.items.filter((_, i) => i !== index),
    index: index < q.index ? q.index - 1 : q.index,
    unshuffled: q.unshuffled ? q.unshuffled.filter((x) => x !== gone) : null,
  };
}

/** drag a row: from one place to another; the playing item stays the playing item */
export function move(q: Queue, from: number, to: number): Queue {
  if (from === to || from < 0 || to < 0 || from >= q.items.length || to >= q.items.length) return q;
  const items = q.items.slice();
  const [moved] = items.splice(from, 1);
  items.splice(to, 0, moved);
  const playing = q.items[q.index];
  return { ...q, items, index: items.indexOf(playing) };
}

export function setRepeat(q: Queue, repeat: RepeatMode): Queue {
  return { ...q, repeat };
}

/** the next repeat mode, the way the button cycles */
export const REPEAT_CYCLE: Record<RepeatMode, RepeatMode> = { off: 'all', all: 'one', one: 'off' };

/** a seeded random (mulberry32), so a shuffle can be tested */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function setShuffle(q: Queue, on: boolean, seed = Date.now()): Queue {
  if (on === q.shuffle || !q.items.length) return { ...q, shuffle: on };
  const playing = q.items[q.index];
  if (on) {
    const rest = q.items.filter((_, i) => i !== q.index);
    const random = rng(seed);
    for (let i = rest.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [rest[i], rest[j]] = [rest[j], rest[i]];
    }
    return { ...q, items: [playing, ...rest], index: 0, shuffle: true, unshuffled: q.items };
  }
  const items = q.unshuffled ?? q.items;
  return {
    ...q,
    items,
    index: Math.max(0, items.indexOf(playing)),
    shuffle: false,
    unshuffled: null,
  };
}

/** what comes after the current item, for an up-next list */
export function upcoming(q: Queue): { item: PlayItem; index: number }[] {
  return q.items.map((item, index) => ({ item, index })).filter((x) => x.index > q.index);
}
