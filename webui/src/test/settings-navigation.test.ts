import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, expect, it, vi } from 'vitest';
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const block = source.slice(
  source.indexOf('function switchSettingsTab('),
  source.indexOf('window.filterSettings = filterSettings;'),
);
let nav: { switchSettingsTab: (tab: string) => void; filterSettings: (query: string) => void };
beforeEach(() => {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.body.replaceChildren(parsed.getElementById('settings-page')!);
  Element.prototype.scrollIntoView = vi.fn();
  nav = new Function(
    'document',
    'window',
    'autoTestSourcesOnce',
    '_logViewerStop',
    '_logViewerInit',
    'switchLibraryMediaTab',
    `${block}; return {switchSettingsTab, filterSettings};`,
  )(document, window, vi.fn(), vi.fn(), vi.fn(), vi.fn());
});
it('uses one category bar instead of a second sidebar', () => {
  expect(document.querySelectorAll('.stg-category-nav .stg-tabbar')).toHaveLength(1);
  expect(document.querySelectorAll('.stg-category-nav .stg-tab')).toHaveLength(8);
  expect(document.querySelector('.stg-nav-rail')).toBeNull();
});
it('search leaves mounted forms and their visibility unchanged until selecting a result', () => {
  nav.switchSettingsTab('connections');
  const input = document.getElementById('video-movies-path') as HTMLInputElement;
  input.value = '/unsaved';
  const section = input.closest('[data-stg]') as HTMLElement;
  const display = section.style.display;
  nav.filterSettings('Additional Movie Libraries');
  expect(section.style.display).toBe(display);
  expect(document.getElementById('video-movies-path')).toBe(input);
  const shown = vi.fn();
  document.addEventListener('soulsync:library-settings-shown', shown, { once: true });
  (document.querySelector('#stg-search-results button') as HTMLButtonElement).click();
  expect(shown).toHaveBeenCalledOnce();
  expect(document.querySelector('.stg-tab[aria-current="page"]')?.getAttribute('data-tab')).toBe(
    'library',
  );
  expect(input.value).toBe('/unsaved');
});
it('has an empty state and clearing search does not switch categories', () => {
  nav.switchSettingsTab('library');
  nav.filterSettings('no-such-setting-xyz');
  expect(document.querySelector('#stg-search-results [role="status"]')?.textContent).toContain(
    'No settings found',
  );
  nav.filterSettings('');
  expect((document.getElementById('stg-search-results') as HTMLElement).hidden).toBe(true);
  expect(document.querySelector('.stg-tab.active')?.getAttribute('data-tab')).toBe('library');
});
