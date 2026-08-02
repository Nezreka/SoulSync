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
export const VANILLA_DISCOVER = readFileSync(
  resolve(process.cwd(), 'src/routes/discover/__fixtures__/-vanilla-discover.js'),
  'utf8',
);

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
  // 'code' | 'tmpl' — plus a stack recording the brace depth each `${` opened at,
  // so a nested template inside a substitution returns to the right template.
  let mode: 'code' | 'tmpl' = 'code';
  const tmplStack: number[] = [];
  // The last significant character, which is how a `/` is classified: after a
  // value it is division, after an operator or opener it starts a regex.
  let prev = '';

  const REGEX_PRECEDERS = '(,=:[!&|?{};+-*%~^<>\n';

  for (; i < source.length; i++) {
    const c = source[i];

    if (mode === 'tmpl') {
      // Inside a template literal. A backtick closes it; `${` re-enters code.
      if (c === '\\') {
        i++;
        continue;
      }
      if (c === '`') {
        mode = 'code';
        prev = '`';
        continue;
      }
      if (c === '$' && source[i + 1] === '{') {
        tmplStack.push(depth);
        depth++;
        mode = 'code';
        i++;
        prev = '{';
        continue;
      }
      continue;
    }

    // Comments. An apostrophe in prose ("decays over the ripple's life") would
    // otherwise open a phantom string that swallows the rest of the function —
    // exactly how extracting `_artMapNodeDisplacement` first failed.
    if (c === '/' && source[i + 1] === '/') {
      while (i < source.length && source[i] !== '\n') i++;
      continue;
    }
    if (c === '/' && source[i + 1] === '*') {
      i += 2;
      while (i < source.length && !(source[i] === '*' && source[i + 1] === '/')) i++;
      i++;
      continue;
    }

    // A regex literal. `/"/g` inside `.replace(/"/g, '&quot;')` would otherwise
    // read as a division followed by an unterminated string, and everything
    // after it desyncs. Character classes are skipped wholesale so a `/` inside
    // `[^/]` cannot end it early.
    if (c === '/' && REGEX_PRECEDERS.includes(prev)) {
      i++;
      let inClass = false;
      for (; i < source.length; i++) {
        const r = source[i];
        if (r === '\\') {
          i++;
          continue;
        }
        if (r === '[') inClass = true;
        else if (r === ']') inClass = false;
        else if (r === '/' && !inClass) break;
      }
      prev = '/';
      continue;
    }

    if (c === '"' || c === "'") {
      const quote = c;
      i++;
      for (; i < source.length; i++) {
        if (source[i] === '\\') {
          i++;
          continue;
        }
        if (source[i] === quote) break;
      }
      prev = quote;
      continue;
    }

    // A template literal. NESTING is the point: the vanilla's context menu holds
    // a template whose substitution contains another template, and treating
    // every backtick as a plain toggle closes the outer one on the inner's
    // opener — which is how `_artMapSetupInteraction` first failed.
    if (c === '`') {
      mode = 'tmpl';
      continue;
    }

    if (c === '{') {
      depth++;
      prev = '{';
      continue;
    }
    if (c === '}') {
      depth--;
      // Closing a `${…}` returns to the template that opened it.
      if (tmplStack.length && depth === tmplStack[tmplStack.length - 1]) {
        tmplStack.pop();
        mode = 'tmpl';
        continue;
      }
      if (depth === 0) return source.slice(m.index, i + 1);
      prev = '}';
      continue;
    }

    if (!/\s/.test(c) || c === '\n') prev = c;
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
