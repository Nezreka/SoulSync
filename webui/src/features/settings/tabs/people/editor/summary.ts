/**
 * "What Maya can do", built from the draft's effective map so it is the
 * truth of the form, not a restatement of it. Same rules as
 * core.permissions.summarize, in the editor's longer words.
 */

import type { Count, IdList, PermMap, QualityOption } from './types';

import { ALL, type Model, daysWord, ratingWord, softLabel, str } from './model';

export type SummaryIcon = 'music' | 'movies' | 'tv' | 'books' | 'requests';

/** a run of words; strong ones are bold, good ones bold and green */
export type Bit = { text: string; strong?: boolean; good?: boolean };

export interface SummaryLine {
  icon: SummaryIcon;
  title: string;
  bits: Bit[];
}

export interface EditorSummary {
  sentence: Bit[];
  lines: SummaryLine[];
  never: string[];
}

const MODE_SHORT: Record<string, string> = {
  download: 'Downloads',
  ask: 'Asks first',
  none: 'Requests off',
};

function qualityWords(opts: QualityOption[], allowed: unknown): string {
  if (allowed === ALL) return opts.length > 1 ? 'any quality' : '';
  const ids = (allowed as IdList) ?? [];
  const named = opts.filter((o) => ids.includes(o.id));
  if (!named.length) return 'no quality allowed';
  if (named.length === 1) return `always ${named[0].name}`;
  return `${named.length} qualities`;
}

function videoRating(model: Model, eff: PermMap, id: 'movies.max_rating' | 'tv.max_rating') {
  const row = model.rowsById.get(id);
  const v = eff[id];
  if (!row || v == null || v === 'any') return 'any rating';
  return `up to ${ratingWord(row, v)}`;
}

export function buildSummary(model: Model, eff: PermMap, isAdmin: boolean): EditorSummary {
  if (isAdmin) {
    return {
      sentence: [
        { text: 'Can use ' },
        { text: 'everything', strong: true },
        { text: ', including settings and people.' },
      ],
      lines: [],
      never: [],
    };
  }
  const sentence: Bit[] = [];
  const lines: SummaryLine[] = [];
  const musicOn = !!eff['music.access'];
  const moviesOn = !!eff['movies.access'];
  const tvOn = !!eff['tv.access'];
  // music, movies and TV each have their own request limit; a kind's limit
  // is shown while that kind asks first (downloads are never capped)
  const limitOf = (kind: 'music' | 'movies' | 'tv'): Count | null => {
    const on = { music: musicOn, movies: moviesOn, tv: tvOn }[kind];
    if (!on || eff[`${kind}.get`] !== 'ask') return null;
    const c = (eff[`${kind}.request_limit`] ?? eff['requests.limit']) as Count | undefined;
    return c?.n ? c : null;
  };
  const NOUN = { music: 'music', movies: 'movie', tv: 'TV' } as const;
  const limitText = (kind: 'music' | 'movies' | 'tv', c: Count) =>
    `${c.n} ${NOUN[kind]} ${c.n === 1 ? 'ask' : 'asks'} ${[1, 7, 30, 365].includes(c.days) ? `a ${daysWord(c.days)}` : `every ${c.days} days`}`;
  const limited = (['music', 'movies', 'tv'] as const)
    .map((k) => ({ kind: k, c: limitOf(k) }))
    .filter((x): x is { kind: 'music' | 'movies' | 'tv'; c: Count } => !!x.c);

  // the sentence: one clause per side
  if (musicOn) {
    const m = eff['music.get'];
    if (m === 'download')
      sentence.push(
        { text: 'Listens and ' },
        { text: 'downloads music', strong: true },
        { text: ' right away. ' },
      );
    else if (m === 'ask')
      sentence.push(
        { text: 'Listens to music and ' },
        { text: 'asks you', strong: true },
        { text: ' for more. ' },
      );
    else sentence.push({ text: 'Listens to music. ' });
  }
  const what = moviesOn && tvOn ? 'movies and shows' : moviesOn ? 'movies' : tvOn ? 'shows' : '';
  if (what) {
    const modes = [moviesOn && eff['movies.get'], tvOn && eff['tv.get']].filter(Boolean);
    const one = modes.every((m) => m === modes[0]) ? modes[0] : null;
    const rating = moviesOn
      ? videoRating(model, eff, 'movies.max_rating')
      : videoRating(model, eff, 'tv.max_rating');
    const tail = rating === 'any rating' ? '' : `, ${rating}`;
    if (one === 'download')
      sentence.push(
        { text: 'Gets ' },
        { text: what, strong: true },
        { text: ` right away${tail}` },
      );
    else if (one === 'ask')
      sentence.push({ text: 'Asks you', strong: true }, { text: ` for ${what}${tail}` });
    else if (one === 'none') sentence.push({ text: `Watches ${what}${tail}` });
    else {
      sentence.push(
        { text: 'Movies: ' },
        { text: MODE_SHORT[str(eff['movies.get'])].toLowerCase(), strong: true },
        { text: ', shows: ' },
        { text: MODE_SHORT[str(eff['tv.get'])].toLowerCase(), strong: true },
        { text: tail },
      );
    }
    sentence.push({ text: '. ' });
  }
  if (limited.length) {
    sentence.push({ text: 'Up to ' });
    limited.forEach(({ kind, c }, i) => {
      if (i) sentence.push({ text: i === limited.length - 1 ? ' and ' : ', ' });
      sentence.push({ text: limitText(kind, c), strong: true });
    });
    sentence.push({ text: '.' });
  }
  if (!musicOn && !what) sentence.push({ text: 'Nothing to use yet: every side is off.' });

  // music
  if (musicOn) {
    const parts = model.stored.filter((r) => r.category === 'music' && r.group);
    const on = parts.filter((r) => !!eff[r.id]);
    const bits: Bit[] = [
      { text: MODE_SHORT[str(eff['music.get'])], good: eff['music.get'] === 'download' },
      { text: eff['music.hide_explicit'] ? 'explicit hidden' : 'explicit shown' },
      { text: on.length === parts.length ? 'every part' : `${on.length} of ${parts.length} parts` },
    ];
    const q = qualityWords(model.quality.music, eff['music.quality']);
    if (q && q !== 'any quality') bits.push({ text: q });
    const ml = limitOf('music');
    if (ml) bits.push({ text: limitText('music', ml) });
    lines.push({ icon: 'music', title: 'Music', bits });
  } else lines.push({ icon: 'music', title: 'Music', bits: [{ text: 'Off' }] });

  for (const [key, title, icon] of [
    ['movies', 'Movies', 'movies'],
    ['tv', 'TV shows', 'tv'],
  ] as const) {
    if (!eff[`${key}.access`]) {
      lines.push({ icon, title, bits: [{ text: 'Off' }] });
      continue;
    }
    const mode = str(eff[`${key}.get`]);
    const bits: Bit[] = [
      { text: MODE_SHORT[mode], good: mode === 'download' },
      { text: videoRating(model, eff, `${key}.max_rating`) },
    ];
    const q = qualityWords(
      key === 'movies' ? model.quality.movies : model.quality.tv,
      eff[`${key}.quality`],
    );
    if (q && mode !== 'none') bits.push({ text: q });
    const vl = limitOf(key);
    if (vl) bits.push({ text: limitText(key, vl) });
    lines.push({ icon, title, bits });
  }

  const books: Bit[] = [];
  if (eff['books.access'])
    books.push({
      text: `audiobooks, ${eff['books.get'] === 'download' ? 'downloads' : "can't request"}`,
      good: eff['books.get'] === 'download',
    });
  if (eff['podcasts.access'])
    books.push({
      text: `podcasts, ${eff['podcasts.get'] === 'download' ? 'downloads' : 'follows only'}`,
      good: eff['podcasts.get'] === 'download',
    });
  if (books.length)
    books[0] = {
      ...books[0],
      text: books[0].text.charAt(0).toUpperCase() + books[0].text.slice(1),
    };
  lines.push({
    icon: 'books',
    title: 'Books & podcasts',
    bits: books.length ? books : [{ text: 'Off' }],
  });

  const req: Bit[] = [
    {
      text: limited.length
        ? limited.map(({ kind, c }) => limitText(kind, c)).join(', ')
        : 'No limit',
    },
  ];
  req.push({ text: eff['requests.see_all'] ? "sees everyone's" : 'sees only their own' });
  if (eff['requests.moderate']) req.push({ text: 'moderator', good: true });
  lines.push({ icon: 'requests', title: 'Requests', bits: req });

  // never: what's off, then what only admins get
  const never: string[] = [];
  for (const kind of ['movies', 'tv'] as const) {
    const opts = kind === 'movies' ? model.quality.movies : model.quality.tv;
    const allowed = eff[`${kind}.quality`];
    const has4k = opts.some((o) => o.tags?.includes('4k'));
    const allowed4k =
      allowed === ALL ||
      opts.some((o) => o.tags?.includes('4k') && (allowed as IdList)?.includes(o.id));
    if (eff[`${kind}.access`] && has4k && !allowed4k && !never.includes('4K')) never.push('4K');
  }
  const sideOff = (r: { category: string; needs: string[] }) =>
    r.needs?.length > 0 && !r.needs.some((id) => !!eff[id]);
  for (const r of model.stored) {
    if (r.kind !== 'bool' || eff[r.id]) continue;
    if (r.id === 'requests.see_all' || r.id === 'requests.moderate') continue;
    if (sideOff(r)) continue; // the side being off already says it
    if (!never.includes(softLabel(r.label))) never.push(softLabel(r.label));
  }
  never.push('settings', 'people', 'tools jobs');
  if (!eff['requests.see_all']) never.push("other people's requests");
  return { sentence, lines, never };
}
