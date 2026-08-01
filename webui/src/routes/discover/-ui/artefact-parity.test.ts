import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every id and class a discover component emits must exist in the vanilla, or
 * be explicitly claimed as new.
 *
 * A component that renders `id="build-playlist-input"` where the vanilla has
 * `id="build-playlist-search"` type-checks, renders, and passes every
 * behavioural test — and then appears unstyled, unfindable by the stylesheet
 * and by the vanilla's own handlers. Nothing throws. That EXACT bug shipped
 * into committed components four separate times on this port (the playlist
 * builder, the download sidebar, the mix modal, the ListenBrainz tabs), each
 * time with a full green mutation pass, because the tests asserted the
 * invention.
 *
 * ── History of this gate itself ────────────────────────────────────────────
 *
 * Its first draft failed twice over: it omitted the stylesheets from the
 * corpus (115 false positives burying 9 real bugs), and then it "fixed" that
 * with substring matching plus a numeric budget — under which
 * `.listenbrainz-tab` passed because it is a substring of
 * `.listenbrainz-tab-content`, and 30+ invented classes hid inside the budget
 * as "pending CSS". Matching is TOKENIZED now, and the allowlist is explicit:
 * every entry is a name, not a count.
 */

const UI = resolve(process.cwd(), 'src/routes/discover/-ui');

const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const JS =
  readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8') +
  readFileSync(resolve(process.cwd(), 'static/discover-section-controller.js'), 'utf8');
const CSS = readdirSync(resolve(process.cwd(), 'static'))
  .filter((f) => f.endsWith('.css'))
  .map((f) => readFileSync(resolve(process.cwd(), 'static', f), 'utf8'))
  .join('\n');

/** Class tokens the vanilla actually declares — attributes and selectors. */
const KNOWN_CLASSES = new Set<string>([
  ...[...(HTML + JS).matchAll(/class="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/)),
  ...[...CSS.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1]),
]);

/** Id tokens the vanilla declares, looks up, or styles. */
const KNOWN_IDS = new Set<string>([
  ...[...(HTML + JS).matchAll(/id="([\w-]+)"/g)].map((m) => m[1]),
  ...[...JS.matchAll(/getElementById\(['"]([\w-]+)['"]\)/g)].map((m) => m[1]),
  ...[...CSS.matchAll(/#([a-zA-Z][\w-]*)/g)].map((m) => m[1]),
]);

/**
 * Ids the port introduces on purpose. All four are DiscoverSectionId layout
 * keys for sections the vanilla identified by class alone — note that
 * `lastfm-radio` never reaches the DOM (the component passes
 * domId="lastfm-radio-section"); it appears here because the scan reads
 * component SOURCE and cannot tell a prop from an attribute.
 */
const NEW_IDS = ['build-a-playlist', 'lastfm-radio', 'listenbrainz', 'recent-releases'];

/**
 * Classes the port introduces because the vanilla styled that element INLINE.
 *
 * Every name below is in the Artist Map / Artist Web panels, overlays and
 * hints, whose vanilla markup carries `style="…"` on every node (several of
 * the builders say so outright: "Inline-styled so it doesn't depend on CSS").
 * Replacing that with semantic classes is the right shape — and it means these
 * have NO stylesheet rules until #258 writes them. That CSS is a prerequisite
 * of the route flip. Adding a name here is a claim that the vanilla element it
 * replaces was inline-styled — nothing else belongs on this list.
 */
const NEW_CLASSES = [
  'artist-map-loading',
  'artmap-card-action-row',
  'artmap-card-actions',
  'artmap-card-avatar',
  'artmap-card-back',
  'artmap-card-canvas',
  'artmap-card-details',
  'artmap-card-explore',
  'artmap-card-genre',
  'artmap-card-genres',
  'artmap-card-glyph',
  'artmap-card-head',
  'artmap-card-img',
  'artmap-card-name',
  'artmap-card-open',
  'artmap-card-pop',
  'artmap-card-type',
  'artmap-card-watch',
  'artmap-island-menu',
  'artmap-island-menu-count',
  'artmap-island-menu-dot',
  'artmap-island-menu-name',
  'artmap-island-nav',
  'artmap-island-nav-current',
  'artmap-island-nav-meta',
  'artmap-island-nav-name',
  'artmap-ministat',
  'artmap-ministat-label',
  'artmap-ministat-value',
  'artmap-panel-body',
  'artmap-panel-coverage',
  'artmap-panel-coverage-fill',
  'artmap-panel-coverage-row',
  'artmap-panel-coverage-track',
  'artmap-panel-empty',
  'artmap-panel-eyebrow',
  'artmap-panel-grip',
  'artmap-panel-head',
  'artmap-panel-heading',
  'artmap-panel-list-title',
  'artmap-panel-rank',
  'artmap-panel-row',
  'artmap-panel-row-name',
  'artmap-panel-star',
  'artmap-panel-stats',
  'artmap-panel-thumb',
  'artweb-avatar',
  'artweb-avatar-glyph',
  'artweb-btn-ghost',
  'artweb-btn-lens',
  'artweb-btn-link',
  'artweb-btn-primary',
  'artweb-card-actions',
  'artweb-card-badge',
  'artweb-card-empty',
  'artweb-card-genre',
  'artweb-card-head',
  'artweb-card-kicker',
  'artweb-card-kicker-spaced',
  'artweb-card-meter',
  'artweb-card-name',
  'artweb-card-pill',
  'artweb-card-pills',
  'artweb-card-stats',
  'artweb-card-sub',
  'artweb-card-title',
  'artweb-firstrun-hint',
  'artweb-genre-clear',
  'artweb-genre-count',
  'artweb-genre-dot',
  'artweb-genre-empty',
  'artweb-genre-name',
  'artweb-hint',
  'artweb-member-name',
  'artweb-member-rank',
  'artweb-member-row',
  'artweb-ministat',
  'artweb-ministat-label',
  'artweb-ministat-value',
  'artweb-panel',
  'artweb-panel-body',
  'artweb-panel-close',
  'artweb-path-dot',
  'artweb-path-hint',
  'artweb-path-link',
  'artweb-path-name',
  'artweb-path-row',
  'artweb-path-tag',
];

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
  const perFile = componentFiles().map((f) => ({
    file: f.slice(UI.length + 1),
    ...literalArtefacts(readFileSync(f, 'utf8')),
  }));

  it('uses no id the vanilla does not have, beyond the named layout keys', () => {
    const bad: string[] = [];
    for (const { file, ids } of perFile) {
      for (const id of new Set(ids)) {
        if (!KNOWN_IDS.has(id) && !NEW_IDS.includes(id)) bad.push(`${file}: id="${id}"`);
      }
    }
    expect(
      bad.sort(),
      bad.length
        ? '\nThese ids exist in no vanilla markup, script or stylesheet.\n' +
            'A wrong id renders unstyled and cannot be found by id.\n'
        : undefined,
    ).toEqual([]);
  });

  it('uses no class the vanilla does not have, beyond the inline-style replacements', () => {
    const bad = new Set<string>();
    for (const { file, classes } of perFile) {
      for (const cls of new Set(classes)) {
        if (!KNOWN_CLASSES.has(cls) && !NEW_CLASSES.includes(cls)) bad.add(`${file}: .${cls}`);
      }
    }
    expect(
      [...bad].sort(),
      bad.size
        ? '\nThese classes are neither in the vanilla nor claimed as new.\n' +
            'If the vanilla element was inline-styled, add the name to\n' +
            'NEW_CLASSES; otherwise the class is WRONG — adopt the vanilla one.\n'
        : undefined,
    ).toEqual([]);
  });

  it('keeps the allowlists free of names the vanilla actually has', () => {
    // An allowlisted name that exists in the vanilla is a stale claim — and a
    // stale allowlist is how the next invented artefact hides.
    expect(NEW_IDS.filter((i) => KNOWN_IDS.has(i))).toEqual([]);
    expect(NEW_CLASSES.filter((c) => KNOWN_CLASSES.has(c))).toEqual([]);
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
    // Self-checks, so the guard cannot rot into a no-op that always passes.
    const found = literalArtefacts('<div id="a-b" className="c-d e-f" />');
    expect(found.ids).toEqual(['a-b']);
    expect(found.classes).toEqual(['c-d', 'e-f']);
    expect(literalArtefacts('<div className={x ? "p" : "q"} />').classes).toEqual([]);
    // TOKENIZED, not substring: containing a known token must not make an
    // unknown one pass — the exact hole the first draft had.
    expect(KNOWN_CLASSES.has('listenbrainz-tab-content')).toBe(true);
    expect(KNOWN_CLASSES.has('listenbrainz-tab')).toBe(false);
  });
});
