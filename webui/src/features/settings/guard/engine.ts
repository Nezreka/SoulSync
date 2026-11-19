/**
 * The guard's probes: what each control on the Settings page saves when a
 * person changes it, and which saved keys it shows when the page loads.
 * Generation and the check run the same probes; the check compares what
 * comes out with manifest.json.
 *
 * The whole page re-renders in ~90 ms on every store change, so a control is
 * not probed on it. The page is mounted whole once per scenario to see what
 * is there, then once per slot (a tab and one of its parts, as the page
 * itself draws them) with only that slot drawn: the same components on the
 * same store, a few ms a render. A control no slot shows alone is probed on
 * the whole page.
 */

import { fireEvent } from '@testing-library/react';

import type { Adapter } from './adapters';

import {
  type Dict,
  type Json,
  type Leaf,
  type Payload,
  type Recorded,
  type Saves,
  type State,
  BASELINE_SETTINGS,
  SETTINGS_URL,
  applyBody,
  backend,
  changes,
  clash,
  lay,
  plain,
  savedKeys,
} from './backend';
import {
  type ButtonMark,
  type Kind,
  type Locator,
  type Value,
  BUTTON_SELECTOR,
  buttonKey,
  change,
  controlsIn,
  find,
  findAll,
  isDisabled,
  keyOf,
  kindOf,
  locateAll,
  markButton,
  otherValue,
  read,
  snapshot,
} from './driver';
import {
  type Side,
  type Unit,
  flushStore,
  mountPage,
  page,
  reload,
  settingsStore,
  settle,
  unmountPage,
} from './harness';

export interface ControlEntry extends Locator {
  kind: Kind;
  /** the sides whose page has the control */
  sides: Side[];
  /** inside a data-music-only / data-video-only wrapper */
  only?: Side;
  /** the first scenario whose page has it */
  scenario: string;
  disabled?: true;
  /** what it showed, the value the probe gave it, and what it showed after */
  from: Value;
  asked?: Value;
  to: Value;
  /** what the probe clicks first, the other fields it fills and the buttons
   *  it clicks for the control to save (review.json) */
  before?: string[];
  also?: Record<string, Value>;
  click?: string[];
  /** what changing it sends, null when it sends nothing */
  saves: Saves | null;
  /** why it sends nothing (review.json) */
  noSave?: string;
  /** the /api/settings keys it shows on load */
  shows?: string[];
  /** the GET endpoint it shows on load */
  showsEndpoint?: string;
}

export interface Scenario {
  name: string;
  side: Side;
  why?: string;
  /** laid onto the recorded settings payload, keys dotted as the store posts them */
  settings?: Record<string, Json>;
  /** fixture answers replaced, by url */
  endpoints?: Record<string, Json>;
  /** buttons clicked once the page is up, by id: what only a click draws */
  clicks?: string[];
}

/** how one control is probed when a plain change is not enough (review.json) */
export interface ProbeRule {
  /** a regular expression on the control's key */
  match: string;
  /** the value to give it, where the driver's own pick is one it refuses */
  value?: Value;
  /** clicked before the change, the nearest going up from the control (the
   *  Unlock beside a locked path) */
  before?: string[];
  /** other controls of its form filled in first, by key */
  also?: Record<string, Value>;
  /** then these clicked, in order: a button by its id (#id) or its words,
   *  the nearest one going up from the control (an explicit Save) */
  click?: string[];
  why?: string;
}

/** hand-written notes (review.json) */
export interface Review {
  probes: ProbeRule[];
  /** why a control that sends nothing sends nothing, by key pattern */
  noSave: Array<{ match: string; why: string }>;
}

const ruleFor = <T extends { match: string }>(rules: T[], key: string): T | undefined =>
  rules.find((rule) => new RegExp(rule.match).test(key));

/** a control's recorded save as the requests it was */
const requestsOf = (saves: Saves): Recorded[] => [
  ...(saves.settings ? [{ method: 'POST', url: SETTINGS_URL, body: saves.settings as Json }] : []),
  ...(saves.requests ?? []),
];

export const scenarioState = (scenario: Scenario): State => ({
  settings: applyBody(BASELINE_SETTINGS, (scenario.settings ?? {}) as Payload),
  answers: new Map(Object.entries(scenario.endpoints ?? {})),
});

function sideOnly(el: Element): Side | undefined {
  if (el.closest('[data-music-only]')) return 'music';
  if (el.closest('[data-video-only]')) return 'video';
  return undefined;
}

function split(requests: Recorded[]): Saves | null {
  const saves: Saves = {};
  for (const request of requests) {
    if (request.url === SETTINGS_URL && request.method === 'POST' && plain(request.body)) {
      const merged: Payload = saves.settings ?? {};
      for (const [section, values] of Object.entries(request.body))
        (merged as Dict)[section] = plain(values) ? { ...merged[section], ...values } : values;
      saves.settings = merged;
    } else {
      (saves.requests ??= []).push(request);
    }
  }
  return saves.settings || saves.requests ? saves : null;
}

/** how long the page is given after a change: every debounce and the 2 s autosave */
const AFTER_CHANGE_MS = 2500;

export const timing = { mounts: 0, units: 0, remounts: 0, rounds: 0, probes: 0, onPage: 0 };
/** where the time went, by step, in ms */
export const spent: Record<string, number> = {};
async function clock<T>(step: string, run: () => Promise<T> | T): Promise<T> {
  const started = performance.now();
  try {
    return await run();
  } finally {
    spent[step] = Math.round((spent[step] ?? 0) + performance.now() - started);
  }
}

export interface Found extends Locator {
  kind: Kind;
  only?: Side;
  /** how many controls the selector finds on the whole page */
  matches: number;
  /** the slot the page drew it in, when the page marks its slots */
  slot?: string;
}

export interface ScenarioRun {
  scenario: Scenario;
  /** every control on this scenario's page, by key, in page order */
  present: Map<string, Found>;
  /** the ones probed here */
  entries: Map<string, ControlEntry>;
  buttons: Map<string, ButtonMark & { count: number }>;
  /** every id on the page (and in a dialog drawn beside it) with its tag,
   *  and every data-* attribute name with the tag of one element carrying it */
  ids: Map<string, string>;
  attrs: Map<string, string>;
  /** the slot each composite widget is drawn in, by adapter name */
  slots: Record<string, Unit | undefined>;
}

const unitName = (unit: Unit) => `${unit.tab}/${unit.part ?? ''}`;
const slotOf = (wrapper: Element | null) =>
  wrapper
    ? `${wrapper.getAttribute('data-stg-react')}/${wrapper.getAttribute('data-stg-part') ?? ''}`
    : undefined;

function discover(root: HTMLElement): Map<string, Found> {
  const all = controlsIn(root);
  const present = new Map<string, Found>();
  const locators = locateAll(root, all);
  all.forEach((el, i) => {
    const loc = locators[i];
    const only = sideOnly(el);
    present.set(keyOf(loc), {
      ...loc,
      kind: kindOf(el),
      ...(only ? { only } : {}),
      matches: loc.nth === undefined ? 1 : findAll(root, loc.sel, all).length,
      slot: slotOf(el.closest('[data-stg-react]')),
    });
  });
  return present;
}

export function discoverButtons(root: HTMLElement) {
  const buttons = new Map<string, ButtonMark & { count: number }>();
  for (const el of root.querySelectorAll(BUTTON_SELECTOR)) {
    const mark = markButton(root, el);
    const key = buttonKey(mark);
    const had = buttons.get(key);
    if (had) had.count += 1;
    else buttons.set(key, { ...mark, count: 1 });
  }
  return buttons;
}

const wordsOf = (el: Element) =>
  el.getAttribute('aria-label') || (el.textContent ?? '').replace(/\s+/g, ' ').trim();

/** the button a probe clicks: the nearest going up from the control, by id
 *  (#id) or by its words; an id may also be anywhere in the document (a
 *  confirm drawn outside the page) */
export function nearestButton(from: Element, ref: string): HTMLElement | null {
  const wanted = (el: Element) =>
    ref.startsWith('#') ? el.id === ref.slice(1) : wordsOf(el) === ref;
  for (let anc = from.parentElement; anc; anc = anc.parentElement) {
    const hit = [
      ...anc.querySelectorAll<HTMLElement>(`${BUTTON_SELECTOR}, input[type="checkbox"]`),
    ].find(wanted);
    if (hit) return hit;
  }
  return null;
}

interface Candidate {
  info: Found;
  entry: ControlEntry;
  leaves: Leaf[];
}

/**
 * Every probe for the controls in `targets`, on the page as it is mounted
 * now. `remount` loads the page afresh from the backend.
 */
async function probe(
  scenario: Scenario,
  targets: Array<[string, Found]>,
  entries: Map<string, ControlEntry>,
  review: Review,
  remount: () => Promise<void>,
) {
  const base = scenarioState(scenario);
  // the controls' values, and how much page there is: a probe that leaves a
  // row behind (a ranked target added) leaves every control as it was, and
  // the next control's save would carry the row along
  const size = () => page().querySelectorAll('*').length;
  let rest = snapshot(page());
  let restSize = size();
  const same = () => {
    const now = snapshot(page());
    return (
      size() === restSize && now.length === rest.length && now.every((line, i) => line === rest[i])
    );
  };
  const others = (also: Record<string, Value> | undefined) =>
    Object.entries(also ?? {}).flatMap(([otherKey, value]) => {
      const other = find(page(), { sel: otherKey });
      return other ? [{ key: otherKey, el: other, value, was: read(other) }] : [];
    });
  // the events are fired one by one, the page drawing itself after each as
  // it does for a person; then its time passes
  const act = async (set: () => void, el?: HTMLElement, click?: string[]) => {
    await clock('change', () => {
      set();
      return settle(AFTER_CHANGE_MS);
    });
    for (const ref of click ?? []) {
      const button = el && nearestButton(el, ref);
      if (!button) throw new Error(`settings guard: no "${ref}" to click near the control`);
      fireEvent.click(button);
      await settle(AFTER_CHANGE_MS);
    }
    // the 2 s autosave has gone by now; this is for a save something held
    if (settingsStore.hasPending()) await flushStore();
  };

  // ── what a change saves ──
  for (const [key, info] of targets) {
    const el = find(page(), info);
    if (!el) throw new Error(`settings guard: ${key} left the page while others were probed`);
    const { matches: _matches, slot: _slot, ...locator } = info;
    const entry: ControlEntry = {
      ...locator,
      sides: [scenario.side],
      scenario: scenario.name,
      from: read(el),
      to: null,
      saves: null,
    };
    entries.set(key, entry);
    if (info.kind === 'hidden' || info.kind === 'file') continue;
    const rule = ruleFor(review.probes, key);
    for (const ref of rule?.before ?? []) {
      const button = nearestButton(el, ref);
      if (button) fireEvent.click(button);
    }
    if (rule?.before) entry.before = rule.before;
    if (isDisabled(el)) {
      entry.disabled = true;
      continue;
    }
    const to = rule?.value ?? otherValue(el);
    if (to === null) continue;
    entry.asked = to;
    if (rule?.also) entry.also = rule.also;
    if (rule?.click) entry.click = rule.click;
    timing.probes += 1;
    backend.take();
    const filled = others(rule?.also);
    await act(
      () => {
        for (const other of filled) change(other.el, other.value);
        change(el, to);
      },
      el,
      rule?.click,
    );
    const after = find(page(), info);
    entry.to = after ? read(after) : to;
    const requests = backend.take();
    // back to rest: the control is set back, and nothing may stay waiting in
    // the store (a save a rule held, such as a naming template the page
    // refuses, would ride along with the next control's)
    if (after && (read(after) !== entry.from || settingsStore.hasPending()))
      await act(() => {
        for (const other of filled) {
          const now = find(page(), { sel: other.key });
          if (now && read(now) !== other.was) change(now, other.was);
        }
        change(after, entry.from);
      });
    if (settingsStore.hasPending())
      throw new Error(`settings guard: ${key} left a save the store will not send`);
    entry.saves = split(requests);
    if (!entry.saves) {
      const why = ruleFor(review.noSave, key)?.why;
      if (why) entry.noSave = why;
    }
    backend.set(base);
    if (!(await clock('same', same))) {
      await clock('remount', remount);
      timing.remounts += 1;
      rest = snapshot(page());
      restSize = size();
    }
    backend.take();
  }

  // ── what it shows on load ──
  // each recorded save is laid onto the backend and the page loaded again:
  // the control must show what the probe left it showing. Laid in rounds,
  // a control in the rounds its number has a bit in, so any two controls
  // are apart in some round and one cannot pass on another's key.
  const candidates: Candidate[] = [];
  for (const [key, info] of targets) {
    const entry = entries.get(key);
    if (!entry?.saves || entry.to === entry.from) continue;
    const found = changes(base, requestsOf(entry.saves));
    if (found.length > 0) candidates.push({ info, entry, leaves: found });
  }
  if (candidates.length === 0) return;
  // a model that loads once a page only reads its endpoint on a fresh mount
  const fresh = candidates.some((c) => c.leaves.some((leaf) => leaf.where !== SETTINGS_URL));
  const load = async (state: State) => {
    backend.set(state);
    timing.rounds += 1;
    await clock('load', () => (fresh ? remount() : reload(scenario.side)));
  };
  const showing = (c: Candidate) => {
    const el = find(page(), c.info);
    return el ? read(el) : undefined;
  };
  // a control that did not show what a round expected is tried again among
  // its like (a switch another control turns off shows nothing while that
  // one is laid), then on its own
  const passed = new Set<Candidate>();
  let group = candidates;
  for (let pass = 0; pass < 2 && group.length > 0; pass += 1) {
    const out = new Set<Candidate>();
    for (let bit = 1; bit <= group.length; bit *= 2) {
      const laid: Candidate[] = [];
      let all: Leaf[] = [];
      group.forEach((c, i) => {
        if (((i + 1) & bit) === 0 || out.has(c)) return;
        if (clash(all, c.leaves)) out.add(c);
        else {
          laid.push(c);
          all = all.concat(c.leaves);
        }
      });
      if (laid.length === 0) continue;
      await load(lay(base, all));
      for (const c of group) {
        if (out.has(c)) continue;
        if (showing(c) !== (laid.includes(c) ? c.entry.to : c.entry.from)) out.add(c);
      }
    }
    for (const c of group) if (!out.has(c)) passed.add(c);
    group = [...out];
  }
  for (const c of group) {
    await load(lay(base, c.leaves));
    if (showing(c) === c.entry.to) passed.add(c);
  }
  for (const c of passed) {
    const urls = [...new Set(c.leaves.map((leaf) => leaf.where))];
    if (urls.includes(SETTINGS_URL)) c.entry.shows = savedKeys(c.entry.saves);
    const endpoint = urls.find((url) => url !== SETTINGS_URL);
    if (endpoint) c.entry.showsEndpoint = endpoint;
  }
  await load(base);
  if (!same()) await remount();
  backend.take();
}

/**
 * One scenario: its page mounted whole to see what is there, then every
 * control no earlier scenario probed, slot by slot.
 */
export async function runScenario(
  scenario: Scenario,
  known: Set<string>,
  review: Review,
  mode: 'slots' | 'page' = 'slots',
  adapters: Record<string, Adapter> = {},
): Promise<ScenarioRun> {
  backend.set(scenarioState(scenario));
  const opened = async () => {
    for (const id of scenario.clicks ?? []) {
      const button = document.getElementById(id);
      if (!button) continue;
      fireEvent.click(button);
      await settle(0);
    }
    backend.take();
  };
  const units = await clock('mount page', () => mountPage(scenario.side));
  await opened();
  timing.mounts += 1;
  const present = await clock('discover', () => discover(page()));
  const buttons = discoverButtons(page());
  const ids = new Map<string, string>();
  const attrs = new Map<string, string>();
  for (const el of document.body.querySelectorAll('*')) {
    const tag = el.tagName.toLowerCase();
    if (el.id) ids.set(el.id, tag);
    for (const attr of el.attributes)
      if (attr.name.startsWith('data-') && !attrs.has(attr.name)) attrs.set(attr.name, tag);
  }
  const slots: ScenarioRun['slots'] = {};
  for (const [name, adapter] of Object.entries(adapters)) {
    const slot = slotOf(adapter.root()?.closest('[data-stg-react]') ?? null);
    slots[name] = mode === 'slots' ? units.find((unit) => unitName(unit) === slot) : undefined;
  }
  const entries = new Map<string, ControlEntry>();
  // a control inside a wrapper for the other side waits for a scenario on
  // its own side: its section only loads there
  const todo = new Map(
    [...present].filter(
      ([key, info]) => !known.has(key) && (!info.only || info.only === scenario.side),
    ),
  );

  if (mode === 'slots' && todo.size > 0) {
    // the slots the page says hold them first (settings-page.tsx marks each
    // slot's wrapper); the rest only when something is still not placed
    const hinted = new Set([...todo.values()].map((info) => info.slot));
    const inOrder = [
      ...units.filter((unit) => hinted.has(unitName(unit))),
      ...units.filter((unit) => !hinted.has(unitName(unit))),
    ];
    for (const unit of inOrder) {
      if (todo.size === 0) break;
      const mount = async () => {
        await mountPage(scenario.side, unit);
        await opened();
      };
      await clock('mount slot', mount);
      const all = controlsIn(page());
      // the ones this slot holds every look-alike of, so a place among
      // look-alikes means here what it means on the whole page
      const mine: Array<[string, Found]> = [];
      for (const [key, info] of todo)
        if (findAll(page(), info.sel, all).length === info.matches) mine.push([key, info]);
      if (mine.length === 0) continue;
      timing.units += 1;
      for (const [key] of mine) todo.delete(key);
      await probe(scenario, mine, entries, review, mount);
    }
  }
  if (todo.size > 0) {
    // what no slot shows alone (or everything, when asked for the whole page)
    const mount = async () => {
      await mountPage(scenario.side);
      await opened();
    };
    await mount();
    timing.mounts += 1;
    timing.onPage += todo.size;
    await probe(scenario, [...todo], entries, review, mount);
  }
  unmountPage();
  // page order, whatever order the slots were probed in
  const ordered = new Map<string, ControlEntry>();
  for (const key of present.keys()) {
    const entry = entries.get(key);
    if (entry) ordered.set(key, entry);
  }
  return { scenario, present, entries: ordered, buttons, ids, attrs, slots };
}

/** a composite widget's pinned behaviour (adapters.ts) */
export interface CompositeRecord {
  side: Side;
  what: string;
  /** what it shows on the recorded payloads */
  start: string[];
  /** each thing a person did, what the widget showed after and what it sent */
  steps: Array<{ step: string; order: string[]; sent: Saves | null }>;
  /** the page, loaded again from what was saved, shows the last order */
  loads: boolean;
  /** the /api/settings keys the steps wrote */
  keys: string[];
}

/** one composite widget: its script run step by step on its slot of the page */
export async function runComposite(
  adapter: Adapter,
  scenario: Scenario,
  unit?: Unit,
): Promise<CompositeRecord> {
  const base = scenarioState(scenario);
  backend.set(base);
  const mount = async (where?: Unit) => {
    await mountPage(scenario.side, where);
    if (adapter.open && adapter.root()) {
      adapter.open();
      await settle(0);
    }
  };
  await mount(unit);
  let where = unit;
  if (!adapter.root() && unit) {
    where = undefined;
    await mount();
  }
  if (!adapter.root()) throw new Error(`settings guard: the page has no ${adapter.what}`);
  const record: CompositeRecord = {
    side: scenario.side,
    what: adapter.what,
    start: adapter.readOrder(),
    steps: [],
    loads: false,
    keys: [],
  };
  const keys = new Set<string>();
  for (const step of adapter.script) {
    backend.take();
    const did = step();
    await settle(AFTER_CHANGE_MS);
    if (settingsStore.hasPending()) await flushStore();
    const sent = split(backend.take());
    for (const key of savedKeys(sent)) keys.add(key);
    record.steps.push({ step: did, order: adapter.readOrder(), sent });
  }
  // the backend holds what the steps saved: a fresh page must show it
  const last = adapter.readOrder();
  await mount(where);
  record.loads = JSON.stringify(adapter.readOrder()) === JSON.stringify(last);
  record.keys = [...keys].sort();
  backend.set(base);
  backend.take();
  unmountPage();
  return record;
}
