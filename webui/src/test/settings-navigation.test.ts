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
  // #1822: Discovery got its own tab, everything else regrouped into the eight
  expect(document.querySelectorAll('.stg-category-nav .stg-tab')).toHaveLength(9);
  expect(document.querySelector('.stg-nav-rail')).toBeNull();
});
it('search leaves mounted forms and their visibility unchanged until selecting a result', () => {
  nav.switchSettingsTab('connections');
  // a part still in the page's own markup (Import Lists, until the video pass
  // ports it): its rows are what video-settings.js keeps
  const rows = document.getElementById('vq-implist-rows') as HTMLElement;
  rows.dataset.probe = 'kept';
  const section = rows.closest('.settings-section-body') as HTMLElement;
  const display = section.style.display;
  nav.filterSettings('Import lists');
  expect(section.style.display).toBe(display);
  expect(document.getElementById('vq-implist-rows')).toBe(rows);
  (document.querySelector('#stg-search-results button') as HTMLButtonElement).click();
  expect(document.querySelector('.stg-tab[aria-current="page"]')?.getAttribute('data-tab')).toBe(
    'discovery',
  );
  expect(section.classList.contains('collapsed')).toBe(false);
  expect(rows.dataset.probe).toBe('kept');
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
