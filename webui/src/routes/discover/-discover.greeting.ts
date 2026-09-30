import type { DiscoverMix } from './-discover.mixes';

/**
 * the top of the page: a greeting, and a grid of the handful of things you
 * actually go back to. spotify's home opens the same way, and it's the
 * fastest path from opening the app to hearing something.
 */

/** 'Good morning' and so on, from the viewer's own clock. */
export function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  if (hour >= 17 && hour < 22) return 'Good evening';
  return 'Up late';
}

/** the mood that suits the hour, for the grid's time-of-day tile. */
export function moodForHour(hour: number): string {
  if (hour >= 6 && hour < 11) return 'mood_feel_good';
  if (hour >= 11 && hour < 17) return 'mood_focus';
  if (hour >= 17 && hour < 22) return 'mood_chill';
  return 'mood_late_night';
}

/** flow spans two cells, so flow + 6 fills two even rows of four */
export const QUICK_TILE_LIMIT = 7;

export type QuickTile = { kind: 'flow' } | { kind: 'mix'; mix: DiscoverMix };

/**
 * flow first (it's the one-tap button), then on repeat, the first two daily
 * mixes, the mood for this hour, repeat rewind, a blend, then more daily
 * mixes. only what exists; never padded with filler.
 */
export function quickTiles(mixes: DiscoverMix[], moods: DiscoverMix[], hour: number): QuickTile[] {
  const byKey = new Map([...mixes, ...moods].map((m) => [m.key, m]));
  const daily = mixes.filter((m) => m.key.startsWith('daily_mix'));
  const wanted: (DiscoverMix | undefined)[] = [
    byKey.get('on_repeat'),
    daily[0],
    daily[1],
    byKey.get(moodForHour(hour)),
    byKey.get('repeat_rewind'),
    mixes.find((m) => m.key.startsWith('blend_')),
    ...daily.slice(2),
  ];
  const tiles: QuickTile[] = [{ kind: 'flow' }];
  const seen = new Set<string>();
  for (const mix of wanted) {
    if (!mix || seen.has(mix.key)) continue;
    seen.add(mix.key);
    tiles.push({ kind: 'mix', mix });
    if (tiles.length >= QUICK_TILE_LIMIT) break;
  }
  return tiles;
}
