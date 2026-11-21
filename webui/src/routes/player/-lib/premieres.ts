/**
 * premieres: something new airs like it would on real tv. a new episode
 * goes on its show's home channel at 8pm the evening it arrived, a new film
 * at 9pm. a few new episodes of one show air back to back (three a night,
 * the rest the next evenings, in order). worked out from when each arrived
 * and the lineup, so every screen shows the same slot.
 */

import type { CatalogMovie, CatalogRecentEpisode, VideoCatalog } from '../-player.types';
import type { Channel } from './channels';
import type { GuideItem, Premiere } from './schedule';

import { premiereAt } from './schedule';

/** how long after it arrives something still counts as new */
export const NEW_FOR_DAYS = 3;
/** a show's new episodes a night */
export const PER_NIGHT = 3;
const DAY_MS = 86_400_000;

/** home channel, best first: where tv would put it */
const EPISODE_HOME = ['network', 'rule', 'server', 'genre', 'kids', 'type'];
const MOVIE_HOME = ['collection', 'studio', 'rule', 'server', 'genre', 'kids', 'type'];

export function addedMs(at: string | null | undefined): number | null {
  if (!at) return null;
  const t = Date.parse(at.includes('T') ? at : `${at.replace(' ', 'T')}Z`);
  return Number.isFinite(t) ? t : null;
}

interface Arrival {
  item: GuideItem;
  kind: 'episode' | 'movie';
  at: number;
}

function homeOf(arrival: Arrival, channels: readonly Channel[]): Channel | null {
  const order = arrival.kind === 'episode' ? EPISODE_HOME : MOVIE_HOME;
  const fits = (c: Channel) =>
    arrival.kind === 'episode'
      ? c.pool.some((p) => p.href === arrival.item.href)
      : c.pool.some((p) => p.key === arrival.item.key);
  for (const kind of order) {
    const home = channels.find((c) => c.kind === kind && !c.music && !c.ordered && fits(c));
    if (home) return home;
  }
  return null;
}

/** a show's usual episode length, from the episodes the guide already knows */
function usualLength(href: string, channels: readonly Channel[]): number {
  for (const c of channels) {
    const lengths = c.pool.filter((p) => p.href === href).map((p) => p.seconds);
    if (lengths.length) return lengths.sort((a, b) => a - b)[Math.floor(lengths.length / 2)];
  }
  return 0;
}

function episodeArrivals(
  recent: readonly CatalogRecentEpisode[],
  channels: readonly Channel[],
  since: number,
): Arrival[] {
  const byShow = new Map<number, CatalogRecentEpisode[]>();
  for (const e of recent) {
    const t = addedMs(e.added_at);
    if (t === null || t < since) continue;
    const list = byShow.get(e.show_id);
    if (list) list.push(e);
    else byShow.set(e.show_id, [e]);
  }
  const out: Arrival[] = [];
  for (const [showId, list] of byShow) {
    const href = `/player/tv/${showId}`;
    const art = channels.flatMap((c) => c.pool).find((p) => p.href === href)?.art ?? null;
    const usual = usualLength(href, channels);
    // in airing order; the first arrival sets the night, the rest follow PER_NIGHT a night
    list.sort((a, b) => a.season_number - b.season_number || a.episode_number - b.episode_number);
    const first = Math.min(...list.map((e) => addedMs(e.added_at)!));
    list.forEach((e, i) => {
      const seconds = e.runtime_seconds || usual;
      if (!seconds) return;
      out.push({
        kind: 'episode',
        at: premiereAt(first, 'episode') + Math.floor(i / PER_NIGHT) * DAY_MS,
        item: {
          key: `episode:${e.id}`,
          title: e.show_title,
          subtitle: `S${e.season_number} E${e.episode_number}${e.title ? ` · ${e.title}` : ''}`,
          seconds,
          href,
          art,
          kind: 'episode',
        },
      });
    });
  }
  return out;
}

function movieArrivals(
  movies: readonly CatalogMovie[],
  channels: readonly Channel[],
  since: number,
): Arrival[] {
  const out: Arrival[] = [];
  for (const m of movies) {
    const t = addedMs(m.added_at);
    if (t === null || t < since || !m.runtime_seconds) continue;
    const item = channels.flatMap((c) => c.pool).find((p) => p.key === `movie:${m.id}`);
    if (item) out.push({ kind: 'movie', at: premiereAt(t, 'movie'), item });
  }
  return out;
}

/**
 * each channel's premieres. one home channel each, so a new episode isn't on
 * five channels at once.
 */
export function premieresFor(
  catalog: Pick<VideoCatalog, 'movies' | 'recent_episodes'>,
  channels: readonly Channel[],
  now: number,
): Map<string, Premiere[]> {
  const since = now - NEW_FOR_DAYS * DAY_MS;
  const out = new Map<string, Premiere[]>();
  const arrivals = [
    ...episodeArrivals(catalog.recent_episodes ?? [], channels, since),
    ...movieArrivals(catalog.movies, channels, since),
  ];
  for (const a of arrivals) {
    const home = homeOf(a, channels);
    if (!home) continue;
    const list = out.get(home.id);
    const p = { item: a.item, at: a.at };
    if (list) list.push(p);
    else out.set(home.id, [p]);
  }
  return out;
}
