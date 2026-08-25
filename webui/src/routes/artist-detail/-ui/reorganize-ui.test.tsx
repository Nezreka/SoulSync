import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { _resetReorganizePolling } from '../-artist-detail.reorganize';
import { ArtistMetaPanel } from './artist-meta-panel';
import { ReorganizeAllModal, ReorganizeModal } from './reorganize-modal';
import { ReorganizeStatusPanel } from './reorganize-status-panel';

/**
 * The reorganize UI + the artist meta panel. Fetch is stubbed per test (the
 * status panel polls /api/library/reorganize/queue the moment it mounts, so
 * every meta-panel test needs a queue-shaped default).
 */

type Handler = (url: string, body: Record<string, unknown> | null) => unknown;

function stubApi(handler: Handler) {
  const calls: { url: string; body: Record<string, unknown> | null }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, body });
      return new Response(JSON.stringify(handler(url, body) ?? {}));
    }),
  );
  return calls;
}

const ALBUM: EnhancedAlbum = {
  id: 7,
  title: 'SAW 85-92',
  tracks: [{ id: 1 }, { id: 2 }],
} as EnhancedAlbum;

afterEach(() => {
  _resetReorganizePolling();
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
  cleanup();
});

describe('ReorganizeModal', () => {
  it('opens with nothing to configure and fetches nothing', async () => {
    const calls = stubApi(() => ({}));
    render(<ReorganizeModal album={ALBUM} onClose={vi.fn()} />);
    expect(document.getElementById('reorganize-modal-title')?.textContent).toBe(
      'Reorganize: SAW 85-92',
    );
    // No source list to load: the plan comes from the library's own rows.
    expect(calls).toEqual([]);
    expect(document.getElementById('reorganize-mode-select')).toBeNull();
    expect(document.getElementById('reorganize-source-select')).toBeNull();
    expect(document.getElementById('reorganize-action-select')).toBeNull();
  });

  it('preview gates Apply: enabled for movable tracks, vetoed by a collision', async () => {
    let tracks: unknown[] = [
      {
        title: 'Xtal',
        file_exists: true,
        new_path: '/new/01 Xtal.flac',
        current_path: '/old.flac',
      },
    ];
    stubApi((url) =>
      url.endsWith('/reorganize/preview') ? { success: true, tracks } : { sources: [] },
    );
    render(<ReorganizeModal album={ALBUM} onClose={vi.fn()} />);
    const apply = () => document.getElementById('reorganize-apply-btn') as HTMLButtonElement;
    expect(apply().disabled).toBe(true);

    fireEvent.click(screen.getByText('Generate Preview'));
    await screen.findByText('1 will move');
    expect(apply().disabled).toBe(false);
    expect(document.querySelector('.reorganize-row-changed')).toBeTruthy();

    tracks = [
      {
        title: 'Xtal',
        file_exists: true,
        new_path: '/n.flac',
        collision: true,
        current_path: '/o',
      },
    ];
    fireEvent.click(screen.getByText('Generate Preview'));
    await screen.findByText('1 collision — likely a source data issue');
    expect(apply().disabled).toBe(true);
  });

  it('applying queues the album, toasts with the position, and closes', async () => {
    const calls = stubApi((url) =>
      url.endsWith('/reorganize')
        ? { success: true, queued: true, position: 2 }
        : url.endsWith('/reorganize/preview')
          ? { success: true, tracks: [{ file_exists: true, new_path: '/n', current_path: '/o' }] }
          : { sources: [] },
    );
    window.showToast = vi.fn() as never;
    const onClose = vi.fn();
    render(<ReorganizeModal album={ALBUM} onClose={onClose} />);
    fireEvent.click(screen.getByText('Generate Preview'));
    await waitFor(() =>
      expect((document.getElementById('reorganize-apply-btn') as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(document.getElementById('reorganize-apply-btn') as HTMLElement);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(window.showToast).toHaveBeenCalledWith('Queued: SAW 85-92 (#2 in queue)', 'info');
    const queue = calls.find((c) => c.url === '/api/library/album/7/reorganize');
    expect(queue?.body).toBeNull(); // nothing to send
  });
});

describe('ReorganizeAllModal', () => {
  it('lists the albums, confirms, queues them all and toasts the combo', async () => {
    const calls = stubApi((url) =>
      url.endsWith('/reorganize-all') ? { success: true, enqueued: 2 } : {},
    );
    window.showToast = vi.fn() as never;
    window.showConfirmDialog = vi.fn(async () => true) as never;
    const onClose = vi.fn();
    render(
      <ReorganizeAllModal
        albums={[ALBUM, { id: 8, title: 'Druqks', tracks: [{ id: 3 }] } as EnhancedAlbum]}
        artistId={42}
        artistName="Aphex Twin"
        onClose={onClose}
      />,
    );
    expect(document.getElementById('reorganize-modal-title')?.textContent).toBe(
      'Reorganize All Albums — Aphex Twin',
    );
    expect(screen.getByText('2 albums will be reorganized:')).toBeTruthy();

    fireEvent.click(screen.getByText('Reorganize All'));
    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(
      String((window.showConfirmDialog as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]?.message),
    ).toContain('This will queue 2 albums for Aphex Twin');
    expect(window.showToast).toHaveBeenCalledWith('Queued 2 albums for Aphex Twin', 'info');
    expect(onClose).toHaveBeenCalled();
    expect(calls.some((c) => c.url === '/api/library/artist/42/reorganize-all')).toBe(true);
  });

  it('a declined confirm queues nothing', async () => {
    const calls = stubApi(() => ({ sources: [] }));
    window.showConfirmDialog = vi.fn(async () => false) as never;
    render(
      <ReorganizeAllModal
        albums={[ALBUM]}
        artistId={42}
        artistName="Aphex Twin"
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('Reorganize All'));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.includes('reorganize-all'))).toBe(false);
  });
});

describe('ReorganizeStatusPanel', () => {
  it('renders nothing while the queue is idle', async () => {
    stubApi(() => ({ active: null, queued: [], recent: [] }));
    render(<ReorganizeStatusPanel artistId={42} onReload={vi.fn()} />);
    await waitFor(() => expect(document.getElementById('reorganize-status-panel')).toBeNull());
  });

  it('shows the active row, expands to the queue, and cancels an item', async () => {
    const calls = stubApi((url) =>
      url.includes('/cancel')
        ? { cancelled: true }
        : {
            active: {
              album_id: 7,
              queue_id: 'q1',
              artist_id: 42,
              album_title: 'SAW 85-92',
              progress_total: 4,
              progress_processed: 1,
              current_track: 'Xtal',
              moved: 1,
            },
            queued: [
              {
                album_id: 8,
                queue_id: 'q2',
                artist_id: 42,
                album_title: 'Druqks',
                source: 'spotify',
              },
            ],
            recent: [],
          },
    );
    window.showToast = vi.fn() as never;
    render(<ReorganizeStatusPanel artistId={42} onReload={vi.fn()} />);
    await screen.findByText('SAW 85-92');
    expect(document.querySelector('.reorg-panel-active-text')?.textContent).toBe(
      'Reorganizing SAW 85-92 (1/4 · 25%) — Xtal',
    );
    expect(document.querySelector('.reorg-panel-queue-badge')?.textContent).toBe('+1 queued');

    fireEvent.click(document.querySelector('.reorg-panel-compact') as HTMLElement);
    expect(screen.getByText('Queued (1)')).toBeTruthy();
    expect(screen.getByText('via spotify')).toBeTruthy();

    fireEvent.click(document.querySelector('.reorg-panel-cancel-btn') as HTMLElement);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Cancelled queued item', 'info'),
    );
    expect(calls.some((c) => c.url === '/api/library/reorganize/queue/q2/cancel')).toBe(true);
  });

  it('marks the per-album Reorganize buttons queued/running', async () => {
    stubApi(() => ({
      active: { album_id: 7, queue_id: 'q1' },
      queued: [{ album_id: 8, queue_id: 'q2' }],
      recent: [],
    }));
    const buttons = document.createElement('div');
    buttons.innerHTML =
      '<button class="enhanced-reorganize-album-btn" data-album-id="7"></button>' +
      '<button class="enhanced-reorganize-album-btn" data-album-id="8"></button>';
    document.body.appendChild(buttons);
    try {
      render(<ReorganizeStatusPanel artistId={42} onReload={vi.fn()} />);
      await waitFor(() => {
        expect(
          buttons.querySelector('[data-album-id="7"]')?.classList.contains('reorg-state-running'),
        ).toBe(true);
        expect(
          buttons.querySelector('[data-album-id="8"]')?.classList.contains('reorg-state-queued'),
        ).toBe(true);
      });
    } finally {
      buttons.remove();
    }
  });
});

describe('ArtistMetaPanel', () => {
  const ARTIST = {
    id: 42,
    name: 'Aphex Twin',
    thumb_url: 'artist.jpg',
    spotify_artist_id: 'sp42',
    spotify_match_status: 'matched',
    genres: ['idm'],
  } as never;

  function renderPanel(isAdmin = true, handler?: Handler) {
    const calls = stubApi(
      handler ??
        ((url) =>
          url.includes('reorganize/queue') ? { active: null, queued: [], recent: [] } : {}),
    );
    const onReload = vi.fn();
    const onArtistPatched = vi.fn();
    render(
      <ArtistMetaPanel
        artist={ARTIST}
        albums={[ALBUM]}
        isAdmin={isAdmin}
        onReload={onReload}
        onArtistPatched={onArtistPatched}
      />,
    );
    return { calls, onReload, onArtistPatched };
  }

  it('renders the name, id badges and the 11-service match chip row', () => {
    renderPanel();
    expect(document.querySelector('.enhanced-artist-meta-name')?.textContent).toBe('Aphex Twin');
    const badge = document.querySelector('.enhanced-id-badge') as HTMLAnchorElement;
    expect(badge.textContent).toBe('Spotify');
    expect(badge.getAttribute('href')).toBe('https://open.spotify.com/artist/sp42');
    // 12 services minus the JioSaavn filter; amazon included, unlike enrich.
    const chips = [...document.querySelectorAll('.enhanced-match-chip')];
    expect(chips).toHaveLength(11);
    expect(chips[0].textContent).toBe('Spotify: matched');
    expect(chips.at(-1)?.textContent).toBe('Amazon: pending');
  });

  it('a chip opens the manual matcher seeded with the artist name', async () => {
    renderPanel(true, (url) =>
      url.includes('reorganize/queue')
        ? { active: null, queued: [], recent: [] }
        : { success: true, results: [] },
    );
    fireEvent.click(document.querySelectorAll('.enhanced-match-chip')[1]);
    expect(screen.getByText('Match artist on MusicBrainz')).toBeTruthy();
    await waitFor(() =>
      expect(
        (document.querySelector('.enhanced-match-search-input') as HTMLInputElement).value,
      ).toBe('Aphex Twin'),
    );
  });

  it('hides the editor + enrich for non-admins but keeps Sync and Reorganize All', () => {
    renderPanel(false);
    expect(document.querySelector('.enhanced-meta-edit-toggle')).toBeNull();
    expect(document.querySelector('.enhanced-enrich-wrap')).toBeNull();
    expect(screen.getByText('🔄 Sync')).toBeTruthy();
    expect(screen.getByText('📁 Reorganize All')).toBeTruthy();
  });

  it('Sync toasts the summary and reloads only when something changed', async () => {
    window.showToast = vi.fn() as never;
    const { onReload } = renderPanel(true, (url) =>
      url.endsWith('/sync')
        ? { success: true, artist_name: 'Aphex Twin', new_albums: 1, new_tracks: 5 }
        : { active: null, queued: [], recent: [] },
    );
    fireEvent.click(screen.getByText('🔄 Sync'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Aphex Twin: +1 albums, +5 tracks', 'success'),
    );
    expect(onReload).toHaveBeenCalled();
  });

  it('Sync with nothing to do reports "Already in sync" and does NOT reload', async () => {
    window.showToast = vi.fn() as never;
    const { onReload } = renderPanel(true, (url) =>
      url.endsWith('/sync')
        ? { success: true, artist_name: 'Aphex Twin' }
        : { active: null, queued: [], recent: [] },
    );
    fireEvent.click(screen.getByText('🔄 Sync'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Aphex Twin: Already in sync', 'success'),
    );
    expect(onReload).not.toHaveBeenCalled();
  });

  it('the edit form diffs against the record and PUTs only the changes', async () => {
    window.showToast = vi.fn() as never;
    const { calls, onArtistPatched } = renderPanel(true, (url) =>
      url.includes('reorganize/queue')
        ? { active: null, queued: [], recent: [] }
        : { success: true, updated_fields: ['label'] },
    );
    fireEvent.click(screen.getByText('Edit Metadata'));
    const label = [...document.querySelectorAll('.enhanced-meta-field-input')].find(
      (el) => (el as HTMLElement).dataset.field === 'label',
    ) as HTMLInputElement;
    fireEvent.change(label, { target: { value: 'Warp' } });
    fireEvent.click(screen.getByText('Save Changes'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Artist metadata saved (label)', 'success'),
    );
    const put = calls.find((c) => c.url === '/api/library/artist/42');
    expect(put?.body).toEqual({ label: 'Warp' });
    expect(onArtistPatched).toHaveBeenCalled();
  });

  it('saving with no edits refuses without a request', async () => {
    window.showToast = vi.fn() as never;
    const { calls } = renderPanel();
    fireEvent.click(screen.getByText('Edit Metadata'));
    fireEvent.click(screen.getByText('Save Changes'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('No changes to save', 'error'),
    );
    expect(calls.some((c) => c.url === '/api/library/artist/42')).toBe(false);
  });

  it("Reorganize All opens the bulk modal over this artist's albums", () => {
    renderPanel();
    fireEvent.click(screen.getByText('📁 Reorganize All'));
    expect(document.getElementById('reorganize-modal-title')?.textContent).toBe(
      'Reorganize All Albums — Aphex Twin',
    );
  });
});
