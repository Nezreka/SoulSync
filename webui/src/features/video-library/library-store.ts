/**
 * one show's library data, shared by everything on its page that reads it:
 * the island under the hero and the file cards inside each opened episode.
 * they are separate React roots (the episode list around them is the page's
 * own), so a change made in one has to reach the others: `invalidate` drops
 * what is cached and tells every listener to read again.
 */

import type { ShowLibrary, TitleKind } from './types';

import { fetchTitleLibrary } from './api';

type Listener = (kind: TitleKind, id: number) => void;

const listeners = new Set<Listener>();
const seasons = new Map<string, Promise<ShowLibrary>>();

/** a show's payload with the disk checks for one season; one request per season */
export function seasonLibrary(showId: number, season: number): Promise<ShowLibrary> {
  const key = `${showId}:${season}`;
  let hit = seasons.get(key);
  if (!hit) {
    hit = fetchTitleLibrary('show', showId, season) as Promise<ShowLibrary>;
    seasons.set(key, hit);
    // a failed read is not an answer worth keeping
    hit.catch(() => seasons.delete(key));
  }
  return hit;
}

/** something about the title changed: forget it and tell everyone reading it */
export function invalidate(kind: TitleKind, id: number): void {
  for (const key of Array.from(seasons.keys())) {
    if (key.startsWith(`${id}:`)) seasons.delete(key);
  }
  for (const listener of Array.from(listeners)) listener(kind, id);
}

export function onInvalidate(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
