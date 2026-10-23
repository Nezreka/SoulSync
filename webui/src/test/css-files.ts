import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

/** Helpers for the CSS guardrail tests (css-budget, css-module-classes). */

export const WEBUI = process.cwd();

/** Build output (vite outDir) and installed packages: not source. */
const SKIP_DIRS = new Set(['node_modules', 'static/dist']);

/** Every file under `dir` (relative to webui/) whose path passes `keep`, as webui-relative paths. */
export function listFiles(dir: string, keep: (path: string) => boolean): string[] {
  const root = resolve(WEBUI, dir);
  const out: string[] = [];
  const walk = (abs: string) => {
    for (const entry of readdirSync(abs, { withFileTypes: true })) {
      const next = join(abs, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(relative(WEBUI, next).replace(/\\/g, '/'))) walk(next);
      } else if (keep(next)) {
        out.push(relative(WEBUI, next).replace(/\\/g, '/'));
      }
    }
  };
  walk(root);
  return out.sort();
}

export function read(path: string): string {
  return readFileSync(resolve(WEBUI, path), 'utf8').replace(/\r\n/g, '\n');
}

/** CSS with comments removed, so commented-out rules don't count. */
export function readCss(path: string): string {
  return read(path).replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Legacy global sheets. tokens.css is excluded: it is where literals are supposed to live. */
export const legacySheets = () =>
  listFiles('static', (p) => p.endsWith('.css') && !p.endsWith('tokens.css'));

export const moduleSheets = () => listFiles('src', (p) => p.endsWith('.module.css'));
