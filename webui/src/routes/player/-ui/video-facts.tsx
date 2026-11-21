/** the ratings strip and fact list the movie and show pages share */

import { useState, type ReactNode } from 'react';

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

// three-letter codes Intl doesn't name the way people say them
const THREE: Record<string, string> = {
  eng: 'en',
  spa: 'es',
  fre: 'fr',
  fra: 'fr',
  ger: 'de',
  deu: 'de',
  ita: 'it',
  jpn: 'ja',
  kor: 'ko',
  chi: 'zh',
  zho: 'zh',
  por: 'pt',
  rus: 'ru',
  dut: 'nl',
  nld: 'nl',
  swe: 'sv',
  dan: 'da',
  nor: 'no',
  fin: 'fi',
  pol: 'pl',
  hin: 'hi',
  ara: 'ar',
  tur: 'tr',
  cze: 'cs',
  ces: 'cs',
  gre: 'el',
  ell: 'el',
  heb: 'he',
  hun: 'hu',
  rum: 'ro',
  ron: 'ro',
  tha: 'th',
  vie: 'vi',
  ind: 'id',
  may: 'ms',
  msa: 'ms',
  per: 'fa',
  fas: 'fa',
  hrv: 'hr',
  ukr: 'uk',
  bul: 'bg',
  slo: 'sk',
  slk: 'sk',
  slv: 'sl',
  srp: 'sr',
  cat: 'ca',
  est: 'et',
  lav: 'lv',
  lit: 'lt',
  ice: 'is',
  isl: 'is',
  tam: 'ta',
  tel: 'te',
  ben: 'bn',
  urd: 'ur',
  fil: 'fil',
};

let names: Intl.DisplayNames | null | undefined;

/** 'cs' -> Czech, 'pt-BR' -> Brazilian Portuguese, 'eng' -> English */
export function languageName(code: string): string {
  const raw = code.trim().replace('_', '-');
  if (!raw) return '';
  const lower = raw.toLowerCase();
  const tag = THREE[lower] ?? raw;
  if (names === undefined) {
    try {
      names = new Intl.DisplayNames(['en'], { type: 'language' });
    } catch {
      names = null;
    }
  }
  try {
    const name = names?.of(tag);
    if (name && name.toLowerCase() !== tag.toLowerCase()) return name;
  } catch {
    /* not a language tag */
  }
  return raw.toUpperCase();
}

/** 'en,ja' or ['en','ja'] or a json array string, as distinct language names */
export function languageNames(value: unknown): string[] {
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
  return [...new Set(codes.map(languageName).filter(Boolean))];
}

/** "English, Japanese" (kept for the short lists) */
export function languageList(value: unknown): string {
  return languageNames(value).join(', ');
}

/** "English, Spanish and 28 more", with the rest a click away */
export function LanguageSummary({ names: list, show = 3 }: { names: string[]; show?: number }) {
  const [open, setOpen] = useState(false);
  if (!list.length) return null;
  if (open || list.length <= show + 1) return <>{list.join(', ')}</>;
  return (
    <>
      {list.slice(0, show).join(', ')}{' '}
      <button type="button" className={s.moreLink} onClick={() => setOpen(true)} data-nav>
        and {list.length - show} more
      </button>
    </>
  );
}

/** awards text worth showing: not a bare "winner" or "nominee" */
export function meaningfulAwards(text: string | null | undefined): string | null {
  const t = (text ?? '').trim();
  if (t.length < 12 || /^(winner|nominee|nominated|won)\.?$/i.test(t)) return null;
  return t;
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
export function fileLanguages(file: VideoFile | undefined): { audio: string[]; subs: string[] } {
  return {
    audio: languageNames(file?.audio_languages),
    subs: languageNames(file?.subtitle_languages),
  };
}

/** $356M, $1.2B: budget and box office */
export function money(n: number | null | undefined): string | null {
  if (!n || n < 1000) return null;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (n >= 1e6) return `$${Math.round(n / 1e6)}M`;
  return `$${Math.round(n / 1e3)}K`;
}
