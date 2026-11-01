/** the small formatters the library tab reads its facts through. pure. */

import type { LibraryFile, SubtitleTrack } from './types';

export function formatBytes(n: number | null | undefined): string {
  if (!n || n <= 0) return '';
  const gb = n / 1073741824;
  if (gb >= 1000) return `${(gb / 1024).toFixed(1)} TB`;
  if (gb >= 1) return `${gb >= 100 ? Math.round(gb) : gb.toFixed(1)} GB`;
  return `${Math.max(1, Math.round(n / 1048576))} MB`;
}

export function channelsLabel(n: number | null | undefined): string {
  const c = Number(n) || 0;
  if (c >= 8) return '7.1';
  if (c === 7) return '6.1';
  if (c === 6) return '5.1';
  if (c === 2) return 'Stereo';
  if (c === 1) return 'Mono';
  return c ? `${c}ch` : '';
}

export function codecLabel(c: string | null | undefined): string {
  if (!c) return '';
  const l = c.toLowerCase();
  if (l.includes('hevc') || l.includes('265')) return 'HEVC';
  if (l.includes('264') || l === 'avc') return 'H.264';
  if (l.includes('av1')) return 'AV1';
  if (l.includes('vp9')) return 'VP9';
  return c.toUpperCase();
}

export function audioLabel(
  f: Pick<LibraryFile, 'audio_codec' | 'audio_channels' | 'atmos'>,
): string {
  return [
    f.audio_codec ? f.audio_codec.toUpperCase() : '',
    channelsLabel(f.audio_channels),
    f.atmos ? 'Atmos' : '',
  ]
    .filter(Boolean)
    .join(' ');
}

export function runtimeLabel(seconds: number | null | undefined): string {
  const s = Number(seconds) || 0;
  if (s <= 0) return '';
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** "1080p · HEVC · EAC3 5.1 · 4.2 GB" — the line a file reads as. */
export function fileFacts(f: LibraryFile): string[] {
  return [
    f.dynamic_range || '',
    codecLabel(f.video_codec),
    audioLabel(f),
    formatBytes(f.size_bytes),
    runtimeLabel(f.runtime_seconds),
  ].filter(Boolean);
}

const LANGS: Record<string, string> = {
  en: 'English',
  es: 'Spanish',
  fr: 'French',
  de: 'German',
  it: 'Italian',
  pt: 'Portuguese',
  nl: 'Dutch',
  pl: 'Polish',
  ru: 'Russian',
  ja: 'Japanese',
  ko: 'Korean',
  zh: 'Chinese',
  ar: 'Arabic',
  tr: 'Turkish',
  sv: 'Swedish',
  da: 'Danish',
  fi: 'Finnish',
  no: 'Norwegian',
  cs: 'Czech',
  el: 'Greek',
  he: 'Hebrew',
  hi: 'Hindi',
  hu: 'Hungarian',
  ro: 'Romanian',
  th: 'Thai',
  uk: 'Ukrainian',
  vi: 'Vietnamese',
  id: 'Indonesian',
};

/** a language code as a name: 'ja' -> 'Japanese', unknown codes in capitals */
export function languageName(code: string): string {
  return LANGS[code.toLowerCase()] ?? code.toUpperCase();
}

/** "Japanese, English": a file's audio tracks, or '' when none are tagged */
export function audioLanguagesLabel(codes: string[] | null | undefined): string {
  return (codes ?? []).map(languageName).join(', ');
}

/**
 * every subtitle a file has: the files beside it and the tracks inside it,
 * each language once, an embedded one marked as such. null when neither is
 * known yet, so nothing claims "no subtitles" before anyone looked.
 */
export function subtitleLabels(
  files: SubtitleTrack[] | null | undefined,
  embedded: string[] | null | undefined,
): string[] | null {
  if (files == null && embedded == null) return null;
  const out = (files ?? []).map(subtitleLabel);
  // the folder wasn't looked at: an empty list would claim there are none
  if (files == null && !(embedded ?? []).length) return null;
  const beside = new Set((files ?? []).map((t) => t.language.toLowerCase()));
  for (const code of embedded ?? []) {
    if (!beside.has(code.toLowerCase())) out.push(`${languageName(code)} (in file)`);
  }
  return out;
}

export function subtitleLabel(t: SubtitleTrack): string {
  const base = LANGS[t.language.toLowerCase()] ?? t.language.toUpperCase();
  const flags = [t.forced ? 'forced' : '', t.hi ? 'SDH' : ''].filter(Boolean);
  return flags.length ? `${base} (${flags.join(', ')})` : base;
}

/** the resolution badge's tone: the bigger the picture, the brighter. */
export function resolutionTone(label: string): 'uhd' | 'hd' | 'mid' | 'sd' | 'unknown' {
  if (label === '4K') return 'uhd';
  if (label === '1080p') return 'hd';
  if (label === '720p') return 'mid';
  if (label === 'SD') return 'sd';
  return 'unknown';
}

export function pad2(n: number | null | undefined): string {
  return String(n ?? 0).padStart(2, '0');
}

export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(String(iso).replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}
