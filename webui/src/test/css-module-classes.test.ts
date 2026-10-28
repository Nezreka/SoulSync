import { dirname, join, normalize } from 'node:path';
import { describe, expect, it } from 'vitest';

import { listFiles, moduleSheets, read, readCss } from './css-files';

/**
 * Every class in a CSS module must be referenced by a component that imports
 * that module (`styles.foo` or `styles['foo']`). Unused classes are how the
 * dead player and duplicate modal CSS piled up unnoticed.
 *
 * Classes picked at runtime (`styles[`tone_${x}`]`) can't be seen statically.
 * List them here with the lookup that uses them; `foo_*` matches a prefix.
 */
const DYNAMIC: Record<string, { classes: string[]; reason: string }[]> = {
  'src/features/onboarding/onboarding.module.css': [
    {
      classes: ['ok', 'warn', 'busy'],
      reason: "ui.tsx Chip: s[tone], tone is 'ok' | 'warn' | 'busy'",
    },
  ],
  'src/routes/audiobooks/-ui/audiobooks-page.module.css': [
    { classes: ['quality_*'], reason: 'audiobook-releases-modal.tsx: styles[`quality_${band}`]' },
    { classes: ['status_*'], reason: 'audiobook-releases-modal.tsx: styles[`status_${status}`]' },
  ],
  'src/routes/import/-ui/import-page.module.css': [
    { classes: ['badge_*'], reason: 'import-page.tsx: styles[`badge_${d.decision}`]' },
  ],
  'src/routes/issues/-ui/issue-detail-modal.module.css': [
    {
      classes: ['issueExternalLink*'],
      reason: 'issue-detail-modal.tsx: styles[link.className] from the external-links table',
    },
  ],
  'src/routes/stats/-ui/stats-page.module.css': [
    {
      classes: ['statsCardDelta_*'],
      reason: 'stats-page.tsx: styles[`statsCardDelta_${direction}`]',
    },
  ],
  'src/routes/stats/-ui/year-story.module.css': [
    { classes: ['glyph_*'], reason: 'year-story.tsx: styles[`glyph_${key}`]' },
  ],
};

const matches = (pattern: string, name: string) =>
  pattern.endsWith('*') ? name.startsWith(pattern.slice(0, -1)) : name === pattern;

const IMPORT = /import\s+(\w+)\s+from\s+'(\.[^']+\.module\.css)'/g;

/** module path → the identifiers and sources of every file that imports it */
function importers(): Map<string, { ident: string; source: string }[]> {
  const out = new Map<string, { ident: string; source: string }[]>();
  const code = listFiles('src', (p) => /\.tsx?$/.test(p) && !/\.(test|spec)\.tsx?$/.test(p));
  for (const file of code) {
    const source = read(file);
    for (const [, ident, path] of source.matchAll(IMPORT)) {
      const target = normalize(join(dirname(file), path)).replace(/\\/g, '/');
      out.set(target, [...(out.get(target) ?? []), { ident, source }]);
      // `export { s }` hands the module on: files importing `{ s }` from here use it too.
      if (new RegExp(`export\\s*\\{[^}]*\\b${ident}\\b`).test(source)) {
        const self = file.replace(/\.tsx?$/, '');
        for (const other of code) {
          const text = read(other);
          for (const [, names, from] of text.matchAll(
            /import\s*\{([^}]*)\}\s*from\s*'(\.[^']+)'/g,
          )) {
            const resolved = normalize(join(dirname(other), from)).replace(/\\/g, '/');
            const local = names.match(new RegExp(`\\b${ident}\\b(?:\\s+as\\s+(\\w+))?`));
            if (resolved === self && local) {
              out.get(target)!.push({ ident: local[1] ?? ident, source: text });
            }
          }
        }
      }
    }
  }
  return out;
}

function classesIn(file: string): Set<string> {
  const selectors = readCss(file)
    .replace(/:global\([^)]*\)/g, '')
    // Keep selectors only: drop declaration bodies (innermost blocks) and strings.
    .replace(/(["'])(?:\\.|(?!\1).)*\1/g, '')
    .replace(/\{[^{}]*\}/g, '{}');
  return new Set([...selectors.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((m) => m[1]));
}

function referenced(users: { ident: string; source: string }[]): Set<string> {
  const names = new Set<string>();
  for (const { ident, source } of users) {
    for (const [, name] of source.matchAll(new RegExp(`\\b${ident}\\.([A-Za-z_]\\w*)`, 'g'))) {
      names.add(name);
    }
    for (const [, name] of source.matchAll(
      new RegExp(`\\b${ident}\\[\\s*['"]([\\w-]+)['"]\\s*\\]`, 'g'),
    )) {
      names.add(name);
    }
  }
  return names;
}

describe('CSS module classes are all used', () => {
  const users = importers();

  it.each(moduleSheets())('%s', (file) => {
    const used = referenced(users.get(file) ?? []);
    const dynamic = (DYNAMIC[file] ?? []).flatMap((entry) => entry.classes);
    const unused = [...classesIn(file)].filter(
      (name) => !used.has(name) && !dynamic.some((pattern) => matches(pattern, name)),
    );
    expect(unused, `unused classes in ${file}: delete them, or list a dynamic lookup`).toEqual([]);
  });

  it('has no stale dynamic-lookup entries', () => {
    for (const [file, entries] of Object.entries(DYNAMIC)) {
      const classes = [...classesIn(file)];
      for (const pattern of entries.flatMap((entry) => entry.classes)) {
        expect(
          classes.some((name) => matches(pattern, name)),
          `${file}: no class matches "${pattern}"`,
        ).toBe(true);
      }
    }
  });
});
