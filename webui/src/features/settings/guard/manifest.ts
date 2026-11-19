/**
 * manifest.json: what the guard recorded from the page, how the runs of the
 * scenarios are gathered into it, and how a later run is held against it.
 * Nothing here loads the page.
 */

import type { ButtonMark } from './driver';
import type { CompositeRecord, ControlEntry, Review, Scenario, ScenarioRun } from './engine';
import type { Side } from './harness';

import { ADAPTERS } from './adapters';
import { type Json, BASELINE_SETTINGS, leaves, savedKeys } from './backend';

export interface ButtonEntry extends ButtonMark {
  count: number;
  sides: Side[];
  /** the first scenario whose page has it */
  scenario: string;
}

export interface Manifest {
  about: string;
  scenarios: Array<{ name: string; side: Side; why?: string }>;
  controls: Record<string, ControlEntry>;
  composites: Record<string, CompositeRecord>;
  buttons: Record<string, ButtonEntry>;
  /** recorded /api/settings keys no control shows, each with why */
  unshownKeys: Record<string, string>;
  /** the audit's ids: the ones that are controls here, and why the rest are not */
  audit: { controls: string[]; auditOnly: Record<string, string> };
}

/** review.json: everything in the manifest a person wrote */
export interface ReviewFile extends Review {
  unshownKeys: Record<string, string>;
  auditOnly: Record<string, string>;
}

export const NOT_REVIEWED = 'NOT REVIEWED';
/** the two scenarios that are the recorded payloads as they are, one per side */
export const BASELINES = ['music', 'video'];

/** a control is settled by a scenario once it saved something there; one
 *  that sent nothing may only be unloaded on that page (the video sections
 *  on the music side), a disabled one may only be waiting on another setting */
const settled = (entry: ControlEntry) =>
  !!entry.saves || entry.kind === 'hidden' || entry.kind === 'file';

export interface Generated {
  /** which sides' pages have each control, whichever scenario probed it */
  seen: Map<string, Set<Side>>;
  controls: Map<string, ControlEntry>;
  composites: Map<string, CompositeRecord>;
  buttons: Map<string, ButtonEntry>;
  ids: Map<string, string>;
  attrs: Map<string, string>;
}

/** fold one scenario's run into what has been gathered so far */
/** where two request bodies part: the keys that differ, not two long lines */
function bodyDiff(was: unknown, now: unknown): string {
  const plain = (v: unknown): v is Record<string, unknown> =>
    !!v && typeof v === 'object' && !Array.isArray(v);
  if (!plain(was) || !plain(now)) return `was ${show(was)}, now ${show(now)}`;
  const keys = [...new Set([...Object.keys(was), ...Object.keys(now)])].filter(
    (key) => !same(was[key], now[key]),
  );
  return keys
    .slice(0, 6)
    .map((key) => `${key} was ${show(was[key])}, now ${show(now[key])}`)
    .join('; ');
}

export function fold(into: Generated, run: ScenarioRun) {
  const { scenario } = run;
  for (const [key, entry] of run.entries) {
    const had = into.controls.get(key);
    if (!had || (!settled(had) && settled(entry))) into.controls.set(key, entry);
  }
  for (const key of run.present.keys()) {
    const sides = into.seen.get(key) ?? new Set<Side>();
    into.seen.set(key, sides.add(scenario.side));
  }
  for (const [key, mark] of run.buttons) {
    const had = into.buttons.get(key);
    if (!had) into.buttons.set(key, { ...mark, sides: [scenario.side], scenario: scenario.name });
    else {
      had.count = Math.max(had.count, mark.count);
      if (!had.sides.includes(scenario.side))
        had.sides = [...had.sides, scenario.side].sort() as Side[];
    }
  }
  for (const [id, tag] of run.ids) if (!into.ids.has(id)) into.ids.set(id, tag);
  for (const [name, tag] of run.attrs) if (!into.attrs.has(name)) into.attrs.set(name, tag);
}

/** the composite widgets a scenario runs: each on its side's baseline */
export const adaptersFor = (scenario: Scenario) =>
  Object.fromEntries(
    Object.entries(ADAPTERS).filter(
      ([, adapter]) => BASELINES.includes(scenario.name) && adapter.side === scenario.side,
    ),
  );

export const emptyGenerated = (): Generated => ({
  seen: new Map(),
  controls: new Map(),
  composites: new Map(),
  buttons: new Map(),
  ids: new Map(),
  attrs: new Map(),
});

/** the controls the scenarios so far have settled: the next one leaves them alone */
export const settledKeys = (generated: Generated) =>
  [...generated.controls].filter(([, entry]) => settled(entry)).map(([key]) => key);

/** once every scenario is in: each control's sides are every side that had it */
export function finish(generated: Generated) {
  for (const [key, entry] of generated.controls)
    entry.sides = [...(generated.seen.get(key) ?? entry.sides)].sort() as Side[];
}

/** the recorded payload's keys that some control or composite shows */
export function shownKeys(manifest: Pick<Manifest, 'controls' | 'composites'>): Set<string> {
  const shown = new Set<string>();
  for (const entry of Object.values(manifest.controls))
    for (const key of entry.shows ?? []) shown.add(key);
  for (const composite of Object.values(manifest.composites))
    if (composite.loads) for (const key of composite.keys) shown.add(key);
  return shown;
}

/** a leaf is shown when a control shows it, or a dict above or below it */
const covers = (shown: Set<string>, leaf: string) =>
  [...shown].some(
    (key) => key === leaf || leaf.startsWith(`${key}.`) || key.startsWith(`${leaf}.`),
  );

export const payloadKeys = () => leaves(BASELINE_SETTINGS as Json).sort();

export function unshown(manifest: Pick<Manifest, 'controls' | 'composites'>): string[] {
  const shown = shownKeys(manifest);
  return payloadKeys().filter((leaf) => !covers(shown, leaf));
}

/** the keys controls post to /api/settings */
export function savedBy(manifest: Pick<Manifest, 'controls'>): Set<string> {
  const saved = new Set<string>();
  for (const entry of Object.values(manifest.controls))
    for (const key of savedKeys(entry.saves)) saved.add(key);
  return saved;
}

// ── holding a run against the manifest ──

const show = (value: unknown) => {
  const text = JSON.stringify(value);
  return text === undefined ? 'nothing' : text.length > 160 ? `${text.slice(0, 160)}…` : text;
};
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** what is different about one control, in words; empty when nothing is */
export function compare(key: string, was: ControlEntry, now: ControlEntry): string[] {
  const out: string[] = [];
  const say = (text: string) => out.push(`control ${key} ${text}`);
  const before = new Map(Object.entries(savedLeaves(was)));
  const after = new Map(Object.entries(savedLeaves(now)));
  for (const [leaf, value] of before) {
    if (!after.has(leaf)) say(`no longer sends ${leaf}`);
    else if (!same(value, after.get(leaf)))
      say(`sends a different ${leaf}: was ${show(value)}, now ${show(after.get(leaf))}`);
  }
  for (const leaf of after.keys()) if (!before.has(leaf)) say(`now also sends ${leaf}`);
  const calls = (entry: ControlEntry) =>
    (entry.saves?.requests ?? []).map((r) => `${r.method} ${r.url}`);
  const wasCalls = calls(was);
  const nowCalls = calls(now);
  for (const call of new Set(wasCalls))
    if (!nowCalls.includes(call)) say(`no longer calls ${call}`);
  for (const call of new Set(nowCalls)) if (!wasCalls.includes(call)) say(`now also calls ${call}`);
  if (same(wasCalls, nowCalls)) {
    (was.saves?.requests ?? []).forEach((request, i) => {
      const body = now.saves?.requests?.[i]?.body;
      if (!same(request.body, body))
        say(
          `sends a different body to ${request.method} ${request.url}: ${bodyDiff(request.body, body)}`,
        );
    });
  } else if (same([...wasCalls].sort(), [...nowCalls].sort())) {
    say(`makes its calls in a different order: was ${show(wasCalls)}, now ${show(nowCalls)}`);
  }
  for (const leaf of was.shows ?? [])
    if (!(now.shows ?? []).includes(leaf)) say(`no longer shows ${leaf} when the page loads`);
  for (const leaf of now.shows ?? [])
    if (!(was.shows ?? []).includes(leaf)) say(`now shows ${leaf} when the page loads`);
  if (was.showsEndpoint !== now.showsEndpoint)
    say(
      `${now.showsEndpoint ? `shows ${now.showsEndpoint}` : 'shows no endpoint'} when the page loads (was ${was.showsEndpoint ?? 'none'})`,
    );
  const fields = [
    'kind',
    'sel',
    'nth',
    'weak',
    'only',
    'scenario',
    'disabled',
    'from',
    'asked',
    'to',
    'also',
    'click',
    'sides',
  ] as const;
  for (const field of fields)
    if (!same(was[field], now[field]))
      say(`has a different ${field}: was ${show(was[field])}, now ${show(now[field])}`);
  return out;
}

function savedLeaves(entry: ControlEntry): Record<string, Json> {
  const out: Record<string, Json> = {};
  for (const [section, values] of Object.entries(entry.saves?.settings ?? {})) {
    if (!values || typeof values !== 'object' || Array.isArray(values)) out[section] = values;
    else for (const [name, value] of Object.entries(values)) out[`${section}.${name}`] = value;
  }
  return out;
}

/**
 * One scenario's run held against the manifest: every control the manifest
 * records under it, every control the page has that the manifest does not,
 * which side each is on, and the buttons.
 */
export function compareRun(manifest: Manifest, run: ScenarioRun, part?: Part): string[] {
  const { scenario } = run;
  const out: string[] = [];
  const mine = part ? new Set(partKeys(manifest, scenario, part)) : null;
  // the page as a whole (who is on it, the buttons) is the first part's
  const whole = !part || part.index === 0;
  for (const [key, was] of Object.entries(manifest.controls)) {
    const here = run.present.has(key);
    if (was.scenario === scenario.name && (!mine || mine.has(key))) {
      const now = run.entries.get(key);
      if (!now) {
        out.push(
          `control ${key} is gone from the page (${scenario.name}): it ${describe(was)}. If that is meant, regenerate the manifest.`,
        );
        continue;
      }
      // the sides come from every scenario together; here only this one's
      out.push(...compare(key, was, { ...now, sides: was.sides }));
    }
    // a baseline's page has every control the baselines recorded for its side
    if (whole && isBaseline(scenario) && BASELINES.includes(was.scenario)) {
      const listed = was.sides.includes(scenario.side);
      if (listed && !here)
        out.push(`control ${key} is no longer on the ${scenario.side} side's page`);
      if (!listed && here)
        out.push(`control ${key} is now on the ${scenario.side} side's page too`);
    }
  }
  for (const key of run.entries.keys())
    if (!(key in manifest.controls))
      out.push(
        `control ${key} is new on the page (${scenario.name}): add it to the manifest (regenerate it, see the header of settings-guard.test.tsx)`,
      );
  for (const [key, was] of Object.entries(whole ? manifest.buttons : {})) {
    const expected =
      was.scenario === scenario.name || (isBaseline(scenario) && was.sides.includes(scenario.side));
    if (!expected) continue;
    const now = run.buttons.get(key);
    if (!now) out.push(`button ${key} is gone from the page (${scenario.name})`);
    else if (now.count < was.count)
      out.push(`button ${key}: ${was.count} of them before, ${now.count} now (${scenario.name})`);
  }
  return out;
}

const isBaseline = (scenario: Scenario) => BASELINES.includes(scenario.name);

function describe(entry: ControlEntry): string {
  const keys = savedKeys(entry.saves);
  const calls = (entry.saves?.requests ?? []).map((r) => `${r.method} ${r.url}`);
  const what = [...keys, ...calls];
  return what.length ? `saved ${what.join(', ')}` : 'saved nothing';
}

/** a scenario's check split over test files that run side by side: this is
 *  part `index` of `of` */
export interface Part {
  index: number;
  of: number;
}

/** the controls of a scenario that one part checks: an even share, in page order */
export function partKeys(manifest: Manifest, scenario: Scenario, part: Part): string[] {
  const keys = Object.entries(manifest.controls)
    .filter(([, entry]) => entry.scenario === scenario.name)
    .map(([key]) => key);
  return keys.filter((_, i) => Math.floor((i * part.of) / keys.length) === part.index);
}

/** the controls a check leaves alone: the ones the manifest records under
 *  another scenario, or that another part of this one checks */
export function knownElsewhere(manifest: Manifest, scenario: Scenario, part?: Part): string[] {
  const mine = part ? new Set(partKeys(manifest, scenario, part)) : null;
  return Object.entries(manifest.controls)
    .filter(([key, entry]) => entry.scenario !== scenario.name || (mine && !mine.has(key)))
    .map(([key]) => key);
}

/** what is different about one composite widget, in words */
export function compareComposite(
  name: string,
  was: CompositeRecord,
  now: CompositeRecord,
): string[] {
  const out: string[] = [];
  const say = (text: string) => out.push(`composite ${name} (${was.what}) ${text}`);
  if (!same(was.start, now.start))
    say(`shows a different order on load: was ${show(was.start)}, now ${show(now.start)}`);
  was.steps.forEach((step, i) => {
    const got = now.steps[i];
    if (!got || got.step !== step.step)
      return say(`step ${i + 1} was "${step.step}", now "${got?.step ?? 'nothing'}"`);
    if (!same(step.order, got.order))
      say(`after "${step.step}" shows ${show(got.order)}, was ${show(step.order)}`);
    if (!same(step.sent, got.sent))
      say(`on "${step.step}" sends ${show(got.sent)}, was ${show(step.sent)}`);
  });
  if (was.loads !== now.loads)
    say(
      now.loads
        ? 'now shows what it saved on a fresh load'
        : 'no longer shows what it saved on a fresh load',
    );
  return out;
}
