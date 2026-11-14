/**
 * Differential tests for the sync page's shell — index.html 2226-2295 and the
 * tab handler at sync-services.js 3694-3811.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SYNC_PRIMARY_TAB_IDS, SYNC_TABS } from '../-sync.shell';
import { SyncShell } from './sync-shell';

function renderShell(over: Partial<React.ComponentProps<typeof SyncShell>> = {}) {
  const props: React.ComponentProps<typeof SyncShell> = {
    panels: {},
    onAutoSync: vi.fn(),
    onActivity: vi.fn(),
    ...over,
  };
  return { props, ...render(<SyncShell {...props} />) };
}

afterEach(() => {
  delete window.openManualLibraryMatchTool;
  delete window.openSyncHistoryModal;
  delete window.openDownloadOriginsModal;
});

describe('the way into the Playlist Explorer', () => {
  // the explorer has no sidebar entry any more: this button is how it opens
  const saved = {
    nav: window.navigateToPage,
    features: window.SoulSyncFeatures,
    bridge: window.SoulSyncWebShellBridge,
  };
  afterEach(() => {
    window.navigateToPage = saved.nav;
    window.SoulSyncFeatures = saved.features;
    window.SoulSyncWebShellBridge = saved.bridge;
  });

  it('sits in the header and opens the explorer in the app', () => {
    const navigateToPage = vi.fn();
    window.navigateToPage = navigateToPage as unknown as typeof window.navigateToPage;
    renderShell();
    const link = screen.getByRole('link', { name: 'Playlist Explorer' });
    // a real link, so it can still be opened in a new tab
    expect(link.getAttribute('href')).toBe('/playlist-explorer');
    expect(link.classList.contains('pl-explorer-btn')).toBe(true);
    fireEvent.click(link);
    expect(navigateToPage).toHaveBeenCalledWith('playlist-explorer');
  });

  it('is gone when the Playlist Explorer is switched off', () => {
    window.SoulSyncFeatures = {
      ...(saved.features ?? {}),
      isOn: (id: string) => id !== 'explorer',
    } as unknown as typeof window.SoulSyncFeatures;
    renderShell();
    expect(screen.queryByRole('link', { name: 'Playlist Explorer' })).toBeNull();
  });

  it('is gone for a profile that was not given the page', () => {
    window.SoulSyncWebShellBridge = {
      ...(saved.bridge ?? {}),
      isPageAllowed: (page: string) => page !== 'playlist-explorer',
    } as unknown as typeof window.SoulSyncWebShellBridge;
    renderShell();
    expect(screen.queryByRole('link', { name: 'Playlist Explorer' })).toBeNull();
  });
});

describe('the header', () => {
  it('renders the title, and a plain subtitle until the library reports', () => {
    const { container } = renderShell();
    expect(container.querySelector('.pl-title span')?.textContent).toBe('Playlists');
    expect(container.querySelector('.pl-subtitle')?.textContent).toBe(
      'Manage, mirror, and synchronize your playlists with your media server',
    );
  });

  it('says what the library holds once it has loaded', () => {
    const { container } = renderShell({
      summary: { playlists: 55, attention: 27, running: 2, nextRun: 'next in 14h' },
    });
    const text = container.querySelector('.pl-subtitle')?.textContent ?? '';
    expect(text).toContain('55 mirrored');
    expect(text).toContain('27 need attention');
    expect(text).toContain('next sync in 14h');
    expect(text).toContain('2 working');
  });

  it('leaves out what it has nothing to say about', () => {
    const { container } = renderShell({
      summary: { playlists: 3, attention: 0, running: 0, nextRun: '' },
    });
    const text = container.querySelector('.pl-subtitle')?.textContent ?? '';
    expect(text).toBe('3 mirrored');
  });

  it('has one main action and the rest behind Tools (#1909)', () => {
    const { container } = renderShell({ onAddPlaylist: vi.fn() });
    expect(container.querySelector('.pl-add-btn')?.textContent).toBe('Add playlist');
    expect(container.querySelector('.pl-tools-btn')?.getAttribute('aria-label')).toBe(
      'Playlist tools',
    );
    // the tools are not on the page until the menu is opened
    expect(document.querySelector('.sync-more-menu')).toBeNull();
  });

  it('Add opens the Add sheet', () => {
    const onAddPlaylist = vi.fn();
    const { container } = renderShell({ onAddPlaylist });
    fireEvent.click(container.querySelector('.pl-add-btn') as HTMLElement);
    expect(onAddPlaylist).toHaveBeenCalledTimes(1);
  });

  function openTools(container: HTMLElement) {
    fireEvent.click(container.querySelector('.pl-tools-btn') as HTMLElement);
    return [...document.querySelectorAll('.sync-more-menu [role="menuitem"]')] as HTMLElement[];
  }

  it('lists the tools in order, each with a line saying what it is for', () => {
    const { container } = renderShell({ onUpdateList: vi.fn() });
    const items = openTools(container);
    expect(items.map((i) => i.querySelector('.sync-more-name')?.textContent)).toEqual([
      'Bulk schedule',
      'Match Review',
      'Wing It Pool',
      'Library Match',
      'Activity',
      'Download Origins',
      'Update list',
    ]);
    for (const item of items)
      expect(item.querySelector('.sync-more-desc')?.textContent).toBeTruthy();
    expect(items[0].getAttribute('title')).toBe(
      'Schedule many mirrored playlists at once, and review the pipeline',
    );
    // the old hook classes ride on the items
    expect(items[0].className).toContain('auto-sync-manager-btn');
    expect(items[3].className).not.toContain('auto-sync-manager-btn');
  });

  it('Update list is only offered when the page can do it', () => {
    const { container } = renderShell();
    const names = openTools(container).map((i) => i.querySelector('.sync-more-name')?.textContent);
    expect(names).not.toContain('Update list');
  });

  it('Update list reloads', () => {
    const onUpdateList = vi.fn();
    const { container } = renderShell({ onUpdateList });
    const item = openTools(container).find(
      (i) => i.querySelector('.sync-more-name')?.textContent === 'Update list',
    ) as HTMLElement;
    fireEvent.click(item);
    expect(onUpdateList).toHaveBeenCalledTimes(1);
  });

  it('routes each item to its own seam, Bulk schedule and Activity to React', () => {
    window.openManualLibraryMatchTool = vi.fn();
    window.openDownloadOriginsModal = vi.fn();
    window.openDiscoveryPoolModal = vi.fn();
    window.openWingItPoolModal = vi.fn();
    const { container, props } = renderShell();
    const click = (name: string) => {
      const item = openTools(container).find(
        (i) => i.querySelector('.sync-more-name')?.textContent === name,
      ) as HTMLElement;
      fireEvent.click(item);
    };
    click('Bulk schedule');
    expect(props.onAutoSync).toHaveBeenCalledTimes(1);
    click('Library Match');
    expect(window.openManualLibraryMatchTool).toHaveBeenCalledTimes(1);
    click('Activity');
    expect(props.onActivity).toHaveBeenCalledTimes(1);
    click('Match Review');
    expect(window.openDiscoveryPoolModal).toHaveBeenCalledTimes(1);
    click('Wing It Pool');
    expect(window.openWingItPoolModal).toHaveBeenCalledTimes(1);
    click('Download Origins');
    // the shared modal is scoped by this literal
    expect(window.openDownloadOriginsModal).toHaveBeenCalledWith('playlist');
  });

  it('does not throw when a vanilla seam is missing', () => {
    const { container } = renderShell();
    const names = openTools(container).map((i) => i.querySelector('.sync-more-name')?.textContent);
    fireEvent.click(container.querySelector('.pl-tools-btn') as HTMLElement);
    expect(() => {
      for (const name of names) {
        const item = openTools(container).find(
          (i) => i.querySelector('.sync-more-name')?.textContent === name,
        ) as HTMLElement;
        fireEvent.click(item);
      }
    }).not.toThrow();
  });
});

describe('the page root', () => {
  it('carries page-shell and the page id, as every flipped route does', () => {
    const { container } = renderShell();
    const root = container.firstElementChild as HTMLElement;
    // the panel is the page frame's; the measured column sits inside it
    expect(root.className).toBe('page-shell pgf-shell');
    expect(root.id).toBe('sync-page');
    expect(root.firstElementChild?.className).toBe('pgf-page pgf-page--plain');
  });
});

describe('the collections', () => {
  it('renders the four collections as pills, named', () => {
    const { container } = renderShell();
    const pills = [...container.querySelectorAll('.pl-collection')];
    expect(pills.map((p) => p.getAttribute('data-tab'))).toEqual([...SYNC_PRIMARY_TAB_IDS]);
    expect(pills.map((p) => p.querySelector('.pl-collection-label')?.textContent)).toEqual([
      'Mirrored',
      'My Playlists',
      'Request Lists',
      'Server Playlists',
    ]);
  });

  it('opens on Mirrored', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="mirrored"]')?.getAttribute('aria-selected')).toBe(
      'true',
    );
    expect(container.querySelector('#mirrored-tab-content')?.className).toContain('active');
  });

  it('moves the active state on both the pill and the panel', () => {
    const { container } = renderShell();
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(container.querySelector('[data-tab="server"]')?.className).toContain('active');
    expect(container.querySelector('[data-tab="mirrored"]')?.className).not.toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
    expect(container.querySelector('#mirrored-tab-content')?.className).not.toContain('active');
    expect(container.querySelectorAll('.pl-collection.active')).toHaveLength(1);
  });

  it('renders a panel for EVERY tab once it is opened — routing depends on it', () => {
    let open: ((tab: (typeof SYNC_TABS)[number]['id']) => void) | undefined;
    const { container } = renderShell({
      registerOpenTab: (fn) => {
        open = fn;
      },
    });
    for (const t of SYNC_TABS) act(() => open?.(t.id));
    for (const t of SYNC_TABS) {
      expect(container.querySelector(`#${t.id}-tab-content`), t.id).not.toBeNull();
    }
  });
});

describe('source panels (the routed tabs, opened from Add)', () => {
  function withOpener(over: Partial<React.ComponentProps<typeof SyncShell>> = {}) {
    let open: ((tab: 'beatport' | 'spotify-public' | 'server') => void) | undefined;
    const r = renderShell({
      panels: {
        beatport: <div data-testid="p-beatport">beatport</div>,
        'spotify-public': <div data-testid="routed-panel" />,
      },
      registerOpenTab: (fn) => {
        open = fn as typeof open;
      },
      ...over,
    });
    return { ...r, open: (t: 'beatport' | 'spotify-public' | 'server') => act(() => open?.(t)) };
  }

  it('opens over the page as a named dialog, and leaves the collection under it', () => {
    const { container, open } = withOpener();
    open('beatport');
    const dialog = document.querySelector('.sync-source-panel[role="dialog"]') as HTMLElement;
    expect(dialog.getAttribute('aria-label')).toBe('Beatport');
    expect(dialog.querySelector('[data-testid="p-beatport"]')).not.toBeNull();
    // the collection pill under it stays the selected one
    expect(container.querySelector('[data-tab="mirrored"]')?.getAttribute('aria-selected')).toBe(
      'true',
    );
  });

  it('Done closes it, back to the collection it was opened over', () => {
    const { container, open } = withOpener();
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    open('beatport');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(document.querySelector('.sync-source-panel[aria-modal="true"]')).toBeNull();
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
  });

  it('Escape closes it too', () => {
    const { open } = withOpener();
    open('beatport');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.querySelector('.sync-source-panel[aria-modal="true"]')).toBeNull();
  });

  it('KEEPS the panel mounted after closing, so reopening finds what it loaded', () => {
    let mounts = 0;
    function Counted() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <div data-testid="counted" />;
    }
    const { open } = withOpener({ panels: { beatport: <Counted /> } });
    expect(mounts).toBe(0);
    open('beatport');
    expect(mounts).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    open('beatport');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    open('beatport');
    expect(mounts).toBe(1);
    expect(document.querySelector('[data-testid="counted"]')).not.toBeNull();
  });

  it('a closed panel is hidden from assistive tech', () => {
    const { open } = withOpener();
    open('beatport');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    const panel = document.querySelector('.sync-source-panel') as HTMLElement;
    expect(panel.getAttribute('aria-hidden')).toBe('true');
  });
});

describe('the tab-change signal', () => {
  it('fires on every switch', () => {
    const onTabChange = vi.fn();
    const { container } = renderShell({ onTabChange });
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(1);
    fireEvent.click(container.querySelector('[data-tab="my-playlists"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(2);
  });

  it('fires for a click on the tab that is ALREADY active', () => {
    const onTabChange = vi.fn();
    const { container } = renderShell({ onTabChange });
    const active = container.querySelector('[data-tab="mirrored"]') as HTMLElement;
    fireEvent.click(active);
    fireEvent.click(active);
    expect(onTabChange).toHaveBeenCalledTimes(2);
  });

  it('is optional — the shell works without it', () => {
    const { container } = renderShell();
    expect(() => {
      fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    }).not.toThrow();
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
  });
});

describe('panel mounting — the one-shot load flags', () => {
  const panels = {
    mirrored: <div data-testid="p-mirrored">mirrored</div>,
    server: <div data-testid="p-server">server</div>,
    'my-playlists': <div data-testid="p-mine">mine</div>,
  };

  it('mounts only the default panel to begin with', () => {
    const { container } = renderShell({ panels });
    expect(container.querySelector('[data-testid="p-mirrored"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-server"]')).toBeNull();
  });

  it('mounts a panel the first time its tab is opened, and KEEPS it after leaving', () => {
    const { container } = renderShell({ panels });
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    fireEvent.click(container.querySelector('[data-tab="my-playlists"]') as HTMLElement);
    expect(container.querySelector('[data-testid="p-server"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-mine"]')).not.toBeNull();
    expect(container.querySelector('#server-tab-content')?.className).not.toContain('active');
    expect(container.querySelector('#my-playlists-tab-content')?.className).toContain('active');
  });

  it('renders an empty panel for a tab with no content supplied', () => {
    const { container } = renderShell({ panels: { mirrored: panels.mirrored } });
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(container.querySelector('#server-tab-content')?.textContent).toBe('');
  });
});

describe('the tab opener', () => {
  it('hands the host an opener that switches tabs like a click does', () => {
    // Add routes a detected link to the tab that loads it, through this.
    let open: ((tab: 'spotify-public') => void) | undefined;
    const onTabChange = vi.fn();
    renderShell({
      panels: { 'spotify-public': <div data-testid="routed-panel" /> },
      onTabChange,
      registerOpenTab: (fn) => {
        open = fn as (tab: 'spotify-public') => void;
      },
    });
    expect(document.querySelector('[data-testid="routed-panel"]')).toBeNull();
    expect(onTabChange).not.toHaveBeenCalled();

    act(() => open?.('spotify-public'));
    expect(document.querySelector('[data-testid="routed-panel"]')).not.toBeNull();
    expect(onTabChange).toHaveBeenCalledTimes(1);
  });
});
