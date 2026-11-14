/** how the manager says sizes, languages, codecs and needs. */

import type { Filters, Need, Side, TitleRow } from './types';

export function formatSize(bytes: number | null | undefined): string {
  const b = Number(bytes) || 0;
  if (b <= 0) return '';
  if (b >= 1e12) return `${(b / 1e12).toFixed(b >= 1e13 ? 1 : 2)} TB`;
  if (b >= 1e9) return `${(b / 1e9).toFixed(b >= 1e10 ? 0 : 1)} GB`;
  if (b >= 1e6) return `${Math.max(1, Math.round(b / 1e6))} MB`;
  return `${Math.max(1, Math.round(b / 1e3))} KB`;
}

const CODEC_NAMES: Record<string, string> = {
  h264: 'H.264',
  hevc: 'HEVC',
  h265: 'HEVC',
  av1: 'AV1',
  mpeg4: 'MPEG-4',
  mpeg2video: 'MPEG-2',
  mpeg1video: 'MPEG-1',
  msmpeg4v3: 'DivX 3',
  vc1: 'VC-1',
  vp9: 'VP9',
  wmv3: 'WMV',
  aac: 'AAC',
  ac3: 'Dolby Digital',
  eac3: 'Dolby Digital+',
  truehd: 'TrueHD',
  dts: 'DTS',
  dca: 'DTS',
  flac: 'FLAC',
  mp3: 'MP3',
  opus: 'Opus',
  vorbis: 'Vorbis',
  pcm: 'PCM',
};

export function codecName(codec: string | null | undefined): string {
  const key = String(codec || '').toLowerCase();
  if (!key) return '';
  return CODEC_NAMES[key] ?? key.toUpperCase();
}

export function channelsLabel(channels: number | null | undefined): string {
  const n = Number(channels) || 0;
  if (n >= 8) return '7.1';
  if (n >= 6) return '5.1';
  if (n === 2) return 'Stereo';
  if (n === 1) return 'Mono';
  return n ? `${n} ch` : '';
}

let languageNames: Intl.DisplayNames | null | undefined;

/** 'ja' as 'Japanese'; a code the browser does not know stays as it is */
export function languageName(code: string): string {
  const key = String(code || '').trim();
  if (!key) return '';
  if (languageNames === undefined) {
    try {
      languageNames = new Intl.DisplayNames(['en'], { type: 'language' });
    } catch {
      languageNames = null;
    }
  }
  try {
    return languageNames?.of(key) || key.toUpperCase();
  } catch {
    return key.toUpperCase();
  }
}

/** a short list of language codes: 'EN, JA +2' */
export function languageCodes(codes: string[], shown = 3): string {
  if (!codes.length) return '';
  const head = codes.slice(0, shown).map((c) => c.toUpperCase());
  const more = codes.length - head.length;
  return head.join(', ') + (more > 0 ? ` +${more}` : '');
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatRuntime(seconds: number | null | undefined): string {
  const s = Number(seconds) || 0;
  if (s <= 0) return '';
  const minutes = Math.round(s / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** the "needs a look" filters, in the words the page uses for them */
export const NEED_COPY: Record<Need, { label: string; movies: string; shows: string }> = {
  missing: {
    label: 'Missing',
    movies: 'Wanted movies with no file yet',
    shows: 'Shows with aired episodes you do not have',
  },
  below_cutoff: {
    label: 'Below quality',
    movies: 'Movies below the quality their profile asks for',
    shows: 'Shows with episodes below the quality their profile asks for',
  },
  copies: {
    label: 'Extra copies',
    movies: 'Movies with more than one file',
    shows: 'Shows with an episode that has more than one file',
  },
  unmatched: {
    label: 'Unmatched',
    movies: 'Movies SoulSync could not identify',
    shows: 'Shows SoulSync could not identify',
  },
  no_subtitles: {
    label: 'No subtitles',
    movies: 'Movies with a file that has no subtitle track',
    shows: 'Shows with a file that has no subtitle track',
  },
  unprobed: {
    label: 'Not read yet',
    movies: 'Movies whose audio and subtitle languages have not been read',
    shows: 'Shows with files whose languages have not been read',
  },
  unmonitored: {
    label: 'Not monitored',
    movies: 'Movies SoulSync is not looking after',
    shows: 'Shows SoulSync is not looking after',
  },
  unwatched: {
    label: 'Unwatched',
    movies: 'Movies you own and have not watched',
    shows: 'Shows you own and have not started',
  },
  not_owned: {
    label: 'Not in library',
    movies: 'Movies listed with no file',
    shows: 'Shows listed with no episodes on disk',
  },
};

/** the needs worth a pill of their own, in the order they are shown */
export const NEED_ORDER: Need[] = [
  'missing',
  'below_cutoff',
  'copies',
  'unmatched',
  'no_subtitles',
  'unprobed',
];

export function noun(side: Side, n: number): string {
  return side === 'movies' ? (n === 1 ? 'movie' : 'movies') : n === 1 ? 'show' : 'shows';
}

/** how many filters are set, not counting search and the need pills */
export function countFilters(filters: Filters): number {
  return (
    [
      filters.status,
      filters.monitored,
      filters.locked,
      filters.resolution,
      filters.codec,
      filters.hdr,
      filters.audio_language,
      filters.subtitle_language,
      filters.drive,
      filters.genre,
      filters.video_library,
      filters.quality_profile,
    ].filter(Boolean).length + (filters.year_from || filters.year_to ? 1 : 0)
  );
}

/** what a title's files are, in one quiet line: 'HEVC · HDR10 · 5.1' */
export function qualityLine(row: TitleRow): string {
  const parts: string[] = [];
  if (row.video_codecs.length) parts.push(row.video_codecs.map(codecName).slice(0, 2).join(' / '));
  if (row.dynamic_range.length) parts.push(row.dynamic_range[0]);
  if (row.atmos) parts.push('Atmos');
  else if (row.audio_channels) parts.push(channelsLabel(row.audio_channels));
  return parts.filter(Boolean).join(' · ');
}

/** the last folders of a path, for a column too narrow for all of it */
export function shortPath(path: string | null | undefined, keep = 2): string {
  const parts = String(path || '')
    .replace(/\\/g, '/')
    .split('/')
    .filter(Boolean);
  if (parts.length <= keep) return parts.join('/');
  return '…/' + parts.slice(-keep).join('/');
}
