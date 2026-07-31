import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every id and class a discover component emits must exist in the vanilla.
 *
 * A React component that renders `id="build-playlist-input"` where the vanilla
 * has `id="build-playlist-search"` type-checks, renders, and passes every
 * behavioural test — and then appears on the page completely unstyled, because
 * `style.css` targets an id that no longer exists and the vanilla's own handler
 * can no longer find it either. Nothing throws. Nothing logs.
 *
 * WHY THIS IS A TEST AND NOT A REVIEW. I found exactly that bug by hand, once,
 * while verifying — and it had already shipped into three committed components
 * (the playlist builder, the download sidebar, and a Last.fm section id). Every
 * check I run by choice is a check I will skip while deep in something else;
 * this is the third time that has cost real defects on this port, after the
 * vacuous-assertion and untested-export gates. So it runs every time now.
 *
 * ── The rule ───────────────────────────────────────────────────────────────
 *
 *   • an id or class in a component must appear in index.html or discover.js,
 *   • or be listed in DELIBERATELY_NEW with a reason,
 *   • and no id may be emitted by two different components.
 *
 * The last clause is not hypothetical either: the Artist Map overlay rendered
 * placeholder tooltip and search-results divs transcribed from the static
 * markup, while the chrome components rendered the real ones — two elements per
 * id, and `getElementById` reaching the empty one.
 */

const UI = resolve(process.cwd(), 'src/routes/discover/-ui');

/**
 * The corpus MUST include the stylesheets.
 *
 * The first draft of this guard checked only index.html and discover.js, and
 * reported every styled class in the port as missing — 115 false positives that
 * buried the nine real ones. A guard that cries wolf is a guard people learn to
 * skim.
 */
const VANILLA = [
  readFileSync(resolve(process.cwd(), 'index.html'), 'utf8'),
  readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8'),
  ...readdirSync(resolve(process.cwd(), 'static'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => readFileSync(resolve(process.cwd(), 'static', f), 'utf8')),
].join('\n');

/**
 * Classes the port introduces because the vanilla styled that element INLINE.
 *
 * The map and web panels build their markup in JS with `style="…"` on every
 * node; the port replaced those with semantic classes, which is the right shape
 * and leaves them unstyled until the CSS is written. That CSS is a prerequisite
 * of the route flip, not optional polish — this number may only go DOWN.
 */
const UNSTYLED_CLASS_BUDGET = 94;

/**
 * Artefacts the port introduces on purpose. Every entry is a claim that the
 * vanilla had no equivalent — not a place to silence a mismatch.
 */
const DELIBERATELY_NEW: Record<string, string> = {
  // Layout KEYS that the vanilla never put on an element. `-discover.layout`
  // uses them to order the page; the sections themselves were class-only.
  listenbrainz: 'layout key; the vanilla section carries no id',
  'build-a-playlist': 'layout key; the vanilla section carries no id',
  // The ListenBrainz tab body holds cards directly in the vanilla; the port
  // gives them a grid element so the mix cards can share one layout.
  'listenbrainz-grid': 'new grid inside the existing #listenbrainz-tab-content',
};

function componentFiles(): string[] {
  return readdirSync(UI)
    .filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx'))
    .map((f) => join(UI, f));
}

/** Literal `id="…"` and `className="…"` values, which are the ones that can drift. */
export function literalArtefacts(source: string): { ids: string[]; classes: string[] } {
  const ids = [...source.matchAll(/\bid="([a-z][a-z0-9-]*)"/g)].map((m) => m[1]);
  const classes = [...source.matchAll(/\bclassName="([^"{}]+)"/g)]
    .flatMap((m) => m[1].split(/\s+/))
    .filter(Boolean);
  return { ids, classes };
}

describe('the components emit the vanilla ARTEFACTS', () => {
  const files = componentFiles();
  const perFile = files.map((f) => ({
    file: f.slice(UI.length + 1),
    ...literalArtefacts(readFileSync(f, 'utf8')),
  }));

  // A plain substring: the vanilla names artefacts in markup, in template
  // literals, and in stylesheet selectors, and all three count.
  const known = (name: string) => name in DELIBERATELY_NEW || VANILLA.includes(name);

  it('uses no id the vanilla does not have', () => {
    const bad: string[] = [];
    for (const { file, ids } of perFile) {
      for (const id of new Set(ids)) {
        if (!known(id)) bad.push(`${file}: id="${id}"`);
      }
    }
    expect(
      bad.sort(),
      bad.length
        ? '\nThese ids exist in no vanilla markup or script.\n' +
            'A wrong id renders unstyled and cannot be found by id.\n' +
            'Fix the id, or list it in DELIBERATELY_NEW with a reason.\n'
        : undefined,
    ).toEqual([]);
  });

  it('holds the unstyled-class budget, which may only go down', () => {
    const bad = new Set<string>();
    for (const { file, classes } of perFile) {
      for (const cls of new Set(classes)) {
        if (!known(cls)) bad.add(`${file}: .${cls}`);
      }
    }
    // Not zero, and honestly so: these are classes that replaced the vanilla's
    // inline styles and have no CSS yet. Every one is an element that renders
    // structurally correct and visually bare.
    expect(
      bad.size,
      bad.size > UNSTYLED_CLASS_BUDGET
        ? `\nNEW unstyled classes:\n  ${[...bad].sort().join('\n  ')}\n`
        : bad.size < UNSTYLED_CLASS_BUDGET
          ? `\nGood — ${UNSTYLED_CLASS_BUDGET - bad.size} fewer. Lower UNSTYLED_CLASS_BUDGET to ${bad.size}.\n`
          : undefined,
    ).toBe(UNSTYLED_CLASS_BUDGET);
  });

  it('never emits one id from two components', () => {
    const owners = new Map<string, Set<string>>();
    for (const { file, ids } of perFile) {
      for (const id of new Set(ids)) {
        if (!owners.has(id)) owners.set(id, new Set());
        owners.get(id)!.add(file);
      }
    }
    const dupes = [...owners.entries()]
      .filter(([, files]) => files.size > 1)
      .map(([id, files]) => `${id}: ${[...files].sort().join(', ')}`);
    expect(
      dupes.sort(),
      dupes.length
        ? '\nTwo components render the same id. getElementById reaches whichever\n' +
            'is first in the DOM, which is rarely the one that matters.\n'
        : undefined,
    ).toEqual([]);
  });

  it('catches the shapes it was written for', () => {
    // A self-check, so the guard cannot rot into a no-op that always passes.
    const found = literalArtefacts('<div id="a-b" className="c-d e-f" />');
    expect(found.ids).toEqual(['a-b']);
    expect(found.classes).toEqual(['c-d', 'e-f']);
    // A computed class is not a literal and is out of scope by design.
    expect(literalArtefacts('<div className={x ? "p" : "q"} />').classes).toEqual([]);
  });
});
