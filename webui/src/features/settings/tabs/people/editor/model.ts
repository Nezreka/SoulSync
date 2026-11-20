/**
 * The editor's pure half: the same layers core/permissions.py builds
 * (policy ceiling > template > personal changes = effective), run on the
 * draft so every row, dot, count and the summary follow the form before
 * anything is saved. The server stays the judge: save sends only the
 * differences and the sheet redraws from what comes back.
 */

import type {
  Catalogue,
  CatalogueRow,
  CategoryId,
  Count,
  IdList,
  PermMap,
  ProfilePermissions,
  QualityOption,
  QualityOptions,
  SaveBody,
  Template,
  Value,
} from './types';

export const ALL = '*';
export const FOLLOW = 'follow';
export const MODES = ['none', 'ask', 'download'];
export const RATINGS = ['G', 'PG', 'PG-13', 'R', 'any'];
const TV_RATING_LABELS: Record<string, string> = {
  G: 'TV-Y7',
  PG: 'TV-PG',
  'PG-13': 'TV-14',
  R: 'TV-MA',
  any: 'Any',
};

/** the draft: what the admin has picked so far */
export interface Draft {
  /** the template followed, null while on their own settings (legacy) */
  templateId: number | null;
  /** personal changes, only rows that differ from the template */
  overrides: PermMap;
  isAdmin: boolean;
}

export interface Model {
  catalogue: Catalogue;
  rowsById: Map<string, CatalogueRow>;
  /** stored rows, in catalogue order */
  stored: CatalogueRow[];
  policy: PermMap;
  templates: Template[];
  saved: ProfilePermissions;
  /** what the profile ran on before any template: its old columns */
  legacyBase: PermMap;
  quality: QualityOptions;
}

/** a value as plain text: strings stay, anything else is its json */
export function str(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return `${v}`;
  return JSON.stringify(v ?? null);
}

export function canon(v: unknown): string {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    return JSON.stringify(
      Object.fromEntries(
        Object.keys(o)
          .sort()
          .map((k) => [k, o[k]]),
      ),
    );
  }
  return JSON.stringify(v ?? null);
}

export const same = (a: unknown, b: unknown) => canon(a) === canon(b);

function copy<T>(v: T): T {
  if (Array.isArray(v)) return [...v] as T;
  if (v && typeof v === 'object') return { ...(v as object) } as T;
  return v;
}

export function buildModel(
  catalogue: Catalogue,
  templates: Template[],
  saved: ProfilePermissions,
  quality: QualityOptions,
): Model {
  const rowsById = new Map(catalogue.rows.map((r) => [r.id, r]));
  const stored = catalogue.rows.filter((r) => r.stored);
  const policy: PermMap = {};
  const legacyBase: PermMap = {};
  for (const r of saved.rows) {
    policy[r.id] = r.ceiling;
    legacyBase[r.id] = r.template_value;
  }
  return { catalogue, rowsById, stored, policy, templates, saved, legacyBase, quality };
}

export function initialDraft(saved: ProfilePermissions): Draft {
  return {
    templateId: saved.mode === 'template' && saved.template ? saved.template.id : null,
    overrides: saved.mode === 'template' ? { ...saved.overrides } : {},
    isAdmin: saved.mode === 'admin' || !!saved.profile.is_admin,
  };
}

/** the template's full map, or the old columns while on their own settings */
export function baseFor(model: Model, templateId: number | null): PermMap {
  if (templateId == null) return model.legacyBase;
  const t = model.templates.find((x) => x.id === templateId);
  const out: PermMap = {};
  for (const r of model.stored) {
    const v = t?.perms?.[r.id];
    out[r.id] = v === undefined ? (r.default as Value) : v;
  }
  return out;
}

export function templateName(model: Model, templateId: number | null): string {
  if (templateId == null) return 'their own settings';
  return model.templates.find((t) => t.id === templateId)?.name ?? 'the template';
}

/** only the rows that differ from the base */
export function diffOverrides(base: PermMap, wanted: PermMap): PermMap {
  const out: PermMap = {};
  for (const [id, v] of Object.entries(wanted)) if (!same(base[id], v)) out[id] = copy(v);
  return out;
}

const rate = (c: Count) => c.n / c.days;

/** one row held under the policy (core.permissions.clamp) */
export function clamp(row: CatalogueRow, value: Value, ceiling: Value): Value {
  switch (row.kind) {
    case 'bool':
      return !!value && !!ceiling;
    case 'restrict':
      return !!value || !!ceiling;
    case 'mode': {
      const order = MODES;
      return order.indexOf(str(value)) <= order.indexOf(str(ceiling)) ? value : ceiling;
    }
    case 'rating':
      return RATINGS.indexOf(str(value)) <= RATINGS.indexOf(str(ceiling)) ? value : ceiling;
    case 'count': {
      const v = value as Count;
      const c = ceiling as Count;
      if (!c?.n) return { ...v };
      if (!v.n || rate(v) > rate(c)) return { ...c };
      return { ...v };
    }
    case 'set': {
      if (ceiling === ALL) return copy(value);
      if (value === ALL) return [...((ceiling as IdList) ?? [])];
      return ((value as IdList) ?? []).filter((x) => (ceiling as IdList).includes(x));
    }
    case 'set_flag': {
      const out: IdList = [];
      for (const x of [...((value as IdList) ?? []), ...((ceiling as IdList) ?? [])]) {
        if (!out.includes(x)) out.push(x);
      }
      return out;
    }
    default:
      return value;
  }
}

export interface Resolved {
  /** template or personal change, before the policy */
  raw: PermMap;
  effective: PermMap;
  base: PermMap;
}

/** core.permissions._resolve on the draft */
export function resolve(model: Model, draft: Draft): Resolved {
  const base = baseFor(model, draft.templateId);
  if (draft.isAdmin || (draft.templateId == null && model.saved.mode !== 'legacy')) {
    // admins skip every layer: nothing to clamp, the server's map is the answer
    return { raw: { ...model.saved.effective }, effective: { ...model.saved.effective }, base };
  }
  const raw: PermMap = {};
  for (const r of model.stored) {
    raw[r.id] = copy(r.id in draft.overrides ? draft.overrides[r.id] : base[r.id]);
  }
  const eff: PermMap = {};
  for (const r of model.stored) {
    if (r.kind === 'choice' || r.id === 'tv.max_rating') continue;
    eff[r.id] = clamp(r, raw[r.id], model.policy[r.id]);
  }
  const tvRow = model.rowsById.get('tv.max_rating');
  if (tvRow) {
    let tvCeiling = model.policy['tv.max_rating'];
    if (tvCeiling === FOLLOW) tvCeiling = model.policy['movies.max_rating'];
    let tvRaw = raw['tv.max_rating'];
    if (tvRaw === FOLLOW) tvRaw = eff['movies.max_rating'];
    eff['tv.max_rating'] = clamp(tvRow, tvRaw, tvCeiling);
  }
  for (const r of model.stored) {
    if (r.kind !== 'choice') continue;
    const pick = raw[r.id] != null ? raw[r.id] : model.policy[r.id];
    const allowed = eff[r.set_row ?? ''];
    eff[r.id] =
      pick != null &&
      (allowed === ALL || (Array.isArray(allowed) && allowed.includes(pick as number)))
        ? pick
        : null;
  }
  return { raw, effective: eff, base };
}

export const isAdminView = (draft: Draft) => draft.isAdmin;

/** the categories a row shows in */
export function rowCategories(row: CatalogueRow): CategoryId[] {
  return [row.category, ...(row.also_in ?? [])];
}

export function rowsIn(model: Model, cat: CategoryId): CatalogueRow[] {
  return model.catalogue.rows.filter((r) => rowCategories(r).includes(cat));
}

/** ids that hold a personal change, per category (the rail's green dots) */
export function changedCategories(model: Model, draft: Draft): Set<CategoryId> {
  const out = new Set<CategoryId>();
  for (const id of Object.keys(draft.overrides)) {
    const row = model.rowsById.get(id);
    if (row) for (const c of rowCategories(row)) out.add(c);
  }
  return out;
}

export function changeCount(draft: Draft): number {
  return Object.keys(draft.overrides).length;
}

/* ── edits ─────────────────────────────────────────────────────────────── */

export function setValue(model: Model, draft: Draft, id: string, value: Value): Draft {
  const base = baseFor(model, draft.templateId);
  const overrides = { ...draft.overrides };
  if (same(base[id], value)) delete overrides[id];
  else overrides[id] = copy(value);
  return { ...draft, overrides };
}

export function resetRows(draft: Draft, ids: string[]): Draft {
  const overrides = { ...draft.overrides };
  for (const id of ids) delete overrides[id];
  return { ...draft, overrides };
}

export function resetCategory(model: Model, draft: Draft, cat: CategoryId): Draft {
  return resetRows(
    draft,
    model.stored.filter((r) => rowCategories(r).includes(cat)).map((r) => r.id),
  );
}

export function resetAll(draft: Draft): Draft {
  return { ...draft, overrides: {} };
}

/** "Follows" changed: personal changes stay, the ones that now equal the
 *  new template drop away (store.change_profile). on their own settings,
 *  every old column that differs comes along as a change. */
export function switchTemplate(model: Model, draft: Draft, templateId: number): Draft {
  const next = baseFor(model, templateId);
  const carried =
    draft.templateId == null && !draft.isAdmin && model.saved.mode === 'legacy'
      ? { ...model.legacyBase, ...draft.overrides }
      : draft.overrides;
  return { ...draft, templateId, overrides: diffOverrides(next, carried) };
}

/* ── saving ────────────────────────────────────────────────────────────── */

/** the exact PUT body that turns what's saved into the draft */
export function saveBody(model: Model, draft: Draft): SaveBody | null {
  const saved = model.saved;
  const savedTid = saved.mode === 'template' && saved.template ? saved.template.id : null;
  const body: SaveBody = { set: {}, reset: [] };
  let kept: PermMap = saved.mode === 'template' ? { ...saved.overrides } : {};
  if (draft.templateId != null && draft.templateId !== savedTid) {
    body.template_id = draft.templateId;
    if (saved.mode !== 'template') {
      // from their own settings or from admin: start clean on the new
      // template, the draft already carries every old setting worth keeping
      body.reset = 'all';
      body.set = { ...draft.overrides };
      return body;
    }
    // what the server keeps through the switch: changes that still differ
    const base = baseFor(model, draft.templateId);
    kept = diffOverrides(base, kept);
  }
  if (saved.mode === 'legacy' && draft.templateId == null) {
    // still on their own settings: the server moves them onto the closest
    // template with their columns as changes, then these go on top
    body.set = { ...draft.overrides };
    return Object.keys(body.set).length ? body : null;
  }
  const want = draft.overrides;
  const reset = Object.keys(kept).filter((id) => !(id in want));
  for (const [id, v] of Object.entries(want)) if (!same(kept[id], v)) body.set[id] = copy(v);
  if (reset.length && !Object.keys(want).length) body.reset = 'all';
  else body.reset = reset;
  const empty = !Object.keys(body.set).length && !body.reset.length && body.template_id == null;
  return empty ? null : body;
}

/** how many rows the save would change, for the footer */
export function unsavedCount(model: Model, draft: Draft): number {
  const fresh = initialDraft(model.saved);
  let n = 0;
  if (draft.isAdmin !== fresh.isAdmin) n += 1;
  if (draft.isAdmin) return n;
  if (draft.templateId !== fresh.templateId) n += 1;
  const ids = new Set([...Object.keys(fresh.overrides), ...Object.keys(draft.overrides)]);
  for (const id of ids) if (!same(fresh.overrides[id], draft.overrides[id])) n += 1;
  return n;
}

/* ── words ─────────────────────────────────────────────────────────────── */

export function daysWord(days: number): string {
  if (days === 1) return 'day';
  if (days === 7) return 'week';
  if (days === 30) return 'month';
  if (days === 365) return 'year';
  return `${days} days`;
}

export function countWords(c: Count | null | undefined): string {
  if (!c || !c.n) return 'No limit';
  return `${c.n} per ${daysWord(c.days)}`;
}

export function ratingWord(row: CatalogueRow, v: Value, movies?: Value): string {
  if (v === FOLLOW) return `Same as movies${movies ? ` (${TV_RATING_LABELS[str(movies)]})` : ''}`;
  if (v === 'any') return 'Any';
  if (row.id.startsWith('tv.')) return TV_RATING_LABELS[str(v)] ?? str(v);
  return str(v);
}

export function qualityOptionsFor(model: Model, media: string | null): QualityOption[] {
  if (media === 'movie') return model.quality.movies;
  if (media === 'tv') return model.quality.tv;
  if (media === 'music') return model.quality.music;
  return [];
}

function names(opts: QualityOption[], ids: IdList): string {
  const out = ids.map((id) =>
    id === '4k' ? 'every 4K profile' : (opts.find((o) => o.id === id)?.name ?? `#${id}`),
  );
  return out.length ? out.join(', ') : 'none';
}

/** a value in the words a row uses ("Teen: Ask first") */
export function valueWords(model: Model, row: CatalogueRow, v: Value): string {
  switch (row.kind) {
    case 'bool':
    case 'restrict':
      return v ? 'on' : 'off';
    case 'mode':
      return modeLabel(row, str(v));
    case 'rating':
      return ratingWord(row, v);
    case 'count':
      return countWords(v as Count);
    case 'set':
      return v === ALL
        ? 'all of them'
        : names(qualityOptionsFor(model, row.media), (v as IdList) ?? []);
    case 'choice':
      return v == null ? 'none picked' : names(qualityOptionsFor(model, row.media), [v as number]);
    case 'set_flag':
      return names(qualityOptionsFor(model, row.media), (v as IdList) ?? []);
    default:
      return str(v);
  }
}

/** why the policy holds a row down, in plain words */
export function lockWords(model: Model, row: CatalogueRow): string {
  const c = model.policy[row.id];
  switch (row.kind) {
    case 'bool':
      return c ? '' : 'Global policy keeps this for admins.';
    case 'restrict':
      return c ? 'Global policy keeps this on for everyone.' : '';
    case 'mode':
      return c === row.values[0]
        ? `Global policy: ${modeLabel(row, str(c)).toLowerCase()}.`
        : `Global policy: up to ${modeLabel(row, str(c))}.`;
    case 'rating':
      return c === 'any' || c === FOLLOW ? '' : `Global policy: up to ${ratingWord(row, c)}.`;
    case 'count':
      return (c as Count)?.n ? `Global policy: at most ${countWords(c as Count)}.` : '';
    default:
      return '';
  }
}

/** does the policy stop some of this row's choices? */
export function ceilingBites(model: Model, row: CatalogueRow): boolean {
  const c = model.policy[row.id];
  switch (row.kind) {
    case 'bool':
      return c === false;
    case 'restrict':
      return c === true;
    case 'mode':
      return c !== row.values[row.values.length - 1];
    case 'rating':
      return c !== 'any' && c !== FOLLOW;
    case 'count':
      return !!(c as Count)?.n;
    default:
      return false;
  }
}

/** a row whose control can't change anything: the policy pins it */
export function fullyLocked(model: Model, row: CatalogueRow): boolean {
  const c = model.policy[row.id];
  if (row.kind === 'bool') return c === false;
  if (row.kind === 'restrict') return c === true;
  if (row.kind === 'mode') return c === row.values[0];
  if (row.kind === 'rating') return c === 'G';
  return false;
}

/** "Turn on Audiobooks first." when nothing it needs is on, else '' */
export function dependsWords(model: Model, row: CatalogueRow, eff: PermMap): string {
  if (!row.needs?.length) return '';
  if (row.needs.some((id) => !!eff[id])) return '';
  const labels = row.needs.map((id) => model.rowsById.get(id)?.label ?? id);
  return `Turn on ${labels.join(' or ')} first.`;
}

/** lowercase a label unless it reads like a name ("Sample Studio") */
export function softLabel(label: string): string {
  const caps = (label.match(/[A-Z]/g) ?? []).length;
  return caps > 1 ? label : label.charAt(0).toLowerCase() + label.slice(1);
}

/** what the picked mode means, in the row's own words */
export function modeHelp(row: CatalogueRow, v: Value): string {
  const verb = row.id.startsWith('movies.') || row.id.startsWith('tv.') ? 'watch' : 'listen to';
  const podcast = row.id.startsWith('podcasts.');
  if (v === 'download') {
    const tail = row.id.startsWith('books.') || podcast ? '' : ' Never counts toward their limit.';
    return podcast
      ? 'Auto-approve: new episodes download as they come out.'
      : `Auto-approve: it downloads the moment they add it.${tail}`;
  }
  if (v === 'ask') {
    if (podcast)
      return "Require approval: they ask for episodes, and each one lands in your Requests. New episodes don't download on their own.";
    return `Require approval: what they pick lands in your Requests.${row.id === 'tv.get' ? ' A season or a whole series is one request.' : ''}`;
  }
  if (podcast) return 'Follow only: they see new episodes, nothing downloads for them.';
  return `Requests off: they ${verb} what's here, nothing new comes in.`;
}

/** the segmented control's words (#2192); a podcast's none is Follow only */
export function modeLabel(row: CatalogueRow, v: string): string {
  if (row.id === 'podcasts.get' && v === 'none') return 'Follow only';
  return (
    row.value_labels?.[v] ??
    { download: 'Auto-approve', ask: 'Require approval', none: 'Requests off' }[v] ??
    v
  );
}
