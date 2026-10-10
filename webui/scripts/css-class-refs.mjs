#!/usr/bin/env node
/**
 * Which legacy CSS classes does the app still reference?
 *
 * Scans the code that can put a class on an element and reports CSS classes
 * that never appear there. Run from webui/:
 *
 *   node scripts/css-class-refs.mjs static/style.css               # dead rules
 *   node scripts/css-class-refs.mjs static/style.css --from 100 --to 900
 *   node scripts/css-class-refs.mjs static/style.css --classes     # per class
 *   node scripts/css-class-refs.mjs --removed-since <git-ref>      # verify a deletion
 *
 * A rule is dead when every selector in its list needs at least one dead
 * class. --removed-since lists the selectors (per @media context) that the
 * ref's legacy stylesheets had and today's don't, and exits 1 if any of them
 * could still match, i.e. needs no dead class.
 *
 * WHAT IS SCANNED
 *   webui/static/**.{js,html,json} (not dist/), webui/*.html, non-test
 *   webui/src/**.{ts,tsx}, templates/, and every .py file in the repo outside
 *   tests/ (web_server.py, core/, api/, services/, …), since the server
 *   renders markup and sends status strings the JS turns into classes.
 *
 * WHEN A CLASS COUNTS AS LIVE (the heuristics lean towards "live": a false
 * "live" keeps a dead rule, a false "dead" deletes a live one)
 *   1. Token. Its exact name appears anywhere in the scanned code, in a
 *      string, an identifier or a comment. This covers literal classes,
 *      lookup tables ({ ok: 'badge-ok' }) and server-sent values that are
 *      literals in the Python.
 *   2. Prefix. It starts with a fragment the code completes at runtime:
 *      a string literal that ends in '-' or '_' ('foo-' + x, "a foo_" + x,
 *      or a constant like const P = 'foo-'; not a bare 'foo-' listed in an
 *      array or passed as a later argument, which is a pattern being matched
 *      against, like helper.js's page hints), a template or f-string fragment
 *      in front of an interpolation (`foo-${x}`, f"foo-{x}", "foo-%s",
 *      "foo-{}".format), 'foo' + '-' + x, and ['foo', x].join('-') or
 *      '-'.join(['foo', x]).
 *   3. Suffix. It ends in a fragment that starts with '-' or '_' (x + '-on',
 *      `${x}-bar`, f"{x}-bar", "%s-bar") and the rest of its name is itself a
 *      token or a prefix match (`${base}-icon` with base = 'enh' keeps
 *      .enh-icon live).
 *   4. Class contexts. Inside classList.add/toggle/remove/replace/contains(…),
 *      className = / += …, setAttribute('class', …), className={…},
 *      class="…" and clsx/cn(…), any literal glued to a computed part counts
 *      as a prefix or suffix even without a '-' or '_' ('is' + State,
 *      `${kind}Badge`).
 *   5. Attribute selectors in code. A [class*="x"], [class^="x"],
 *      [class$="x"] or [class|="x"] that the code queries keeps every class
 *      containing, starting with or ending in x live.
 *
 * LIMITS (what it can still miss, so read the output before deleting)
 *   - A name assembled from pieces with no '-'/'_' boundary outside a class
 *     context: const k = 'btn' + kind; … el.className = k.
 *   - Names transformed after the fact: toLowerCase(), replace(), slicing,
 *     or a value only known at runtime (API payloads, settings, database
 *     rows, third-party scripts) that never appears as a literal.
 *   - Classes added by code outside the scanned paths, e.g. markup inside
 *     Markdown, images or a CDN script.
 *   - Rules this tool calls dead because their only class is unreferenced
 *     can still be needed when CSS alone toggles them, e.g. a class the HTML
 *     never had but a :has() or sibling selector expects. Checking the
 *     selector by eye stays part of the job.
 * The selector side ignores classes inside :not() (the element must not have
 * them) and :is()/:where()/:matches()/:-*-any() (one alternative of several),
 * and ignores attribute selectors and strings.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import postcss from 'postcss';

const WEBUI = resolve(import.meta.dirname, '..');
const REPO = resolve(WEBUI, '..');

const SOURCES = [
  { dir: join(WEBUI, 'static'), ext: /\.(js|html|json)$/, skip: /[\\/]dist[\\/]/ },
  { dir: WEBUI, ext: /\.html$/, depth: 0 },
  { dir: join(WEBUI, 'src'), ext: /\.tsx?$/, skip: /\.test\.tsx?$|[\\/]test[\\/]/ },
  { dir: join(REPO, 'templates'), ext: /\.(html|js|py)$/ },
  {
    dir: REPO,
    ext: /\.py$/,
    skip: new RegExp(`^${escapeRe(REPO)}[\\\\/](tests|webui|node_modules|venv)[\\\\/]`),
  },
];

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function walk(dir, ext, skip, depth, out) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (name === 'node_modules' || name === '__pycache__' || name.startsWith('.')) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (depth === 0 || (skip && skip.test(`${full}/`))) continue;
      walk(full, ext, skip, depth === undefined ? undefined : depth - 1, out);
    } else if (ext.test(name) && !(skip && skip.test(full))) {
      out.push(full);
    }
  }
  return out;
}

export function sourceFiles() {
  const files = [];
  for (const s of SOURCES) walk(s.dir, s.ext, s.skip, s.depth, files);
  return [...new Set(files)];
}

// A name-ish fragment: starts with a letter or '_', then word chars or '-'.
const NAME = String.raw`[A-Za-z_][\w-]*`;
const QUOTE = String.raw`['"\x60]`;

// Calls and assignments whose argument is a class list. `attr` contexts are
// a class="…" attribute inside markup, which ends at its own quote.
const CLASS_CONTEXTS = [
  { re: /\.classList\.(?:add|toggle|remove|replace|contains)\s*\(/g },
  { re: /\bclassName\s*\+?=\s*(?![=>])/g },
  { re: /\bsetAttribute\(\s*['"]class['"]\s*,/g },
  { re: /\b(?:clsx|cn|classNames)\s*\(/g },
  { re: /\bclass\s*=\s*\\?(['"])/g, attr: true },
];

/** End of the string literal that opens at `i`. */
function stringEnd(text, i) {
  const q = text[i];
  let depth = 0;
  for (let j = i + 1; j < text.length; j++) {
    const ch = text[j];
    if (ch === '\\') j++;
    else if (q === '`' && ch === '$' && text[j + 1] === '{') (depth++, j++);
    else if (q === '`' && ch === '}' && depth) depth--;
    else if (ch === q && !depth) return j;
    else if (ch === '\n' && q !== '`') return j;
  }
  return text.length;
}

/**
 * The expression that starts at `start`: up to an unmatched closing bracket,
 * a ';' or line end, or a string literal that nothing continues (JSX's
 * className="a" key={…}).
 */
function expressionText(text, start) {
  let depth = 0;
  let i = start;
  while (i < text.length && i - start < 2000) {
    const ch = text[i];
    if (ch === '"' || ch === "'" || ch === '`') {
      i = stringEnd(text, i) + 1;
      if (depth) continue;
      const next = text.slice(i).match(/^[ \t]*(\S?)/)[1];
      if (!next || !'+?:.)]}'.includes(next)) break;
      continue;
    }
    if (ch === '(' || ch === '{' || ch === '[') depth++;
    else if (ch === ')' || ch === '}' || ch === ']') {
      if (depth === 0) break;
      depth--;
    } else if ((ch === ';' || ch === '\n') && depth === 0) break;
    i++;
  }
  return text.slice(start, i);
}

/** The value of a class="…" attribute that opens just before `start`. */
function attrText(text, start, quote) {
  const end = text.indexOf(quote, start);
  return text.slice(start, end === -1 ? Math.min(text.length, start + 500) : end);
}

/**
 * Every token, prefix, suffix and attribute pattern in some sources: strings,
 * or { path, text } (a .py path skips the [class*=…] rule, because the
 * Python's selectors scrape third-party pages, not ours).
 */
export function refsFromTexts(sources) {
  const tokens = new Set();
  const prefixes = new Set();
  const suffixes = new Set();
  const contains = new Set();
  const starts = new Set();
  const ends = new Set();
  const add = (set, re, text, f = (m) => m[1]) => {
    for (const m of text.matchAll(re)) set.add(f(m));
  };
  for (const source of sources) {
    const { path = '', text } = typeof source === 'string' ? { text: source } : source;
    add(tokens, new RegExp(NAME, 'g'), text, (m) => m[0]);

    // 'foo-' + x, const P = 'foo_', `foo-${x}`, f"foo-{x}", "foo-%s". A bare
    // 'foo-' that is an array element or a later argument and is not
    // concatenated is a pattern to match against (helper.js's page hints),
    // not a name being built.
    const builders = text.replace(
      new RegExp(String.raw`([[,]\s*)(['"])${NAME}[-_]\2(?!\s*\+)`, 'g'),
      '$1""',
    );
    add(prefixes, new RegExp(String.raw`(${NAME}[-_])(?:${QUOTE}|\$\{|\{|%)`, 'g'), builders);
    // 'foo' + '-' + x
    add(
      prefixes,
      new RegExp(String.raw`(${NAME})['"]\s*\+\s*['"]([-_])`, 'g'),
      text,
      (m) => m[1] + m[2],
    );
    // ['foo', x].join('-')
    add(
      prefixes,
      new RegExp(String.raw`\[\s*['"](${NAME})['"]\s*,[^\]]*\]\s*\.join\(\s*['"]([-_])['"]`, 'g'),
      text,
      (m) => m[1] + m[2],
    );
    // '-'.join(['foo', x])
    add(
      prefixes,
      new RegExp(String.raw`['"]([-_])['"]\s*\.join\(\s*[[(]\s*['"](${NAME})['"]`, 'g'),
      text,
      (m) => m[2] + m[1],
    );

    // x + '-on', `${x}-bar`, f"{x}-bar", "%s-bar"
    add(suffixes, new RegExp(String.raw`(?:${QUOTE}|\}|%[sd])([-_][\w-]*\w)`, 'g'), text);

    // [class*="x"] and friends, in querySelector and the like.
    const selectors = path.endsWith('.py')
      ? []
      : text.matchAll(/\[\s*class\s*([*^$|~]?)=\s*(['"]?)([^'"\]\s]+)\2\s*\]/g);
    for (const m of selectors) {
      const [, op, , value] = m;
      if (op === '*') contains.add(value);
      else if (op === '^') starts.add(value);
      else if (op === '$') ends.add(value);
      else if (op === '|') prefixes.add(`${value}-`);
      tokens.add(value);
    }

    for (const { re, attr } of CLASS_CONTEXTS) {
      for (const m of text.matchAll(re)) {
        const at = m.index + m[0].length;
        const ctx = attr ? attrText(text, at, m[0].slice(-1)) : expressionText(text, at);
        add(prefixes, new RegExp(String.raw`(${NAME})(?:['"]\s*\+|\$\{)`, 'g'), ctx);
        add(suffixes, new RegExp(String.raw`(?:\+\s*['"]|\})([\w-]*\w)`, 'g'), ctx);
      }
    }
  }
  return {
    tokens,
    prefixes: [...prefixes],
    suffixes: [...suffixes],
    contains: [...contains],
    starts: [...starts],
    ends: [...ends],
  };
}

/** Every reference in the scanned code (or in the given files). */
export function collectRefs(files = sourceFiles()) {
  return refsFromTexts(files.map((path) => ({ path, text: readFileSync(path, 'utf8') })));
}

const startsWith = (cls, refs) =>
  refs.prefixes.some((p) => cls.startsWith(p) && cls.length > p.length);

export function isLive(cls, refs) {
  if (refs.tokens.has(cls)) return true;
  if (startsWith(cls, refs)) return true;
  if (refs.contains.some((v) => cls.includes(v))) return true;
  if (refs.starts.some((v) => cls.startsWith(v))) return true;
  if (refs.ends.some((v) => cls.endsWith(v))) return true;
  return refs.suffixes.some((s) => {
    if (!cls.endsWith(s) || cls.length <= s.length) return false;
    const stem = cls.slice(0, -s.length);
    return refs.tokens.has(stem) || startsWith(stem, refs);
  });
}

const CLASS_RE = /\.(-?[_a-zA-Z][\w-]*)/g;

// A class inside :not() is one the element must NOT have, and one inside
// :is()/:where()/:matches()/:-*-any() is one alternative of several, so
// neither is a class the selector needs.
const OPTIONAL_PSEUDO = /:(?:not|is|where|matches|-webkit-any|-moz-any)\(/gi;

function dropOptionalPseudos(selector) {
  let out = '';
  let last = 0;
  for (const m of selector.matchAll(OPTIONAL_PSEUDO)) {
    if (m.index < last) continue;
    out += selector.slice(last, m.index);
    let depth = 1;
    let i = m.index + m[0].length;
    for (; i < selector.length && depth; i++) {
      if (selector[i] === '(') depth++;
      else if (selector[i] === ')') depth--;
    }
    last = i;
  }
  return out + selector.slice(last);
}

export function selectorClasses(selector) {
  // Drop attribute selectors and strings so `[href$=".css"]` isn't a class.
  const clean = dropOptionalPseudos(
    selector.replace(/\[[^\]]*\]/g, '').replace(/(['"]).*?\1/g, ''),
  );
  return [...clean.matchAll(CLASS_RE)].map((m) => m[1]);
}

function inKeyframes(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (p.type === 'atrule' && /keyframes$/i.test(p.name)) return true;
  }
  return false;
}

/** Rules of a stylesheet with their line span and dead/live verdict. */
export function analyseRules(cssText, refs) {
  const root = postcss.parse(cssText);
  const rules = [];
  root.walkRules((rule) => {
    if (inKeyframes(rule)) return;
    const selectors = rule.selectors;
    const deadClasses = new Set();
    let deadSelectors = 0;
    for (const sel of selectors) {
      const dead = selectorClasses(sel).filter((c) => !isLive(c, refs));
      if (dead.length) {
        deadSelectors++;
        dead.forEach((c) => deadClasses.add(c));
      }
    }
    rules.push({
      selector: rule.selector,
      start: rule.source.start.line,
      end: rule.source.end.line,
      dead: deadSelectors === selectors.length,
      partlyDead: deadSelectors > 0 && deadSelectors < selectors.length,
      deadClasses: [...deadClasses],
    });
  });
  return rules;
}

export function legacyStylesheets() {
  return walk(join(WEBUI, 'static'), /\.css$/, /[\\/]dist[\\/]/, undefined, []);
}

/** Every individual selector, keyed by the at-rules it sits in. */
function selectorKeys(cssText, name) {
  const out = new Set();
  let root;
  try {
    root = postcss.parse(cssText);
  } catch (err) {
    console.warn(`skipping ${name}: ${err.reason} at line ${err.line}`);
    return out;
  }
  root.walkRules((rule) => {
    if (inKeyframes(rule)) return;
    const ctx = [];
    for (let p = rule.parent; p && p.type === 'atrule'; p = p.parent)
      ctx.unshift(`@${p.name} ${p.params}`.replace(/\s+/g, ' '));
    for (const sel of rule.selectors) out.add([...ctx, sel.replace(/\s+/g, ' ')].join(' | '));
  });
  return out;
}

/**
 * Selectors (per @media context) in the before sheets that are gone from the
 * after sheets, and the ones of those that need no dead class.
 */
export function removedSelectors(beforeSheets, afterSheets, refs) {
  const before = new Set();
  for (const { name, text } of beforeSheets) selectorKeys(text, name).forEach((k) => before.add(k));
  const after = new Set();
  for (const { name, text } of afterSheets) selectorKeys(text, name).forEach((k) => after.add(k));
  // A selector that moved out to an enclosing context (a later top-level rule
  // overriding an @media copy) still matches, so it isn't gone.
  const enclosed = (k) => {
    const parts = k.split(' | ');
    for (let i = parts.length - 1; i > 0; i--)
      if (after.has([...parts.slice(0, i - 1), parts.at(-1)].join(' | '))) return true;
    return false;
  };
  const gone = [...before].filter((k) => !after.has(k) && !enclosed(k)).sort();
  const live = gone.filter((k) => {
    const sel = k.split(' | ').at(-1);
    return selectorClasses(sel).every((c) => isLive(c, refs));
  });
  return { gone, live };
}

function removedSince(ref, refs) {
  const list = execFileSync('git', ['ls-tree', '-r', '--name-only', ref, '--', 'static'], {
    cwd: WEBUI,
    encoding: 'utf8',
  });
  const beforeSheets = list
    .split('\n')
    .filter((p) => p.endsWith('.css') && !p.includes('/dist/'))
    .map((path) => ({
      name: `${ref}:${path}`,
      text: execFileSync('git', ['show', `${ref}:webui/${path}`], {
        cwd: WEBUI,
        encoding: 'utf8',
        maxBuffer: 1 << 28,
      }),
    }));
  const afterSheets = legacyStylesheets().map((f) => ({
    name: relative(WEBUI, f),
    text: readFileSync(f, 'utf8'),
  }));
  const { gone, live } = removedSelectors(beforeSheets, afterSheets, refs);
  console.log(`${gone.length} selectors no longer in legacy CSS since ${ref}`);
  if (live.length) {
    console.log(`${live.length} of them can still match (no dead class):`);
    for (const k of live) console.log(`  ${k}`);
    process.exitCode = 1;
  } else {
    console.log('every one of them needs a class the code never uses');
  }
}

function main(argv) {
  const args = argv.slice(2);
  const opt = (name) => {
    const i = args.indexOf(name);
    return i === -1 ? undefined : args[i + 1];
  };
  const refs = collectRefs();
  const ref = opt('--removed-since');
  if (ref) return removedSince(ref, refs);

  const file = args.find((a) => a.endsWith('.css'));
  if (!file) {
    console.error('usage: css-class-refs.mjs <file.css> [--from N --to N] [--classes]');
    process.exitCode = 2;
    return;
  }
  const from = Number(opt('--from') ?? 1);
  const to = Number(opt('--to') ?? Infinity);
  const rules = analyseRules(readFileSync(file, 'utf8'), refs).filter(
    (r) => r.start >= from && r.end <= to,
  );

  if (args.includes('--classes')) {
    const seen = new Map();
    for (const r of rules)
      for (const sel of r.selector.split(','))
        for (const c of selectorClasses(sel)) seen.set(c, isLive(c, refs));
    for (const [c, live] of [...seen].sort()) console.log(`${live ? 'live' : 'DEAD'} .${c}`);
    return;
  }

  const dead = rules.filter((r) => r.dead);
  const lines = dead.reduce((n, r) => n + r.end - r.start + 1, 0);
  for (const r of dead) console.log(`${r.start}-${r.end}\t${r.selector.replace(/\s+/g, ' ')}`);
  for (const r of rules.filter((x) => x.partlyDead))
    console.log(`${r.start}-${r.end}\tPARTLY\t${r.deadClasses.join(' ')}`);
  console.log(
    `${relative(WEBUI, resolve(file))}: ${dead.length}/${rules.length} rules dead (${lines} lines)`,
  );
}

if (import.meta.url === `file://${process.argv[1]}`) main(process.argv);
