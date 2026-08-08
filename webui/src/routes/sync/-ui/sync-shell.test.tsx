/**
 * Differential tests for the sync page's shell — index.html 2226-2295 and the
 * tab handler at sync-services.js 3694-3803.
 */

import { fireEvent, render } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SYNC_TABS } from '../-sync.shell';
import { SyncShell } from './sync-shell';

function renderShell(over: Partial<React.ComponentProps<typeof SyncShell>> = {}) {
  const props: React.ComponentProps<typeof SyncShell> = {
    panels: {},
    onAutoSync: vi.fn(),
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

  it('renders the four action buttons in order, with their tooltips', () => {
    const { container } = renderShell();
    const btns = Array.from(container.querySelectorAll('.sync-header-actions button'));
    expect(btns.map((b) => b.textContent)).toEqual([
      'Auto-Sync',
      'Library Match',
      'Sync History',
      'Download Origins',
    ]);
    expect(btns[0].getAttribute('title')).toBe(
      'Schedule mirrored playlists to refresh, discover, sync, and queue missing tracks',
    );
    expect(btns[3].getAttribute('title')).toBe('See every track your playlist syncs downloaded');
    // Only Auto-Sync carries the extra hook class the vanilla gives it.
    expect(btns[0].className).toContain('auto-sync-manager-btn');
    expect(btns[1].className).not.toContain('auto-sync-manager-btn');
  });

  it('routes Auto-Sync to React and the other three to their vanilla seams', () => {
    window.openManualLibraryMatchTool = vi.fn();
    window.openSyncHistoryModal = vi.fn();
    window.openDownloadOriginsModal = vi.fn();
    const { container, props } = renderShell();
    const btns = container.querySelectorAll('.sync-header-actions button');

    fireEvent.click(btns[0]);
    expect(props.onAutoSync).toHaveBeenCalledTimes(1);
    fireEvent.click(btns[1]);
    expect(window.openManualLibraryMatchTool).toHaveBeenCalledTimes(1);
    fireEvent.click(btns[2]);
    expect(window.openSyncHistoryModal).toHaveBeenCalledTimes(1);
    fireEvent.click(btns[3]);
    // 2241: the shared modal is scoped by this literal.
    expect(window.openDownloadOriginsModal).toHaveBeenCalledWith('playlist');
  });

  it('does not throw when a vanilla seam is missing', () => {
    const { container } = renderShell();
    const btns = container.querySelectorAll('.sync-header-actions button');
    expect(() => {
      fireEvent.click(btns[1]);
      fireEvent.click(btns[2]);
      fireEvent.click(btns[3]);
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

describe('the tab strip (2249-2295)', () => {
  it('renders all fifteen tabs, in order, with their labels', () => {
    const { container } = renderShell();
    const btns = Array.from(container.querySelectorAll('.sync-tab-button'));
    expect(btns).toHaveLength(15);
    expect(btns.map((b) => b.getAttribute('data-tab'))).toEqual([
      'server',
      'spotify',
      'spotify-public',
      'itunes-link',
      'tidal',
      'qobuz',
      'deezer',
      'deezer-link',
      'youtube',
      'beatport',
      'listenbrainz-sync',
      'lastfm-sync',
      'soulsync-discovery-sync',
      'import-file',
      'mirrored',
    ]);
    expect(btns[0].querySelector('.sync-tab-label')?.textContent).toBe('Server Playlists');
    expect(btns[12].querySelector('.sync-tab-label')?.textContent).toBe('SoulSync Discovery');
  });

  it('gives each tab its sprite class, sharing one across link variants', () => {
    const { container } = renderShell();
    const icon = (tab: string) =>
      container.querySelector(`[data-tab="${tab}"] .tab-icon`)?.className;
    expect(icon('spotify')).toBe('tab-icon spotify-icon');
    // The link variants are the same service, so they reuse the sprite.
    expect(icon('spotify-public')).toBe('tab-icon spotify-icon');
    expect(icon('deezer-link')).toBe('tab-icon deezer-icon');
    expect(icon('listenbrainz-sync')).toBe('tab-icon listenbrainz-icon');
  });

  it('marks ONLY the three URL-import tabs with data-link', () => {
    const { container } = renderShell();
    const linked = Array.from(container.querySelectorAll('.sync-tab-button[data-link="true"]')).map(
      (b) => b.getAttribute('data-tab'),
    );
    expect(linked).toEqual(['spotify-public', 'itunes-link', 'deezer-link']);
  });

  it('puts the divider after Server Playlists, and only there', () => {
    const { container } = renderShell();
    const dividers = container.querySelectorAll('.sync-tab-divider');
    expect(dividers).toHaveLength(1);
    // It follows the server button in document order.
    expect(dividers[0].previousElementSibling?.getAttribute('data-tab')).toBe('server');
  });

  it('gives the server tab its own extra class', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="server"]')?.className).toContain('sync-tab-server');
    expect(container.querySelector('[data-tab="spotify"]')?.className).not.toContain(
      'sync-tab-server',
    );
  });

  it('titles every tab with its own label', () => {
    const { container } = renderShell();
    for (const t of SYNC_TABS) {
      expect(container.querySelector(`[data-tab="${t.id}"]`)?.getAttribute('title')).toBe(t.label);
    }
  });
});

describe('switching tabs (3702-3715)', () => {
  it('opens on Server Playlists', () => {
    const { container } = renderShell();
    expect(container.querySelector('[data-tab="server"]')?.className).toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).toContain('active');
  });

  it('moves the active class on both the button and the panel', () => {
    const { container } = renderShell();
    fireEvent.click(container.querySelector('[data-tab="tidal"]') as HTMLElement);

    expect(container.querySelector('[data-tab="tidal"]')?.className).toContain('active');
    expect(container.querySelector('[data-tab="server"]')?.className).not.toContain('active');
    expect(container.querySelector('#tidal-tab-content')?.className).toContain('active');
    expect(container.querySelector('#server-tab-content')?.className).not.toContain('active');
  });

  it('marks exactly ONE tab active at a time', () => {
    const { container } = renderShell();
    fireEvent.click(container.querySelector('[data-tab="mirrored"]') as HTMLElement);
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
    expect(container.querySelector('[data-tab="server"]')?.getAttribute('aria-selected')).toBe(
      'true',
    );
    fireEvent.click(container.querySelector('[data-tab="qobuz"]') as HTMLElement);
    expect(container.querySelector('[data-tab="server"]')?.getAttribute('aria-selected')).toBe(
      'false',
    );
    expect(container.querySelector('[data-tab="qobuz"]')?.getAttribute('aria-selected')).toBe(
      'true',
    );
  });
});

describe('panel mounting — the one-shot load flags (3724-3803)', () => {
  const panels = {
    server: <div data-testid="p-server">server</div>,
    tidal: <div data-testid="p-tidal">tidal</div>,
    qobuz: <div data-testid="p-qobuz">qobuz</div>,
  };

  it('mounts only the default panel to begin with', () => {
    const { container } = renderShell({ panels });
    expect(container.querySelector('[data-testid="p-server"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-tidal"]')).toBeNull();
  });

  it('mounts a panel the first time its tab is opened', () => {
    const { container } = renderShell({ panels });
    fireEvent.click(container.querySelector('[data-tab="tidal"]') as HTMLElement);
    expect(container.querySelector('[data-testid="p-tidal"]')).not.toBeNull();
  });

  it('KEEPS a panel mounted after leaving it — the one-shot flags never reset', () => {
    // The vanilla sets e.g. `mirroredPlaylistsLoaded = true` once and never
    // clears it, so returning to a tab shows what it already loaded rather
    // than re-fetching. Unmounting on leave would re-fetch every visit.
    const { container } = renderShell({ panels });
    fireEvent.click(container.querySelector('[data-tab="tidal"]') as HTMLElement);
    fireEvent.click(container.querySelector('[data-tab="qobuz"]') as HTMLElement);
    expect(container.querySelector('[data-testid="p-tidal"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="p-qobuz"]')).not.toBeNull();
    // ...and only the current one is visible.
    expect(container.querySelector('#tidal-tab-content')?.className).not.toContain('active');
    expect(container.querySelector('#qobuz-tab-content')?.className).toContain('active');
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
    const { container } = renderShell({ panels: { tidal: <Counted /> } });
    const tidal = container.querySelector('[data-tab="tidal"]') as HTMLElement;
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
    const { container } = renderShell({ panels });
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
