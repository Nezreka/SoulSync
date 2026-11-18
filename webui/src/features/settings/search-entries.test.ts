/**
 * Help search's Settings entries (help plan 6.1): every anchor is in the tab
 * file it names, so moving or renaming a field fails here, not in front of
 * someone searching. And landing on one opens the tab, the section and the
 * field.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ACCOUNT_ENTRIES,
  SETTINGS_ENTRIES,
  findAnchor,
  landSetting,
  tabTitle,
  type SettingsEntry,
} from './search-entries';
import { TAB_TITLES } from './shell';

const TABS = join(process.cwd(), 'src', 'features', 'settings', 'tabs');
const read = (file: string) => readFileSync(join(TABS, file), 'utf8');

function anchorIn(entry: SettingsEntry, source: string): boolean {
  if ('id' in entry.anchor) {
    const id = entry.anchor.id;
    // the People tab's part buttons: id={`ppl-pill-${id}`}, one per PILLS row
    const pill = /^ppl-pill-([a-z]+)$/.exec(id);
    if (pill && source.includes('id={`ppl-pill-${id}`}')) return source.includes(`['${pill[1]}', `);
    return source.includes(`"${id}"`) || source.includes(`'${id}'`);
  }
  const h = entry.anchor.heading.replace(/&/g, '&amp;');
  const raw = entry.anchor.heading;
  return [h, raw].some(
    (t) =>
      source.includes(`title="${t}"`) ||
      new RegExp(`>${t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</h[34]>`).test(source),
  );
}

describe('the settings entries', () => {
  it('every anchor is in the file it names', () => {
    const missing = SETTINGS_ENTRIES.filter((e) => !anchorIn(e, read(e.file))).map(
      (e) => `${e.label}: ${JSON.stringify(e.anchor)} in ${e.file}`,
    );
    expect(missing).toEqual([]);
  });

  it('ids are unique and every tab is a real tab', () => {
    const ids = SETTINGS_ENTRIES.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const e of SETTINGS_ENTRIES) expect(Object.keys(TAB_TITLES), e.label).toContain(e.tab);
    expect(tabTitle('advanced')).toBe('System');
    expect(SETTINGS_ENTRIES.length).toBeGreaterThan(100);
  });

  it('every My Account heading exists in the modal', () => {
    const modal = readFileSync(join(process.cwd(), 'src', 'shell', 'my-accounts.ts'), 'utf8');
    for (const e of ACCOUNT_ENTRIES) expect(modal, e.anchor).toContain(`id="${e.anchor}"`);
  });

  it('no copy uses a dash for a pause', () => {
    for (const e of SETTINGS_ENTRIES) {
      expect(`${e.label} ${e.hint ?? ''}`, e.label).not.toMatch(/[–—]/);
    }
  });
});

describe('landing on one', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    delete window.navigateToPage;
    delete window.switchSettingsTab;
    vi.useRealTimers();
  });

  /** the settings page as the shell renders it: tabs, a collapsed section */
  function page() {
    document.body.innerHTML = `
      <div id="settings-page">
        <button class="stg-tab" data-tab="library">Library</button>
        <button class="stg-tab" data-tab="downloads">Downloads</button>
        <div class="settings-section-header collapsed" data-stg="downloads"><h3>Retry Logic</h3></div>
        <div class="settings-section-body collapsed" data-stg="downloads" style="display:none">
          <div class="form-group"><label>x</label><input id="max-concurrent-downloads"></div>
        </div>
      </div>`;
    const header = document.querySelector<HTMLElement>('.settings-section-header')!;
    header.addEventListener('click', () => {
      header.classList.toggle('collapsed');
      const body = header.nextElementSibling as HTMLElement;
      body.classList.toggle('collapsed');
      body.style.display = body.classList.contains('collapsed') ? 'none' : '';
    });
  }

  it('goes to Settings, opens the tab, expands the section and marks the field', async () => {
    page();
    const go = vi.fn();
    const tab = vi.fn();
    window.navigateToPage = go;
    window.switchSettingsTab = tab;
    const entry = SETTINGS_ENTRIES.find((e) => e.label === 'Concurrent downloads')!;
    const ok = await landSetting(entry, 'music');
    expect(ok).toBe(true);
    expect(go).toHaveBeenCalledWith('settings');
    expect(tab).toHaveBeenCalledWith('downloads');
    const body = document.querySelector<HTMLElement>('.settings-section-body')!;
    expect(body.classList.contains('collapsed')).toBe(false);
    expect(document.querySelector('.form-group')!.classList.contains('stg-search-target')).toBe(
      true,
    );
    expect(document.getElementById('max-concurrent-downloads')).toHaveFocus();
  });

  it('opens the People part an entry sits on before looking for it', async () => {
    document.body.innerHTML = `
      <div id="settings-page">
        <button class="stg-tab" data-tab="people">People</button>
      </div>`;
    window.navigateToPage = vi.fn();
    window.switchSettingsTab = vi.fn();
    const part = vi.fn((which: string) => {
      // the part mounts its pane once it is picked
      if (which === 'templates') {
        const pane = document.createElement('div');
        pane.id = 'ppl-tpl-pane';
        document.getElementById('settings-page')!.append(pane);
      }
    });
    (window as unknown as { openPeopleSettings: typeof part }).openPeopleSettings = part;
    const entry = SETTINGS_ENTRIES.find((e) => e.label === 'New people start as')!;
    expect(await landSetting(entry, 'music')).toBe(true);
    expect(window.switchSettingsTab).toHaveBeenCalledWith('people');
    expect(part).toHaveBeenCalledWith('templates');
    delete (window as unknown as { openPeopleSettings?: unknown }).openPeopleSettings;
  });

  it('finds a section by its heading', () => {
    page();
    expect(findAnchor({ heading: 'Retry Logic' })?.tagName).toBe('H3');
    expect(findAnchor({ heading: 'Nope' })).toBeNull();
  });
});
