/**
 * What Settings > People says, worked out from the API's payloads: the
 * table's cells, the hero sentence, the impact line, the policy's locks on a
 * template's rows, and the plain words for every value. Pure functions, so
 * each screen's words are tested on their own.
 */

import type {
  AuditEntry,
  Catalogue,
  CatalogueRow,
  Count,
  Impact,
  Invite,
  Mode,
  PermMap,
  PermValue,
  Person,
  PersonPerms,
  QualityOption,
  QualityOptions,
} from './types';

/** a scalar value as text, anything else as JSON */
export const str = (v: PermValue | undefined): string =>
  typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
    ? String(v)
    : JSON.stringify(v ?? null);

export const MODES: Mode[] = ['download', 'ask', 'none'];
const MODE_RANK: Record<string, number> = { none: 0, ask: 1, download: 2 };
export const RATINGS = ['any', 'R', 'PG-13', 'PG', 'G'];
const RATING_RANK: Record<string, number> = { G: 0, PG: 1, 'PG-13': 2, R: 3, any: 4 };
const TV_RATING: Record<string, string> = {
  G: 'TV-Y7',
  PG: 'TV-PG',
  'PG-13': 'TV-14',
  R: 'TV-MA',
  any: 'Any',
};

const WORDS = [
  'no',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
  'ten',
  'eleven',
  'twelve',
];

/** "seven", "Seven", "14" */
export function numberWord(n: number, capital = false): string {
  const word = n >= 0 && n < WORDS.length ? WORDS[n] : String(n);
  return capital ? word.charAt(0).toUpperCase() + word.slice(1) : word;
}

export const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function listWords(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

export const isCount = (v: PermValue): v is Count =>
  !!v && typeof v === 'object' && !Array.isArray(v) && 'n' in v;

export function periodWords(days: number): string {
  if (days === 1) return 'a day';
  if (days === 7) return 'a week';
  if (days === 30) return 'a month';
  return `every ${days} days`;
}

export function countText(v: PermValue): string {
  if (!isCount(v) || !v.n) return 'No limit';
  return `${v.n} ${plural(v.n, 'ask', 'asks')} ${periodWords(v.days)}`;
}

// ── catalogue ──

export function rowById(catalogue: Catalogue): Record<string, CatalogueRow> {
  return Object.fromEntries(catalogue.rows.map((row) => [row.id, row]));
}

/** the rows a layer edits in one category, in catalogue order. the shared
 *  video pages sit in Movies and show under TV shows too (`also_in`) */
export function rowsFor(catalogue: Catalogue, category: string, layer: 'P' | 'T'): CatalogueRow[] {
  return catalogue.rows.filter(
    (row) =>
      (row.category === category || row.also_in.includes(category)) &&
      (row.scope.includes(layer) || row.kind === 'fixed') &&
      !row.presentation,
  );
}

/** the categories a layer has anything in. the policy has no Sidebar & look */
export function categoriesFor(catalogue: Catalogue, layer: 'P' | 'T') {
  return catalogue.categories.filter((c) =>
    c.id === 'look' ? layer === 'T' : rowsFor(catalogue, c.id, layer).length > 0,
  );
}

export function qualityFor(row: CatalogueRow, quality: QualityOptions | null): QualityOption[] {
  if (!quality || !row.media) return [];
  return row.media === 'music'
    ? quality.music
    : row.media === 'movie'
      ? quality.movies
      : quality.tv;
}

// ── values in words ──

/** one word for each mode, the same in the policy, templates and people
 *  (#2192); a podcast's none is Follow only, from its own value labels */
export function modeLabel(row: CatalogueRow | undefined, mode: string, _layer: 'P' | 'T' = 'T') {
  return (
    row?.value_labels?.[mode] ||
    { download: 'Auto-approve', ask: 'Require approval', none: 'Requests off' }[mode] ||
    mode
  );
}

export function ratingLabel(value: PermValue, tv = false): string {
  if (value === 'follow') return 'Same as movies';
  if (value === 'any' || value == null) return 'Any';
  return tv ? TV_RATING[str(value)] || str(value) : str(value);
}

function idsText(value: PermValue, options: QualityOption[]): string {
  if (value === '*') return 'All';
  if (!Array.isArray(value) || value.length === 0) return 'None';
  const names = value.map((id) =>
    id === '4k' ? '4K' : options.find((o) => o.id === id)?.name || `#${id}`,
  );
  return listWords(names);
}

/** any value, as the rows and the impact line say it */
export function valueText(
  row: CatalogueRow | undefined,
  value: PermValue,
  quality: QualityOptions | null,
  layer: 'P' | 'T' = 'T',
): string {
  if (!row) return str(value);
  switch (row.kind) {
    case 'bool':
      return value ? 'On' : layer === 'P' ? 'Admins only' : 'Off';
    case 'restrict':
      return value ? 'On' : 'Off';
    case 'mode':
      return modeLabel(row, str(value), layer);
    case 'rating':
      return ratingLabel(value, row.id.startsWith('tv.'));
    case 'count':
      return countText(value);
    case 'set':
    case 'set_flag':
      return idsText(value, qualityFor(row, quality));
    case 'choice':
      return value == null
        ? 'Server default'
        : qualityFor(row, quality).find((o) => o.id === value)?.name || `#${str(value)}`;
    default:
      return str(value);
  }
}

// ── the policy over a template ──

const rate = (c: Count) => (c.n ? c.n / Math.max(1, c.days) : Infinity);

/** whether a value goes past the policy's ceiling for its row */
export function aboveCeiling(row: CatalogueRow, value: PermValue, ceiling: PermValue): boolean {
  if (ceiling === undefined) return false;
  switch (row.kind) {
    case 'bool':
      return value === true && ceiling === false;
    case 'restrict':
      return ceiling === true && value !== true;
    case 'mode':
      return (MODE_RANK[str(value)] ?? 0) > (MODE_RANK[str(ceiling)] ?? 2);
    case 'rating': {
      if (value === 'follow') return false;
      return (RATING_RANK[str(value)] ?? 4) > (RATING_RANK[str(ceiling)] ?? 4);
    }
    case 'count':
      return isCount(ceiling) && isCount(value) && rate(value) > rate(ceiling);
    default:
      return false;
  }
}

/** what the policy lets a value be: the value, or the ceiling it's held at
 *  (core.permissions.clamp for the kinds a row shows). the control shows
 *  this, the value the server enforces, never the stored one (#2189) */
export function heldAt(
  row: CatalogueRow,
  value: PermValue,
  ceiling: PermValue | undefined,
): PermValue {
  if (ceiling === undefined || !aboveCeiling(row, value, ceiling)) return value;
  switch (row.kind) {
    case 'bool':
      return false;
    case 'mode':
    case 'rating':
    case 'count':
      return ceiling;
    default:
      return value;
  }
}

/** a quality id the policy doesn't allow */
export function idOutside(ceiling: PermValue, id: number): boolean {
  return ceiling !== '*' && Array.isArray(ceiling) && !ceiling.includes(id);
}

/** the lock line under a template row the policy holds down. ``stored``
 *  is what the template itself says: when the policy holds it lower, the
 *  line says so in its own sentence, apart from the value in force (#2189) */
export function capLine(
  row: CatalogueRow,
  ceiling: PermValue,
  quality: QualityOptions | null,
  stored?: PermValue,
) {
  if (row.kind === 'bool') return 'Global policy: admins only. Change it there.';
  if (row.kind === 'restrict') return 'Global policy keeps this on for everyone.';
  const held = `Global policy keeps this at ${valueText(row, ceiling, quality, 'T')}.`;
  const kept =
    stored !== undefined && aboveCeiling(row, stored, ceiling)
      ? ` The template is set to ${valueText(row, stored, quality, 'T')}, which comes back if the policy allows it.`
      : '';
  return `${held}${kept} Change it there.`;
}

// ── dependent rows ──

/** "Turn on Movies first." when a row needs another that's off */
export function needsLine(
  row: CatalogueRow,
  values: PermMap,
  rows: Record<string, CatalogueRow>,
): string | null {
  if (!row.needs.length) return null;
  if (row.needs.some((id) => values[id] === true)) return null;
  const names = row.needs.map((id) => rows[id]?.label || id);
  return `Turn on ${names.join(' or ')} first.`;
}

// ── the people table ──

export type Tone = 'dl' | 'ask' | 'off' | 'plain';
export interface Cell {
  tone: Tone;
  text: string;
  sub?: string;
}

function modeCell(mode: PermValue, noneText: string): Pick<Cell, 'tone' | 'text'> {
  if (mode === 'download') return { tone: 'dl', text: 'Downloads' };
  if (mode === 'ask') return { tone: 'ask', text: 'Asks first' };
  return { tone: 'off', text: noneText };
}

function qualityNames(value: PermValue, options: QualityOption[]): string | null {
  if (value === '*' || !Array.isArray(value)) return null;
  if (!value.length) return 'No quality';
  return value
    .map((id) => options.find((o) => o.id === id)?.name)
    .filter(Boolean)
    .join(', ');
}

export interface PersonCells {
  music: Cell;
  movies: Cell;
  tv: Cell;
  books: Cell;
  requests: Cell;
}

/** music, movies and TV each have their own request limit */
export type LimitKind = 'music' | 'movies' | 'tv';
const LIMIT_LABEL: Record<LimitKind, string> = { music: 'Music', movies: 'Movies', tv: 'TV' };
const LIMIT_NOUN: Record<LimitKind, string> = { music: 'music', movies: 'movie', tv: 'TV' };

/** a kind's limit from a map; an older map's one requests.limit reads for every kind */
export function limitOf(eff: PermMap, kind: LimitKind): { n: number; days: number } | null {
  const v = eff[`${kind}.request_limit`] ?? eff['requests.limit'];
  return isCount(v) && v.n ? { n: v.n, days: v.days } : null;
}

export type AsksByKind = Partial<Record<LimitKind, { used: number; limit: number }>>;

export function personCells(
  eff: PermMap,
  quality: QualityOptions | null,
  asks: AsksByKind | null = null,
): PersonCells {
  const q = quality ?? { music: [], movies: [], tv: [] };
  const sub = (...bits: Array<string | null | false | undefined>) =>
    bits.filter(Boolean).join(' · ') || undefined;
  const off: Cell = { tone: 'off', text: 'Off' };

  const music: Cell = eff['music.access']
    ? {
        ...modeCell(eff['music.get'], 'Listens'),
        sub: sub(
          eff['music.hide_explicit'] === true && 'Clean',
          qualityNames(eff['music.quality'], q.music),
        ),
      }
    : off;
  const movieRating = eff['movies.max_rating'];
  const movies: Cell = eff['movies.access']
    ? {
        ...modeCell(eff['movies.get'], 'Watches'),
        sub: sub(
          qualityNames(eff['movies.quality'], q.movies),
          typeof movieRating === 'string' && movieRating !== 'any' && movieRating,
        ),
      }
    : off;
  const tvRating = eff['tv.max_rating'] === 'follow' ? movieRating : eff['tv.max_rating'];
  const tv: Cell = eff['tv.access']
    ? {
        ...modeCell(eff['tv.get'], 'Watches'),
        sub: sub(
          qualityNames(eff['tv.quality'], q.tv),
          typeof tvRating === 'string' && tvRating !== 'any' && ratingLabel(tvRating, true),
        ),
      }
    : off;
  const books: Cell = eff['books.access']
    ? modeCell(eff['books.get'], 'Listens')
    : eff['podcasts.access']
      ? { tone: 'off', text: 'Podcasts only' }
      : off;
  // each kind that asks first, on its own: "2 of 5 used" once the server
  // counted them (downloads right away are never capped)
  const on: Record<LimitKind, boolean> = {
    music: !!eff['music.access'] && eff['music.get'] === 'ask',
    movies: !!eff['movies.access'] && eff['movies.get'] === 'ask',
    tv: !!eff['tv.access'] && eff['tv.get'] === 'ask',
  };
  const limits = (['music', 'movies', 'tv'] as const)
    .filter((k) => on[k])
    .map((k) => ({ kind: k, limit: limitOf(eff, k) }))
    .filter((x): x is { kind: LimitKind; limit: { n: number; days: number } } => !!x.limit);
  const usedText = (kind: LimitKind, n: number) => {
    const a = asks?.[kind];
    return a && a.limit === n ? `${Math.min(a.used, n)} of ${n} used` : null;
  };
  let requests: Cell;
  if (!limits.length) requests = { tone: 'plain', text: 'No limit' };
  else if (limits.length === 1) {
    const { kind, limit } = limits[0];
    const used = usedText(kind, limit.n);
    requests = used
      ? { tone: 'plain', text: used, sub: `${LIMIT_NOUN[kind]} asks ${periodWords(limit.days)}` }
      : {
          tone: 'plain',
          text: `${limit.n} ${LIMIT_NOUN[kind]} ${plural(limit.n, 'ask', 'asks')}`,
          sub: periodWords(limit.days),
        };
  } else {
    const sameDays = limits.every((x) => x.limit.days === limits[0].limit.days);
    requests = {
      tone: 'plain',
      text: limits
        .map(({ kind, limit }) => {
          const a = asks?.[kind];
          const count =
            a && a.limit === limit.n ? `${Math.min(a.used, limit.n)}/${limit.n}` : `${limit.n}`;
          return `${LIMIT_LABEL[kind]} ${count}${sameDays ? '' : ` ${periodWords(limit.days)}`}`;
        })
        .join(' · '),
      sub: sameDays ? `asks ${periodWords(limits[0].limit.days)}` : undefined,
    };
  }
  return { music, movies, tv, books, requests };
}

export interface PersonRow {
  person: Person;
  perms: PersonPerms | null;
  /** the permissions read failed: the row says so instead of guessing */
  failed: boolean;
  /** asks spent per limited kind in its window (/api/permissions/asks-used) */
  asks?: Partial<
    Record<LimitKind, { used: number; limit: number; days: number; remaining: number }>
  > | null;
}

export const isAdmin = (r: PersonRow) => r.person.is_admin || r.person.id === 1;
export const changeCount = (r: PersonRow) =>
  r.perms?.mode === 'template' ? Object.keys(r.perms.overrides || {}).length : 0;
export const needsLook = (r: PersonRow) => !isAdmin(r) && !!r.perms && r.perms.mode === 'legacy';

export type Filter = 'everyone' | 'admins' | 'changes' | 'off' | 'look';

export function filterPeople(rows: PersonRow[], filter: Filter, query: string): PersonRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((r) => {
    if (q && !r.person.name.toLowerCase().includes(q)) return false;
    if (filter === 'admins') return isAdmin(r);
    if (filter === 'changes') return changeCount(r) > 0;
    if (filter === 'off') return !!r.person.disabled;
    if (filter === 'look') return needsLook(r);
    return true;
  });
}

export interface Counts {
  people: number;
  members: number;
  admins: number;
  following: number;
  changed: number;
  look: number;
  off: number;
}

export function countPeople(rows: PersonRow[]): Counts {
  const admins = rows.filter(isAdmin).length;
  const members = rows.filter((r) => !isAdmin(r));
  return {
    people: rows.length,
    members: members.length,
    admins,
    following: members.filter((r) => r.perms?.mode === 'template').length,
    changed: members.filter((r) => changeCount(r) > 0).length,
    look: members.filter(needsLook).length,
    off: rows.filter((r) => r.person.disabled).length,
  };
}

export interface HeroPart {
  text: string;
  em?: boolean;
}

/** "Seven people use SoulSync here. Five follow a template and two have
 *  changes of their own. Three requests are waiting for you." as parts, so
 *  the counts can stand out */
export function heroParts(c: Counts, waiting: number | null): HeroPart[] {
  const parts: HeroPart[] = [];
  if (c.people === 1) {
    parts.push({ text: 'Just you', em: true }, { text: ' use SoulSync here so far.' });
  } else {
    parts.push(
      { text: `${numberWord(c.people, true)} people`, em: true },
      { text: ' use SoulSync here.' },
    );
  }
  if (c.members) {
    if (c.following === c.members && !c.changed) {
      parts.push({
        text:
          c.members === 1
            ? ' The one member follows a template.'
            : ` Every member follows a template.`,
      });
    } else {
      parts.push({
        text: ` ${numberWord(c.following, true)} ${plural(c.following, 'follows', 'follow')} a template`,
      });
      if (c.changed) {
        parts.push(
          { text: ' and ' },
          {
            text: `${numberWord(c.changed)} ${plural(c.changed, 'has', 'have')} changes of their own`,
            em: true,
          },
        );
      }
      parts.push({ text: '.' });
    }
  }
  if (c.look) {
    parts.push({
      text: ` ${numberWord(c.look, true)} ${plural(c.look, 'needs', 'need')} a look.`,
      em: true,
    });
  }
  if (waiting != null) {
    parts.push({
      text: waiting
        ? ` ${numberWord(waiting, true)} ${plural(waiting, 'request is', 'requests are')} waiting for you.`
        : ' No requests are waiting for you.',
    });
  }
  return parts;
}

/** the line under a name */
export function metaLine(p: Person, meId: number | null): string {
  const bits: string[] = [];
  if (p.id === 1) bits.push('Owner');
  else bits.push(p.is_admin ? 'Admin' : 'Member');
  if (p.has_password) bits.push('password');
  else if (p.has_pin) bits.push('PIN');
  if (meId != null && p.id === meId) bits.push('you');
  return bits.join(' · ');
}

// ── needs a look ──

/** the migration's reasons, said plainly. it kept these people on their old
 *  settings because no template plus changes says exactly the same thing */
export function lookReasons(reasons: string[]): string[] {
  const out: string[] = [];
  const PAGE_NAMES: Record<string, string> = { hydrabase: 'Hydrabase' };
  for (const raw of reasons) {
    const r = String(raw);
    let m: RegExpExecArray | null;
    let text: string | null = null;
    if ((m = /max_rating '([^']*)'/.exec(r)))
      text = `A rating limit of ${m[1]}, which SoulSync can't match to a rating here.`;
    else if ((m = /request limit (\d+) above/.exec(r)))
      text = `A request limit of ${m[1]}, more than a template can hold.`;
    else if ((m = /request window (\d+) days above/.exec(r)))
      text = `A request window of ${m[1]} days, longer than a template can hold.`;
    else if (/request limit unreadable/.test(r)) text = "A request limit that couldn't be read.";
    else if ((m = /page '([^']*)' isn't in the catalogue/.exec(r)))
      text = `A page called ${PAGE_NAMES[m[1]] ?? m[1]} that a template can't give.`;
    else if (/only some pages of/.test(r))
      text = 'Only some pages of one part, which a template gives all or none of.';
    else if (/allowed_pages isn't a list/.test(r)) text = "A page list that couldn't be read.";
    else if (/effective differs/.test(r)) text = null;
    else text = r.replace(/_/g, ' ');
    if (text && !out.includes(text)) out.push(text);
  }
  if (!out.length) out.push('Their old settings say something no template can say exactly.');
  return out;
}

// ── impact of a save ──

export interface ImpactLine {
  lead: string;
  people: Array<{ name: string; text: string }>;
  more: number;
  tail: string | null;
}

const SUBJECT: Partial<Record<string, string>> = {
  rating: 'rating',
  set: 'quality',
  choice: 'starting quality',
  set_flag: 'quality that needs your OK',
};

/** what a row is about, on its own: "Up to" is "Movies rating" */
function subject(row: CatalogueRow | undefined, catalogue: Catalogue): string {
  if (!row) return '';
  const what = SUBJECT[row.kind];
  if (!what) return row.label;
  const cat = catalogue.categories.find((c) => c.id === row.category)?.label ?? '';
  return `${cat} ${what}`.trim();
}

/** "Saving reaches all 5 members now." then who changes and how */
export function impactLine(
  impact: Impact,
  catalogue: Catalogue,
  quality: QualityOptions | null,
  scope: 'policy' | 'template',
): ImpactLine {
  const rows = rowById(catalogue);
  const n = impact.reaches;
  let lead: string;
  if (scope === 'policy') {
    lead =
      n === 0
        ? 'Nobody follows a template yet, so this reaches nobody now.'
        : n === 1
          ? 'Saving reaches the one member now.'
          : `Saving reaches all ${n} members now.`;
  } else {
    lead =
      n === 0
        ? 'Nobody follows this template yet.'
        : `Saving reaches ${n === 1 ? 'one person' : `${n} people`} now.`;
  }
  const people = impact.changes.slice(0, 2).map((change) => {
    const bits = change.rows.slice(0, 2).map((row) => {
      const def = rows[row.id];
      const label = subject(def, catalogue) || row.id;
      return `${label} goes from ${valueText(def, row.before, quality)} to ${valueText(def, row.after, quality)}`;
    });
    if (change.rows.length > 2) bits.push(`${change.rows.length - 2} more`);
    return { name: change.name || 'Someone', text: listWords(bits) };
  });
  if (n > 0 && !impact.changes.length) lead += " Nobody's access changes.";
  const others = impact.not_following.map((p) => p.name);
  const tail = others.length
    ? `${listWords(others)} ${plural(others.length, 'runs', 'run')} on old settings. The policy holds them too, but those settings aren't rewritten.`
    : null;
  return { lead, people, more: Math.max(0, impact.changes.length - 2), tail };
}

// ── drafts ──

const canon = (v: PermValue) =>
  JSON.stringify(Array.isArray(v) ? [...v].map(String).sort() : (v ?? null));

export const sameValue = (a: PermValue, b: PermValue) => canon(a) === canon(b);

/** only the rows a draft moved */
export function changedRows(saved: PermMap, draft: PermMap): PermMap {
  const out: PermMap = {};
  for (const key of Object.keys(draft)) {
    if (!sameValue(saved[key], draft[key])) out[key] = draft[key];
  }
  return out;
}

// ── invites and activity ──

export function presetLine(preset: Record<string, unknown> | null): string {
  const pr = preset || {};
  const bits: string[] = [];
  if (typeof pr.template_name === 'string') bits.push(pr.template_name);
  const sides =
    pr.allowed_sides === 'both'
      ? 'Music & movies'
      : pr.allowed_sides === 'video'
        ? 'Movies & TV'
        : 'Music';
  bits.push(sides);
  bits.push(pr.can_download === false ? 'asks first' : 'downloads');
  if (pr.hide_explicit) bits.push('clean');
  if (typeof pr.max_rating === 'string' && pr.max_rating) bits.push(`up to ${pr.max_rating}`);
  if (Array.isArray(pr.allowed_pages)) bits.push(`${pr.allowed_pages.length} pages`);
  const n = Number(pr.request_limit || 0);
  if (n > 0)
    bits.push(
      `${n} ${plural(n, 'ask', 'asks')} ${periodWords(Number(pr.request_limit_days || 7))}`,
    );
  return bits.join(' · ');
}

/** sqlite's naive utc text as a date */
export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function ago(value: string | null | undefined, now = Date.now()): string {
  const d = parseUtc(value);
  if (!d) return '';
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} ${plural(h, 'hour', 'hours')} ago`;
  const days = Math.round(h / 24);
  if (days < 30) return days === 1 ? 'yesterday' : `${days} days ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function until(value: string | null | undefined, now = Date.now()): string {
  const d = parseUtc(value);
  if (!d) return '';
  const h = Math.round((d.getTime() - now) / 3600000);
  if (h <= 0) return 'soon';
  if (h < 24) return `in ${h} ${plural(h, 'hour', 'hours')}`;
  const days = Math.round(h / 24);
  return `in ${days} ${plural(days, 'day', 'days')}`;
}

function detailLabels(detail: string | null, catalogue: Catalogue | null): string {
  if (!detail || !catalogue) return '';
  try {
    const parsed = JSON.parse(detail) as {
      set?: Record<string, unknown>;
      perms?: Record<string, unknown>;
    };
    const ids = Object.keys(parsed.set || parsed.perms || {});
    const rows = rowById(catalogue);
    const labels = [...new Set(ids.map((id) => rows[id]?.label).filter(Boolean) as string[])];
    if (!labels.length) return '';
    return labels.length > 3
      ? `${labels.slice(0, 3).join(', ')} and ${labels.length - 3} more`
      : listWords(labels);
  } catch {
    return '';
  }
}

/** "Boulder changed Maya's access: Getting music" */
export function auditSentence(e: AuditEntry, catalogue: Catalogue | null = null): string {
  const actor = e.actor_name || 'Someone';
  const self = e.target_id != null && e.target_id === e.actor_id;
  const target = e.target_name || 'a profile';
  const theirs = self ? 'their' : `${target}'s`;
  const what = detailLabels(e.detail, catalogue);
  const withWhat = (s: string) => (what ? `${s}: ${what}` : s);
  switch (e.action) {
    case 'permissions_policy':
      return withWhat(`${actor} changed the global policy`);
    case 'permissions_template':
      return withWhat(`${actor} changed the ${e.target_name || ''} template`.replace('  ', ' '));
    case 'permissions_template_create':
      return `${actor} made the ${target} template`;
    case 'permissions_template_delete':
      return `${actor} deleted a template`;
    case 'permissions_profile':
      return withWhat(`${actor} changed ${theirs} access`);
    case 'profile_created':
      return `${actor} added ${target}`;
    case 'profile_updated':
      return `${actor} changed ${theirs} profile`;
    case 'profile_deleted':
      return `${actor} deleted ${target}`;
    case 'pin_reset':
      return self ? `${actor} changed their PIN` : `${actor} reset ${theirs} PIN`;
    case 'pin_removed':
      return `${actor} removed ${theirs} PIN`;
    case 'password_set':
      return `${actor} set ${theirs} login password`;
    case 'admin_granted':
      return `${actor} made ${target} an admin`;
    case 'admin_revoked':
      return `${actor} took admin away from ${target}`;
    case 'signed_out_everywhere':
      return self
        ? `${actor} signed out their other devices`
        : `${actor} signed ${target} out everywhere`;
    case 'invite_created':
      return `${actor} made an invite link`;
    case 'invite_revoked':
      return `${actor} revoked an invite link`;
    case 'invite_used':
      return `${e.target_name || actor} joined with an invite link`;
    case 'avatar_changed':
      return `${actor} changed ${theirs} picture`;
    case 'profile_disabled':
      return `${actor} turned ${theirs} profile off`;
    case 'profile_enabled':
      return `${actor} turned ${theirs} profile back on`;
    case 'features_changed':
      return `${actor} changed what ${target} sees`;
    case 'feature_defaults_changed':
      return `${actor} changed what new profiles see`;
    case 'device_signed_out':
      return self
        ? `${actor} signed out one of their devices`
        : `${actor} signed ${target} out on one device`;
    default:
      return `${actor}: ${String(e.action || '').replace(/_/g, ' ')}`;
  }
}

export const isAccessEntry = (e: AuditEntry) =>
  e.action.startsWith('permissions_') ||
  ['admin_granted', 'admin_revoked', 'features_changed', 'feature_defaults_changed'].includes(
    e.action,
  );

/** invites still open */
export const openInvites = (invites: Invite[]) => invites.filter((i) => i.state === 'open').length;
