/**
 * a video's tracks as the server reports them (/api/player/tracks), and
 * which to start with: the language this device last picked, else the
 * file's default. subtitles stay off unless a language was picked, except
 * forced ones (signs, foreign lines) in the audio's language, which show
 * on their own, the way a disc player does.
 */

import type { Marker } from './timeline';

export interface AudioTrack {
  index: number;
  language: string | null;
  title: string | null;
  codec: string;
  channels: number | null;
  layout: string | null;
  default: boolean;
  plays: boolean;
}

export interface SubtitleTrack {
  id: string;
  language: string | null;
  title: string | null;
  codec: string;
  kind: 'text' | 'image' | 'unknown';
  forced: boolean;
  sdh?: boolean;
  default: boolean;
  external: boolean;
}

export interface Tracks {
  audio: AudioTrack[];
  subtitles: SubtitleTrack[];
  /** the file's own chapter marks (a video with fewer than two has none) */
  chapters?: { start: number; end?: number | null; title: string }[];
  /** intro, credits and recap spans, the best source of each (server side) */
  markers?: Marker[];
}

/** 'off', or a language code ('en', 'eng'); null: never picked */
export interface TrackPrefs {
  audio: string | null;
  subtitle: string | null;
}

/** 'eng' and 'en' are the same language */
const TWO: Record<string, string> = {
  eng: 'en',
  jpn: 'ja',
  spa: 'es',
  fre: 'fr',
  fra: 'fr',
  ger: 'de',
  deu: 'de',
  ita: 'it',
  por: 'pt',
  rus: 'ru',
  chi: 'zh',
  zho: 'zh',
  kor: 'ko',
  dut: 'nl',
  nld: 'nl',
  swe: 'sv',
  nor: 'no',
  dan: 'da',
  fin: 'fi',
  pol: 'pl',
  tur: 'tr',
  ara: 'ar',
  hin: 'hi',
  heb: 'he',
  gre: 'el',
  ell: 'el',
  cze: 'cs',
  ces: 'cs',
  hun: 'hu',
  tha: 'th',
  vie: 'vi',
  ind: 'id',
  ukr: 'uk',
  rum: 'ro',
  ron: 'ro',
};

export function sameLanguage(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const norm = (x: string) => {
    const base = x.toLowerCase().split('-')[0];
    return TWO[base] ?? base;
  };
  return norm(a) === norm(b);
}

export function pickAudio(tracks: readonly AudioTrack[], prefs: TrackPrefs): number {
  if (!tracks.length) return 0;
  const byPref = prefs.audio
    ? tracks.find((t) => sameLanguage(t.language, prefs.audio))
    : undefined;
  return (byPref ?? tracks.find((t) => t.default) ?? tracks[0]).index;
}

export function pickSubtitle(
  subs: readonly SubtitleTrack[],
  prefs: TrackPrefs,
  audioLanguage: string | null,
): string | null {
  const usable = subs.filter((s) => s.kind !== 'unknown');
  if (prefs.subtitle && prefs.subtitle !== 'off') {
    const mine = usable.filter((s) => sameLanguage(s.language, prefs.subtitle));
    // a full track over a forced-only one, text over pictures
    const best =
      mine.find((s) => !s.forced && s.kind === 'text') ?? mine.find((s) => !s.forced) ?? mine[0];
    if (best) return best.id;
  }
  if (prefs.subtitle === 'off') return null;
  const forced = usable.find(
    (s) => s.forced && (!audioLanguage || sameLanguage(s.language, audioLanguage)),
  );
  return forced?.id ?? null;
}

const NAMES =
  typeof Intl !== 'undefined' && 'DisplayNames' in Intl
    ? new Intl.DisplayNames(undefined, { type: 'language' })
    : null;

export function languageLabel(code: string | null | undefined): string {
  if (!code) return 'Unknown language';
  try {
    const base = code.toLowerCase().split('-')[0];
    return NAMES?.of(TWO[base] ?? base) ?? code;
  } catch {
    return code;
  }
}

const LAYOUTS: Record<number, string> = { 1: 'Mono', 2: 'Stereo', 6: '5.1', 8: '7.1' };

export function audioLabel(t: AudioTrack): { label: string; detail: string } {
  const lang = languageLabel(t.language);
  const ch = t.channels ? (LAYOUTS[t.channels] ?? `${t.channels} ch`) : '';
  return {
    label:
      t.title && !t.title.toLowerCase().includes(lang.toLowerCase())
        ? `${lang} · ${t.title}`
        : lang,
    detail: [ch, t.codec.toUpperCase()].filter(Boolean).join(' · '),
  };
}

export function subtitleLabel(t: SubtitleTrack): { label: string; detail: string } {
  const bits = [
    t.forced ? 'Forced' : '',
    t.sdh ? 'SDH' : '',
    t.kind === 'image' ? 'Drawn in' : '',
    t.external ? 'File' : '',
  ].filter(Boolean);
  return {
    label: t.title ? `${languageLabel(t.language)} · ${t.title}` : languageLabel(t.language),
    detail: bits.join(' · ') || t.codec.toUpperCase(),
  };
}
