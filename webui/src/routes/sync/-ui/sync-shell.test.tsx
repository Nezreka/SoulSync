/**
 * Differential tests for the sync page's shell — index.html 2226-2295 and the
 * tab handler at sync-services.js 3694-3811.
 */

import { act, fireEvent, render } from '@testing-library/react';
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

describe('the header (2229-2243)', () => {
  it('renders the title, icon and subtitle', () => {
    const { container } = renderShell();
    expect(container.querySelector('.sync-title span')?.textContent).toBe('Playlist Sync');
    expect(container.querySelector('.page-header-icon')?.getAttribute('src')).toBe(
      '/static/sync.png',
    );
    // Decorative — the text beside it carries the meaning.
    expect(container.querySelector('.page-header-icon')?.getAttribute('alt')).toBe('');
    expect(container.querySelector('.sync-subtitle')?.textContent).toBe(
      'Synchronize your Spotify, Tidal, and YouTube playlists with your media server',
    );
  });

  it('renders the action buttons in order, with their tooltips', () => {
    const { container } = renderShell();
    const btns = Array.from(container.querySelectorAll('.sync-header-actions button')).filter(
      (b) => b.textContent !== '+ Add playlist',
    );
    expect(btns.map((b) => b.textContent)).toEqual([
      'Bulk schedule',
      'Discovery Pool',
      'Wing It Pool',
      'Library Match',
      'Activity',
      'Download Origins',
    ]);
    expect(btns[0].getAttribute('title')).toBe(
      'Schedule many mirrored playlists at once, and review the pipeline',
    );
    expect(btns[5].getAttribute('title')).toBe('See every track your playlist syncs downloaded');
    // Only the bulk-schedule button carries the extra hook class the vanilla
    // gives it; the CLASS keeps its auto-sync name because vanilla CSS and the
    // dashboard tile both still select on it. Only the LABEL changed.
    const byLabel = (label: string) =>
      [...container.querySelectorAll('.sync-header-actions button')].find(
        (b) => b.textContent === label,
      ) as HTMLElement;
    expect(byLabel('Bulk schedule').className).toContain('auto-sync-manager-btn');
    expect(byLabel('Library Match').className).not.toContain('auto-sync-manager-btn');
  });

  it('routes each button to its own seam, Bulk schedule and Activity to React', () => {
    // Selected by LABEL, not index: the row gained two buttons when the pool
    // modals moved up from the Mirrored tab, and index-based assertions all
    // silently pointed at the wrong control.
    window.openManualLibraryMatchTool = vi.fn();
    window.openDownloadOriginsModal = vi.fn();
    window.openDiscoveryPoolModal = vi.fn();
    window.openWingItPoolModal = vi.fn();
    const { container, props } = renderShell();
    const click = (label: string) => {
      const btn = [...container.querySelectorAll('.sync-header-actions button')].find(
        (b) => b.textContent === label,
      ) as HTMLElement;
      fireEvent.click(btn);
    };

    click('Bulk schedule');
    expect(props.onAutoSync).toHaveBeenCalledTimes(1);
    click('Library Match');
    expect(window.openManualLibraryMatchTool).toHaveBeenCalledTimes(1);
    // Activity is React, not a window seam: it holds the sync history AND the
    // scheduled-run history, and the vanilla modal knows only the first.
    click('Activity');
    expect(props.onActivity).toHaveBeenCalledTimes(1);
    click('Discovery Pool');
    expect(window.openDiscoveryPoolModal).toHaveBeenCalledTimes(1);
    click('Wing It Pool');
    expect(window.openWingItPoolModal).toHaveBeenCalledTimes(1);
    click('Download Origins');
    // 2241: the shared modal is scoped by this literal.
    expect(window.openDownloadOriginsModal).toHaveBeenCalledWith('playlist');
  });

  it('does not throw when a vanilla seam is missing', () => {
    const { container } = renderShell();
    const btns = [...container.querySelectorAll('.sync-header-actions button')];
    expect(() => {
      for (const btn of btns) fireEvent.click(btn as HTMLElement);
    }).not.toThrow();
  });
});

describe('the page root', () => {
  it('carries page-shell and the page id, as every flipped route does', () => {
    const { container } = renderShell();
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toBe('page-shell');
    // The vanilla nests page-shell inside `<div class="page" id="sync-page">`;
    // the React roots collapse the two and keep the id.
    expect(root.id).toBe('sync-page');
  });
});

describe('the tab strip', () => {
  it('renders THREE permanent chips, not fifteen', () => {
    // Six of the fifteen were duplicates of one another and the four
    // paste-a-URL tabs differed at the input step not at all. They are reached
    // through Add playlist now, which detects the service from the link.
    const { container } = renderShell();
    const btns = Array.from(container.querySelectorAll('.sync-tab-button'));
    expect(btns.map((b) => b.getAttribute('data-tab'))).toEqual([
      'mirrored',
      'server',
      'beatport',
    ]);
  });

  it('opens on Mirrored — the library, not a source directory', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="mirrored"]')?.className).toContain('active');
    expect(container.querySelector('#mirrored-tab-content')?.className).toContain('active');
  });

  it('shows a routed tab while it is active, and drops it again after', () => {
    // A panel with no chip is a room with no door.
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
    expect(withRouted).toEqual(['mirrored', 'server', 'beatport', 'spotify-public']);
    expect(container.querySelector('[data-tab="spotify-public"]')?.className).toContain('active');

    act(() => {
      open('server');
    });
    expect(container.querySelectorAll('.sync-tab-button')).toHaveLength(3);
  });

  it('keeps a routed panel MOUNTED after its chip disappears', () => {
    // The chip is navigation; the panel is state. Losing the panel when the
    // chip goes would throw away a playlist the user just loaded.
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
    const icon = (tab: string) =>
      container.querySelector(`[data-tab="${tab}"] .tab-icon`)?.className;
    expect(icon('mirrored')).toBe('tab-icon mirrored-icon');
    expect(icon('server')).toBe('tab-icon server-icon');
    expect(icon('beatport')).toBe('tab-icon beatport-icon');
    for (const t of SYNC_TABS.filter((x) => SYNC_PRIMARY_TAB_IDS.includes(x.id))) {
      expect(container.querySelector(`[data-tab="${t.id}"]`)?.getAttribute('title')).toBe(t.label);
    }
  });

  it('gives the server tab its own extra class', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="server"]')?.className).toContain('sync-tab-server');
    expect(container.querySelector('[data-tab="mirrored"]')?.className).not.toContain(
      'sync-tab-server',
    );
  });

  it('renders a panel for EVERY tab, strip or not — routing depends on it', () => {
    const { container } = renderShell();
    for (const t of SYNC_TABS) {
      expect(container.querySelector(`#${t.id}-tab-content`)).not.toBeNull();
    }
  });

  it('no longer renders the fifteen-tab divider', () => {
    // It marked the boundary after Server Playlists in a crowded strip; with
    // three chips it separates nothing.
    const { container } = renderShell();
    expect(container.querySelectorAll('.sync-tab-divider')).toHaveLength(0);
  });
});

describe('switching tabs (3702-3715)', () => {
  it('opens on Mirrored', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="mirrored"]')?.className).toContain('active');
    expect(container.querySelector('#mirrored-tab-content')?.className).toContain('active');
  });

  it('moves the active class on both the button and the panel', () => {
    const { container } = renderShell();
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);

    expect(container.querySelector('[data-tab="beatport"]')?.className).toContain('active');
    expect(container.querySelector('[data-tab="server"]')?.className).not.toContain('active');
    expect(container.querySelector('#beatport-tab-content')?.className).toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).not.toContain('active');
  });

  it('marks exactly ONE tab active at a time', () => {
    const { container } = renderShell();
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    expect(container.querySelectorAll('.sync-tab-button.active')).toHaveLength(1);
    expect(container.querySelectorAll('.sync-tab-content.active')).toHaveLength(1);
  });

  it('renders a panel for every tab, so the ids resolve like the vanilla ones', () => {
    // 3714 does an unguarded getElementById(`${tabId}-tab-content`); every id
    // must exist or the vanilla would throw mid-handler.
    const { container } = renderShell();
    for (const t of SYNC_TABS) {
      expect(container.querySelector(`#${t.id}-tab-content`)).not.toBeNull();
    }
  });

  it('exposes the selection to assistive tech', () => {
    const { container } = renderShell();
    const aria = (tab: string) =>
      container.querySelector(`[data-tab="${tab}"]`)?.getAttribute('aria-selected');
    expect(aria('mirrored')).toBe('true');
    expect(aria('beatport')).toBe('false');

    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    expect(aria('mirrored')).toBe('false');
    expect(aria('beatport')).toBe('true');
  });
});

describe('the tab-change signal', () => {
  it('fires on every switch, with the tab already updated', () => {
    const onTabChange = vi.fn();
    const { container } = renderShell({ onTabChange });
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(1);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(onTabChange).toHaveBeenCalledTimes(2);
  });

  it('fires for a click on the tab that is ALREADY active', () => {
    // The vanilla handler runs its whole body on any tab click, including the
    // active one, and its unconditional sidebar re-hide is what the page keys
    // off. Filtering same-tab clicks would change that behaviour.
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
      fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    }).not.toThrow();
    expect(container.querySelector('#beatport-tab-content')?.className).toContain('active');
  });
});

describe('panel mounting — the one-shot load flags (3724-3803)', () => {
  const panels = {
    mirrored: <div data-testid="p-mirrored">mirrored</div>,
    server: <div data-testid="p-server">server</div>,
    beatport: <div data-testid="p-beatport">beatport</div>,
  };

  it('mounts only the default panel to begin with', () => {
    const { container } = renderShell({ panels });
    expect(container.querySelector('[data-testid="p-mirrored"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-beatport"]')).toBeNull();
  });

  it('mounts a panel the first time its tab is opened', () => {
    const { container } = renderShell({ panels });
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    expect(container.querySelector('[data-testid="p-beatport"]')).not.toBeNull();
  });

  it('KEEPS a panel mounted after leaving it — the one-shot flags never reset', () => {
    // The vanilla sets e.g. `mirroredPlaylistsLoaded = true` once and never
    // clears it, so returning to a tab shows what it already loaded rather
    // than re-fetching. Unmounting on leave would re-fetch every visit.
    const { container } = renderShell({ panels });
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    fireEvent.click(container.querySelector('[data-tab="server"]') as HTMLElement);
    expect(container.querySelector('[data-testid="p-beatport"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-server"]')).not.toBeNull();
    // ...and only the current one is visible.
    expect(container.querySelector('#beatport-tab-content')?.className).not.toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
  });

  it('mounts each panel only ONCE across repeated visits', () => {
    // Counted in an EFFECT, not the render body: a panel that stays mounted
    // still re-renders when the shell's state changes, so a render counter
    // would tick without a remount and prove nothing. What matters is that the
    // fetch-on-mount never runs a second time.
    let mounts = 0;
    function Counted() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <div data-testid="counted" />;
    }
    const { container } = renderShell({ panels: { beatport: <Counted /> } });
    const tidal = container.querySelector('[data-tab="beatport"]') as HTMLElement;
    const server = container.querySelector('[data-tab="server"]') as HTMLElement;

    expect(mounts).toBe(0);
    fireEvent.click(tidal);
    expect(mounts).toBe(1);
    fireEvent.click(server);
    fireEvent.click(tidal);
    fireEvent.click(server);
    fireEvent.click(tidal);
    expect(mounts).toBe(1);
  });

  it('renders an empty panel for a tab with no content supplied', () => {
    const { container } = renderShell({ panels: { mirrored: panels.mirrored } });
    fireEvent.click(container.querySelector('[data-tab="beatport"]') as HTMLElement);
    expect(container.querySelector('#beatport-tab-content')?.textContent).toBe('');
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
