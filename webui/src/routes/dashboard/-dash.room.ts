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
    const data = (await response.json()) as { faces?: unknown; sleeves?: unknown };
    publishTilePictures('faces', data.faces);
    publishTilePictures('sleeves', data.sleeves);
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
  emit();
}
