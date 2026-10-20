import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Every .css file under `dir`, skipping build output and installed packages. */
function cssFiles(dir: string): string[] {
  return readdirSync(dir, { recursive: true, encoding: 'utf8' })
    .map((p) => join(dir, p).replace(/\\/g, '/'))
    .filter((p) => p.endsWith('.css') && !p.includes('node_modules') && !p.includes('static/dist'))
    .sort();
}

/**
 * An `-rgb` custom property holds a bare channel triple for
 * `rgba(var(--x-rgb), a)`. Its fallback has to be the same shape: a
 * space-separated `88 101 242` makes `rgba(88 101 242, 0.16)`, which is invalid,
 * so whenever the var is unset the whole declaration silently drops out.
 */
describe('-rgb fallbacks', () => {
  it('are comma-separated triples or another var', () => {
    const bad: string[] = [];
    for (const file of [...cssFiles('static'), ...cssFiles('src')]) {
      const css = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      for (const [use, fallback] of css.matchAll(/var\(\s*--[\w-]+-rgb\s*,\s*([^)]*)\)/g)) {
        const value = fallback.trim();
        if (
          !/^\d+(\.\d+)?\s*,\s*\d+(\.\d+)?\s*,\s*\d+(\.\d+)?$/.test(value) &&
          !value.startsWith('var(')
        ) {
          bad.push(`${file}: ${use}`);
        }
      }
    }
    expect(bad).toEqual([]);
  });
});
