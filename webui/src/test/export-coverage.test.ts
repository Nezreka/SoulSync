import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every export must be named by at least one test.
 *
 * An export is a contract. An export no test mentions is an untested contract —
 * nothing anywhere states what it should do, and nothing fails when it stops
 * doing it. Copy drifts silently; functions can be anything at all.
 *
 * WHY THIS IS A TEST AND NOT A SCRIPT. A mutation pass is the strongest check I
 * have, and it is blind here by construction: it only mutates lines the tests
 * already reach, so code no test touches passes every pass trivially. I ran this
 * sweep by hand, once, when prompted — and it found 44 unnamed exports in
 * modules I had just declared verified, eight of them functions written and
 * never checked. A check I have to remember is a check that fails when I am deep
 * in something else. So it runs every time now.
 *
 * ── The rule ───────────────────────────────────────────────────────────────
 *
 * PER MODULE, not per repo. A module either has full coverage or is named in
 * KNOWN_GAPS below. That means:
 *
 *   - a NEW module must be fully covered from the start; it cannot be born
 *     dirty and hide inside a global tolerance;
 *   - a clean module that regresses fails immediately, by name;
 *   - a listed module that becomes clean also fails, telling you to delete its
 *     entry — so the list can only shrink and cannot go quietly stale.
 *
 * The alternative, a single global count, lets a new gap be paid for by fixing
 * an old one. That is exactly the trade this is meant to prevent.
 *
 * SCOPE: the discover route, which this migration owns. A repo-wide run reports
 * ~375 unnamed exports across the older pages; widening this is worth doing and
 * is its own job.
 */

const ROOT = resolve(process.cwd(), 'src/routes/discover');

/**
 * Modules with pre-existing gaps, from the phases of this port that predate the
 * check. Every entry is a to-do, not an exemption.
 */
const KNOWN_GAPS: Record<string, number> = {
  '-discover.adventurousness.ts': 1,
  '-discover.api.ts': 28,
  '-discover.blacklist.ts': 3,
  '-discover.build-playlist.ts': 3,
  '-discover.cache-sections.ts': 3,
  '-discover.decade-shelf.ts': 3,
  '-discover.download-bar.ts': 1,
  '-discover.lastfm-radio.ts': 2,
  '-discover.layout.ts': 1,
  '-discover.listenbrainz.ts': 8,
  '-discover.playlist-sync.ts': 4,
  '-discover.recent-releases.ts': 1,
  '-discover.recommended.ts': 1,
  '-discover.seasonal.ts': 3,
  '-discover.types.ts': 2,
  '-discover.your-albums-actions.ts': 4,
  '-discover.your-albums.ts': 2,
  '-discover.your-artists-actions.ts': 2,
  '-discover.your-artists.ts': 4,
};

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if ((name.endsWith('.ts') || name.endsWith('.tsx')) && !name.endsWith('.d.ts'))
      out.push(full);
  }
  return out;
}

const isTest = (path: string) => path.endsWith('.test.ts') || path.endsWith('.test.tsx');

export function exportedNames(source: string): string[] {
  return [
    ...source.matchAll(/^export (?:async )?(?:function|const) ([A-Za-z_][A-Za-z0-9_]*)/gm),
  ].map((m) => m[1]);
}

describe('every export is named by a test', () => {
  const files = walk(ROOT);
  const corpus = files
    .filter(isTest)
    .map((f) => readFileSync(f, 'utf8'))
    .join('\n');

  const gaps = new Map<string, string[]>();
  for (const file of files.filter((f) => !isTest(f))) {
    const missing = exportedNames(readFileSync(file, 'utf8')).filter(
      (name) => !new RegExp(`\\b${name}\\b`).test(corpus),
    );
    if (missing.length) gaps.set(relative(ROOT, file).replace(/\\/g, '/'), missing);
  }

  it('never leaves a NEW module partly untested', () => {
    const offenders = [...gaps.entries()].filter(([file]) => !(file in KNOWN_GAPS));
    expect(
      offenders.map(([file, names]) => `${file}: ${names.join(', ')}`),
      offenders.length
        ? `\nThese modules export values no test mentions.\n` +
            `An export is a contract; an untested export is an untested contract.\n` +
            `Test them, or stop exporting them.\n`
        : undefined,
    ).toEqual([]);
  });

  it('holds the listed modules to their recorded gap, so the list only shrinks', () => {
    const drift: string[] = [];
    for (const [file, expected] of Object.entries(KNOWN_GAPS)) {
      const actual = gaps.get(file)?.length ?? 0;
      if (actual > expected) drift.push(`${file}: ${actual} now, was ${expected} — a NEW gap`);
      else if (actual < expected)
        drift.push(`${file}: ${actual} now, was ${expected} — good, lower it to ${actual}`);
    }
    expect(drift, drift.length ? `\n${drift.join('\n')}\n` : undefined).toEqual([]);
  });

  it('catches the shape it was written for', () => {
    // A self-check, so the guard cannot rot into a no-op that always passes.
    expect(exportedNames('export function alpha() {}\nexport const BETA = 1;')).toEqual([
      'alpha',
      'BETA',
    ]);
    // A non-exported helper is not a contract and is not required to be named.
    expect(exportedNames('function private_() {}\nconst LOCAL = 1;')).toEqual([]);
  });

  it('reports the visualisation modules as fully covered', () => {
    // The modules this phase wrote. They were at 44 unnamed exports before the
    // sweep; this pins them at zero so they cannot slide back.
    const viz = [...gaps.keys()].filter(
      (f) => f.includes('artist-map') || f.includes('artist-web'),
    );
    expect(viz).toEqual([]);
  });
});
