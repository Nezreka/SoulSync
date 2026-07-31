import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Lift real functions out of a vanilla script so a port can be compared against
 * the code it replaces, rather than against what the porter believed it did.
 *
 * Reading a function and re-implementing it is how the search port shipped an
 * artist link that went nowhere. Every differential test in the discover port
 * runs the REAL vanilla body beside the port over a matrix of awkward inputs.
 *
 * SOURCE NOTE: this reads the LIVE webui/static/discover.js on purpose. The file
 * still exists during the port PR, so there is no reason to commit a
 * 12,319-line copy of it. When the cleanup PR deletes discover.js, this switches
 * to a frozen `__fixtures__/-vanilla-discover.js` — the same way the downloads
 * port did it — because converting to hand-written expectations would swap
 * "matches the code it replaced" for "matches what I believed it did", which is
 * the exact failure this exists to rule out.
 */
export const VANILLA_DISCOVER = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');

/**
 * Lift one function out by brace-matching, string- and regex-literal aware.
 *
 * A regex cannot do this — the bodies contain nested braces, template literals
 * and regex literals with braces in them.
 *
 * The body's opening brace is found by first walking the PARAMETER LIST to its
 * closing paren. Jumping to the first `{` after the declaration looks equivalent
 * and is not: a default-valued object parameter (`sourceData = {}`) puts a brace
 * inside the parameter list, and matching from there returns a truncated
 * function that fails to parse. That is exactly how `_buildDiscoverArtistContext`
 * broke this harness.
 */
export function extractFunction(name: string, source: string = VANILLA_DISCOVER): string {
  const decl = new RegExp(`^(?:async )?function ${name}\\s*\\(`, 'm');
  const m = decl.exec(source);
  if (!m) throw new Error(`vanilla function ${name} not found`);

  // Walk the parameter list to its matching ')', then take the next '{'.
  let p = source.indexOf('(', m.index);
  let parens = 0;
  for (; p < source.length; p++) {
    if (source[p] === '(') parens++;
    else if (source[p] === ')') {
      parens--;
      if (parens === 0) break;
    }
  }
  let i = source.indexOf('{', p);
  let depth = 0;
  let inString: string | null = null;
  let escaped = false;
  let comment: '//' | '/*' | null = null;

  for (; i < source.length; i++) {
    const c = source[i];
    if (comment === '//') {
      if (c === '\n') comment = null;
      continue;
    }
    if (comment === '/*') {
      if (c === '*' && source[i + 1] === '/') {
        comment = null;
        i++;
      }
      continue;
    }
    if (inString) {
      if (escaped) escaped = false;
      else if (c === '\\') escaped = true;
      else if (c === inString) inString = null;
      continue;
    }
    // COMMENTS MUST BE SKIPPED, not just strings. An apostrophe in a prose
    // comment ("decays over the ripple's life") otherwise opens a phantom string
    // that swallows the rest of the function — which is exactly how extracting
    // `_artMapNodeDisplacement` first failed. A backslash outside a string is an
    // escape inside a regex literal, so skip the char after it and a `\/` can
    // never be mistaken for a comment either.
    if (c === '\\') {
      i++;
      continue;
    }
    if (c === '/' && source[i + 1] === '/') {
      comment = '//';
      i++;
    } else if (c === '/' && source[i + 1] === '*') {
      comment = '/*';
      i++;
    } else if (c === '"' || c === "'" || c === '`') inString = c;
    else if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return source.slice(m.index, i + 1);
    }
  }
  throw new Error(`unbalanced braces extracting ${name}`);
}

/**
 * Evaluate the named vanilla functions in a scratch scope.
 *
 * `escapeHtml` is supplied as IDENTITY on purpose. The vanilla escapes inline
 * because its output is destined for innerHTML; the React port returns raw text
 * and lets React escape at render. Neutralising escapeHtml makes the vanilla
 * return raw text too, so the comparison comes down to the LOGIC — which is the
 * thing that has to match. (Escaping itself is covered by React.)
 *
 * `extraPreamble` supplies whatever module state or collaborators the extracted
 * bodies close over; `extraExports` returns those bindings so a test can inspect
 * what the vanilla wrote to them.
 */
export function loadVanilla<T>(
  names: string[],
  extraPreamble = '',
  extraExports: string[] = [],
): T {
  const preamble = `const escapeHtml = (s) => s;\n${extraPreamble}`;
  const body = names.map((n) => extractFunction(n)).join('\n');
  const exports = [...names, ...extraExports].join(', ');
  return new Function(`${preamble}\n${body}\nreturn { ${exports} };`)() as T;
}
