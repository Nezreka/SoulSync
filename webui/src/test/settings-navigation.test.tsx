import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
import { filterSettings, switchSettingsTab } from '@/features/settings/shell';

// the tab switch and the search are shell.ts's (settings.js's, until
// SETTINGS_PORT.md step V4b), run here on the page (React since step V4c;
// its shell without the tabs)
const nav = { switchSettingsTab, filterSettings };
beforeEach(() => {
  render(<SettingsPage tabs={{}} />);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);
it('uses one category bar instead of a second sidebar', () => {
  expect(document.querySelectorAll('.stg-category-nav .stg-tabbar')).toHaveLength(1);
  // #1822: Discovery got its own tab, everything else regrouped into the
  // eight; #2166 added People, which only an admin sees
  expect(document.querySelectorAll('.stg-category-nav .stg-tab')).toHaveLength(10);
  expect(document.querySelectorAll('.stg-category-nav .stg-tab:not([hidden])')).toHaveLength(9);
  expect(
    document.querySelector<HTMLButtonElement>('.stg-category-nav .stg-tab[data-tab="people"]')!
      .hidden,
  ).toBe(true);
  expect(document.querySelector('.stg-nav-rail')).toBeNull();
});
it('search leaves mounted forms and their visibility unchanged until selecting a result', () => {
  nav.switchSettingsTab('connections');
  // the page's own group (the Sources tab's Download Sources heading; every
  // other section is a tab's, SETTINGS_PORT.md)
  const group = document.querySelector(
    '#settings-page .settings-group[data-stg="sources"]',
  ) as HTMLElement;
  group.dataset.probe = 'kept';
  const display = group.style.display;
  nav.filterSettings('Download Sources');
  expect(group.style.display).toBe(display);
  expect(document.querySelector('#settings-page .settings-group[data-stg="sources"]')).toBe(group);
  (document.querySelector('#stg-search-results button') as HTMLButtonElement).click();
  expect(document.querySelector('.stg-tab[aria-current="page"]')?.getAttribute('data-tab')).toBe(
    'sources',
  );
  expect(group.dataset.probe).toBe('kept');
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
