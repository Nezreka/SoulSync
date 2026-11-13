/**
 * Small shared state for the dashboard's redrawn page: pictures the page
 * shows that no component fetches on its own.
 *
 *  - the two count tiles show a few of what they count (the newest watched
 *    artists' faces, the sleeves of the newest wishes), from one small
 *    request the tiles make when they mount and whenever a count changes.
 *  - the hero is lit by the cover of the last song played, which the
 *    Recently Played shelf already holds.
 *
 * A store and not props because the publisher and the reader sit in
 * different branches of the page, exactly as with the library card's stats.
 */

import { useSyncExternalStore } from 'react';

export interface TilePicture {
  name: string;
  image_url: string;
}

type Kind = 'faces' | 'sleeves';

const pictures: Record<Kind, TilePicture[]> = { faces: [], sleeves: [] };

/** What each count is made of, and the newest thing in it. */
export interface TileFacts {
  watchlist: {
    /** watched artists with a release in the last 30 days */
    releasedMonth: number;
    /** releases found for them in the last 30 days */
    releasesMonth: number;
    /** artists added to the watchlist in the last 7 days */
    addedWeek: number;
    newest: { album: string; artist: string; date: string; image_url: string | null } | null;
  };
  wishlist: {
    albums: number;
    artists: number;
    kinds: { album: number; single: number; ep: number };
    newest: { name: string; artist: string; image_url: string | null } | null;
  };
}

const NO_FACTS: TileFacts = {
  watchlist: { releasedMonth: 0, releasesMonth: 0, addedWeek: 0, newest: null },
  wishlist: { albums: 0, artists: 0, kinds: { album: 0, single: 0, ep: 0 }, newest: null },
};
let facts: TileFacts = NO_FACTS;

const count = (value: unknown): number => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
};
const text = (value: unknown): string => (typeof value === 'string' ? value : '');
const picture = (value: unknown): string | null =>
  typeof value === 'string' && /^(https?:\/\/|\/api\/)/.test(value) ? value : null;

/** The server's facts, read defensively: anything missing is a zero. */
export function readTileFacts(value: unknown): TileFacts {
  const raw = (value ?? {}) as {
    watchlist?: Record<string, unknown>;
    wishlist?: Record<string, unknown>;
  };
  const watch = raw.watchlist ?? {};
  const wish = raw.wishlist ?? {};
  const kinds = (wish.kinds ?? {}) as Record<string, unknown>;
  const newestWatch = watch.newest as Record<string, unknown> | null | undefined;
  const newestWish = wish.newest as Record<string, unknown> | null | undefined;
  return {
    watchlist: {
      releasedMonth: count(watch.released_month),
      releasesMonth: count(watch.releases_month),
      addedWeek: count(watch.added_week),
      newest:
        newestWatch && text(newestWatch.album)
          ? {
              album: text(newestWatch.album),
              artist: text(newestWatch.artist),
              date: text(newestWatch.date),
              image_url: picture(newestWatch.image_url),
            }
          : null,
    },
    wishlist: {
      albums: count(wish.albums),
      artists: count(wish.artists),
      kinds: { album: count(kinds.album), single: count(kinds.single), ep: count(kinds.ep) },
      newest:
        newestWish && text(newestWish.name)
          ? {
              name: text(newestWish.name),
              artist: text(newestWish.artist),
              image_url: picture(newestWish.image_url),
            }
          : null,
    },
  };
}

export function publishTileFacts(value: unknown): void {
  const next = readTileFacts(value);
  if (JSON.stringify(next) === JSON.stringify(facts)) return;
  facts = next;
  emit();
}

export function useTileFacts(): TileFacts {
  return useSyncExternalStore(
    subscribe,
    () => facts,
    () => facts,
  );
}
let heroArt: string | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Only what can be drawn: a name and a link a browser can load. */
export function readTilePictures(value: unknown): TilePicture[] {
  if (!Array.isArray(value)) return [];
  const out: TilePicture[] = [];
  for (const entry of value) {
    const url = (entry as { image_url?: unknown } | null)?.image_url;
    if (typeof url !== 'string' || !/^(https?:\/\/|\/api\/)/.test(url)) continue;
    out.push({ name: String((entry as { name?: unknown }).name ?? ''), image_url: url });
    if (out.length >= 5) break;
  }
  return out;
}

export function publishTilePictures(kind: Kind, value: unknown): void {
  const next = readTilePictures(value);
  const same =
    next.length === pictures[kind].length &&
    next.every((p, i) => p.image_url === pictures[kind][i].image_url);
  if (same) return;
  pictures[kind] = next;
  emit();
}

/**
 * Ask for both tiles' pictures. One small request of its own: the watchlist
 * count normally arrives over the socket, so nothing can ride along with it.
 * A failure leaves whatever is showing.
 */
export async function loadTilePictures(): Promise<void> {
  try {
    const response = await fetch('/api/dashboard/tile-pictures');
    if (!response.ok) return;
    const data = (await response.json()) as {
      faces?: unknown;
      sleeves?: unknown;
      facts?: unknown;
    };
    publishTilePictures('faces', data.faces);
    publishTilePictures('sleeves', data.sleeves);
    publishTileFacts(data.facts);
  } catch {
    // the tiles are whole without pictures
  }
}

export function useTilePictures(kind: Kind): TilePicture[] {
  return useSyncExternalStore(
    subscribe,
    () => pictures[kind],
    () => pictures[kind],
  );
}

export function publishHeroArt(url: string | null | undefined): void {
  const next = url || null;
  if (next === heroArt) return;
  heroArt = next;
  emit();
}

export function useHeroArt(): string | null {
  return useSyncExternalStore(
    subscribe,
    () => heroArt,
    () => heroArt,
  );
}

/** Tests start clean. */
export function resetDashRoom(): void {
  pictures.faces = [];
  pictures.sleeves = [];
  heroArt = null;
  facts = NO_FACTS;
  emit();
}
