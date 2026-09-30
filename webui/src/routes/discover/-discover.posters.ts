import type { InboxItem } from './-discover.inbox';
import type { RecentAlbum } from './-discover.recent-releases';

/**
 * the poster row: loud, flat colour cards like spotify's campaign tiles. one
 * for a show coming up, one for a release, one for who you played most this
 * week. these are the pure parts: the colours and what goes on each poster.
 */

type Rgb = [number, number, number];

function parseRgb(value: string): Rgb | null {
  const parts = value.split(',').map((p) => Number(p.trim()));
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return null;
  return parts.map((n) => Math.max(0, Math.min(255, Math.round(n)))) as Rgb;
}

function toHsl([r, g, b]: Rgb): [number, number, number] {
  const [rr, gg, bb] = [r / 255, g / 255, b / 255];
  const max = Math.max(rr, gg, bb);
  const min = Math.min(rr, gg, bb);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rr) h = (gg - bb) / d + (gg < bb ? 6 : 0);
  else if (max === gg) h = (bb - rr) / d + 2;
  else h = (rr - gg) / d + 4;
  return [h * 60, s, l];
}

function fromHsl(h: number, s: number, l: number): Rgb {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    h < 60
      ? [c, x, 0]
      : h < 120
        ? [x, c, 0]
        : h < 180
          ? [0, c, x]
          : h < 240
            ? [0, x, c]
            : h < 300
              ? [x, 0, c]
              : [c, 0, x];
  return [r, g, b].map((n) => Math.round((n + m) * 255)) as Rgb;
}

/**
 * an artwork's colour pushed to poster loudness: the same hue, saturated and
 * mid-light. a grey cover has no hue to push, so it gets the fallback.
 */
export function poppy(rgb: string | null | undefined, fallback: string): string {
  const parsed = rgb ? parseRgb(rgb) : null;
  if (!parsed) return fallback;
  const [h, s] = toHsl(parsed);
  if (s < 0.12) return fallback;
  return fromHsl(h, Math.max(s, 0.78), 0.56).join(', ');
}

/** black or white type, whichever reads on this colour. */
export function inkFor(rgb: string): 'dark' | 'light' {
  const parsed = parseRgb(rgb);
  if (!parsed) return 'light';
  const lin = parsed.map((n) => {
    const c = n / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  // contrast against black beats contrast against white above ~0.18
  return lum > 0.18 ? 'dark' : 'light';
}

/** the poster palette for things with no artwork (a concert) */
export const POSTER_PALETTE = [
  '255, 79, 154', // hot pink
  '198, 244, 50', // lime
  '255, 122, 26', // tangerine
  '41, 211, 255', // cyan
  '155, 92, 255', // violet
  '255, 210, 63', // sun
];

/** the same name always gets the same colour. */
export function paletteFor(seed: string): string {
  let h = 0;
  for (const ch of seed.toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return POSTER_PALETTE[h % POSTER_PALETTE.length];
}

export interface PosterDay {
  month: string;
  day: string;
  weekday: string;
}

/** "2027-02-19" as FEB / 19 / FRI, for a gig poster. */
export function posterDay(value: string | undefined): PosterDay | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}/.test(value)) return null;
  const d = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  return {
    month: d.toLocaleDateString('en-US', { month: 'short' }).toUpperCase(),
    day: String(d.getDate()),
    weekday: d.toLocaleDateString('en-US', { weekday: 'short' }).toUpperCase(),
  };
}

export interface ConcertPick {
  item: InboxItem;
  /** other upcoming shows in the inbox */
  more: number;
}

/** the soonest show that hasn't happened, and how many more there are. */
export function pickConcert(
  items: InboxItem[] | undefined,
  today: Date = new Date(),
): ConcertPick | null {
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const shows = (items ?? [])
    .filter((i) => i.kind === 'concert' && i.artist_name && i.state !== 'dismissed')
    .filter((i) => {
      const day = posterDay(i.item_date);
      return (
        day !== null && new Date(`${i.item_date!.slice(0, 10)}T00:00:00`).getTime() >= midnight
      );
    })
    .sort((a, b) => (a.item_date ?? '').localeCompare(b.item_date ?? ''));
  return shows.length ? { item: shows[0], more: shows.length - 1 } : null;
}

/**
 * a release for the album poster: one with a cover you don't own yet, and not
 * the one already in the spotlight.
 */
export function pickPosterAlbum(
  albums: RecentAlbum[],
  skipIndex: number | null,
): { album: RecentAlbum; index: number } | null {
  for (let index = 0; index < albums.length; index += 1) {
    const album = albums[index];
    if (index === skipIndex) continue;
    if (!album?.album_name || !album.artist_name || !album.album_cover_url) continue;
    if (album.in_library) continue;
    return { album, index };
  }
  return null;
}
