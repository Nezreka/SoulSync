import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { createSettingsHost } from '@/features/settings/host';
import { nav } from '@/features/settings/nav';
import { SettingsPage } from '@/features/settings/settings-page';
import { switchSettingsTab } from '@/features/settings/shell';

// the page's shell without the tabs: one bar of eight tabs, where the page is
// kept in nav.ts, and the old ways in (window.switchSettingsTab with any key
// the ten-tab page had) still landing
beforeEach(() => {
  nav.reset();
  window.SoulSyncSettings = createSettingsHost({ tabs: {}, effects: [] });
  render(<SettingsPage tabs={{}} />);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  delete window.SoulSyncSettings;
});

const open = () =>
  document.querySelector('.stg-tab[aria-current="page"]')?.getAttribute('data-tab');

it('uses one category bar of eight tabs', () => {
  expect(document.querySelectorAll('.stg-tabbar')).toHaveLength(1);
  // ten became eight: Sources went into Downloads, Logs into System
  expect(
    [...document.querySelectorAll('.stg-tab')].map((tab) => tab.getAttribute('data-tab')),
  ).toEqual([
    'connections',
    'downloads',
    'quality',
    'library',
    'discovery',
    'people',
    'appearance',
    'advanced',
  ]);
  // #2166: People is for admins only
  expect(document.querySelectorAll('.stg-tab:not([hidden])')).toHaveLength(7);
  expect(document.querySelector<HTMLButtonElement>('.stg-tab[data-tab="people"]')!.hidden).toBe(
    true,
  );
  expect(document.querySelector('.stg-nav-rail')).toBeNull();
});

it('a tab click opens it: the title, the open mark, and its pills', () => {
  act(() => document.querySelector<HTMLButtonElement>('.stg-tab[data-tab="downloads"]')!.click());
  expect(open()).toBe('downloads');
  expect(document.getElementById('stg-active-title')?.textContent).toBe('Downloads');
  expect(
    [...document.querySelectorAll('[role="tablist"] [role="tab"]')].map((pill) => pill.textContent),
  ).toEqual(['Sources', 'Behaviour', 'Filtering']);
  // Library has its pills (phase 4); Subtitles is the video side's own
  act(() => document.querySelector<HTMLButtonElement>('.stg-tab[data-tab="library"]')!.click());
  expect(
    [...document.querySelectorAll('[role="tablist"] [role="tab"]')]
      .filter((pill) => pill.id.startsWith('stg-pill-'))
      .map((pill) => pill.textContent),
  ).toEqual(['Folders', 'Naming', 'Tags and artwork', 'Import and files']);
  // a tab with one page shows no pills
  act(() => document.querySelector<HTMLButtonElement>('.stg-tab[data-tab="people"]')!.click());
  expect(document.querySelector('[id^="stg-pill-"]')).toBeNull();
});

it('the old tab keys still land: sources is Downloads > Sources, logs is System > Logs', () => {
  act(() => switchSettingsTab('sources'));
  expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'sources' });
  act(() => switchSettingsTab('logs'));
  expect(nav.get()).toMatchObject({ tab: 'advanced', view: 'logs' });
  expect(open()).toBe('advanced');
  expect(document.getElementById('stg-active-title')?.textContent).toBe('System');
  act(() => switchSettingsTab('advanced'));
  expect(nav.get()).toMatchObject({ tab: 'advanced', view: 'general' });
  act(() => switchSettingsTab('server-activity'));
  expect(nav.get()).toMatchObject({ tab: 'advanced', view: 'activity' });
  act(() => switchSettingsTab('library'));
  expect(open()).toBe('library');
});

it('a pill is hidden on the side it is not for, unless it is the open one', () => {
  document.body.setAttribute('data-side', 'video');
  try {
    cleanup();
    render(<SettingsPage tabs={{}} />);
    act(() => nav.go('downloads'));
    const pills = () =>
      [...document.querySelectorAll('[role="tablist"] [role="tab"]')].map(
        (pill) => pill.textContent,
      );
    expect(pills()).toEqual(['Sources', 'Behaviour']);
    // a link or the search can still open it
    act(() => nav.go('downloads', 'filtering'));
    expect(pills()).toEqual(['Sources', 'Behaviour', 'Filtering']);
  } finally {
    document.body.removeAttribute('data-side');
  }
});
