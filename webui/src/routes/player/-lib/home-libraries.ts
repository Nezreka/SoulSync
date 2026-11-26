/**
 * home and the player's libraries (scrap/HOME_LIBRARIES_CONTRACT.md), the
 * same way the phone and tv apps do it:
 *
 * - one card per title: a title in two libraries (Movies and Movies 4K) is
 *   one card on the rows built from every title. the card opens the copy in
 *   the main library, with a 4K chip when another copy is 4K.
 * - rows about one library: recently added in it, continue in it.
 * - on this day: home videos filmed today in earlier years.
 *
 * pure, so tested without a server.
 */

import type { CatalogMovie, CatalogShow, PlayerLibrary } from '../-player.types';
import type { HomeVideo } from './home-videos';

import { clockDuration } from './format';
import { dayLabel, thumbSrc, yearsAgo } from './home-videos';
import { movieCard, newestFirst, showCard, type RailCard } from './rails';

type Title = CatalogMovie | CatalogShow;
type Kind = 'movie' | 'show';

/** a library row's id: "lib-recent:7", "lib-continue:7" */
export function parseLibRow(id: string): { row: 'recent' | 'continue'; library: number } | null {
  const m = /^lib-(recent|continue):(\d{1,9})$/.exec(id);
  return m ? { row: m[1] as 'recent' | 'continue', library: Number(m[2]) } : null;
}

export const MEMORIES_ROW = 'home-memories';

/** a 4K library: the server says (uhd), or an older one's name does */
export function isUhd(lib: Pick<PlayerLibrary, 'name' | 'kind'> & { uhd?: boolean }): boolean {
  if (lib.kind === 'home') return false;
  return lib.uhd ?? /(^|[^a-z0-9])(4k|uhd|2160p?)([^a-z0-9]|$)/i.test(lib.name);
}

/** the main library of a kind (a title in no library folder is in it) */
function mainOf(libraries: readonly PlayerLibrary[], kind: Kind): number | null {
  return libraries.find((l) => l.kind === kind && l.main)?.id ?? null;
}

/** is this title in that library (one with no library is in the main one) */
export function inLibrary(
  item: { library_id?: number | null },
  library: number,
  main: number | null,
): boolean {
  return item.library_id === library || (item.library_id == null && library === main);
}

interface Copies {
  /** the copy a card goes to */
  pick: Title;
  /** another copy of it is in a 4K library */
  uhd: boolean;
}

/** "movie:12" -> the title's copies, for every title in two libraries or more */
export type CopyIndex = Map<string, Copies & { group: string }>;

const sameTitle = (t: Title) =>
  t.tmdb_id
    ? `tmdb:${t.tmdb_id}`
    : t.year
      ? `name:${t.title.trim().toLowerCase()}|${t.year}`
      : null;

/**
 * which titles are one title in several libraries: the same tmdb id, else the
 * same title and year. the main library's copy is the one a card opens (else
 * one not in a 4K library, else the first).
 */
export function titleCopies(
  movies: readonly CatalogMovie[],
  shows: readonly CatalogShow[],
  libraries: readonly PlayerLibrary[],
): CopyIndex {
  const out: CopyIndex = new Map();
  const uhd = new Set(libraries.filter(isUhd).map((l) => l.id));
  const lists: [Kind, readonly Title[]][] = [
    ['movie', movies],
    ['show', shows],
  ];
  for (const [kind, items] of lists) {
    const main = mainOf(libraries, kind);
    const groups = new Map<string, Title[]>();
    for (const t of items) {
      const key = sameTitle(t);
      if (!key) continue;
      const list = groups.get(key);
      if (list) list.push(t);
      else groups.set(key, [t]);
    }
    for (const [key, list] of groups) {
      if (list.length < 2) continue;
      const is4k = (t: Title) => t.library_id != null && uhd.has(t.library_id);
      const pick =
        list.find((t) => main !== null && inLibrary(t, main, main)) ??
        list.find((t) => !is4k(t)) ??
        list[0];
      const copies = {
        group: `${kind}:${key}`,
        pick,
        uhd: list.some((t) => t !== pick && is4k(t)),
      };
      for (const t of list) out.set(`${kind}:${t.id}`, copies);
    }
  }
  return out;
}

/**
 * a row with each title once: a later copy of one already shown is left out,
 * and a card goes to the main library's copy, with a 4K chip when another
 * copy is 4K. cards that aren't a movie or a show pass as they are.
 */
export function oneCardPerTitle(cards: readonly RailCard[], copies: CopyIndex): RailCard[] {
  if (!copies.size) return cards.slice();
  const seen = new Set<string>();
  const out: RailCard[] = [];
  for (const card of cards) {
    const p = card.preview;
    const found = p ? copies.get(`${p.kind}:${p.id}`) : undefined;
    if (!p || !found) {
      out.push(card);
      continue;
    }
    if (seen.has(found.group)) continue;
    seen.add(found.group);
    const base =
      found.pick.id === p.id
        ? card
        : p.kind === 'movie'
          ? movieCard(found.pick as CatalogMovie)
          : showCard(found.pick as CatalogShow);
    out.push(found.uhd ? { ...base, chip: '4K' } : base);
  }
  return out;
}

/** a list of titles with each one once (the billboard, a pool to pick from) */
export function oneOfEach<T extends Title>(
  items: readonly T[],
  kind: Kind,
  copies: CopyIndex,
): T[] {
  if (!copies.size) return items.slice();
  const seen = new Set<string>();
  const out: T[] = [];
  for (const t of items) {
    const found = copies.get(`${kind}:${t.id}`);
    if (!found) {
      out.push(t);
      continue;
    }
    if (seen.has(found.group)) continue;
    seen.add(found.group);
    out.push(found.pick as T);
  }
  return out;
}

/** where a library's own page is: Movies / TV for the main one */
export function libraryHref(lib: Pick<PlayerLibrary, 'id' | 'kind' | 'main'>): string {
  const base = lib.kind === 'movie' ? '/player/movies' : '/player/tv';
  return lib.main ? `${base}?tab=library` : `${base}?tab=library&library=${lib.id}`;
}

/** "Recently added in Movies 4K": that library's own copies, newest first */
export function libraryRecentCards(
  lib: PlayerLibrary,
  libraries: readonly PlayerLibrary[],
  movies: readonly CatalogMovie[],
  shows: readonly CatalogShow[],
  limit = 20,
): RailCard[] {
  const main = mainOf(libraries, lib.kind as Kind);
  if (lib.kind === 'movie')
    return newestFirst(
      movies.filter((m) => inLibrary(m, lib.id, main)),
      (m) => m.added_at,
    )
      .slice(0, limit)
      .map((m) => ({ ...movieCard(m), key: `lib-${lib.id}:movie:${m.id}` }));
  if (lib.kind === 'show')
    return newestFirst(
      shows.filter((x) => inLibrary(x, lib.id, main)),
      (x) => x.added_at,
    )
      .slice(0, limit)
      .map((x) => ({ ...showCard(x), key: `lib-${lib.id}:show:${x.id}` }));
  return [];
}

/** the title a continue card is about (a movie, or an episode's show) */
function continueTitle(card: RailCard): { kind: Kind; id: number } | null {
  const m = /\/player\/(movies|tv)\/(\d+)/.exec(card.href ?? '');
  if (m) return { kind: m[1] === 'movies' ? 'movie' : 'show', id: Number(m[2]) };
  return card.preview ?? null;
}

/** "Continue in Movies 4K": what you started whose title is in that library */
export function libraryContinueCards(
  watching: readonly RailCard[],
  lib: PlayerLibrary,
  libraries: readonly PlayerLibrary[],
  movies: readonly CatalogMovie[],
  shows: readonly CatalogShow[],
): RailCard[] {
  if (lib.kind !== 'movie' && lib.kind !== 'show') return [];
  const main = mainOf(libraries, lib.kind);
  const items: readonly Title[] = lib.kind === 'movie' ? movies : shows;
  const ids = new Set(items.filter((t) => inLibrary(t, lib.id, main)).map((t) => t.id));
  return watching.filter((card) => {
    const t = continueTitle(card);
    return !!t && t.kind === lib.kind && ids.has(t.id);
  });
}

/** on this day's cards: the still, how long ago, the day and album */
export function memoryCards(videos: readonly HomeVideo[], today = new Date()): RailCard[] {
  return videos
    .filter((v) => !v.missing)
    .map((v) => {
      const pct =
        v.duration_s && v.progress.position_s > 0 && !v.progress.watched
          ? Math.min(1, v.progress.position_s / v.duration_s)
          : undefined;
      return {
        key: `home:${v.id}`,
        title: v.title,
        subtitle: [yearsAgo(v.filmed_at, today), dayLabel(v.filmed_at)].filter(Boolean).join(' · '),
        art: thumbSrc(v),
        href: `/player/home-videos${v.library ? `?library=${v.library}` : ''}`,
        shape: 'wide' as const,
        badge: v.duration_s ? clockDuration(v.duration_s) : undefined,
        progress: pct,
      };
    });
}

/** the viewer's own day, for on this day: "2026-11-26" */
export function localDay(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
