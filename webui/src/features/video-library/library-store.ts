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

/* ── what the page's roots share besides the files ───────────────────────────
 * the island under the hero, the episode list and each dialog are separate
 * React roots or far apart in one, so the small things they agree on live
 * here: the show's library data, the ticked episodes, the last change that
 * can be put back, and what the page's own menu asked the island to open. */

type Plain = () => void;

function channel<T>(first: T) {
  let value = first;
  const subs = new Set<Plain>();
  return {
    get: () => value,
    set(next: T) {
      value = next;
      for (const sub of Array.from(subs)) sub();
    },
    subscribe(sub: Plain) {
      subs.add(sub);
      return () => {
        subs.delete(sub);
      };
    },
  };
}

/** the show the page is on, with its files, as the island last read it */
export const showData = channel<ShowLibrary | null>(null);

/** the episodes ticked in the list, by episode id */
export const picked = channel<readonly number[]>([]);

export function togglePicked(episodeId: number, on: boolean): void {
  const now = picked.get();
  if (on === now.includes(episodeId)) return;
  picked.set(on ? [...now, episodeId] : now.filter((id) => id !== episodeId));
}

/** a change that was just made and can be put back */
export interface Undoable {
  batch: string;
  text: string;
  kind: TitleKind;
  id: number;
}

export const lastChange = channel<Undoable | null>(null);

/** what the page's More menu can ask the island to open */
export type PageCommand = 'history' | 'move' | 'rename';

const commands = new Set<(what: PageCommand) => void>();

export function sendCommand(what: PageCommand): void {
  for (const listener of Array.from(commands)) listener(what);
}

export function onCommand(listener: (what: PageCommand) => void): () => void {
  commands.add(listener);
  return () => {
    commands.delete(listener);
  };
}
