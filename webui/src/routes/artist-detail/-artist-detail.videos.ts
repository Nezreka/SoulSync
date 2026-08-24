import type { SearchVideo } from '../search/-search.types';

export const ARTIST_VIDEO_LIMIT = 8;

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

export function curateArtistVideos(
  videos: SearchVideo[],
  artistName: string,
  limit = ARTIST_VIDEO_LIMIT,
): SearchVideo[] {
  const seen = new Set<string>();
  return videos
    .map((video, index) => ({ video, index, score: artistVideoScore(video, artistName, index) }))
    .filter(({ video }) => {
      if (!hasArtistVideoSignal(video, artistName)) return false;
      const key = String(video.video_id || video.url || video.title || '')
        .trim()
        .toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .map(({ video }) => video);
}
