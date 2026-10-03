/**
 * the banners that talk about YOU: your week in music, the genre you play
 * far more than you own, and a spotlight on a new release from the artist
 * you've had on repeat.
 *
 * all of it comes from data the app already has. the stats worker keeps a
 * cached 7-day summary per profile (/api/stats/cached?range=7d), and the
 * recent-releases shelf is already on the page. nothing here fetches on its
 * own or invents a number: every banner returns null when its data isn't
 * there, and the page simply doesn't render it.
 */

export interface StatsArtist {
  name: string;
  play_count?: number;
  image_url?: string | null;
  id?: string | number | null;
}

export interface StatsTrack {
  name?: string;
  artist?: string;
  album?: string;
  play_count?: number;
  image_url?: string | null;
}

export interface WeekStats {
  success?: boolean;
  // total_time_ms exists too, but it's only summed where a play carried a
  // duration: 2,333 plays came to 3.1 hours on real data. not shown.
  overview?: { total_plays?: number; unique_artists?: number };
  previous?: { total_plays?: number };
  top_artists?: StatsArtist[];
  top_tracks?: StatsTrack[];
  timeline?: { date: string; plays: number }[];
  genres?: { genre: string; play_count?: number; percentage?: number }[];
  rhythm?: { current_streak?: number };
  own_vs_play?: {
    genre: string;
    owned_pct?: number;
    played_pct?: number;
    gap?: number;
  }[];
}

export interface WeekDay {
  date: string;
  plays: number;
  /** 0..1 of the busiest day, for the bar height */
  share: number;
  /** one letter, from the date itself (never from the viewer's clock) */
  label: string;
}

export interface WeekSummary {
  plays: number;
  /** whole percent vs the week before, null when there's no week before */
  change: number | null;
  artists: number;
  streak: number;
  topArtist: StatsArtist | null;
  topGenre: string | null;
  days: WeekDay[];
  /** the tracks behind "play your top tracks", in rank order */
  topTracks: StatsTrack[];
}

const DAY_LETTERS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

/** 'S', 'M', ... for a yyyy-mm-dd, read as a calendar date (no timezone drift). */
export function dayLetter(date: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return '';
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return DAY_LETTERS[d.getUTCDay()];
}

/** your week, or null when there's no listening to talk about. */
export function weekSummary(stats: WeekStats | null | undefined): WeekSummary | null {
  const plays = stats?.overview?.total_plays ?? 0;
  if (!stats || plays <= 0) return null;
  const prev = stats.previous?.total_plays ?? 0;
  const timeline = (stats.timeline ?? []).slice(-7);
  const peak = Math.max(1, ...timeline.map((d) => d.plays || 0));
  return {
    plays,
    change: prev > 0 ? Math.round(((plays - prev) / prev) * 100) : null,
    artists: stats.overview?.unique_artists ?? 0,
    streak: stats.rhythm?.current_streak ?? 0,
    topArtist: stats.top_artists?.[0] ?? null,
    topGenre: stats.genres?.[0]?.genre ?? null,
    days: timeline.map((d) => ({
      date: d.date,
      plays: d.plays || 0,
      share: (d.plays || 0) / peak,
      label: dayLetter(d.date),
    })),
    topTracks: (stats.top_tracks ?? []).filter((t) => t.name && t.artist),
  };
}

export interface TasteGap {
  genre: string;
  playedPct: number;
  ownedPct: number;
  /** how many times more you play it than you own it, rounded, at least 2 */
  ratio: number;
}

/** below these it's noise, not a gap worth a banner */
export const GAP_MIN_PLAYED_PCT = 5;
export const GAP_MIN_RATIO = 2;

/**
 * the genre you play most out of proportion to what you own. the stats
 * worker already computes own-vs-play per genre; this only picks the one
 * worth saying out loud.
 */
export function tasteGap(stats: WeekStats | null | undefined): TasteGap | null {
  let best: TasteGap | null = null;
  for (const row of stats?.own_vs_play ?? []) {
    const played = row.played_pct ?? 0;
    const owned = row.owned_pct ?? 0;
    if (!row.genre || played < GAP_MIN_PLAYED_PCT) continue;
    // owning none of it at all is the biggest gap there is
    const ratio = owned > 0 ? played / owned : Number.POSITIVE_INFINITY;
    if (ratio < GAP_MIN_RATIO) continue;
    const gap = played - owned;
    if (!best || gap > best.playedPct - best.ownedPct) {
      best = {
        genre: row.genre,
        playedPct: played,
        ownedPct: owned,
        ratio: Number.isFinite(ratio) ? Math.round(ratio) : 0,
      };
    }
  }
  return best;
}

/** "3× more than you own it", or "and you own none of it". */
export function tasteGapLine(gap: TasteGap): string {
  return gap.ratio > 0
    ? `You play it ${gap.ratio}× more than you collect it`
    : `You play it, but you don't own any of it yet`;
}

export interface SpotlightAlbum {
  album_name?: string;
  artist_name?: string;
  album_cover_url?: string;
  release_date?: string;
  album_type?: string;
  in_library?: boolean;
  [key: string]: unknown;
}

export interface Spotlight {
  album: SpotlightAlbum;
  /** index into the list it came from, so the page can open that exact row */
  index: number;
  /** the plays behind the pick, 0 when it's just the newest */
  plays: number;
  reason: string;
}

const norm = (s: string | undefined | null) => (s ?? '').trim().toLowerCase();

/** 'album' / 'ep' / 'single' as the eyebrow says it */
export function releaseKind(type: string | undefined): string {
  const t = norm(type);
  if (t === 'ep') return 'New EP';
  if (t === 'single') return 'New single';
  if (t === 'compilation') return 'New compilation';
  return 'New album';
}

/**
 * the one release worth a full-width spotlight: from the artist you've
 * played most this week, full albums over singles, newest first. falls back
 * to the newest full album when none of your artists has something new.
 * never something you already own.
 */
export function pickSpotlight(
  albums: SpotlightAlbum[],
  topArtists: StatsArtist[] = [],
): Spotlight | null {
  const plays = new Map(topArtists.map((a) => [norm(a.name), a.play_count ?? 0]));
  const weight = (t: string | undefined) => (norm(t) === 'album' ? 2 : norm(t) === 'ep' ? 1 : 0);
  let best: Spotlight | null = null;
  albums.forEach((album, index) => {
    if (!album?.album_name || !album.artist_name || album.in_library) return;
    if (!album.album_cover_url) return; // a spotlight without art is not a spotlight
    const p = plays.get(norm(album.artist_name)) ?? 0;
    const cand: Spotlight = {
      album,
      index,
      plays: p,
      reason:
        p > 0
          ? `You played ${album.artist_name} ${p} time${p === 1 ? '' : 's'} this week`
          : `New from ${album.artist_name}`,
    };
    if (!best) {
      best = cand;
      return;
    }
    const b: Spotlight = best;
    const better =
      cand.plays !== b.plays
        ? cand.plays > b.plays
        : weight(album.album_type) !== weight(b.album.album_type)
          ? weight(album.album_type) > weight(b.album.album_type)
          : norm(album.release_date) > norm(b.album.release_date);
    if (better) best = cand;
  });
  return best;
}

/** "Sep 25" from a yyyy-mm-dd, as a calendar date. '' when it isn't one. */
export function shortDate(date: string | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date ?? '');
  if (!m) return '';
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}`;
}
