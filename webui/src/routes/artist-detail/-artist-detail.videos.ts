import type { SearchVideo } from '../search/-search.types';

/** how many videos each "show more" reveals, and how many the shelf opens with. */
export const ARTIST_VIDEO_PAGE = 8;
/** the first yt-dlp ask. the search page's number, fast enough for a page load. */
export const ARTIST_VIDEO_FETCH_INITIAL = 20;
/** the second ask, made only when the curated pool runs dry. mirrors the server's ceiling. */
export const ARTIST_VIDEO_FETCH_MAX = 60;

const WEAK_VIDEO_TERMS = /\b(reaction|review|interview|podcast|tutorial|lesson|explained|cover)\b/i;
const MUSIC_VIDEO_TERMS = /\b(official|music video|official video|video|visualizer|lyric video)\b/i;

function compact(value: unknown): string {
  const raw = typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  return raw
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function artistTokens(artistName: string): string[] {
  return compact(artistName)
    .split(' ')
    .filter((token) => token.length > 1);
}

export function artistVideoSearchQuery(artistName: string | null | undefined): string {
  return String(artistName ?? '').trim();
}

export function videoWatchUrl(video: SearchVideo): string {
  if (video.url) return video.url;
  const id = String(video.video_id ?? '').trim();
  return id ? `https://www.youtube.com/watch?v=${encodeURIComponent(id)}` : '';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** yt-dlp's upload_date is YYYYMMDD; "Jan 2024" reads, "20240101" doesn't. */
export function formatUploadDate(value: string | number | null | undefined): string {
  const raw = typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
  const match = /^(\d{4})(\d{2})(\d{2})$/.exec(raw);
  if (!match) return raw;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${match[1]}` : raw;
}

/** the one identity a video has across the pool, the queue and the download map. */
export function videoKey(video: SearchVideo): string {
  return String(video.video_id || video.url || video.title || '')
    .trim()
    .toLowerCase();
}

/**
 * the embed url for the plain-iframe fallback (the IFrame API builds its own).
 *
 * nocookie host so a watch here doesn't feed the viewer's youtube history;
 * `origin` is what youtube checks postMessage against when enablejsapi is on.
 */
export function videoEmbedUrl(video: SearchVideo, origin = ''): string {
  const id = String(video.video_id ?? '').trim();
  if (!id) return '';
  const params = new URLSearchParams({
    autoplay: '1',
    rel: '0',
    playsinline: '1',
    enablejsapi: '1',
  });
  if (origin) params.set('origin', origin);
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?${params.toString()}`;
}

declare global {
  /* eslint-disable no-var */
  /** media-player.js: syncs the play/pause icon, visualizer and media session. */
  var setPlayingState: ((playing: boolean) => void) | undefined;
  /* eslint-enable no-var */
}

/**
 * silence the app's own player before a video starts. two things playing at
 * once is the one thing nobody wants, and audioPlayer is the vanilla's global
 * <audio> (media-player.js), reached the same way library-globals does.
 *
 * pause() alone leaves the mini player showing a pause icon over a silent
 * track: the vanilla always pairs it with setPlayingState(false), so do the
 * same when that is around.
 */
export function pauseLibraryPlayback(): void {
  try {
    const player = globalThis.audioPlayer;
    if (!player || player.paused) return;
    player.pause();
    if (typeof globalThis.setPlayingState === 'function') globalThis.setPlayingState(false);
  } catch {
    // no player on this page is fine
  }
}

export type LoadMoreStep = 'reveal' | 'fetch' | 'none';

/**
 * what "show more" does next.
 *
 * reveal while the curated pool still has hidden videos; fetch a bigger pool
 * once it runs dry and the server can still give more; none when both are
 * spent. fetching only when the pool is dry keeps the artist page at one
 * yt-dlp call unless somebody actually scrolls past the first pages.
 */
export function loadMoreStep(input: {
  curatedCount: number;
  visible: number;
  fetchedLimit: number;
}): LoadMoreStep {
  if (input.curatedCount > input.visible) return 'reveal';
  if (input.fetchedLimit < ARTIST_VIDEO_FETCH_MAX) return 'fetch';
  return 'none';
}

/** the video after `key` in the queue, or null at the end. */
export function nextVideoAfter(queue: SearchVideo[], key: string | null): SearchVideo | null {
  if (!key) return queue[0] ?? null;
  const index = queue.findIndex((video) => videoKey(video) === key);
  if (index < 0) return null;
  return queue[index + 1] ?? null;
}

/** the video before `key` in the queue, or null at the start. */
export function previousVideoBefore(queue: SearchVideo[], key: string | null): SearchVideo | null {
  if (!key) return null;
  const index = queue.findIndex((video) => videoKey(video) === key);
  return index > 0 ? queue[index - 1] : null;
}

export function hasArtistVideoSignal(video: SearchVideo, artistName: string): boolean {
  const tokens = artistTokens(artistName);
  if (!tokens.length) return true;
  const haystack = `${compact(video.title)} ${compact(video.channel)}`.trim();
  return tokens.some((token) => haystack.includes(token));
}

export function artistVideoScore(video: SearchVideo, artistName: string, index = 0): number {
  const artist = compact(artistName);
  const title = compact(video.title);
  const channel = compact(video.channel);
  const haystack = `${title} ${channel}`.trim();
  if (!artist || !haystack) return 0;

  let score = Math.max(0, 20 - index);
  if (title.includes(artist)) score += 55;
  if (channel.includes(artist)) score += 30;

  const tokens = artistTokens(artistName);
  const matchedTokens = tokens.filter((token) => haystack.includes(token)).length;
  if (tokens.length) score += Math.round((matchedTokens / tokens.length) * 30);

  if (MUSIC_VIDEO_TERMS.test(String(video.title ?? ''))) score += 24;
  if (/\bofficial\b/i.test(String(video.title ?? ''))) score += 12;
  if (WEAK_VIDEO_TERMS.test(String(video.title ?? ''))) score -= 35;

  const views = Number(video.view_count);
  if (Number.isFinite(views) && views > 0) score += Math.min(18, Math.log10(views));

  return score;
}

/**
 * the artist's videos, best first, deduped, with the off-topic ones dropped.
 *
 * `limit` defaults to the whole pool: the section keeps the full curated
 * list and decides how many to show, so "show more" never has to re-score.
 */
export function curateArtistVideos(
  videos: SearchVideo[],
  artistName: string,
  limit = Number.POSITIVE_INFINITY,
): SearchVideo[] {
  const seen = new Set<string>();
  return videos
    .map((video, index) => ({ video, index, score: artistVideoScore(video, artistName, index) }))
    .filter(({ video }) => {
      if (!hasArtistVideoSignal(video, artistName)) return false;
      const key = videoKey(video);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .map(({ video }) => video);
}
