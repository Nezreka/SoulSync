/**
 * The mirrored tab, rendered against a captured fetch and the REAL
 * useSourceVertical hook — load → card → the three actions → click dispatch.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useCallback, useMemo, useRef, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { mirroredPipelineStateWriter } from '../-sync.mirrored';
import { SYNC_SOURCES } from '../-sync.sources';
import { useMirroredPipeline } from '../-sync.use-pipeline';
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
  /**
   * The harness performs the PAGE's wiring, not a stub: one controller, its
   * state writer from the shared module, and reload delegated to a slot the
   * tab fills via registerReload. A stubbed controller would leave the two
   * pipeline tests below asserting against a no-op.
   */
  const reloadRef = useRef<(() => void) | undefined>(undefined);
  const onState = useMemo(() => mirroredPipelineStateWriter(vertical), [vertical]);
  const reload = useCallback(() => reloadRef.current?.(), []);
  const pipeline = useMirroredPipeline({ onState, reload });
  return (
    <div>
      <MirroredTab
        vertical={vertical}
        onOpen={setOpenId}
        pipeline={pipeline}
        registerReload={(fn) => {
          reloadRef.current = fn;
        }}
        {...props}
      />
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

/**
 * Run one of a card's overflow actions.
 *
 * They used to be five icon-only buttons on every card at once; they live
 * behind the ⋯ menu now, so a test has to open it the way a user does.
 */
function runCardAction(label: string, cardIndex = 0): void {
  const more = document.querySelectorAll('.pl-card-more')[cardIndex] as HTMLElement;
  fireEvent.click(more);
  const item = [...document.querySelectorAll('.pl-menu-item')].find(
    (b) => b.textContent === label,
  ) as HTMLElement;
  fireEvent.click(item);
}

describe('MirroredTab — load and card', () => {
  it('renders the card: cover, brand mark, name, one meta line, schedule', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    vi.spyOn(Date, 'now').mockReturnValue(Date.UTC(2026, 0, 15, 12, 0, 0));
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    const card = document.querySelector('#mirrored-card-3')!;
    expect(card.className).toBe('pl-card');
    // The brand mark replaces the emoji, and appears twice on purpose: once on
    // the cover (as its fallback) and once beside the name.
    expect(card.querySelector('.pl-card-art .tidal-icon')).not.toBeNull();
    expect(card.querySelector('.pl-card-name .tidal-icon')).not.toBeNull();
    // ONE meta line in place of the old chip row (source badge, count,
    // mirrored-ago, ratio, phase, export status).
    expect(card.querySelector('.pl-card-meta')!.textContent).toBe('25 tracks · Mirrored 30m ago');
    // Untouched playlist: no ring, and Clear discovery is not offered.
    expect(card.querySelector('.pl-ring')).toBeNull();
    fireEvent.click(card.querySelector('.pl-card-more') as HTMLElement);
    expect([...document.querySelectorAll('.pl-menu-item')].map((b) => b.textContent)).not.toContain(
      'Clear discovery',
    );
  });

  it('a renamed playlist keeps its original name, and a partial one rings', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [{ ...ROW, custom_name: 'My Alias', display_name: 'My Alias', discovered_count: 9 }]
        : { states: [] };
    render(<Harness sourceName="Plex" />);
    await waitFor(() => expect(screen.getByText('My Alias')).toBeInTheDocument());
    // The "↳ original name" line is gone — a whole extra line on every renamed
    // card for something you read once. It moved to the name's hover text.
    expect(document.querySelector('.pl-card-name b')!.getAttribute('title')).toBe(
      'My Alias — originally "Road Trip"',
    );
    // The ratio chip became the ring on the cover: 9 of 25 is 36%.
    const ring = document.querySelector('.pl-ring')!;
    expect(ring.getAttribute('data-pct')).toBe('36%');
    expect(ring.className).toContain('pl-ring--short');
    // Clear discovery is offered now that there IS a discovery to clear.
    fireEvent.click(document.querySelector('.pl-card-more') as HTMLElement);
    expect([...document.querySelectorAll('.pl-menu-item')].map((b) => b.textContent)).toContain(
      'Clear discovery',
    );
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

  it('the empty and error placeholders read in the user\u2019s words', async () => {
    // Deliberate copy change: the vanilla said "playlists you PARSE ... as
    // persistent BACKUPS", which describes the implementation. A user adds a
    // playlist; they do not parse one.
    stubFetch();
    responder = () => [];
    const { unmount } = render(<Harness />);
    await waitFor(() =>
      expect(
        screen.getByText('Playlists you add from any service will appear here.'),
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

    runCardAction('Clear discovery');
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
    runCardAction('Clear discovery');
    await waitFor(() => expect(toast).toHaveBeenCalledWith('busy', 'error'));
    expect(calls.filter((c) => c.url === '/api/mirrored-playlists').length).toBe(before);
  });

  it('clear on a card that HAS state removes the entry entirely (1187)', async () => {
    const toast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true) as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    await loaded({ ...ROW, discovered_count: 4 });

    // A state has to exist first, or neither the guard nor the delete is
    // observable. The real path there is card → detail modal → Discover,
    // which registers the mirror and hydrates (2043-2144).
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [{ ...ROW, discovered_count: 4 }]
        : url === '/api/mirrored-playlists/3'
          ? { name: 'Road Trip', source: 'spotify', tracks: [] }
          : url.includes('prepare-discovery')
            ? {}
            : url.includes('clear-discovery')
              ? { success: true, cleared: 4 }
              : { states: [] };
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Discover')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Discover'));
    await waitFor(() => expect(screen.getByTestId('seeded')).toHaveTextContent('Road Trip'));

    runCardAction('Clear discovery');
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
    runCardAction('Clear discovery');
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.length).toBe(before);
  });

  it('delete: the destructive confirm shape, endpoint and toast (2024-2032)', async () => {
    const confirm = vi.fn(async () => true);
    const toast = vi.fn();
    window.showConfirmDialog = confirm as typeof window.showConfirmDialog;
    window.showToast = toast as typeof window.showToast;
    await loaded();

    runCardAction('Delete');
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
    runCardAction('Delete');
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.length).toBe(before);
  });

  it('rename: an INPUT, not window.prompt — Enter PATCHes and toasts', async () => {
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const prompt = vi.fn();
    window.prompt = prompt as typeof window.prompt;
    await loaded();

    runCardAction('Rename');
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
    runCardAction('Rename');
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
    runCardAction('Rename');
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
    runCardAction('Rename');
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
    runCardAction('Clear discovery');
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });
});

describe('MirroredTab — deferred controls and click dispatch', () => {
  it('every control renders unconditionally — the tab takes no handler props', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    // None is conditional: the card actions own their controllers.
    fireEvent.click(document.querySelector('.pl-card-more') as HTMLElement);
    const labels = [...document.querySelectorAll('.pl-menu-item')].map((b) => b.textContent);
    expect(labels).toContain('Export');
    expect(screen.getByText('Sync now')).toBeInTheDocument();
    expect(labels).toContain('Edit source link');
  });

  it('a FRESH card click opens the tracks detail modal (641), not the discovery one', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [ROW]
        : url === '/api/mirrored-playlists/3'
          ? {
              name: 'Road Trip',
              source: 'spotify',
              owner: 'boulder',
              tracks: [
                {
                  position: 1,
                  track_name: 'Alright',
                  artist_name: 'Kendrick',
                  duration_ms: 219000,
                },
              ],
            }
          : { states: [] };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(document.querySelector('#mirrored-track-modal')).not.toBeNull());
    // The detail modal's own chrome, not the discovery modal's.
    expect(screen.getByText('Mirrored Playlist')).toBeInTheDocument();
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
    expect(screen.getByText('Alright')).toBeInTheDocument();
    // The discovery modal stays shut until Discover is pressed.
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });

  it('Discover registers the mirror BEFORE starting (prepare-discovery, 2062)', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [ROW]
        : url === '/api/mirrored-playlists/3'
          ? {
              name: 'Road Trip',
              source: 'spotify',
              tracks: [
                { id: 9, track_name: 'Alright', artist_name: 'Kendrick', duration_ms: 219000 },
              ],
            }
          : url.includes('prepare-discovery')
            ? {}
            : { states: [] };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Discover')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Discover'));

    await waitFor(() => expect(screen.getByTestId('open-id')).toHaveTextContent('mirrored_3'));
    const prep = calls.find((c) => c.url.includes('prepare-discovery'));
    expect(prep, 'the port used to skip this entirely').toBeDefined();
    expect(prep?.method).toBe('POST');
    // Registered first, then the state seeded — the modal opens with the
    // playlist, not empty.
    expect(screen.getByTestId('seeded')).toHaveTextContent('Road Trip');
    // The detail modal closed on the way through (2044).
    expect(document.querySelector('#mirrored-track-modal')).toBeNull();
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
    runCardAction('Export');
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
    runCardAction('Export');
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
    runCardAction('Export');
    await waitFor(() => expect(screen.getByText('Export playlist')).toBeInTheDocument());
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });
});

describe('MirroredTab — Auto-Sync and the 🔗 source ref', () => {
  it('Sync now runs the pipeline and paints its phase onto the card', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/mirrored-playlists') return [ROW];
      if (url.endsWith('/pipeline/run')) {
        return { state: { status: 'running', progress: 0, phase: 'Refreshing' } };
      }
      if (url.endsWith('/pipeline/status'))
        return { status: 'running', progress: 45, phase: 'Syncing' };
      return { states: [] };
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Sync now'));
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/pipeline/run')).toBe(true),
    );
    expect(window.showToast).toHaveBeenCalledWith('Auto-Sync started for Road Trip', 'success');
    // applyPipelineState wrote pipeline_running onto the SHARED state, so the
    // mirrored phase line renders it from there rather than the stale row.
    await waitFor(() => expect(screen.getByTestId('phase')).toHaveTextContent('pipeline_running'));
    await waitFor(() => expect(screen.getByText('Syncing 45%')).toBeInTheDocument());
  });

  it('the Sync now click never opens the card behind it', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Sync now'));
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');
  });

  it('a row the backend says is RUNNING resumes its poll on render (653-655)', async () => {
    stubFetch();
    responder = (url) => {
      if (url === '/api/mirrored-playlists') {
        return [{ ...ROW, pipeline_state: { status: 'running', progress: 20 } }];
      }
      if (url.endsWith('/pipeline/status')) return { status: 'running', progress: 20 };
      return { states: [] };
    };
    render(<Harness />);
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/pipeline/status')).toBe(true),
    );
    // Idempotent: re-renders must not stack a second poller.
    const polls = calls.filter((c) => c.url.endsWith('/pipeline/status')).length;
    fireEvent.mouseOver(screen.getByText('Road Trip'));
    expect(calls.filter((c) => c.url.endsWith('/pipeline/status')).length).toBe(polls);
  });

  it('an idle row does NOT start a poller', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [{ ...ROW, pipeline_state: { status: 'idle' } }]
        : { states: [] };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith('/pipeline/status'))).toBe(false);
  });

  it('🔗 opens the editor pre-filled from getMirroredSourceRef and PATCHes', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    runCardAction('Edit source link');
    // ROW has no source_ref and no URL description, so the id is the default.
    const input = document.querySelector('.mirrored-source-ref-input') as HTMLInputElement;
    expect(input.value).toBe('tp1');
    expect(screen.getByTestId('open-id')).toHaveTextContent('none');

    fireEvent.change(input, { target: { value: 'tp2' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/source-ref')).toBe(true),
    );
    const patch = calls.find((c) => c.url === '/api/mirrored-playlists/3/source-ref')!;
    expect(patch.method).toBe('PATCH');
    expect(patch.body).toEqual({ source_ref: 'tp2' });
    expect(window.showToast).toHaveBeenCalledWith('Updated source for Road Trip', 'success');
  });

  it('a rejected source ref surfaces the backend message', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => {
      if (url === '/api/mirrored-playlists') return [ROW];
      if (url.endsWith('/source-ref')) return { error: 'not a playlist url' };
      return { states: [] };
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    runCardAction('Edit source link');
    const input = document.querySelector('.mirrored-source-ref-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'nope' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Error: not a playlist url', 'error'),
    );
  });

  it('an empty source ref is rejected without a PATCH', async () => {
    stubFetch();
    window.showToast = vi.fn() as typeof window.showToast;
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    runCardAction('Edit source link');
    const input = document.querySelector('.mirrored-source-ref-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.click(screen.getByText('Save'));
    expect(window.showToast).toHaveBeenCalledWith('Source link or ID is required', 'error');
    expect(calls.some((c) => c.url.endsWith('/source-ref'))).toBe(false);
    // ...and the editor stays open so the value can be fixed.
    expect(document.querySelector('.mirrored-source-ref-input')).not.toBeNull();
  });
});

describe('MirroredTab — it hands its reload to the controller owner', () => {
  /**
   * The page owns ONE pipeline controller so the Auto-Sync board and this tab
   * cannot poll the same playlist twice. The controller needs a `reload` at
   * construction and only this tab can do one, so the owner holds a slot and
   * the tab fills it. If the tab never registers, the controller's terminal
   * arms call a no-op and the mirrored list silently stops refreshing when a
   * pipeline finishes — no error, just a stale card.
   */
  it('does NOT re-register when the caller passes a fresh function each render', async () => {
    // registerReload is a prop, so `registerReload={(fn) => …}` inline is a
    // NEW function every render. Listing it as a dependency would re-fire the
    // effect on every one — the trap useAutoSync's `now` fell into, where it
    // looped forever refetching five endpoints. Held in a ref instead, so only
    // `reload` decides.
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    stubFetch();

    let registrations = 0;
    function Wrapper({ tick }: { tick: number }) {
      return (
        <div>
          <span data-testid="tick">{tick}</span>
          <Harness
            registerReload={() => {
              registrations += 1;
            }}
          />
        </div>
      );
    }
    const { rerender } = render(<Wrapper tick={0} />);
    await screen.findByText('Road Trip');
    const after = registrations;

    rerender(<Wrapper tick={1} />);
    rerender(<Wrapper tick={2} />);
    expect(registrations).toBe(after);
  });

  it('registers a working reload that refetches the list', async () => {
    const calls: string[] = [];
    responder = (url) => {
      calls.push(url);
      return url === '/api/mirrored-playlists' ? [ROW] : { states: [] };
    };
    stubFetch();

    let registered: (() => void) | undefined;
    render(
      <Harness
        registerReload={(fn) => {
          registered = fn;
        }}
      />,
    );
    await screen.findByText('Road Trip');

    // It registered a function, not nothing.
    expect(typeof registered).toBe('function');

    // ...and calling it actually refetches the list, rather than being a
    // stale closure over a dead render.
    const before = calls.filter((u) => u === '/api/mirrored-playlists').length;
    await act(async () => {
      registered?.();
    });
    const after = calls.filter((u) => u === '/api/mirrored-playlists').length;
    expect(after).toBeGreaterThan(before);
  });
});

/*
 * The pool buttons moved OUT of this tab and into the page header — both are
 * app-level overlays reviewing tracks across everything, not one tab's
 * controls, and the Tools page opens the Discovery Pool through the same seam.
 * Their wiring is covered by sync-shell.test.tsx now; what this tab must prove
 * is that it no longer renders them itself.
 */
describe('MirroredTab — the pools live in the page header now', () => {
  it('renders neither pool button of its own', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(screen.queryByText('Discovery Pool')).toBeNull();
    expect(screen.queryByText('Wing It Pool')).toBeNull();
  });
});

describe('MirroredTab — the source-ref reopen tail (2434-2438)', () => {
  const DETAIL = {
    name: 'Road Trip',
    source: 'spotify',
    source_ref: 'https://open.spotify.com/playlist/old',
    tracks: [],
  };

  it('reopens the detail modal when the edit came FROM it', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [ROW]
        : url === '/api/mirrored-playlists/3'
          ? DETAIL
          : { success: true };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Edit Source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Edit Source'));
    // The editor replaces the detail modal while it is open (React cannot
    // stack them the way the vanilla's prompt sat on top).
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );
    expect(document.querySelector('#mirrored-track-modal')).toBeNull();

    const input = document.querySelector('#mirrored-source-ref-modal input');
    fireEvent.change(input as Element, {
      target: { value: 'https://open.spotify.com/playlist/new' },
    });
    fireEvent.click(screen.getByText('Save'));

    // Back to the detail modal, refetched so it shows the new source.
    await waitFor(() => expect(document.querySelector('#mirrored-track-modal')).not.toBeNull());
    const patched = calls.filter((c) => c.url === '/api/mirrored-playlists/3/source-ref');
    expect(patched).toHaveLength(1);
    expect(patched[0].method).toBe('PATCH');
  });

  it('does NOT reopen it when the edit came from the card 🔗', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? [ROW]
        : url === '/api/mirrored-playlists/3'
          ? DETAIL
          : { success: true };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    runCardAction('Edit source link');
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );
    const input = document.querySelector('#mirrored-source-ref-modal input');
    fireEvent.change(input as Element, {
      target: { value: 'https://open.spotify.com/playlist/new' },
    });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/source-ref')).toBe(true),
    );
    // The vanilla's probe finds no open modal here, so nothing opens.
    expect(document.querySelector('#mirrored-track-modal')).toBeNull();
  });

  it('cancelling from the detail modal leaves nothing open', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists' ? [ROW] : url === '/api/mirrored-playlists/3' ? DETAIL : {};
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Edit Source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Edit Source'));
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(document.querySelector('#mirrored-source-ref-modal')).toBeNull());
    expect(document.querySelector('#mirrored-track-modal')).toBeNull();
  });
});

describe('MirroredTab — the reopen origin cannot leak between entry points', () => {
  const DETAIL = { name: 'Road Trip', source: 'spotify', tracks: [] };

  function serve(url: string): unknown {
    if (url === '/api/mirrored-playlists') return [ROW];
    if (url === '/api/mirrored-playlists/3') return DETAIL;
    return { success: true };
  }

  function save(value: string): void {
    const input = document.querySelector('#mirrored-source-ref-modal input');
    fireEvent.change(input as Element, { target: { value } });
    fireEvent.click(screen.getByText('Save'));
  }

  it('a CANCELLED detail edit does not make the next card edit reopen it', async () => {
    stubFetch();
    responder = serve;
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    // Open from the detail modal, then back out.
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Edit Source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Edit Source'));
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(document.querySelector('#mirrored-source-ref-modal')).toBeNull());

    // Now edit from the CARD and commit.
    runCardAction('Edit source link');
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );
    save('https://open.spotify.com/playlist/new');
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/mirrored-playlists/3/source-ref')).toBe(true),
    );
    // The card edit has no modal behind it, so nothing may open.
    expect(document.querySelector('#mirrored-track-modal')).toBeNull();
  });

  it('seeds the editor from the DETAIL payload, not the list row (1084-1085)', async () => {
    stubFetch();
    responder = (url) =>
      url === '/api/mirrored-playlists'
        ? // The list is stale: an older name and no source_ref at all.
          [{ ...ROW, name: 'Stale Name', source: 'spotify', source_ref: undefined }]
        : url === '/api/mirrored-playlists/3'
          ? {
              name: 'Road Trip',
              source: 'spotify',
              source_ref: 'https://open.spotify.com/playlist/fresh',
              tracks: [],
            }
          : { success: true };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Stale Name')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Stale Name'));
    await waitFor(() => expect(screen.getByText('Edit Source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Edit Source'));
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );

    // The prompt names what the MODAL showed, and the field is pre-filled from
    // the modal's source_ref — neither is available on the stale list row.
    const input = document.querySelector('#mirrored-source-ref-modal input') as HTMLInputElement;
    expect(input.value).toBe('https://open.spotify.com/playlist/fresh');
    expect(document.querySelector('#mirrored-source-ref-modal')?.textContent).toContain(
      'Road Trip',
    );
  });

  it('refreshes the LIST before reopening the detail (2432-2437 order)', async () => {
    stubFetch();
    responder = serve;
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Road Trip'));
    await waitFor(() => expect(screen.getByText('Edit Source')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Edit Source'));
    await waitFor(() =>
      expect(document.querySelector('#mirrored-source-ref-modal')).not.toBeNull(),
    );

    const before = calls.length;
    save('https://open.spotify.com/playlist/new');
    await waitFor(() => expect(document.querySelector('#mirrored-track-modal')).not.toBeNull());

    const after = calls.slice(before).map((c) => c.url);
    const listAt = after.indexOf('/api/mirrored-playlists');
    const detailAt = after.lastIndexOf('/api/mirrored-playlists/3');
    expect(listAt).toBeGreaterThanOrEqual(0);
    // loadMirroredPlaylists() runs first, so the card behind the modal is
    // already current when the modal comes back.
    expect(listAt).toBeLessThan(detailAt);
  });
});

describe('MirroredTab — the schedule pill', () => {
  it('is a BUTTON that opens the schedule menu, not the detail modal', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    const pill = document.querySelector('.pl-card-pill') as HTMLElement;
    expect(pill).not.toBeNull();
    // A span here means onSchedule never reached the card, and the click would
    // fall through to the card and open the detail modal instead.
    expect(pill.tagName).toBe('BUTTON');

    fireEvent.click(pill);
    expect(document.querySelector('.pl-menu--schedule')).not.toBeNull();
  });
});

describe('MirroredTab — finding one playlist among many', () => {
  const LIBRARY = [
    ROW,
    { ...ROW, id: 4, name: 'Discover Weekly', source_playlist_id: 'tp2' },
    { ...ROW, id: 5, name: 'Deep Focus', custom_name: 'Monday', source_playlist_id: 'tp3' },
  ];

  async function renderLibrary() {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? LIBRARY : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    return screen.getByLabelText('Search playlists') as HTMLInputElement;
  }

  it('narrows the grid as you type', async () => {
    const input = await renderLibrary();
    fireEvent.change(input, { target: { value: 'discover' } });
    expect(screen.getByText('Discover Weekly')).toBeInTheDocument();
    expect(screen.queryByText('Road Trip')).toBeNull();
  });

  it('finds a renamed playlist by the name it was IMPORTED under', async () => {
    // The card shows "Monday"; someone who remembers adding "Deep Focus"
    // should still find it.
    const input = await renderLibrary();
    fireEvent.change(input, { target: { value: 'Deep Focus' } });
    expect(screen.queryByText('Road Trip')).toBeNull();
    expect(document.querySelectorAll('.pl-card')).toHaveLength(1);
  });

  it('says what is being shown, not what the library holds', async () => {
    // "3 playlists" over one visible card is simply wrong.
    const input = await renderLibrary();
    fireEvent.change(input, { target: { value: 'discover' } });
    expect(screen.getByText('1 of 3 playlists')).toBeInTheDocument();
  });

  it('names the query when nothing matches, so you know WHICH filter emptied it', async () => {
    const input = await renderLibrary();
    fireEvent.change(input, { target: { value: 'zzzz' } });
    expect(screen.getByText(/No playlist matches/)).toBeInTheDocument();
    expect(screen.getByText(/zzzz/)).toBeInTheDocument();
  });

  it('Escape clears it — a box you cannot empty is a box you fight', async () => {
    const input = await renderLibrary();
    fireEvent.change(input, { target: { value: 'discover' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input.value).toBe('');
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
  });

  it('the clear button appears only when there is something to clear', async () => {
    const input = await renderLibrary();
    expect(document.querySelector('.library-search-clear')).toBeNull();
    fireEvent.change(input, { target: { value: 'x' } });
    fireEvent.click(document.querySelector('.library-search-clear') as HTMLElement);
    expect(input.value).toBe('');
  });

  it('the source chips do NOT shrink to the search result', async () => {
    // Chips that vanished as you typed would move under the cursor.
    const input = await renderLibrary();
    const before = document.querySelectorAll('.library-source').length;
    fireEvent.change(input, { target: { value: 'zzzz' } });
    expect(document.querySelectorAll('.library-source')).toHaveLength(before);
  });
});

describe('MirroredTab — the Scheduled tab keeps up with the menu', () => {
  /** One automation, pointing at ROW (id 3), in the shape the card map reads. */
  const AUTOMATION = {
    id: 91,
    // The name carries ownership: an automation someone else made for this
    // playlist must not be touched.
    name: 'Auto-Sync: playlist 3',
    enabled: true,
    trigger_type: 'schedule',
    trigger_config: { interval: 24, unit: 'hours' },
    action_type: 'playlist_pipeline',
    action_config: { playlist_id: 3 },
  };

  it('drops a playlist off Scheduled the moment you unschedule it', async () => {
    // Reported: scheduling moved a card out of Unscheduled at once, but
    // unscheduling left it sitting on Scheduled until Update list was pressed.
    let automations: unknown[] = [AUTOMATION];
    stubFetch();
    responder = (url, method) => {
      if (url === '/api/mirrored-playlists') return [ROW];
      if (url.startsWith('/api/automations')) {
        // The DELETE is what the unschedule path issues.
        if (method === 'DELETE') {
          automations = [];
          return { success: true };
        }
        return { automations };
      }
      if (url.startsWith('/api/playlist-pipeline/history')) return { history: [] };
      return { states: [] };
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    // Stand on the Scheduled tab; the card is there because it has a cadence.
    const scheduledTab = [...document.querySelectorAll('.library-tab')].find((t) =>
      t.textContent?.startsWith('Scheduled'),
    ) as HTMLElement;
    expect(scheduledTab, 'no Scheduled tab rendered').toBeTruthy();
    fireEvent.click(scheduledTab);
    await waitFor(() => expect(document.querySelectorAll('.pl-card')).toHaveLength(1));

    // Unschedule through the card's own menu.
    fireEvent.click(document.querySelector('.pl-card-pill') as HTMLElement);
    const notScheduled = [...document.querySelectorAll('.pl-menu-item')].find((b) =>
      b.textContent?.startsWith('Not scheduled'),
    ) as HTMLElement;
    fireEvent.click(notScheduled);

    // It no longer has a cadence, so it no longer belongs on this tab.
    await waitFor(() => expect(document.querySelectorAll('.pl-card')).toHaveLength(0));
  });

  it('picks up a cadence removed SOMEWHERE ELSE when the page reloads it', () => {
    // The reported bug. Scheduling from the card updated the tabs at once,
    // because that write refreshes its own map. Unscheduling from the Bulk
    // schedule modal did not: that modal holds a second copy of the same
    // automations, and nothing told this tab its copy had gone stale. The tab
    // only caught up when Update list was pressed — and even that refetched
    // rows only, never the schedules.
    let automations: unknown[] = [AUTOMATION];
    let reloadFromPage: (() => void) | undefined;
    stubFetch();
    responder = (url) => {
      if (url === '/api/mirrored-playlists') return [ROW];
      if (url.startsWith('/api/automations')) return { automations };
      if (url.startsWith('/api/playlist-pipeline/history')) return { history: [] };
      return { states: [] };
    };
    render(
      <Harness
        registerReload={(fn: () => void) => {
          reloadFromPage = fn;
        }}
      />,
    );

    return waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument()).then(
      async () => {
        const scheduledTab = () =>
          [...document.querySelectorAll('.library-tab')].find((t) =>
            t.textContent?.startsWith('Scheduled'),
          );
        await waitFor(() => expect(scheduledTab()).toBeTruthy());

        // Something outside this tab removes the schedule.
        automations = [];

        // The page reconciles — which is what closing the modal now does.
        reloadFromPage?.();

        // The tab is gone because nothing is scheduled any more.
        await waitFor(() => expect(scheduledTab()).toBeFalsy());
      },
    );
  });
});

describe('MirroredTab — the sort control', () => {
  const MANY = [
    { ...ROW, id: 11, name: 'Zebra', track_count: 5, source_playlist_id: 'a' },
    { ...ROW, id: 12, name: 'apple', track_count: 90, source_playlist_id: 'b' },
    { ...ROW, id: 13, name: 'Mango', track_count: 50, source_playlist_id: 'c' },
    { ...ROW, id: 14, name: 'Kiwi', track_count: 20, source_playlist_id: 'd' },
  ];

  async function renderMany() {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? MANY : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Zebra')).toBeInTheDocument());
  }

  const names = () => [...document.querySelectorAll('.pl-card-name b')].map((n) => n.textContent);

  it('reorders the grid by the name a user sees', async () => {
    await renderMany();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'name' } });
    expect(names()).toEqual(['apple', 'Kiwi', 'Mango', 'Zebra']);
  });

  it('reorders by track count, biggest first', async () => {
    await renderMany();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tracks' } });
    expect(names()).toEqual(['apple', 'Mango', 'Kiwi', 'Zebra']);
  });

  it('is not offered for a library too small to need reordering', async () => {
    stubFetch();
    responder = (url) => (url === '/api/mirrored-playlists' ? [ROW] : { states: [] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});
