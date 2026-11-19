/** where on the page: the address and back, and the old tab keys */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TABS, legacyKey, nav, navFromSearch, navToSearch, resolveTab } from './nav';

beforeEach(() => nav.reset());

describe('the tabs', () => {
  it('are eight, in setup order', () => {
    expect(TABS.map((tab) => tab.label)).toEqual([
      'Connections',
      'Downloads',
      'Quality',
      'Library',
      'Discovery',
      'People',
      'Appearance',
      'System',
    ]);
  });
  it('old tab keys land where their content went', () => {
    expect(resolveTab('sources')).toEqual({ tab: 'downloads', view: 'sources' });
    expect(resolveTab('logs')).toEqual({ tab: 'advanced', view: 'logs' });
    expect(resolveTab('advanced')).toEqual({ tab: 'advanced', view: 'system' });
    expect(resolveTab('system')).toEqual({ tab: 'advanced', view: 'system' });
    expect(resolveTab('downloads')).toEqual({ tab: 'downloads', view: 'sources' });
    // nonsense is the first tab, never a blank page
    expect(resolveTab('nope')).toEqual({ tab: 'connections', view: 'services' });
    // Connections has its pills now (settings-bic phase 2)
    expect(resolveTab('connections', 'metasync')).toEqual({ tab: 'connections', view: 'metasync' });
    expect(resolveTab('downloads', 'nope')).toEqual({ tab: 'downloads', view: 'sources' });
  });
  it('the tabs not rebuilt yet still answer to their old key', () => {
    expect(legacyKey({ tab: 'advanced', view: 'system' })).toBe('advanced');
    expect(legacyKey({ tab: 'advanced', view: 'logs' })).toBe('logs');
    expect(legacyKey({ tab: 'library', view: 'all' })).toBe('library');
  });
});

describe('the address', () => {
  it('reads tab, view, kind and open, and ignores what is not a plain word', () => {
    expect(
      navFromSearch({ tab: 'downloads', view: 'behaviour', kind: 'video', open: 'soulseek' }),
    ).toEqual({ tab: 'downloads', view: 'behaviour', kind: 'video', open: 'soulseek' });
    expect(navFromSearch({ tab: 'system', view: 'logs' })).toMatchObject({
      tab: 'advanced',
      view: 'logs',
    });
    expect(navFromSearch({ open: '<script>', kind: 7 })).toEqual({
      tab: 'connections',
      view: 'services',
      kind: null,
      open: null,
    });
  });
  it('writes only what is needed', () => {
    expect(navToSearch({ tab: 'library', view: 'all', kind: null, open: null })).toEqual({
      tab: 'library',
    });
    expect(
      navToSearch({ tab: 'connections', view: 'notifications', kind: null, open: null }),
    ).toEqual({ tab: 'connections', view: 'notifications' });
    expect(navToSearch({ tab: 'advanced', view: 'logs', kind: null, open: null })).toEqual({
      tab: 'system',
      view: 'logs',
    });
    expect(
      navToSearch({ tab: 'downloads', view: 'sources', kind: 'video', open: 'torrent' }),
    ).toEqual({ tab: 'downloads', view: 'sources', kind: 'video', open: 'torrent' });
  });
});

describe('moving', () => {
  it('a tab or a pill is a new place; the kind and the sheet refine the same one', () => {
    const heard = vi.fn();
    nav.subscribe(heard);
    nav.go('sources');
    expect(nav.get()).toEqual({ tab: 'downloads', view: 'sources', kind: null, open: null });
    expect(heard).toHaveBeenLastCalledWith(nav.get(), 'push');
    nav.patch({ open: 'soulseek' });
    expect(heard).toHaveBeenLastCalledWith(nav.get(), 'replace');
    nav.view('filtering');
    expect(nav.get()).toEqual({ tab: 'downloads', view: 'filtering', kind: null, open: null });
    expect(heard).toHaveBeenLastCalledWith(nav.get(), 'push');
  });
  it('asking for the open tab again keeps its pill', () => {
    nav.go('downloads', 'behaviour');
    const heard = vi.fn();
    nav.subscribe(heard);
    nav.go('downloads');
    expect(nav.get().view).toBe('behaviour');
    expect(heard).not.toHaveBeenCalled();
    // an old key that names a pill goes to that pill
    nav.go('sources');
    expect(nav.get().view).toBe('sources');
  });
  it('the address changing is heard as the address, so it is not written back', () => {
    const heard = vi.fn();
    nav.subscribe(heard);
    nav.fromAddress({ tab: 'library' });
    expect(heard).toHaveBeenLastCalledWith(nav.get(), 'address');
    nav.fromAddress({ tab: 'library' });
    expect(heard).toHaveBeenCalledTimes(1);
  });
});
