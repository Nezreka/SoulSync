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
const JS = readFileSync(
  resolve(process.cwd(), 'src/routes/discover/__fixtures__/-vanilla-discover.js'),
  'utf8',
);
// discover-section-controller.js was deleted outright in the aug 26 TS
// migration (dead code: its consumer, the vanilla discover page, is long
// gone) - the fixture below already carries the vanilla class vocabulary.
const CSS = readdirSync(resolve(process.cwd(), 'static'))
  .filter((f) => f.endsWith('.css'))
  .map((f) => readFileSync(resolve(process.cwd(), 'static', f), 'utf8'))
  .join('\n');

/** Class tokens the vanilla actually declares — attributes and selectors. */
const KNOWN_CLASSES = new Set<string>([
  // The vanilla's class attributes live in template literals — strip the
  // `${…}` interpolations BEFORE tokenizing, or `class="decade-tab
  // lb-subtab${active}"` yields the token `lb-subtab${active}` and the real
  // class never becomes known.
  ...[...(HTML + JS).matchAll(/class="([^"]+)"/g)].flatMap((m) =>
    m[1].replace(/\$\{[^}]*\}?/g, ' ').split(/\s+/),
  ),
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
const NEW_IDS = [
  'build-a-playlist',
  'lastfm-radio',
  'listenbrainz',
  'recent-releases',
  // The four zone anchors upstream's Discover redesign added (3.2.5, regrouped
  // in 3.3.0). They are scroll targets the page's own zone map jumps to, not
  // vanilla artefacts — the vanilla had no zones at all.
  'discover-zone-for-you',
  'discover-zone-library',
  'discover-zone-new-missing',
  'discover-zone-tools',
  // the play-now bridge (aug 25): library radio's discover card
  'library-radio-section',
  // recommended stations row (aug 25)
  'recommended-stations-section',
  // M05: the dial is a native range input now, and aria-labelledby /
  // aria-describedby need ids to point at. Styled by class, so they never
  // appear in the stylesheet.
  'adv-wave-label',
  'adv-wave-help',
];

/**
 * Artefacts whose ONLY vanilla home was the index.html discover markup, which
 * the flip deleted (the duplicate-id guard in tests/ forbids a React page's
 * ids from also living in index.html). Everything else the components emit is
 * still cross-checked against discover.js and the stylesheets; these names
 * survive as a FROZEN ledger of what that markup declared. discover.js's own
 * deletion in PR 2 will move more names here the same way.
 */
const DELETED_MARKUP_IDS = [
  'adv-wave',
  'adv-wave-fill',
  'adv-wave-svg',
  'bp-info-panel',
  'build-playlist-sync-btn',
  'discover-hero-view-all',
  // 'your-albums-section' left this ledger when the rails redesign gave it
  // real CSS (the wrapping-grid exemption) — the parity test caught the
  // stale claim, exactly as designed.
  'your-artists-carousel',
  'your-artists-section',
];
const DELETED_MARKUP_CLASSES = ['artweb-size-btn', 'watch-all-text'];

/**
 * Classes the port introduces because the vanilla styled that element INLINE.
 *
 * EMPTY — and it must stay that way. The 88 names that used to live here
 * (the Artist Map / Artist Web panels, overlays and hints, whose vanilla
 * markup carried `style="…"` on every node) got real stylesheet rules in
 * #258 — the "React discover port" block at the end of static/style.css,
 * every rule a 1:1 transcription with the vanilla line cited. That made
 * them KNOWN_CLASSES, which is this list's designed end state: a name may
 * only pass through here on its way to a stylesheet.
 */
const NEW_CLASSES: string[] = [
  // 3.3.0 zone regroup: the tools zone's grid modifier, styled in style.css.
  // It arrived in 3.2.5 as a modifier on the styled `.discovery-zone-section`
  // base with no rule of its own; it leaves this list the moment the scan can
  // see the rule that now exists.
  'discovery-zone-section--map-tools',
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
  // Ternary classNames — className={cond ? 'a b' : 'c'} — where BOTH branches
  // are plain string literals. The quoted-only scan above missed these, which
  // is how four classes (artmap-panel--sheet among them) never reached the
  // allowlist at all until #258's CSS cross-check caught them. Template-string
  // classNames stay out of scope: their literals mix with expression text and
  // matching them loosely would flag comparison strings as classes.
  for (const m of source.matchAll(/\bclassName=\{[^{}]*?'([^']*)'\s*:\s*'([^']*)'\s*\}/g)) {
    for (const branch of [m[1], m[2]]) {
      for (const tok of branch.split(/\s+/)) if (tok) classes.push(tok);
    }
  }
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
        if (!KNOWN_IDS.has(id) && !NEW_IDS.includes(id) && !DELETED_MARKUP_IDS.includes(id)) {
          bad.push(`${file}: id="${id}"`);
        }
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
        if (
          !KNOWN_CLASSES.has(cls) &&
          !NEW_CLASSES.includes(cls) &&
          !DELETED_MARKUP_CLASSES.includes(cls)
        ) {
          bad.add(`${file}: .${cls}`);
        }
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
    expect(DELETED_MARKUP_IDS.filter((i) => KNOWN_IDS.has(i))).toEqual([]);
    expect(DELETED_MARKUP_CLASSES.filter((c) => KNOWN_CLASSES.has(c))).toEqual([]);
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
    // Ternaries with SINGLE-quoted branches are in scope, both branches.
    expect(literalArtefacts("<div className={x ? 'p q' : 'r'} />").classes).toEqual([
      'p',
      'q',
      'r',
    ]);
    // TOKENIZED, not substring: containing a known token must not make an
    // unknown one pass — the exact hole the first draft had.
    expect(KNOWN_CLASSES.has('listenbrainz-tab-content')).toBe(true);
    expect(KNOWN_CLASSES.has('listenbrainz-tab')).toBe(false);
    // Interpolations are stripped before tokenizing: the vanilla's
    // `class="decade-tab lb-subtab${…}"` must yield `lb-subtab`, not
    // `lb-subtab${…}`.
    expect(KNOWN_CLASSES.has('lb-subtab')).toBe(true);
  });
});
