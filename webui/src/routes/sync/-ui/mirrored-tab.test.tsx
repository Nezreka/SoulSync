/**
 * The mirrored tab, rendered against a captured fetch and the REAL
 * useSourceVertical hook — load → card → the three actions → click dispatch.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { MirroredTab } from './mirrored-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string, method: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url, method)));
    }),
  );
}

const ROW = {
  id: 3,
  name: 'Road Trip',
  source: 'tidal',
  source_playlist_id: 'tp1',
  track_count: 25,
  discovered_count: 0,
  updated_at: '2026-01-15T11:30:00Z',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
});

function Harness(props: Partial<Parameters<typeof MirroredTab>[0]> = {}) {
  const vertical = useSourceVertical(SYNC_SOURCES.mirrored);
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div>
      <MirroredTab vertical={vertical} onOpen={setOpenId} {...props} />
      <span data-testid="open-id">{openId ?? 'none'}</span>
      <span data-testid="phase">{vertical.states.mirrored_3?.phase ?? 'unseeded'}</span>
      <span data-testid="seeded">
        {typeof vertical.states.mirrored_3?.playlist?.name === 'string'
          ? vertical.states.mirrored_3.playlist.name
          : 'no-playlist'}
      </span>
    </div>
  );
}

describe('MirroredTab — load and card', () => {
  it('renders the vanilla card DOM: icon, name, badge, count, timeAgo', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    vi.spyOn(Date, 'now').mockReturnValue(Date.UTC(2026, 0, 15, 12, 0, 0));
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    const card = document.querySelector('#mirrored-card-3')!;
    expect(card.className).toBe('mirrored-playlist-card');
    expect(card.querySelector('.source-icon')!.textContent).toBe('🌊');
    expect(card.querySelector('.source-icon')!.className).toContain('tidal');
    expect(card.querySelector('.source-badge')!.textContent).toBe('tidal');
    expect(screen.getByText('25 tracks')).toBeInTheDocument();
    expect(screen.getByText('Mirrored 30m ago')).toBeInTheDocument();
    // no discovered_count -> no ratio and no ↺ clear button (578, 603)
    expect(card.querySelector('.discovery-ratio')).toBeNull();
    expect(card.querySelector('.mirrored-card-clear')).toBeNull();
  });

  it('the custom_name subline and the ratio appear only when earned', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [{ ...ROW, custom_name: 'My Alias', display_name: 'My Alias', discovered_count: 9 }]
        : { states: [] };
    render(<Harness sourceName="Plex" />);
    await waitFor(() => expect(screen.getByText('My Alias')).toBeInTheDocument());
    expect(screen.getByTitle('Original (upstream) playlist name — still tracked').textContent).toBe(
      '↳ Road Trip',
    );
    const ratio = document.querySelector('.discovery-ratio')!;
    expect(ratio.textContent).toBe('9/25 discovered on Plex');
    expect(ratio.className).not.toContain('complete');
    expect(document.querySelector('.mirrored-card-clear')).not.toBeNull();
  });

  it('a pipeline_state with no live state paints the pipeline phase (534-542)', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [{ ...ROW, pipeline_state: { status: 'running', progress: 40, phase: 'Discovering' } }]
        : { states: [] };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Discovering 40%')).toBeInTheDocument());
    expect(screen.getByText('Discovering 40%')).toHaveStyle({ color: '#38bdf8' });
  });

  it('a LIVE state beats the row pipeline_state (the 534 precedence)', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/mirrored-playlists') {
        return [{ ...ROW, pipeline_state: { status: 'running', progress: 40 } }];
      }
      if (url === '/api/mirrored-playlists/discovery-states') {
        return {
          states: [
            {
              // the real shape: url_hash is the key, playlist_id a bare int
              url_hash: 'mirrored_3',
              playlist_id: 3,
              phase: 'discovered',
              spotify_matches: 7,
              spotify_total: 25,
            },
          ],
        };
      }
      return {};
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Discovered 7/25')).toBeInTheDocument());
    expect(screen.queryByText(/Pipeline|Discovering/)).not.toBeInTheDocument();
  });

  it('the loading placeholder is the vanilla string (503)', () => {
    stubFetch();
    responder = () => new Promise(() => undefined) as unknown as never;
    render(<Harness />);
    expect(screen.getByText('Loading mirrored playlists...')).toBeInTheDocument();
  });

  it('the empty and error placeholders are the vanilla strings (511, 522)', async () => {
    stubFetch();
    responder = () => [];
    const { unmount } = render(<Harness />);
    await waitFor(() =>
      expect(
        screen.getByText(
          'Playlists you parse from any service will appear here as persistent backups.',
        ),
      ).toBeInTheDocument(),
    );
    unmount();

    stubFetch();
    responder = () => ({ error: 'db down' });
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByText('Error loading mirrored playlists: db down')).toBeInTheDocument(),
    );
  });
});

describe('MirroredTab — the three actions', () => {
  const loaded = async (row: Record<string, unknown> = ROW) => {
    stubFetch();
    responder = (url, method) => {
      if (url === '/api/mirrored-playlists' && method === 'GET') return [row];
      if (url.includes('discovery-states')) return { states: [] };
      return { success: true, cleared: 12 };
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
  };

  it('clear: confirm copy, endpoint, toast, and the cancel-signal phase write', async () => {
    const confirm = vi.fn(async () => true);
    const toast = vi.fn();
    window.showConfirmDialog = confirm as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    await loaded({ ...ROW, discovered_count: 4 });

    fireEvent.click(screen.getByTitle('Clear discovery data'));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith({
      title: 'Clear Discovery Data',
      message:
        'Clear discovery data for "Road Trip"? You can re-discover afterwards to get updated cover art.',
    });
    expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/clear-discovery')).toBe(true);
    expect(toast).toHaveBeenCalledWith('Cleared discovery for Road Trip (12 tracks)', 'success');
    // 1187: the entry is DELETED, not left at 'cancelled' — otherwise the
    // next card click reads it as non-fresh and opens an empty modal.
    expect(screen.getByTestId('phase')).toHaveTextContent('unseeded');
    // and the list is reloaded (1190)
    expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBeGreaterThan(1);
  });

  it('clear: a !success response toasts the backend error and reloads nothing', async () => {
    const toast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true) as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    stubFetch();
    responder = (url, method) => {
      if (url === '/api/mirrored-playlists' && method === 'GET')
        return [{ ...ROW, discovered_count: 4 }];
      if (url.includes('discovery-states')) return { states: [] };
      return { success: false, error: 'busy' };
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    const before = calls.filter((c) => c.url === '/api/mirrored-playlists').length;
    fireEvent.click(screen.getByTitle('Clear discovery data'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('busy', 'error'));
    expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBe(before);
  });

  it('clear on a card that HAS state removes the entry entirely (1187)', async () => {
    const toast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true) as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    await loaded({ ...ROW, discovered_count: 4 });

    // Click first so a state exists — without one, neither the guard nor the
    // delete is observable.
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByTestId('seeded')).toHaveTextContent('Road Trip'));

    fireEvent.click(screen.getByTitle('Clear discovery data'));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    // The vanilla deletes the entry; leaving it at 'cancelled' would make the
    // next card click take the non-fresh branch and open an empty modal.
    expect(screen.getByTestId('phase')).toHaveTextContent('unseeded');
    expect(screen.getByTestId('seeded')).toHaveTextContent('no-playlist');
  });

  it('clear: declining the confirm does nothing at all', async () => {
    window.showConfirmDialog = vi.fn(async () => false) as typeof window.showConfirmDialog;
    window.showToast = vi.fn() as typeof window.showToast;
    await loaded({ ...ROW, discovered_count: 4 });
    const before = calls.length;
    fireEvent.click(screen.getByTitle('Clear discovery data'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.length).toBe(before);
  });

  it('delete: the destructive confirm shape, endpoint and toast (2024-2032)', async () => {
    const confirm = vi.fn(async () => true);
    const toast = vi.fn();
    window.showConfirmDialog = confirm as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    await loaded();

    fireEvent.click(screen.getByTitle('Delete mirror'));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledWith({
      title: 'Delete Playlist',
      message: 'Delete mirrored playlist "Road Trip"?',
      confirmText: 'Delete',
      destructive: true,
    });
    const del = calls.find((c) => c.method === 'DELETE')!;
    expect(del.url).toBe('/api/mirrored-playlists/3');
    expect(toast).toHaveBeenCalledWith('Deleted mirror: Road Trip', 'success');
    // the list reload at 2030
    expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBeGreaterThan(1);
  });

  it('delete: declining the destructive confirm sends nothing', async () => {
    window.showConfirmDialog = vi.fn(async () => false) as typeof window.showConfirmDialog;
    window.showToast = vi.fn() as typeof window.showToast;
    await loaded();
    const before = calls.length;
    fireEvent.click(screen.getByTitle('Delete mirror'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.length).toBe(before);
  });

  it('rename: an INPUT, not window.prompt — Enter PATCHes and toasts', async () => {
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const prompt = vi.fn();
    window.prompt = prompt as typeof window.prompt;
    await loaded();

    fireEvent.click(screen.getByTitle(/^Rename/));
    const input = document.querySelector('.mirrored-rename-input') as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(prompt).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '  My Alias  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const patch = calls.find((c) => c.method === 'PATCH')!;
    expect(patch.url).toBe('/api/mirrored-playlists/3/custom-name');
    expect(patch.body).toEqual({ custom_name: 'My Alias' });
    expect(toast).toHaveBeenCalledWith('Renamed to "My Alias"', 'success');
    expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBeGreaterThan(1);
  });

  it('rename: a failure keeps the vanilla Error: prefix (auto-sync.js 2406)', async () => {
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    stubFetch();
    let patched = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        calls.push({ url, method, body: undefined });
        if (method === 'PATCH') {
          patched = true;
          return new Response(JSON.stringify({ error: 'taken' }), { status: 409 });
        }
        if (url === '/api/mirrored-playlists') return new Response(JSON.stringify([ROW]));
        return new Response(JSON.stringify({ states: [] }));
      }),
    );
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle(/^Rename/));
    const input = document.querySelector('.mirrored-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'X' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(patched).toBe(true));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Error: taken', 'error'));
  });

  it('rename: a blank value reverts to the original name (2398)', async () => {
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    await loaded();
    fireEvent.click(screen.getByTitle(/^Rename/));
    const input = document.querySelector('.mirrored-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ custom_name: '' });
    expect(toast).toHaveBeenCalledWith('Reverted to "Road Trip"', 'success');
  });

  it('rename: Escape cancels without a request (the vanilla null return, 2386)', async () => {
    window.showToast = vi.fn() as typeof window.showToast;
    await loaded();
    fireEvent.click(screen.getByTitle(/^Rename/));
    const input = document.querySelector('.mirrored-rename-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Nope' } });
    const before = calls.length;
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(document.querySelector('.mirrored-rename-input')).toBeNull();
    expect(calls.length).toBe(before);
  });

  it('an action click never opens the card (event.stopPropagation, 603-608)', async () => {
    window.showConfirmDialog = vi.fn(async () => false) as typeof window.showConfirmDialog;
    window.showToast = vi.fn() as typeof window.showToast;
    await loaded({ ...ROW, discovered_count: 4 });
    fireEvent.click(screen.getByTitle('Clear discovery data'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });
});

describe('MirroredTab — deferred controls and click dispatch', () => {
  it('the pool + Auto-Sync buttons render ONLY with a handler; export always does', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    const { unmount } = render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(screen.queryByText('Discovery Pool')).not.toBeInTheDocument();
    expect(screen.queryByText('Wing It Pool')).not.toBeInTheDocument();
    expect(screen.queryByText('Auto-Sync')).not.toBeInTheDocument();
    // Export owns its own modal + controller since P5g, so it takes no handler
    // and is never conditional.
    expect(screen.getByTitle('Export to ListenBrainz / JSPF')).toBeInTheDocument();
    unmount();

    stubFetch();
    render(
      <Harness
        onOpenDiscoveryPool={() => undefined}
        onOpenWingItPool={() => undefined}
        onAutoSync={() => undefined}
      />,
    );
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(screen.getByText('Discovery Pool')).toBeInTheDocument();
    expect(screen.getByText('Wing It Pool')).toBeInTheDocument();
    expect(screen.getByText('Auto-Sync')).toBeInTheDocument();
  });

  it('a card click opens the shared modal under the mirrored_<id> hash', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('mirrored_3'));
    // the fresh branch must SEED the row, or the shared modal opens with no
    // playlist at all (discovery-modal 123/127 fall back to [] / 'Playlist')
    expect(screen.getByTestId('seeded')).toHaveTextContent('Road Trip');
  });

  it('Update list refetches (the refresh button keeps its vanilla id)', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    const before = calls.filter((c) => c.url === '/api/mirrored-playlists').length;
    expect(document.querySelector('#mirrored-refresh-btn')).not.toBeNull();
    fireEvent.click(screen.getByText('Update list'));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBe(before + 1),
    );
  });
});

describe('MirroredTab — export (#903)', () => {
  const withExport = (over: Record<string, unknown> = {}): typeof responder => {
    return (url) => {
      if (url === '/api/mirrored-playlists') return [ROW];
      if (url === '/api/discover/your-albums/sources') return { connected: ['spotify'] };
      if (url.includes('/export/status/')) return over.status ?? { job: { phase: 'pushing' } };
      if (url.includes('/export/')) return over.start ?? { success: true, job_id: 'j9' };
      return { states: [] };
    };
  };

  it('📤 opens the picker; a choice starts the job and paints INTO the card meta', async () => {
    stubFetch();
    responder = withExport();
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Export to ListenBrainz / JSPF'));
    await waitFor(() => expect(screen.getByText('Export playlist')).toBeInTheDocument());
    // The picker names the card's shown name (607).
    expect(screen.getByText('Road Trip → ListenBrainz')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Sync to ListenBrainz'));
    expect(screen.queryByText('Export playlist')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('.export-status-span')?.textContent).toBe(
        'Pushing to ListenBrainz…',
      ),
    );
    // _setExportStatus injects into the card's own .card-meta row (809-813).
    expect(
      document.querySelector('#mirrored-card-3 .card-meta .export-status-span'),
    ).not.toBeNull();
    expect(calls.some((c) => c.url === '/api/playlists/3/export/listenbrainz')).toBe(true);
  });

  it('a gated service choice nudges to Settings instead of exporting', async () => {
    stubFetch();
    responder = withExport();
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Export to ListenBrainz / JSPF'));
    await waitFor(() =>
      expect(document.querySelector('[data-mode="deezer"]')).toHaveAttribute('data-disconnected'),
    );
    fireEvent.click(document.querySelector('[data-mode="deezer"]')!);
    await waitFor(() =>
      expect(document.querySelector('.export-status-span')?.textContent).toBe(
        'Connect Deezer in Settings → Connections to export here',
      ),
    );
    expect(calls.some((c) => c.url.includes('/export/'))).toBe(false);
  });

  it('the 📤 click never opens the card behind it', async () => {
    stubFetch();
    responder = withExport();
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Export to ListenBrainz / JSPF'));
    await waitFor(() => expect(screen.getByText('Export playlist')).toBeInTheDocument());
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });
});
