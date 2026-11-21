/**
 * What the guard's test files share: reading and writing manifest.json,
 * building it from a run, the checks that need no page, and the numbers.
 */

import { execSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { vi } from 'vitest';

import type { CompositeRecord, ControlEntry, Scenario } from './engine';
import type { Result } from './run';

import { type Json, savedKeys } from './backend';
import {
  type Generated,
  type Manifest,
  type Part,
  type ReviewFile,
  NOT_REVIEWED,
  adaptersFor,
  compareComposite,
  compareRun,
  emptyGenerated,
  finish,
  fold,
  knownElsewhere,
  settledKeys,
  payloadKeys,
  savedBy,
  shownKeys,
  unshown,
} from './manifest';
import manifestJson from './manifest.json';
import reviewJson from './review.json';
import { SCENARIOS } from './scenarios';

const HERE = dirname(fileURLToPath(import.meta.url));
const MANIFEST_PATH = join(HERE, 'manifest.json');
/** the phase 0 audit, when this checkout has it (scrap/ is not in git) */
const AUDIT_DIR = join(HERE, '../../../../../scrap/settings-bic/audit-parts');

/** a request body is kept as one line of JSON in the file (a whole form is
 *  sixty keys, and four hundred controls send one), and read back here */
function eachBody(m: Manifest, turn: (body: Json) => Json): Manifest {
  const copy = structuredClone(m);
  const saves = [
    ...Object.values(copy.controls).map((entry) => entry.saves),
    ...Object.values(copy.composites).flatMap((c) => c.steps.map((step) => step.sent)),
  ];
  for (const sent of saves)
    for (const request of sent?.requests ?? []) request.body = turn(request.body);
  return copy;
}
const pack = (m: Manifest) => eachBody(m, (body) => JSON.stringify(body));
const unpack = (m: Manifest) =>
  eachBody(m, (body) => (typeof body === 'string' ? (JSON.parse(body) as Json) : body));

export const manifest = unpack(manifestJson as unknown as Manifest);
export const review = reviewJson as unknown as ReviewFile;
/** SETTINGS_GUARD=write records the page into manifest.json */
export const WRITE = process.env.SETTINGS_GUARD === 'write';
/** SETTINGS_GUARD_MOUNT=page probes every control on the whole page (slow) */
export const MOUNT = process.env.SETTINGS_GUARD_MOUNT === 'page' ? 'page' : 'slots';

const ruleFor = (rules: Array<{ match: string; why: string }>, key: string) =>
  rules.find((rule) => new RegExp(rule.match).test(key))?.why;

/** the ids the audit's tables name, in the order it names them */
export function auditIds(): string[] | null {
  if (!existsSync(AUDIT_DIR)) return null;
  const ids: string[] = [];
  for (const file of readdirSync(AUDIT_DIR).sort()) {
    if (!file.endsWith('.md') || file === 'house-style.md') continue;
    let column: number | null = null;
    for (const line of readFileSync(join(AUDIT_DIR, file), 'utf8').split(/\r?\n/)) {
      if (!line.startsWith('|')) {
        column = null;
        continue;
      }
      const cells = line
        .trim()
        .replace(/^\||\|$/g, '')
        .split('|')
        .map((cell) => cell.trim());
      if (column === null) {
        column = cells.findIndex((cell) => cell.toLowerCase().startsWith('id'));
        continue;
      }
      if (/^[|\-: ]+$/.test(line.trim()) || column < 0 || column >= cells.length) continue;
      for (const [, token] of cells[column].matchAll(/`([^`]+)`/g)) {
        const id = token.trim().match(/^#?([A-Za-z][\w-]*)$/)?.[1];
        if (id && !ids.includes(id)) ids.push(id);
      }
    }
  }
  return ids;
}

/** each audit id: a control in the manifest, or why it is not one */
function crossCheck(ids: string[], generated: Generated): Manifest['audit'] {
  const keys = [...generated.controls.keys()];
  const buttonIds = new Set([...generated.buttons.values()].map((b) => b.id));
  const controls: string[] = [];
  const auditOnly: Record<string, string> = {};
  for (const id of ids) {
    if (generated.controls.has(`#${id}`) || keys.some((key) => key.includes(`[${id}`))) {
      controls.push(id);
    } else if (review.auditOnly[id]) {
      auditOnly[id] = review.auditOnly[id];
    } else if (buttonIds.has(id)) {
      auditOnly[id] = 'a button, held in `buttons`';
    } else if (generated.ids.has(id)) {
      auditOnly[id] = `not a form control: a <${generated.ids.get(id)}> on the page`;
    } else if (generated.attrs.has(id)) {
      auditOnly[id] = `an attribute of a <${generated.attrs.get(id)}>, which is not a form control`;
    } else {
      auditOnly[id] = NOT_REVIEWED;
    }
  }
  return { controls, auditOnly };
}

export function build(generated: Generated): Manifest {
  const composites: Record<string, CompositeRecord> = Object.fromEntries(generated.composites);
  const controls: Record<string, ControlEntry> = {};
  for (const [key, entry] of generated.controls) {
    if (!entry.saves && entry.kind !== 'hidden')
      entry.noSave = ruleFor(review.noSave, key) ?? NOT_REVIEWED;
    controls[key] = entry;
  }
  const next: Manifest = {
    about:
      'Generated by settings-guard.test.tsx (SETTINGS_GUARD=write). Do not edit by hand: the words come from review.json.',
    scenarios: SCENARIOS.map(({ name, side, why }) => ({ name, side, why })),
    controls,
    composites,
    buttons: Object.fromEntries(generated.buttons),
    unshownKeys: {},
    audit: manifest.audit ?? { controls: [], auditOnly: {} },
  };
  for (const key of unshown(next)) next.unshownKeys[key] = review.unshownKeys[key] ?? NOT_REVIEWED;
  const ids = auditIds();
  if (ids) next.audit = crossCheck(ids, generated);
  return next;
}

export function save(next: Manifest) {
  writeFileSync(MANIFEST_PATH, `${JSON.stringify(pack(next), null, 2)}\n`);
  // the repo's formatter checks json too (npm run check)
  try {
    execSync(`npx oxfmt "${MANIFEST_PATH}"`, { cwd: join(HERE, '../../../..'), stdio: 'ignore' });
  } catch {
    console.warn('settings guard: could not run oxfmt on manifest.json, run it by hand');
  }
}

/** what a person still has to write in review.json, and fixtures still missing */
export function unreviewed(m: Manifest): string[] {
  const out: string[] = [];
  for (const [key, entry] of Object.entries(m.controls))
    if (entry.noSave === NOT_REVIEWED)
      out.push(`control ${key} sends nothing: say why in review.json noSave`);
  for (const [key, why] of Object.entries(m.unshownKeys))
    if (why === NOT_REVIEWED)
      out.push(`no control shows ${key}: say why in review.json unshownKeys`);
  for (const [id, why] of Object.entries(m.audit.auditOnly))
    if (why === NOT_REVIEWED)
      out.push(
        `the audit names ${id}, which no scenario's page has: add a scenario, or say why in review.json auditOnly`,
      );
  return out;
}

/** the checks that need no page: the manifest against the recorded payload */
export function staticProblems(m: Manifest): string[] {
  const out = unreviewed(m);
  const now = unshown(m);
  for (const key of now)
    if (!(key in m.unshownKeys))
      out.push(
        `no control shows ${key} when the page loads, and the manifest does not say why (it is in the recorded payload)`,
      );
  for (const key of Object.keys(m.unshownKeys))
    if (!now.includes(key)) out.push(`unshownKeys lists ${key}, which a control shows`);
  for (const id of m.audit.controls)
    if (!(`#${id}` in m.controls) && !Object.keys(m.controls).some((key) => key.includes(`[${id}`)))
      out.push(`the audit's ${id} is no longer a control in the manifest`);
  return out;
}

export function numbers(m: Manifest) {
  const entries = Object.entries(m.controls);
  const count = (test: (entry: ControlEntry, key: string) => boolean) =>
    entries.filter(([key, entry]) => test(entry, key)).length;
  const loaded = new Set(entries.flatMap(([, entry]) => entry.shows ?? []));
  return {
    controls: entries.length,
    onMusic: count((e) => e.sides.includes('music')),
    onVideo: count((e) => e.sides.includes('video')),
    storeBacked: count((e) => !!e.saves?.settings),
    endpointBacked: count((e) => !!e.saves?.requests && !e.saves.settings),
    both: count((e) => !!e.saves?.settings && !!e.saves.requests),
    savesNothing: count((e) => !e.saves && !e.disabled && e.kind !== 'hidden'),
    weakRow: count((e) => e.weak === 'row'),
    weakContext: count((e) => e.weak === 'context'),
    weakPosition: count((e) => e.weak === 'position'),
    disabled: count((e) => !!e.disabled),
    hidden: count((e) => e.kind === 'hidden'),
    keysSaved: savedBy(m).size,
    keysShownOnLoad: loaded.size,
    showEndpoint: count((e) => !!e.showsEndpoint),
    composites: Object.keys(m.composites).length,
    buttons: Object.keys(m.buttons).length,
    payloadKeys: payloadKeys().length,
    payloadKeysShown: payloadKeys().length - Object.keys(m.unshownKeys).length,
    unshownKeys: Object.keys(m.unshownKeys).length,
    auditIds: m.audit.controls.length + Object.keys(m.audit.auditOnly).length,
    auditInManifest: m.audit.controls.length,
    auditOnly: Object.keys(m.audit.auditOnly).length,
    scenarios: m.scenarios.length,
  };
}

export { savedKeys, shownKeys };

/** one scenario on a fresh page: every module of the page loaded anew */
export async function fresh(
  scenario: Scenario,
  known: string[],
  composites = true,
): Promise<Result> {
  vi.resetModules();
  const { runOne } = await import('./run');
  return runOne(scenario, known, review, MOUNT, composites ? adaptersFor(scenario) : {});
}

/** one scenario run again and held against the manifest; its composites too */
export async function checkScenario(scenario: Scenario, part?: Part): Promise<string[]> {
  // the composite widgets go with the last part
  const composites = !part || part.index === part.of - 1;
  const result = await fresh(scenario, knownElsewhere(manifest, scenario, part), composites);
  const problems = compareRun(manifest, result.run, part);
  for (const [name, now] of Object.entries(result.composites)) {
    const was = manifest.composites[name];
    if (!was) problems.push(`composite ${name} is not in the manifest: regenerate it`);
    else problems.push(...compareComposite(name, was, now));
  }
  for (const url of result.unanswered) problems.push(`no fixture answers GET ${url}`);
  return problems;
}

/** every scenario in turn, each probing what the ones before did not settle */
export async function generate(): Promise<{
  manifest: Manifest;
  problems: string[];
  results: Result[];
}> {
  const generated = emptyGenerated();
  const results: Result[] = [];
  const unanswered = new Set<string>();
  for (const scenario of SCENARIOS) {
    const result = await fresh(scenario, settledKeys(generated));
    fold(generated, result.run);
    for (const [name, record] of Object.entries(result.composites))
      generated.composites.set(name, record);
    for (const url of result.unanswered) unanswered.add(url);
    results.push(result);
  }
  finish(generated);
  const next = build(generated);
  const problems = unreviewed(next);
  for (const url of unanswered) problems.push(`no fixture answers GET ${url}`);
  return { manifest: next, problems, results };
}
