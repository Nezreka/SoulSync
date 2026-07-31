import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A guard against assertions that move with the bug they should catch.
 *
 * The failure this exists for, stated precisely: writing
 *
 *     expect(measure(1400, 900, null).height).toBe(900 - TOOLBAR_FALLBACK);
 *
 * looks like it pins the fallback. It does not. Change the constant and the
 * expectation changes with it, so it passes for every possible value — it
 * asserts arithmetic, not behaviour. It is invisible in review because it reads
 * as MORE rigorous than a bare number, and it is only ever caught by a mutation
 * pass, which is a manual step nobody is obliged to run.
 *
 * It happened five times across the discover port before this rule existed.
 *
 * ── What is actually wrong, and what only looks wrong ──────────────────────
 *
 * NOT a problem — an IDENTITY check on a string constant:
 *
 *     expect(bpGenerateError(...)).toBe(BP_GENERATE_FAILED);
 *
 * This pins WHICH message comes back, not its wording. Mutate the wording and
 * both sides move, true — but the wording is pinned elsewhere, against the
 * vanilla text. The thing under test here is the branch, and it holds.
 *
 * A problem — a NUMERIC constant as the expected value:
 *
 *     expect(rows).toHaveLength(LEGEND_LIMIT);
 *     expect(labelledAnchors).toBe(STAR_ANCHORS);
 *
 * Here the NUMBER is the thing under test. Halve the constant and the
 * expectation halves with it, and a real cap change sails through. Two of the
 * five originals were exactly this.
 *
 * Also a problem — a constant EMBEDDED in a larger expected value, even against
 * the vanilla source:
 *
 *     expect(SOURCE).toContain(`ratio: ${CAMERA.cameraToRatio}`);
 *
 * This was the worst of the five: it passed a real 0.12 → 0.15 change, because
 * another function in the same file genuinely uses 0.15. A substring search for
 * an interpolated value can match by coincidence, so an oracle does not redeem
 * it.
 *
 * ── The rule ───────────────────────────────────────────────────────────────
 *
 * A constant may be the SUBJECT of an expectation — `expect(FALLBACK).toBe(50)`
 * is exactly right and is how you pin a value. As an EXPECTED value it is
 * reported when it is numeric, or when it is embedded in a bigger expression.
 * A bare string constant compared for identity is left alone.
 *
 * Deliberate exceptions are annotated, never silent: `pin-ok: <reason>` on the
 * assertion's line or the one above it.
 *
 * SCOPE: the discover route and the shared test helpers — the code this
 * migration owns. Widening it to the older pages is worth doing and is its own
 * job, not something to blanket-annotate here.
 */

const SCOPED = [resolve(process.cwd(), 'src/routes/discover'), resolve(process.cwd(), 'src/test')];

/** This file's own doc examples and self-check fixtures are prose, not assertions. */
const SELF = resolve(process.cwd(), 'src/test/constant-assertions.test.ts');

/**
 * How many weak sites remain, as a RATCHET.
 *
 * Every one is `expect(SOURCE).toContain(\`…\${CONST}…\`)` — comparing the
 * vanilla text against an interpolated constant. They are not worthless (they do
 * check the vanilla still contains something) but they can match coincidentally,
 * which is exactly how a real 0.12 → 0.15 change slipped through once.
 *
 * The number may only go DOWN. Adding one fails; fixing one fails too, and tells
 * you to lower it — which keeps the figure honest instead of quietly stale.
 * Converting them to literals is tracked separately rather than done blind.
 */
const REMAINING_WEAK = 51;

/** Matchers whose argument is the EXPECTED value. */
const MATCHERS = [
  'toBe',
  'toEqual',
  'toStrictEqual',
  'toContain',
  'toContainEqual',
  'toHaveLength',
  'toBeCloseTo',
  'toMatchObject',
  'toHaveBeenCalledWith',
  'toHaveBeenNthCalledWith',
];

/** Matchers where the argument is inherently a NUMBER under test. */
const NUMERIC_MATCHERS = new Set(['toHaveLength', 'toBeCloseTo']);

/**
 * Subjects that are independent of the module under test.
 *
 * `SOURCE`/`SHELL` are the vanilla files read from disk; `V.` and `theirs` are
 * the extracted vanilla's own output. Comparing one of those against our
 * constant is a real cross-check: mutate the constant and it FAILS, because the
 * oracle still holds the real value.
 *
 * This redeems a WHOLE-VALUE comparison only. It does not redeem an interpolated
 * one — `toContain(\`ratio: \${X}\`)` can match another function's use of the
 * same number, which is exactly how the 0.12 → 0.15 change slipped through.
 */
const ORACLES = [/\bSOURCE\b/, /\bSHELL\b/, /\bVANILLA_/, /\bV\./, /\btheirs\b/];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.test\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

interface Imported {
  name: string;
  from: string;
}

/** SCREAMING_CASE imports, with the module they came from. */
export function importedConstants(source: string): Imported[] {
  const found: Imported[] = [];
  const importRe = /import\s*(?:type\s*)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g;
  let m: RegExpExecArray | null;
  while ((m = importRe.exec(source))) {
    for (const raw of m[1].split(',')) {
      const name = raw
        .replace(/\btype\b/, '')
        .split(/\s+as\s+/)
        .pop()
        ?.trim();
      if (name && /^[A-Z][A-Z0-9_]{1,}$/.test(name)) found.push({ name, from: m[2] });
    }
  }
  return found;
}

/** Whether a constant's declaration is a plain number. */
export function isNumericConstant(imp: Imported, fromFile: string): boolean {
  if (!imp.from.startsWith('.')) return false;
  for (const ext of ['.ts', '.tsx']) {
    const path = resolve(dirname(fromFile), imp.from + ext);
    if (!existsSync(path)) continue;
    const decl = new RegExp(`export const ${imp.name}\\s*(?::[^=]+)?=\\s*(-?[\\d.]+)\\s*;`).exec(
      readFileSync(path, 'utf8'),
    );
    return !!decl;
  }
  return false;
}

/** The span from an opening paren to its match. */
export function parenSpan(source: string, openParen: number): string {
  let depth = 0;
  for (let i = openParen; i < source.length; i++) {
    if (source[i] === '(') depth++;
    else if (source[i] === ')') {
      depth--;
      if (depth === 0) return source.slice(openParen + 1, i);
    }
  }
  return source.slice(openParen + 1);
}

/**
 * Is the expected value the constant itself (possibly a property or index of
 * it), or is it BUILT from the constant?
 *
 * `LIMIT`, `TITLES.genre` and `SHORTCUTS[0].keys` are the constant. `900 - CAP`
 * and `` `ratio: ${X}` `` are built from it, and that is the dangerous form:
 * the expectation moves with the value AND, against a text oracle, can match
 * something else entirely.
 */
function isWholeValue(arg: string, name: string): boolean {
  const trimmed = arg.trim();
  if (trimmed.includes('`')) return false;
  return new RegExp(`^${name}(?:\\.\\w+|\\[[^\\]]+\\])*$`).test(trimmed);
}

interface Violation {
  file: string;
  line: number;
  constant: string;
  reason: 'numeric' | 'embedded';
  text: string;
}

export function scanSource(source: string, file = '<inline>'): Violation[] {
  const imports = importedConstants(source);
  if (imports.length === 0) return [];
  const numeric = new Map(imports.map((i) => [i.name, isNumericConstant(i, file)]));
  const lines = source.split('\n');
  const found: Violation[] = [];

  const expectRe = /\bexpect\s*\(/g;
  let m: RegExpExecArray | null;
  while ((m = expectRe.exec(source))) {
    const subjectOpen = source.indexOf('(', m.index);
    const subject = parenSpan(source, subjectOpen);
    const afterSubject = subjectOpen + subject.length + 2;
    const tail = source.slice(afterSubject, afterSubject + 400);
    const matcher = new RegExp(`^[\\s.]*(?:not\\.)?(${MATCHERS.join('|')})\\s*\\(`).exec(tail);
    if (!matcher) continue;

    const argOpen = afterSubject + tail.indexOf('(', matcher.index);
    const arg = parenSpan(source, argOpen);
    const lineNo = source.slice(0, m.index).split('\n').length;

    // The annotation may sit anywhere in the contiguous comment block above the
    // assertion, not just the line immediately before it — a reason worth
    // writing is usually more than one line long.
    const here = lines[lineNo - 1] ?? '';
    let annotated = here.includes('pin-ok:');
    for (let i = lineNo - 2; i >= 0 && !annotated; i--) {
      const line = (lines[i] ?? '').trim();
      if (!line.startsWith('//') && !line.startsWith('*') && !line.startsWith('/*')) break;
      if (line.includes('pin-ok:')) annotated = true;
    }
    if (annotated) continue;

    for (const { name } of imports) {
      if (!new RegExp(`\\b${name}\\b`).test(arg)) continue;
      // The subject is the constant itself — that IS the pin. Leave it.
      if (subject.trim() === name) continue;

      if (!isWholeValue(arg, name)) {
        found.push({ file, line: lineNo, constant: name, reason: 'embedded', text: here.trim() });
        continue;
      }
      // A whole-value comparison against an independent oracle is a real check.
      if (ORACLES.some((re) => re.test(subject))) continue;
      if (numeric.get(name) || NUMERIC_MATCHERS.has(matcher[1])) {
        found.push({ file, line: lineNo, constant: name, reason: 'numeric', text: here.trim() });
      }
    }
  }
  return found;
}

describe('assertions do not move with the bug they should catch', () => {
  it('never uses a constant as an expected value where its VALUE is under test', () => {
    const violations = SCOPED.flatMap((dir) => walk(dir))
      .filter((f) => f !== SELF)
      .flatMap((f) => scanSource(readFileSync(f, 'utf8'), f));
    const numeric = violations.filter((v) => v.reason === 'numeric');
    const report = violations
      .map(
        (v) =>
          `  ${relative(process.cwd(), v.file)}:${v.line}  ${v.constant} (${v.reason})\n` +
          `    ${v.text}`,
      )
      .join('\n');
    // A numeric constant as the expected value is the strongest form of the
    // mistake and is never allowed, baseline or not.
    expect(
      numeric,
      numeric.length
        ? `\n${numeric.length} assertion(s) compare a computed value against a numeric\n` +
            `constant, so the expectation moves with the number under test.\n` +
            `Use the literal, then pin the constant separately.\n\n` +
            numeric
              .map(
                (v) =>
                  `  ${relative(process.cwd(), v.file)}:${v.line}  ${v.constant}\n    ${v.text}`,
              )
              .join('\n')
        : undefined,
    ).toEqual([]);

    expect(
      violations.length,
      violations.length > REMAINING_WEAK
        ? `\n${violations.length} assertion(s) would move with the value they should pin.\n\n` +
            `Assert the LITERAL, then pin the constant separately:\n` +
            `    expect(rows()).toHaveLength(8);\n` +
            `    expect(LEGEND_LIMIT).toBe(8);\n\n` +
            `'numeric'  — the number is the thing under test, so it must be literal.\n` +
            `'embedded' — the constant is inside a bigger expression, so even a\n` +
            `             comparison against the vanilla text can match by coincidence.\n\n` +
            `Deliberate? Add \`pin-ok: <reason>\` on that line.\n\n${report}\n`
        : violations.length < REMAINING_WEAK
          ? `\nGood — you fixed some. Lower REMAINING_WEAK to ${violations.length}.\n`
          : undefined,
    ).toBe(REMAINING_WEAK);
  });

  // ── Self-checks, so the guard cannot rot into a no-op that always passes ──

  it('catches an arithmetic expected value', () => {
    const bad = `
      import { TOOLBAR_FALLBACK } from './x';
      expect(measure(900, null).height).toBe(900 - TOOLBAR_FALLBACK);
    `;
    expect(scanSource(bad).map((v) => v.reason)).toEqual(['embedded']);
  });

  it('catches an interpolated one, even against the vanilla source', () => {
    // The 0.12 → 0.15 case: a substring search can match another function's use.
    const bad = `
      import { CAMERA } from './x';
      expect(SOURCE).toContain(\`ratio: \${CAMERA}\`);
    `;
    expect(scanSource(bad).map((v) => v.reason)).toEqual(['embedded']);
  });

  it('catches a count matcher, whatever the constant’s type', () => {
    const bad = `
      import { LEGEND_LIMIT } from './x';
      expect(rows()).toHaveLength(LEGEND_LIMIT);
    `;
    expect(scanSource(bad).map((v) => v.reason)).toEqual(['numeric']);
  });

  it('leaves a bare identity check on a message alone', () => {
    const fine = `
      import { BP_GENERATE_FAILED } from './x';
      expect(bpGenerateError(true, {})).toBe(BP_GENERATE_FAILED);
    `;
    expect(scanSource(fine)).toEqual([]);
  });

  it('leaves a whole-value comparison against an oracle alone', () => {
    const fine = `
      import { WEB_LIBRARY_URL, SHORTCUTS } from './x';
      expect(SOURCE).toContain(WEB_LIBRARY_URL);
      expect(theirs.keys).toEqual(SHORTCUTS[0].keys);
    `;
    expect(scanSource(fine)).toEqual([]);
  });

  it('still catches an INTERPOLATED comparison against an oracle', () => {
    const bad = `
      import { LIMIT } from './x';
      expect(SOURCE).toContain(\`slice(0, \${LIMIT})\`);
    `;
    expect(scanSource(bad).map((v) => v.reason)).toEqual(['embedded']);
  });

  it('leaves the constant-as-subject pin alone', () => {
    const fine = `
      import { TOOLBAR_FALLBACK } from './x';
      expect(TOOLBAR_FALLBACK).toBe(50);
    `;
    expect(scanSource(fine)).toEqual([]);
  });

  it('honours an annotated exception', () => {
    const annotated = `
      import { PALETTE } from './x';
      // pin-ok: the palette is pinned against the vanilla text separately
      expect(colourOf('rock')).toBe(PALETTE[0]);
    `;
    expect(scanSource(annotated)).toEqual([]);
  });

  it('is not fooled by a negated matcher', () => {
    const negated = `
      import { CAP } from './x';
      expect(rows()).not.toHaveLength(CAP);
    `;
    expect(scanSource(negated).map((v) => v.reason)).toEqual(['numeric']);
  });
});
