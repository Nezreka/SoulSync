import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import stylelint from 'stylelint';
import { describe, expect, it } from 'vitest';

import baseline from './css-budget.json';
import { WEBUI, legacySheets, listFiles, moduleSheets, read, readCss } from './css-files';

/**
 * CSS ratchet. Each number counts a kind of sprawl the CSS cleanup removed.
 * It may go down, never up.
 *
 * - Went up: use a token from static/tokens.css, edit the existing rule, or
 *   reuse an existing breakpoint/z-index instead of adding a new one.
 * - Went down: lower the baseline so it stays down:
 *     UPDATE_CSS_BUDGET=1 npx vitest run src/test/css-budget.test.ts
 *   That only ever lowers numbers; commit the updated css-budget.json.
 */

type Counts = { hex: number; rgbLiterals: number; important: number };
type Budget = {
  legacy: Counts & { duplicateSelectors: number };
  modules: Counts;
  distinctZIndexValues: number;
  distinctBreakpoints: number;
  duplicateKeyframes: number;
  undefinedCustomProperties: number;
  stylesheetLinks: string[];
};

const BUDGET_PATH = resolve(WEBUI, 'src/test/css-budget.json');

/**
 * Hook properties: read with a fallback so a caller *may* set them, but
 * nothing does yet. Anything else used and never defined is a bug — the
 * declaration is invalid, or its fallback silently always wins.
 */
const OPTIONAL_HOOKS = new Set([
  '--stat-index',
  '--progress-index',
  '--button-icon-size',
  '--button-icon-font-size',
]);

const DECLARATION = /[\w-]+\s*:\s*([^;{}]+)(?=[;}])/g;
const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const RGB_LITERAL = /\brgba?\(\s*[\d.]/g;
const count = (text: string, re: RegExp) => text.match(re)?.length ?? 0;

function countsFor(files: string[]): Counts {
  const totals = { hex: 0, rgbLiterals: 0, important: 0 };
  for (const file of files) {
    const css = readCss(file);
    for (const [, value] of css.matchAll(DECLARATION)) totals.hex += count(value, HEX);
    totals.rgbLiterals += count(css, RGB_LITERAL);
    totals.important += count(css, /!important/g);
  }
  return totals;
}

function distinct(files: string[], re: RegExp): string[] {
  const values = new Set<string>();
  for (const file of files) {
    for (const match of readCss(file).matchAll(re)) {
      values.add(
        match[1]
          .replace(/!important/, '')
          .replace(/\s+/g, ' ')
          .trim(),
      );
    }
  }
  return [...values].sort();
}

function breakpoints(files: string[]): string[] {
  const values = new Set<string>();
  for (const file of files) {
    for (const [query] of readCss(file).matchAll(/@media[^{]+/g)) {
      for (const [, px] of query.matchAll(/width\s*(?::|[<>]=?)\s*([\d.]+px)/g)) values.add(px);
    }
  }
  return [...values].sort((a, b) => parseFloat(a) - parseFloat(b));
}

function duplicateKeyframes(): string[] {
  // Module keyframes are hashed per file, so only the global sheets can collide.
  const seen = new Map<string, number>();
  for (const file of listFiles('static', (p) => p.endsWith('.css'))) {
    for (const [, name] of readCss(file).matchAll(/@keyframes\s+([\w-]+)/g)) {
      seen.set(name, (seen.get(name) ?? 0) + 1);
    }
  }
  return [...seen].filter(([, n]) => n > 1).map(([name]) => name);
}

function undefinedCustomProperties(): string[] {
  const sheets = [...listFiles('static', (p) => p.endsWith('.css')), ...moduleSheets()];
  const defined = new Set<string>();
  const used = new Set<string>();
  for (const file of sheets) {
    const css = readCss(file);
    for (const [, name] of css.matchAll(/(--[\w-]+)\s*:/g)) defined.add(name);
    for (const [, name] of css.matchAll(/var\(\s*(--[\w-]+)/g)) used.add(name);
  }
  // Scripts and components set properties at runtime (setProperty, style={{ '--x': … }}).
  const code = [
    ...listFiles('static', (p) => p.endsWith('.js')),
    ...listFiles('src', (p) => /\.tsx?$/.test(p) && !/\.(test|spec)\.tsx?$/.test(p)),
    'index.html',
  ];
  // They also read them in inline styles, so their var() uses count too (bar a
  // name built in a template, like `--batch-color-${i}`).
  for (const file of code) {
    const text = read(file);
    for (const [, name] of text.matchAll(/var\(\s*(--[\w-]+)(?![\w-]*\$\{)/g)) used.add(name);
    const rest = text.replace(/var\(\s*--[\w-]+/g, '');
    for (const [name] of rest.matchAll(/--[A-Za-z][\w-]*/g)) defined.add(name);
  }
  return [...used].filter((name) => !defined.has(name) && !OPTIONAL_HOOKS.has(name)).sort();
}

/**
 * Repeated selector lists in the legacy sheets. stylelint's
 * no-duplicate-selectors can't be on for style.css and video-side.css yet
 * (their remaining copies are order-sensitive), and the other sheets mark
 * theirs with disable comments, so count every copy, comments or not.
 */
async function duplicateSelectors(files: string[]): Promise<number> {
  const { results } = await stylelint.lint({
    files: files.map((file) => resolve(WEBUI, file)),
    config: { rules: { 'no-duplicate-selectors': true } },
    configBasedir: WEBUI,
    ignoreDisables: true,
  });
  return results.reduce((n, result) => n + result.warnings.length, 0);
}

function stylesheetLinks(): string[] {
  return [...read('index.html').matchAll(/<link\s+rel="stylesheet"[^>]*filename='([^']+)'/g)].map(
    (m) => m[1],
  );
}

async function measure(): Promise<Budget & { details: Record<string, string[]> }> {
  const legacy = legacySheets();
  const modules = moduleSheets();
  const zIndex = distinct([...legacy, ...modules], /z-index\s*:\s*([^;}]+)/g);
  const widths = breakpoints([...legacy, ...modules]);
  const keyframes = duplicateKeyframes();
  const undefinedProps = undefinedCustomProperties();
  return {
    legacy: { ...countsFor(legacy), duplicateSelectors: await duplicateSelectors(legacy) },
    modules: countsFor(modules),
    distinctZIndexValues: zIndex.length,
    distinctBreakpoints: widths.length,
    duplicateKeyframes: keyframes.length,
    undefinedCustomProperties: undefinedProps.length,
    stylesheetLinks: stylesheetLinks(),
    details: { zIndex, breakpoints: widths, keyframes, undefinedProps },
  };
}

const NUMERIC = [
  'legacy.hex',
  'legacy.rgbLiterals',
  'legacy.important',
  'legacy.duplicateSelectors',
  'modules.hex',
  'modules.rgbLiterals',
  'modules.important',
  'distinctZIndexValues',
  'distinctBreakpoints',
  'duplicateKeyframes',
  'undefinedCustomProperties',
] as const;

const DETAILS = {
  distinctZIndexValues: 'zIndex',
  distinctBreakpoints: 'breakpoints',
  duplicateKeyframes: 'keyframes',
  undefinedCustomProperties: 'undefinedProps',
} as const;

const get = (budget: Budget, key: string): number =>
  key
    .split('.')
    .reduce<unknown>((obj, part) => (obj as Record<string, unknown>)[part], budget) as number;

function set(budget: Budget, key: string, value: number) {
  const parts = key.split('.');
  const last = parts.pop()!;
  const parent = parts.reduce<Record<string, unknown>>(
    (obj, part) => obj[part] as Record<string, unknown>,
    budget as unknown as Record<string, unknown>,
  );
  parent[last] = value;
}

const actual = await measure();

describe('CSS budget (may only go down)', () => {
  const budget = baseline as Budget;

  it.each(NUMERIC)('%s', (key) => {
    const now = get(actual, key);
    const allowed = get(budget, key);
    const detail = DETAILS[key as keyof typeof DETAILS];
    expect(
      now,
      `${key} went up from ${allowed} to ${now}.` +
        (detail ? `\nNow: ${actual.details[detail].join(', ')}` : ''),
    ).toBeLessThanOrEqual(allowed);
  });

  it('links no new stylesheets from index.html', () => {
    // A new sheet is how the "premier"/"overhaul" layers started. Edit the
    // existing sheet, or build the UI as a React component with a CSS module.
    expect(actual.stylesheetLinks).toEqual(budget.stylesheetLinks);
  });

  it('reports when the baseline can be lowered', () => {
    const lower = NUMERIC.filter((key) => get(actual, key) < get(budget, key));
    if (process.env.UPDATE_CSS_BUDGET) {
      const next = structuredClone(budget);
      for (const key of lower) set(next, key, get(actual, key));
      writeFileSync(BUDGET_PATH, `${JSON.stringify(next, null, 2)}\n`);
    } else if (lower.length) {
      console.warn(
        `CSS budget can be lowered: ${lower
          .map((key) => `${key} ${get(budget, key)} → ${get(actual, key)}`)
          .join(', ')}.\nRun UPDATE_CSS_BUDGET=1 npx vitest run src/test/css-budget.test.ts ` +
          'and commit src/test/css-budget.json.',
      );
    }
    expect(true).toBe(true);
  });
});
