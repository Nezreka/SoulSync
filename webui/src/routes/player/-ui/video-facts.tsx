/** the ratings strip and fact list the movie and show pages share */

import type { ReactNode } from 'react';

import type { VideoDetailBase, VideoFile } from '../-player.types';

import { styles as s } from './kit';

export function Ratings({ d }: { d: VideoDetailBase }) {
  const items: { label: string; value: string; hint: string }[] = [];
  if (d.rating)
    items.push({
      label: 'TMDB',
      value: d.rating.toFixed(1),
      hint: 'TMDB audience score, out of 10',
    });
  if (d.imdb_rating)
    items.push({ label: 'IMDb', value: d.imdb_rating.toFixed(1), hint: 'IMDb rating, out of 10' });
  if (d.rt_rating)
    items.push({
      label: 'Rotten Tomatoes',
      value: `${d.rt_rating}%`,
      hint: 'Rotten Tomatoes critics',
    });
  if (d.metacritic)
    items.push({
      label: 'Metacritic',
      value: String(d.metacritic),
      hint: 'Metacritic, out of 100',
    });
  if (d.trakt_rating)
    items.push({
      label: 'Trakt',
      value: `${Math.round(d.trakt_rating * 10)}%`,
      hint: 'Trakt community',
    });
  if (!items.length) return null;
  return (
    <div className={s.ratings} aria-label="Ratings">
      {items.map((r) => (
        <span key={r.label} className={s.rating} title={r.hint}>
          <span className={s.ratingValue}>{r.value}</span>
          <span className={s.ratingLabel}>{r.label}</span>
        </span>
      ))}
    </div>
  );
}

const LANGS: Record<string, string> = {
  en: 'English',
  eng: 'English',
  es: 'Spanish',
  spa: 'Spanish',
  fr: 'French',
  fre: 'French',
  fra: 'French',
  de: 'German',
  ger: 'German',
  deu: 'German',
  it: 'Italian',
  ita: 'Italian',
  ja: 'Japanese',
  jpn: 'Japanese',
  ko: 'Korean',
  kor: 'Korean',
  zh: 'Chinese',
  chi: 'Chinese',
  zho: 'Chinese',
  pt: 'Portuguese',
  por: 'Portuguese',
  ru: 'Russian',
  rus: 'Russian',
  nl: 'Dutch',
  dut: 'Dutch',
  nld: 'Dutch',
  sv: 'Swedish',
  swe: 'Swedish',
  da: 'Danish',
  dan: 'Danish',
  no: 'Norwegian',
  nor: 'Norwegian',
  fi: 'Finnish',
  fin: 'Finnish',
  pl: 'Polish',
  pol: 'Polish',
  hi: 'Hindi',
  hin: 'Hindi',
  ar: 'Arabic',
  ara: 'Arabic',
  tr: 'Turkish',
  tur: 'Turkish',
};

/** 'en,ja' or ['en','ja'] or a json array string, as "English, Japanese" */
export function languageList(value: unknown): string {
  let codes: string[] = [];
  if (Array.isArray(value)) codes = value.map(String);
  else if (typeof value === 'string' && value.trim()) {
    const text = value.trim();
    if (text.startsWith('[')) {
      try {
        const parsed: unknown = JSON.parse(text);
        if (Array.isArray(parsed)) codes = parsed.map(String);
      } catch {
        codes = [];
      }
    } else codes = text.split(/[,\s]+/);
  }
  const names = codes
    .map((c) => c.trim().toLowerCase())
    .filter(Boolean)
    .map((c) => LANGS[c] ?? c.toUpperCase());
  return [...new Set(names)].join(', ');
}

export function Facts({ rows }: { rows: [string, ReactNode | null | undefined | false][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== false && v !== '');
  if (!shown.length) return null;
  return (
    <dl className={s.facts}>
      {shown.map(([label, value]) => (
        <div key={label} className={s.factRow}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** what the best copy sounds like: "English, Japanese" from its tracks */
export function fileLanguages(file: VideoFile | undefined): { audio: string; subs: string } {
  return {
    audio: languageList(file?.audio_languages),
    subs: languageList(file?.subtitle_languages),
  };
}
