/**
 * premieres: something new airs like it would on real tv. a new episode
 * goes on its show's home channel at 8pm the evening it arrived, a new film
 * at 9pm.
 *
 * nothing here is worked out on this device. the server gives each premiere
 * its slot (once, and keeps it), the item to air, and the channels it could
 * go on, best first (core/player/premieres.py). this only puts each one on
 * the first of those channels this guide has, so every screen shows the same
 * thing in the same slot.
 */

import type { Channel } from './channels';
import type { GuideItem, Premiere } from './schedule';

import { posterUrl } from './rails';

/** a premiere as the server sends it (GET /api/player/guide/premieres) */
export interface ServerPremiere {
  key: string;
  kind: 'episode' | 'movie';
  id: number;
  show_id: number | null;
  title: string;
  subtitle: string | null;
  seconds: number;
  /** when it airs, epoch ms */
  at: number;
  /** channel ids it could air on, best first */
  homes: string[];
  has_poster: boolean;
}

export function premiereItem(p: ServerPremiere): GuideItem {
  const show = p.kind === 'episode';
  const owner = show ? (p.show_id ?? p.id) : p.id;
  return {
    key: p.key,
    title: p.title,
    subtitle: p.subtitle ?? undefined,
    seconds: p.seconds,
    href: show ? `/player/tv/${owner}` : `/player/movies/${owner}`,
    art: p.has_poster ? posterUrl(show ? 'show' : 'movie', owner) : null,
    kind: show ? 'episode' : 'movie',
  };
}

/** a channel a premiere can air on: video, and not a marathon that runs in order */
const canHost = (c: Channel) => !c.music && !c.ordered;

/**
 * each channel's premieres, by channel id. one home channel each (the first
 * the server named that this guide has), so a new episode isn't on five
 * channels at once. one no channel here can host airs nowhere.
 */
export function premieresFor(
  list: readonly ServerPremiere[] | null | undefined,
  channels: readonly Channel[],
): Map<string, Premiere[]> {
  const out = new Map<string, Premiere[]>();
  if (!list?.length) return out;
  const hosts = new Set(channels.filter(canHost).map((c) => c.id));
  for (const p of list) {
    const home = p.homes.find((id) => hosts.has(id));
    if (!home || !(p.seconds > 0) || !Number.isFinite(p.at)) continue;
    const mine = out.get(home);
    const premiere = { item: premiereItem(p), at: p.at };
    if (mine) mine.push(premiere);
    else out.set(home, [premiere]);
  }
  return out;
}

/** the part of a channel's schedule options that carries its premieres */
export function premiereOptions(mine: readonly Premiere[] | undefined) {
  if (!mine?.length) return {};
  return { premieres: mine, premiereKey: mine.map((p) => `${p.item.key}@${p.at}`).join(',') };
}

/** a premiere still to come (or on now), with the channel it airs on */
export interface UpcomingPremiere {
  premiere: ServerPremiere;
  channel: Channel;
}

/**
 * tonight's premieres for the home row: the ones on now or still to air in
 * the next day, soonest first, each with its home channel. homed among
 * ``channels`` exactly as the guide homes them.
 */
export function upcomingPremieres(
  list: readonly ServerPremiere[] | null | undefined,
  channels: readonly Channel[],
  now: number,
  horizonMs = 24 * 3_600_000,
): UpcomingPremiere[] {
  if (!list?.length) return [];
  const hosts = new Map(channels.filter(canHost).map((c) => [c.id, c]));
  const out: UpcomingPremiere[] = [];
  for (const p of list) {
    const home = p.homes.find((id) => hosts.has(id));
    if (!home || !(p.seconds > 0)) continue;
    if (p.at + p.seconds * 1000 <= now || p.at > now + horizonMs) continue;
    out.push({ premiere: p, channel: hosts.get(home)! });
  }
  return out.sort((a, b) => a.premiere.at - b.premiere.at);
}

/** "Tonight 8:00 PM", "On now", "Tomorrow 9:00 PM" */
export function premiereWhen(at: number, now: number): string {
  if (at <= now) return 'On now';
  const d = new Date(at);
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const sameDay = new Date(now).toDateString() === d.toDateString();
  if (sameDay) return `${d.getHours() >= 17 ? 'Tonight' : 'Today'} ${time}`;
  return `Tomorrow ${time}`;
}
