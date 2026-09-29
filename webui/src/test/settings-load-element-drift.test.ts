import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// Drift guard: loadSettingsData() touches form fields by id without null
// guards, so a settings.js change that references an id missing from
// index.html throws mid-load, aborts the whole settings page (the #879 guard
// then blocks saves too). The verify-flac-decode checkbox shipped exactly
// this way — referenced in the load path, never added to the page.
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

function loadSettingsIds(): string[] {
  const start = source.indexOf('async function loadSettingsData()');
  const end = source.indexOf('// Mirrors ConfigManager.REDACTED_SENTINEL');
  const body = source.slice(start, end);
  const ids = new Set<string>();
  for (const m of body.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)) ids.add(m[1]);
  return [...ids].sort();
}

describe('settings load element drift guard', () => {
  it('every id loadSettingsData touches exists in index.html', () => {
    const ids = loadSettingsIds();
    expect(ids.length).toBeGreaterThan(100);
    const missing = ids.filter((id) => !html.includes(`id="${id}"`));
    expect(missing).toEqual([]);
  });

  it('pins the verify-flac-decode checkbox regression', () => {
    expect(html.includes('id="verify-flac-decode"')).toBe(true);
    expect(loadSettingsIds()).toContain('verify-flac-decode');
  });
});
