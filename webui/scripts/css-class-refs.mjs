#!/usr/bin/env node
/**
 * Which legacy CSS classes does the app still reference?
 *
 * Scans the code that can put a class on an element (legacy JS, HTML, the
 * React sources, and the Python that renders markup) and reports CSS classes
 * that never appear there. A class counts as live when its exact name shows
 * up as a token anywhere in that code, when it starts with a prefix the code
 * builds by concatenation ('foo-' + x, `foo-${x}`), or when it is a code token
 * followed by a suffix the code concatenates (`${prefix}-icon` with
 * prefix = 'enh' keeps .enh-icon live).
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
 * Run from webui/.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import postcss from 'postcss';

const WEBUI = resolve(import.meta.dirname, '..');
const REPO = resolve(WEBUI, '..');

const SOURCES = [
  { dir: join(WEBUI, 'static'), ext: /\.(js|html)$/, skip: /[\\/](dist)[\\/]/ },
  { dir: WEBUI, ext: /\.html$/, depth: 0 },
  { dir: join(WEBUI, 'src'), ext: /\.tsx?$/, skip: /\.test\.tsx?$|[\\/]test[\\/]/ },
  { dir: join(REPO, 'templates'), ext: /\.(html|js|py)$/ },
  { dir: join(REPO, 'core'), ext: /\.(py|html|js)$/ },
  { dir: join(REPO, 'api'), ext: /\.(py|html|js)$/ },
  { dir: join(REPO, 'services'), ext: /\.(py|html|js)$/ },
  { file: join(REPO, 'web_server.py') },
];

function walk(dir, ext, skip, depth, out) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (name === 'node_modules' || name.startsWith('.')) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (depth === 0) continue;
      walk(full, ext, skip, depth === undefined ? undefined : depth - 1, out);
    } else if (ext.test(name) && !(skip && skip.test(full))) {
      out.push(full);
    }
  }
  return out;
}

export function sourceFiles() {
  const files = [];
  for (const s of SOURCES) {
    if (s.file) files.push(s.file);
    else walk(s.dir, s.ext, s.skip, s.depth, files);
  }
  return [...new Set(files)];
}

/** Every identifier-ish token in the code, plus concatenation prefixes. */
export function collectRefs(files = sourceFiles()) {
  const tokens = new Set();
  const prefixes = new Set();
  const suffixes = new Set();
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    for (const m of text.matchAll(/[A-Za-z_][\w-]*/g)) tokens.add(m[0]);
    // 'foo-' + x   "foo-" + x   'a foo-' + x
    for (const m of text.matchAll(/([\w-]+-)['"]\s*\+/g)) prefixes.add(m[1]);
    // `foo-${x}`
    for (const m of text.matchAll(/([\w-]+-)\$\{/g)) prefixes.add(m[1]);
    // 'foo' + '-' + x  →  prefix 'foo-'
    for (const m of text.matchAll(/([\w-]+)['"]\s*\+\s*['"]-/g)) prefixes.add(`${m[1]}-`);
    // x + '-foo'   `${x}-foo`
    for (const m of text.matchAll(/\+\s*['"](-[\w-]+)/g)) suffixes.add(m[1]);
    for (const m of text.matchAll(/\}(-[\w-]+)/g)) suffixes.add(m[1]);
  }
  return { tokens, prefixes: [...prefixes], suffixes: [...suffixes] };
}

export function isLive(cls, refs) {
  if (refs.tokens.has(cls)) return true;
  if (refs.prefixes.some((p) => cls.startsWith(p) && cls.length > p.length)) return true;
  return refs.suffixes.some(
    (s) => cls.endsWith(s) && cls.length > s.length && refs.tokens.has(cls.slice(0, -s.length)),
  );
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

function removedSince(ref, refs) {
  const before = new Set();
  const list = execFileSync('git', ['ls-tree', '-r', '--name-only', ref, '--', 'static'], {
    cwd: WEBUI,
    encoding: 'utf8',
  });
  for (const path of list.split('\n').filter((p) => p.endsWith('.css') && !p.includes('/dist/'))) {
    const text = execFileSync('git', ['show', `${ref}:webui/${path}`], {
      cwd: WEBUI,
      encoding: 'utf8',
      maxBuffer: 1 << 28,
    });
    selectorKeys(text, `${ref}:${path}`).forEach((k) => before.add(k));
  }
  const after = new Set();
  for (const f of legacyStylesheets())
    selectorKeys(readFileSync(f, 'utf8'), relative(WEBUI, f)).forEach((k) => after.add(k));
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
