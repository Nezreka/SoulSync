/**
 * Tests for the sync page's shell — the Standard/Advanced page mode.
 *
 * Standard is the three-view IA (Overview / Library / Discover): the title
 * block, + Add playlist, Bulk schedule, and the routed-tab machinery. Advanced
 * is the page as it was before the overhaul — the six-button header and the
 * full tab strip, unreskinned — and it is never the default.
 *
 * What survived unchanged across both modes: the title block, + Add playlist,
 * the routed-tab machinery (open/remember/hide), the one-shot panel mounting,
 * the sidebar slot, and every vanilla seam — they just live in new places in
 * Standard.
 */

import { act, fireEvent, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { useEffect } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SYNC_TABS } from '../-sync.shell';
import { SYNC_VIEWS } from '../-sync.views';
import { SyncShell, runSyncHeaderAction } from './sync-shell';

function renderShell(over: Partial<React.ComponentProps<typeof SyncShell>> = {}) {
  const props: React.ComponentProps<typeof SyncShell> = {
    panels: {},
    onAutoSync: vi.fn(),
    onActivity: vi.fn(),
    ...over,
  };
  return { props, ...render(<SyncShell {...props} />) };
}

/** The tab strip only exists inside the Library view now. */
function openLibrary(container: HTMLElement) {
  const btn = [...container.querySelectorAll('.pl-view-switch button')].find(
    (b) => b.textContent === 'Library',
  ) as HTMLElement;
  fireEvent.click(btn);
}

afterEach(() => {
  delete window.openManualLibraryMatchTool;
  delete window.openSyncHistoryModal;
  delete window.openDownloadOriginsModal;
  delete window.openDiscoveryPoolModal;
  delete window.openWingItPoolModal;
  // routed tabs are remembered in localStorage now - without this, one
  // test's opened tab leaks into the next one's initial strip
  window.localStorage.clear();
});

describe('the header', () => {
  it('renders the title, icon and subtitle', () => {
    const { container } = renderShell();
    expect(container.querySelector('.sync-title span')?.textContent).toBe('Playlists');
    expect(container.querySelector('.page-header-icon')?.getAttribute('src')).toBe(
      '/static/sync.png',
    );
    // Decorative — the text beside it carries the meaning.
    expect(container.querySelector('.page-header-icon')?.getAttribute('alt')).toBe('');
    expect(container.querySelector('.sync-subtitle')?.textContent).toBe(
      'Manage, mirror, and synchronize your playlists with your media server',
    );
  });

  it('keeps only the two actions that CHANGE something: Add playlist and Bulk schedule', () => {
    // Match Review, Wing It Pool and Library Match moved to the Discover
    // view's pipeline; Activity and Download Origins moved to the Overview.
    // The header keeps the page's two verbs. (The mode switch is not an
    // action — it chooses the page's composition.)
    const { container } = renderShell({ onAddPlaylist: vi.fn() });
    const labels = [...container.querySelectorAll('.sync-header-actions button')]
      .filter((b) => !b.closest('.pl-view-switch') && !b.closest('.pl-mode-switch'))
      .map((b) => b.textContent);
    expect(labels).toContain('+ Add playlist');
    expect(labels).toContain('Bulk schedule');
    expect(labels).not.toContain('Match Review');
    expect(labels).not.toContain('Wing It Pool');
    expect(labels).not.toContain('Library Match');
    expect(labels).not.toContain('Activity');
    expect(labels).not.toContain('Download Origins');
  });

  it('Bulk schedule still opens the Auto-Sync modal', () => {
    const { container, props } = renderShell();
    const btn = [...container.querySelectorAll('.sync-header-actions button')].find(
      (b) => b.textContent === 'Bulk schedule',
    ) as HTMLElement;
    expect(btn.getAttribute('title')).toBe(
      'Schedule many mirrored playlists at once, and review the pipeline',
    );
    // Only the bulk-schedule button carries the extra hook class the vanilla
    // gives it; the CLASS keeps its auto-sync name because vanilla CSS and the
    // dashboard tile both still select on it. Only the LABEL changed.
    expect(btn.className).toContain('auto-sync-manager-btn');
    fireEvent.click(btn);
    expect(props.onAutoSync).toHaveBeenCalledTimes(1);
  });

  it('does not throw when a vanilla seam is missing', () => {
    const { container } = renderShell();
    const btns = [...container.querySelectorAll('.sync-header-actions button')];
    expect(() => {
      for (const btn of btns) fireEvent.click(btn as HTMLElement);
    }).not.toThrow();
  });
});

describe('the moved header actions', () => {
  // The five buttons left the header, but their seams did not move:
  // runSyncHeaderAction is the one implementation the views call.
  it('routes each moved action to its own seam, Activity to React', () => {
    window.openManualLibraryMatchTool = vi.fn();
    window.openDownloadOriginsModal = vi.fn();
    window.openDiscoveryPoolModal = vi.fn();
    window.openWingItPoolModal = vi.fn();
    const onAutoSync = vi.fn();
    const onActivity = vi.fn();

    runSyncHeaderAction('library-match', onAutoSync, onActivity);
    expect(window.openManualLibraryMatchTool).toHaveBeenCalledTimes(1);
    runSyncHeaderAction('discovery-pool', onAutoSync, onActivity);
    expect(window.openDiscoveryPoolModal).toHaveBeenCalledTimes(1);
    runSyncHeaderAction('wing-it-pool', onAutoSync, onActivity);
    expect(window.openWingItPoolModal).toHaveBeenCalledTimes(1);
    runSyncHeaderAction('download-origins', onAutoSync, onActivity);
    // the shared modal is scoped by this literal.
    expect(window.openDownloadOriginsModal).toHaveBeenCalledWith('playlist');
    // Activity is React, not a window seam: it holds the sync history AND the
    // scheduled-run history, and the vanilla modal knows only the first.
    runSyncHeaderAction('activity', onAutoSync, onActivity);
    expect(onActivity).toHaveBeenCalledTimes(1);
    runSyncHeaderAction('auto-sync', onAutoSync, onActivity);
    expect(onAutoSync).toHaveBeenCalledTimes(1);
  });

  it('does not throw when a vanilla seam is missing', () => {
    const onAutoSync = vi.fn();
    const onActivity = vi.fn();
    expect(() => {
      for (const key of [
        'discovery-pool',
        'wing-it-pool',
        'library-match',
        'activity',
        'download-origins',
      ]) {
        runSyncHeaderAction(key, onAutoSync, onActivity);
      }
    }).not.toThrow();
  });
});

describe('the view switcher', () => {
  it('offers the three views, in order', () => {
    const { container } = renderShell();
    const labels = [...container.querySelectorAll('.pl-view-switch button')].map(
      (b) => b.textContent,
    );
    expect(labels).toEqual(SYNC_VIEWS.map((v) => v.label));
    expect(labels).toEqual(['Overview', 'Library', 'Discover']);
  });

  it('lands on Overview — mission control, not the tab strip', () => {
    const { container } = renderShell({
      overview: <div data-testid="overview-node" />,
    });
    const selected = container.querySelector('.pl-view-switch button[aria-selected="true"]');
    expect(selected?.textContent).toBe('Overview');
    expect(container.querySelector('[data-testid="overview-node"]')).not.toBeNull();
    // the library's tab strip is not on screen until you go there
    expect(container.querySelector('.sync-tabs')).toBeNull();
  });

  it('switching views swaps the mounted node', () => {
    const { container } = renderShell({
      overview: <div data-testid="overview-node" />,
      discover: <div data-testid="discover-node" />,
    });
    const click = (label: string) => {
      const btn = [...container.querySelectorAll('.pl-view-switch button')].find(
        (b) => b.textContent === label,
      ) as HTMLElement;
      fireEvent.click(btn);
    };
    click('Discover');
    expect(container.querySelector('[data-testid="discover-node"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="overview-node"]')).toBeNull();
    expect(
      container.querySelector('.pl-view-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Discover');
    click('Library');
    expect(container.querySelector('.sync-tabs')).not.toBeNull();
  });

  it('exposes the selection to assistive tech', () => {
    const { container } = renderShell();
    const aria = (label: string) =>
      [...container.querySelectorAll('.pl-view-switch button')]
        .find((b) => b.textContent === label)
        ?.getAttribute('aria-selected');
    expect(aria('Overview')).toBe('true');
    expect(aria('Library')).toBe('false');
  });
});

describe('the Standard/Advanced mode switch', () => {
  const modeLabels = (container: HTMLElement) =>
    [...container.querySelectorAll('.pl-mode-switch button')].map((b) => b.textContent);

  const clickMode = (container: HTMLElement, label: string) => {
    const btn = [...container.querySelectorAll('.pl-mode-switch button')].find(
      (b) => b.textContent === label,
    ) as HTMLElement;
    fireEvent.click(btn);
  };

  const actionLabels = (container: HTMLElement) =>
    [...container.querySelectorAll('.sync-header-actions button')]
      .filter((b) => !b.closest('.pl-view-switch') && !b.closest('.pl-mode-switch'))
      .map((b) => b.textContent);

  it('offers Standard and Advanced, in order, with Standard selected', () => {
    const { container } = renderShell();
    expect(modeLabels(container)).toEqual(['Standard', 'Advanced']);
    expect(
      container.querySelector('.pl-mode-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Standard');
    expect(container.querySelector('.pl-mode-switch')?.getAttribute('aria-label')).toBe(
      'Playlists page mode',
    );
  });

  it('Standard is the default: the view switcher shows, the classic header does not', () => {
    const { container } = renderShell({ onAddPlaylist: vi.fn() });
    expect(container.querySelector('.pl-view-switch')).not.toBeNull();
    expect(actionLabels(container)).toEqual(['+ Add playlist', 'Bulk schedule']);
    expect(container.firstElementChild?.className).toBe('page-shell pl-overhaul');
  });

  it('Advanced restores the classic page: every header button, the full strip, no reskin', () => {
    const { container } = renderShell({
      onAddPlaylist: vi.fn(),
      overview: <div data-testid="overview-node" />,
      discover: <div data-testid="discover-node" />,
    });
    clickMode(container, 'Advanced');

    // the view switcher is gone; all six header actions are back, divider included
    expect(container.querySelector('.pl-view-switch')).toBeNull();
    expect(actionLabels(container)).toEqual([
      '+ Add playlist',
      'Bulk schedule',
      'Match Review',
      'Wing It Pool',
      'Library Match',
      'Activity',
      'Download Origins',
    ]);
    expect(container.querySelector('.sync-header-divider')).not.toBeNull();
    // the strip renders immediately — no Library view to open first — bare,
    // with none of the reskin's wrappers, and the overview/discover nodes
    // are not mounted anywhere
    expect(container.querySelector('.sync-tabs')).not.toBeNull();
    expect(container.querySelector('.pl-library')).toBeNull();
    expect(container.querySelector('[data-testid="overview-node"]')).toBeNull();
    expect(container.querySelector('[data-testid="discover-node"]')).toBeNull();
    expect(container.firstElementChild?.className).toBe('page-shell');
  });

  it('switching back to Standard restores the overhaul', () => {
    const { container } = renderShell({
      onAddPlaylist: vi.fn(),
      overview: <div data-testid="overview-node" />,
    });
    clickMode(container, 'Advanced');
    expect(container.querySelector('.pl-view-switch')).toBeNull();

    clickMode(container, 'Standard');
    expect(container.querySelector('.pl-view-switch')).not.toBeNull();
    expect(actionLabels(container)).toEqual(['+ Add playlist', 'Bulk schedule']);
    expect(container.querySelector('[data-testid="overview-node"]')).not.toBeNull();
    expect(container.firstElementChild?.className).toBe('page-shell pl-overhaul');
  });

  it('remembers the choice across reloads', () => {
    const { container, unmount } = renderShell();
    clickMode(container, 'Advanced');
    expect(window.localStorage.getItem('soulsync.sync.mode')).toBe('advanced');
    unmount();

    const again = renderShell();
    expect(
      again.container.querySelector('.pl-mode-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Advanced');
    expect(again.container.querySelector('.pl-view-switch')).toBeNull();
  });

  it('an unknown stored value falls back to Standard', () => {
    window.localStorage.setItem('soulsync.sync.mode', 'fancy');
    const { container } = renderShell();
    expect(
      container.querySelector('.pl-mode-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Standard');
    expect(container.querySelector('.pl-view-switch')).not.toBeNull();
  });

  it("in Advanced, open('beatport') opens the tab — nothing routes to Discover", () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      panels: { beatport: <div data-testid="beatport-panel" /> },
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    clickMode(container, 'Advanced');
    act(() => {
      open('beatport');
    });
    // the beatport chip sits in the strip and goes active; there is no
    // Discover view to land in
    expect(container.querySelector('.pl-view-switch')).toBeNull();
    expect(container.querySelector('[data-tab="beatport"]')?.className).toContain('active');
    expect(container.querySelector('[data-testid="beatport-panel"]')).not.toBeNull();
  });

  it('in Advanced, opening a routed source tab keeps it in the strip with no view hop', () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    clickMode(container, 'Advanced');
    act(() => {
      open('spotify');
    });
    const chips = Array.from(container.querySelectorAll('.sync-tab-button')).map((b) =>
      b.getAttribute('data-tab'),
    );
    // beatport is a permanent primary chip in the classic strip — exactly as
    // before the overhaul — with the routed source appended after it
    expect(chips).toEqual(['mirrored', 'server', 'beatport', 'spotify']);
    expect(container.querySelector('[data-tab="spotify"]')?.className).toContain('active');
  });

  it('does not throw when a classic header seam is missing', () => {
    const { container } = renderShell();
    clickMode(container, 'Advanced');
    const btns = [...container.querySelectorAll('.sync-header-actions button')].filter(
      (b) => !b.closest('.pl-mode-switch'),
    );
    expect(() => {
      for (const btn of btns) fireEvent.click(btn as HTMLElement);
    }).not.toThrow();
  });
});

describe('the page root', () => {
  it('carries page-shell, the overhaul class, and the page id', () => {
    const { container } = renderShell();
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toBe('page-shell pl-overhaul');
    // The vanilla nests page-shell inside `<div class="page" id="sync-page">`;
    // the React roots collapse the two and keep the id.
    expect(root.id).toBe('sync-page');
  });
});

describe('the library tab strip', () => {
  it('renders TWO permanent chips — mirrored and server, not beatport', () => {
    // Beatport moved to the Discover view; it is a chart browser, not library.
    const { container } = renderShell();
    openLibrary(container);
    const btns = Array.from(container.querySelectorAll('.sync-tab-button'));
    expect(btns.map((b) => b.getAttribute('data-tab'))).toEqual(['mirrored', 'server']);
  });

  it('opens YouTube Music as a routed tab, same as the other sources', () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('ytmusic');
    });
    // routing a source tab lands in the library view, where its chip lives
    expect(
      container.querySelector('.pl-view-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Library');
    const withRouted = Array.from(container.querySelectorAll('.sync-tab-button')).map((b) =>
      b.getAttribute('data-tab'),
    );
    expect(withRouted).toEqual(['mirrored', 'server', 'ytmusic']);
    expect(container.querySelector('[data-tab="ytmusic"]')?.className).toContain('active');
  });

  it('opens the library on Mirrored — the library, not a source directory', () => {
    const { container } = renderShell();
    openLibrary(container);
    expect(container.querySelector('[data-tab="mirrored"]')?.className).toContain('active');
    expect(container.querySelector('#mirrored-tab-content')?.className).toContain('active');
  });

  it('shows a routed tab once opened, and KEEPS it after leaving', () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('spotify-public');
    });
    const withRouted = Array.from(container.querySelectorAll('.sync-tab-button')).map((b) =>
      b.getAttribute('data-tab'),
    );
    expect(withRouted).toEqual(['mirrored', 'server', 'spotify-public']);
    expect(container.querySelector('[data-tab="spotify-public"]')?.className).toContain('active');

    act(() => {
      open('server');
    });
    // the chip stays; only the highlight moves
    expect(container.querySelectorAll('.sync-tab-button')).toHaveLength(3);
    expect(container.querySelector('[data-tab="spotify-public"]')?.className).not.toContain(
      'active',
    );
  });

  it('hides a routed chip with its ×, for good, and Add playlist brings it back (#1402)', () => {
    let open!: (tab: string) => void;
    const view = renderShell({
      panels: { deezer: <div id="probe" /> },
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('spotify');
    });
    act(() => {
      open('deezer');
    });
    const chips = () =>
      Array.from(view.container.querySelectorAll('.sync-tab-button')).map((b) =>
        b.getAttribute('data-tab'),
      );
    expect(chips()).toEqual(['mirrored', 'server', 'spotify', 'deezer']);
    expect(view.container.querySelectorAll('.sync-tab-close')).toHaveLength(2);

    fireEvent.click(view.getByLabelText('Hide Deezer tab'));
    expect(chips()).toEqual(['mirrored', 'server', 'spotify']);
    // it was the active one, so we land back on the library
    expect(view.container.querySelector('[data-tab="mirrored"]')?.className).toContain('active');
    // chip gone, panel kept
    expect(view.container.querySelector('#probe')).not.toBeNull();

    // stays gone after a reload
    view.unmount();
    const again = renderShell({
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    openLibrary(again.container as unknown as HTMLElement);
    const chipsAgain = () =>
      Array.from(again.container.querySelectorAll('.sync-tab-button')).map((b) =>
        b.getAttribute('data-tab'),
      );
    expect(chipsAgain()).toEqual(['mirrored', 'server', 'spotify']);

    // opening the source again brings it back
    act(() => {
      open('deezer');
    });
    expect(chipsAgain()).toContain('deezer');
  });

  it('keeps a routed panel MOUNTED after its chip disappears', () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      panels: { 'spotify-public': <div id="probe" /> },
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('spotify-public');
    });
    expect(container.querySelector('#probe')).not.toBeNull();
    act(() => {
      open('server');
    });
    expect(container.querySelector('#probe')).not.toBeNull();
  });

  it('gives each rendered chip its sprite class and its own title', () => {
    const { container } = renderShell();
    openLibrary(container);
    const icon = (tab: string) =>
      container.querySelector(`[data-tab="${tab}"] .tab-icon`)?.className;
    expect(icon('mirrored')).toBe('tab-icon mirrored-icon');
    expect(icon('server')).toBe('tab-icon server-icon');
    for (const id of ['mirrored', 'server'] as const) {
      const t = SYNC_TABS.find((x) => x.id === id)!;
      expect(container.querySelector(`[data-tab="${t.id}"]`)?.getAttribute('title')).toBe(t.label);
    }
  });

  it('gives the server tab its own extra class', () => {
    const { container } = renderShell();
    openLibrary(container);
    expect(container.querySelector('[data-tab="server"]')?.className).toContain('sync-tab-server');
    expect(container.querySelector('[data-tab="mirrored"]')?.className).not.toContain(
      'sync-tab-server',
    );
  });

  it('renders a panel for EVERY tab, strip or not — routing depends on it', () => {
    const { container } = renderShell();
    openLibrary(container);
    for (const t of SYNC_TABS) {
      expect(container.querySelector(`#${t.id}-tab-content`)).not.toBeNull();
    }
  });

  it('no longer renders the fifteen-tab divider', () => {
    const { container } = renderShell();
    openLibrary(container);
    expect(container.querySelectorAll('.sync-tab-divider')).toHaveLength(0);
  });
});

describe('opening a tab routes to its view', () => {
  it("open('beatport') lands in Discover, where its panel lives now", () => {
    let open!: (tab: string) => void;
    const { container } = renderShell({
      discover: <div data-testid="discover-node" />,
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('beatport');
    });
    expect(
      container.querySelector('.pl-view-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Discover');
    expect(container.querySelector('[data-testid="discover-node"]')).not.toBeNull();
  });

  it("open('mirrored') lands in the library — the import tab's post-write target", () => {
    // importFileSubmit's tail clicked the mirrored tab button and reloaded the
    // list; the opener is that click now.
    let open!: (tab: string) => void;
    const { container } = renderShell({
      registerOpenTab: (fn) => {
        open = fn as (tab: string) => void;
      },
    });
    act(() => {
      open('mirrored');
    });
    expect(
      container.querySelector('.pl-view-switch button[aria-selected="true"]')?.textContent,
    ).toBe('Library');
    expect(container.querySelector('[data-tab="mirrored"]')?.className).toContain('active');
  });
});

describe('switching tabs inside the library', () => {
  it('moves the active class on both the button and the panel', () => {
    const { container } = renderShell();
    openLibrary(container);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);

    expect(container.querySelector('[data-tab="server"]')?.className).toContain('active');
    expect(container.querySelector('[data-tab="mirrored"]')?.className).not.toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
    expect(container.querySelector('#mirrored-tab-content')?.className).not.toContain('active');
  });

  it('marks exactly ONE tab active at a time', () => {
    const { container } = renderShell();
    openLibrary(container);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(container.querySelectorAll('.sync-tab-button.active')).toHaveLength(1);
    expect(container.querySelectorAll('.sync-tab-content.active')).toHaveLength(1);
  });

  it('exposes the selection to assistive tech', () => {
    const { container } = renderShell();
    openLibrary(container);
    const aria = (tab: string) =>
      container.querySelector(`[data-tab="${tab}"]`)?.getAttribute('aria-selected');
    expect(aria('mirrored')).toBe('true');
    expect(aria('server')).toBe('false');

    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(aria('mirrored')).toBe('false');
    expect(aria('server')).toBe('true');
  });
});

describe('the tab-change signal', () => {
  it('fires on every switch, with the tab already updated', () => {
    const onTabChange = vi.fn();
    const { container } = renderShell({ onTabChange });
    openLibrary(container);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(1);
    // switching VIEWS is not a tab switch — the signal stays about tabs
    fireEvent.click(container.querySelector('[data-tab="mirrored"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(2);
  });

  it('fires for a click on the tab that is ALREADY active', () => {
    // The vanilla handler runs its whole body on any tab click, including the
    // active one, and its unconditional sidebar re-hide is what the page keys
    // off. Filtering same-tab clicks would change that behaviour.
    const onTabChange = vi.fn();
    const { container } = renderShell({ onTabChange });
    openLibrary(container);
    const active = container.querySelector('[data-tab="mirrored"]') as HTMLElement;
    fireEvent.click(active);
    fireEvent.click(active);
    expect(onTabChange).toHaveBeenCalledTimes(2);
  });

  it('is optional — the shell works without it', () => {
    const { container } = renderShell();
    openLibrary(container);
    expect(() => {
      fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    }).not.toThrow();
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
  });
});

describe('panel mounting — the one-shot load flags (3724-3803)', () => {
  const panels = {
    mirrored: <div data-testid="p-mirrored">mirrored</div>,
    server: <div data-testid="p-server">server</div>,
  };

  it('mounts the overview node on landing, not the library panels', () => {
    // The old default mounted Mirrored; the new default is mission control.
    // Library panels keep their one-shot semantics — they just start
    // unopened now.
    const { container } = renderShell({
      panels,
      overview: <div data-testid="p-overview">overview</div>,
    });
    expect(container.querySelector('[data-testid="p-overview"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-mirrored"]')).toBeNull();
  });

  it('mounts a library panel the first time its view opens', () => {
    const { container } = renderShell({ panels });
    openLibrary(container);
    expect(container.querySelector('[data-testid="p-mirrored"]')).not.toBeNull();
  });

  it('KEEPS a panel mounted after leaving it — the one-shot flags never reset', () => {
    // The vanilla sets e.g. `mirroredPlaylistsLoaded = true` once and never
    // clears it, so returning to a tab shows what it already loaded rather
    // than re-fetching. Unmounting on leave would re-fetch every visit.
    const { container } = renderShell({ panels });
    openLibrary(container);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    // leaving the library for another view keeps the panels too
    fireEvent.click(
      [...container.querySelectorAll('.pl-view-switch button')].find(
        (b) => b.textContent === 'Overview',
      ) as HTMLElement,
    );
    expect(container.querySelector('[data-testid="p-server"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-mirrored"]')).not.toBeNull();
  });

  it('mounts each panel only ONCE across repeated visits', () => {
    let mounts = 0;
    function Counted() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <div data-testid="counted" />;
    }
    const { container } = renderShell({ panels: { server: <Counted /> } });
    openLibrary(container);
    const server = container.querySelector('[data-tab="server"]') as HTMLElement;
    const mirrored = container.querySelector('[data-tab="mirrored"]') as HTMLElement;

    expect(mounts).toBe(0);
    fireEvent.click(server);
    expect(mounts).toBe(1);
    fireEvent.click(mirrored);
    fireEvent.click(server);
    expect(mounts).toBe(1);
  });

  it('renders an empty panel for a tab with no content supplied', () => {
    const { container } = renderShell({ panels: { mirrored: panels.mirrored } });
    openLibrary(container);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(container.querySelector('#server-tab-content')?.textContent).toBe('');
  });
});

describe('the sidebar slot', () => {
  it('renders the sidebar beside the main panel when given one', () => {
    const { container } = renderShell({ sidebar: <aside data-testid="side" /> });
    const area = container.querySelector('.sync-content-area') as HTMLElement;
    expect(area.querySelector('[data-testid="side"]')).not.toBeNull();
    // Second column, after the main panel.
    expect(area.children[0].className).toBe('sync-main-panel');
  });

  it('omits it entirely when there is none', () => {
    const { container } = renderShell();
    expect(container.querySelector('.sync-content-area')?.children).toHaveLength(1);
  });

  it('hands the host an opener that switches tabs like a click does', () => {
    // The import tab has to send the user to Mirrored after a write
    // (sync-services.js 449-455) and the shell owns tab state, so it registers
    // the opener upward. Opening this way must be indistinguishable from a
    // click: the panel mounts AND the sidebar re-hide fires, because the
    // vanilla got there BY clicking the button.
    // Targets a tab that is NOT the default and NOT in the strip — which is
    // now this opener's main user: Add playlist routes a detected link to the
    // tab that loads it.
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

  it('widens the grid to two columns only while the sidebar is shown', () => {
    // showSyncSidebar/hideSyncSidebar (downloads.js 4041-4057) set
    // gridTemplateColumns inline alongside the sidebar's own display. The port
    // splits them — each element's visibility lives with the component that
    // renders it — so the shell owns this half and the sidebar owns the other.
    const closed = renderShell({ sidebar: <aside /> });
    expect(closed.container.querySelector('.sync-content-area')?.className).toBe(
      'sync-content-area',
    );
    closed.unmount();

    const open = renderShell({ sidebar: <aside />, sidebarVisible: true });
    expect(open.container.querySelector('.sync-content-area')?.className).toBe(
      'sync-content-area sync-content-area--with-sidebar',
    );
  });
});

describe('the library chips are named, not just drawn', () => {
  const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

  it('the label is rendered in the markup at all', () => {
    const { container } = renderShell();
    openLibrary(container);
    const labels = [...container.querySelectorAll('.sync-tab-label')].map((n) => n.textContent);
    expect(labels.length).toBeGreaterThan(0);
    expect(labels).toContain('Mirrored');
  });

  it('and the stylesheet lets it OPEN above the icon-only breakpoint', () => {
    // The strip collapsed every label back when it held fifteen chips. It holds
    // two, and two unlabelled icons whose meaning lives in a tooltip is the
    // thing the card redesign removed. The chip also has to stop being a fixed
    // 40x40 with overflow:hidden, or the label has nowhere to appear however
    // wide you let the label itself be — measured, it stayed 10px.
    const block = /@media\s*\(min-width:\s*721px\)\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
    expect(block, 'no min-width:721px block opens the tab labels').toContain('.sync-tab-label');
    expect(block).toMatch(/width:\s*auto/);
    expect(block).not.toMatch(/max-width:\s*0/);
  });
});
