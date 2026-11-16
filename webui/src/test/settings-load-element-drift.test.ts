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
  // both markers found, in order: a missing one would slice nothing and pass
  if (start < 0 || end <= start) throw new Error('loadSettingsData markers moved');
  const body = source.slice(start, end);
  const ids = new Set<string>();
  for (const m of body.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)) ids.add(m[1]);
  return [...ids].sort();
}

describe('settings load element drift guard', () => {
  it('every id loadSettingsData touches exists in index.html', () => {
    const ids = loadSettingsIds();
    // no settings field is left in the load: every one is React since
    // SETTINGS_PORT.md step 10c. What it still touches is the page's own
    // (the save button, the Hydrabase nav link); a parse that found nothing
    // would miss these two
    expect(ids).toEqual(['hydrabase-nav', 'save-settings']);
    const missing = ids.filter((id) => !html.includes(`id="${id}"`));
    expect(missing).toEqual([]);
  });

  it('pins the verify-flac-decode checkbox regression', () => {
    // the checkbox moved to the React Quality tab (SETTINGS_PORT.md step 6),
    // which renders it and reads it from the same key
    const tab = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/quality-tab.tsx'),
      'utf8',
    );
    expect(tab).toContain('id="verify-flac-decode"');
    expect(tab).toContain('state.settings.post_processing?.verify_flac_decode === true');
    expect(loadSettingsIds()).not.toContain('verify-flac-decode');
  });
});
