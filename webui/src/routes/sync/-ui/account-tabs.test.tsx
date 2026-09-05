/**
 * The two account tabs and the two components they render. The card's ids and
 * classes are the ADOPTED-REGION contract — the vanilla engine finds them by
 * selector — so they are asserted as literals, not derived.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountDetailsModal } from './account-details-modal';
import { AccountPlaylistCard } from './account-playlist-card';
import { DEEZER_PLAYLIST_PROGRESS_EVENT, DeezerArlTab, SpotifyTab } from './account-tabs';

interface Call {
  url: string;
  method: string;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET' });
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

const SPOTIFY_ROW = { id: 'p1', name: 'Road Trip', track_count: 40, sync_status: 'Needs Sync' };
const ARL_ROW = { id: 7, name: 'Deep Cuts', track_count: 12, sync_status: 'Synced 2 days ago' };

beforeEach(() => {
  stubFetch();
  // vi.stubGlobal, NOT `window.x =` — a direct assignment survives
  // unstubAllGlobals and leaks into every later test file in this worker.
  vi.stubGlobal('showToast', vi.fn());
  vi.stubGlobal('showLoadingOverlay', vi.fn());
  vi.stubGlobal('hideLoadingOverlay', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AccountPlaylistCard — the adopted-region contract', () => {
  it('renders the exact ids and classes the engine paints into (1645-1664)', () => {
    render(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
        glyph="spotify"
        statusClass="status-needs-sync"
        statusLabel="Needs Sync"
        selectable
        selected={false}
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    const card = document.querySelector('.playlist-card');
    expect(card?.getAttribute('data-playlist-id')).toBe('p1');
    // updateCardToSyncing writes this element (downloads.js 4139).
    expect(document.querySelector('#progress-p1')?.className).toBe('sync-progress-indicator');
    // updatePlaylistCardUI writes these two (1679-1721).
    expect(document.querySelector('#action-btn-p1')?.textContent).toBe('Sync / Download');
    const progressBtn = document.querySelector('#progress-btn-p1');
    expect(progressBtn?.className).toBe('view-progress-btn hidden');
    expect(progressBtn?.textContent?.trim()).toBe('View Progress');
    // updateCardToDefault writes the status span's text AND class (4202).
    expect(document.querySelector('.playlist-card-status')?.className).toBe(
      'playlist-card-status status-needs-sync',
    );
    expect(screen.getByText('40 tracks')).toBeInTheDocument();
  });

  it('shows the row cover, and the art tile adds NO id or class the engine selects on', () => {
    render(
      <AccountPlaylistCard
        cardId="p1"
        row={{ ...SPOTIFY_ROW, image_url: '/api/image-cache/cover' }}
        glyph="spotify"
        statusClass="status-needs-sync"
        statusLabel="Needs Sync"
        selectable
        selected={false}
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    const img = document.querySelector('.playlist-card-art img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/api/image-cache/cover?v=card');
    // The whole point of the tile being a sibling: the adopted region is intact.
    expect(document.querySelector('#progress-p1')?.className).toBe('sync-progress-indicator');
    expect(document.querySelector('#action-btn-p1')?.textContent).toBe('Sync / Download');
    expect(document.querySelector('.playlist-card-status')?.className).toBe(
      'playlist-card-status status-needs-sync',
    );
  });

  it('a row with no cover shows the SOURCE MARK, so it stays identifiable', () => {
    render(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
        glyph="spotify"
        statusClass="status-needs-sync"
        statusLabel="Needs Sync"
        selectable
        selected={false}
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    // Real brand SVG (the same sprite the tab strip uses), not an emoji.
    expect(document.querySelector('.playlist-card-art img')).toBeNull();
    expect(document.querySelector('.playlist-card-art .spotify-icon')).not.toBeNull();
  });

  it('a click on either BUTTON never toggles selection (1799)', () => {
    const onToggleSelect = vi.fn();
    const onOpenDetails = vi.fn();
    render(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
        glyph="spotify"
        statusClass="status-synced"
        statusLabel="Synced"
        selectable
        selected={false}
        onToggleSelect={onToggleSelect}
        onOpenDetails={onOpenDetails}
        onViewProgress={vi.fn()}
      />,
    );
    fireEvent.click(document.querySelector('#action-btn-p1') as Element);
    expect(onOpenDetails).toHaveBeenCalled();
    expect(onToggleSelect).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector('.playlist-card-name') as Element);
    expect(onToggleSelect).toHaveBeenCalled();
  });

  it('an ARL card carries the extra class and does NOT toggle at all (2503)', () => {
    const onToggleSelect = vi.fn();
    render(
      <AccountPlaylistCard
        cardId="deezer_arl_7"
        row={ARL_ROW}
        glyph="spotify"
        statusClass="status-synced"
        statusLabel="Synced 2 days ago"
        extraClassName="deezer-arl-playlist-card"
        selectable={false}
        selected={false}
        onToggleSelect={onToggleSelect}
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    const card = document.querySelector('.playlist-card');
    expect(card?.className).toBe('playlist-card deezer-arl-playlist-card');
    fireEvent.click(document.querySelector('.playlist-card-name') as Element);
    expect(onToggleSelect).not.toHaveBeenCalled();
  });

  it('the selected class is rendered from the prop, not from local state', () => {
    const { rerender } = render(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
        glyph="spotify"
        statusClass="status-synced"
        statusLabel="Synced"
        selectable
        selected={false}
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    expect(document.querySelector('.playlist-card')?.className).not.toContain('selected');
    rerender(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
        glyph="spotify"
        statusClass="status-synced"
        statusLabel="Synced"
        selectable
        selected
        onOpenDetails={vi.fn()}
        onViewProgress={vi.fn()}
      />,
    );
    expect(document.querySelector('.playlist-card')?.className).toContain('selected');
  });
});

describe('AccountDetailsModal', () => {
  const detail = {
    name: 'Road Trip',
    owner: 'boulder',
    tracks: [{ id: 't1', name: 'Alright', artists: ['Kendrick'], duration_ms: 219000 }],
  };

  it('renders the header, the hidden sync row and the track list (1895-1934)', () => {
    render(
      <AccountDetailsModal
        modalId="playlist-details-modal"
        playlistId="p1"
        row={SPOTIFY_ROW}
        detail={detail}
        trackCount={40}
        onClose={vi.fn()}
        closeBeforeDownload={false}
        onDownloadMissing={vi.fn()}
        onSync={vi.fn()}
      />,
    );
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
    expect(screen.getByText('by boulder')).toBeInTheDocument();
    // The sync engine unhides and fills this block; React renders it hidden.
    const syncRow = document.querySelector('#modal-sync-status-p1') as HTMLElement;
    expect(syncRow.style.display).toBe('none');
    expect(document.querySelector('#modal-total-p1')?.textContent).toBe('0');
    expect(document.querySelector('#modal-percentage-p1')?.textContent).toBe('0');
    expect(screen.getByText('Alright')).toBeInTheDocument();
    expect(screen.getByText('3:39')).toBeInTheDocument();
    expect(screen.getByText('40 tracks')).toBeInTheDocument();
  });

  it('omits the description block entirely when there is none (1918)', () => {
    render(
      <AccountDetailsModal
        modalId="playlist-details-modal"
        playlistId="p1"
        row={{ id: 'p1' }}
        detail={{ tracks: [] }}
        trackCount={0}
        onClose={vi.fn()}
        closeBeforeDownload={false}
        onDownloadMissing={vi.fn()}
        onSync={vi.fn()}
      />,
    );
    expect(document.querySelector('.playlist-description')).toBeNull();
  });

  it('ARL closes BEFORE handing off; Spotify does not (2639 vs 1948)', () => {
    const arl = { onClose: vi.fn(), onDownloadMissing: vi.fn(), onSync: vi.fn() };
    const { unmount } = render(
      <AccountDetailsModal
        modalId="deezer-arl-playlist-details-modal"
        playlistId="deezer_arl_7"
        row={ARL_ROW}
        detail={detail}
        trackCount={1}
        closeBeforeDownload
        {...arl}
      />,
    );
    fireEvent.click(screen.getByText('📥 Download Missing Tracks'));
    expect(arl.onClose).toHaveBeenCalled();
    expect(arl.onDownloadMissing).toHaveBeenCalled();
    unmount();

    const spotify = { onClose: vi.fn(), onDownloadMissing: vi.fn(), onSync: vi.fn() };
    render(
      <AccountDetailsModal
        modalId="playlist-details-modal"
        playlistId="p1"
        row={SPOTIFY_ROW}
        detail={detail}
        trackCount={40}
        closeBeforeDownload={false}
        {...spotify}
      />,
    );
    fireEvent.click(screen.getByText('📥 Download Missing Tracks'));
    expect(spotify.onClose).not.toHaveBeenCalled();
    expect(spotify.onDownloadMissing).toHaveBeenCalled();
  });

  it('the restored sync footer: mode select with the engine-read id + Sync button (Boulder: lost in port)', () => {
    const onSync = vi.fn();
    render(
      <AccountDetailsModal
        modalId="playlist-details-modal"
        playlistId="p1"
        row={SPOTIFY_ROW}
        detail={detail}
        trackCount={40}
        onClose={vi.fn()}
        closeBeforeDownload={false}
        onDownloadMissing={vi.fn()}
        onSync={onSync}
      />,
    );
    // startPlaylistSync reads THIS id for the mode (downloads.js:3848).
    const select = document.querySelector('#sync-mode-p1') as HTMLSelectElement;
    expect(select).not.toBeNull();
    expect(select.value).toBe('');
    // The engine mutates THIS id while syncing (adopted, downloads.js:3873).
    const btn = document.querySelector('#sync-btn-p1') as HTMLButtonElement;
    expect(btn.textContent).toBe('Sync Playlist');
    fireEvent.click(btn);
    expect(onSync).toHaveBeenCalledTimes(1);
  });
});

describe('seeding — the engine must be able to FIND the playlist (2235-2240)', () => {
  it('Spotify seeds every loaded row, the way 1612 assigns the whole array', async () => {
    const seeded: unknown[] = [];
    vi.stubGlobal('registerSyncAccountPlaylist', (row: unknown) => {
      seeded.push(row);
    });
    // The second row's id is a NUMBER — the engine matches with === against
    // the string the card rendered (2235), so seeding must coerce.
    responder = () => [SPOTIFY_ROW, { id: 99, name: 'Second', track_count: 3 }];
    render(<SpotifyTab registerRows={() => {}} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(seeded).toHaveLength(2);
    expect(seeded[0]).toMatchObject({ id: 'p1', name: 'Road Trip' });
    expect(seeded[1]).toMatchObject({ id: '99', name: 'Second' });
    expect(typeof (seeded[1] as { id: unknown }).id).toBe('string');
  });

  it('ARL seeds at hand-off, with the shim shape and the FETCHED count (2646)', async () => {
    const seeded: unknown[] = [];
    vi.stubGlobal('registerSyncAccountPlaylist', (row: unknown) => {
      seeded.push(row);
    });
    const openDownloadMissingModal = vi.fn();
    vi.stubGlobal('openDownloadMissingModal', openDownloadMissingModal);
    responder = (url) =>
      url === '/api/deezer/arl-playlists'
        ? [ARL_ROW]
        : url === '/api/deezer/arl-playlist/7?async=1'
          ? { pending: true, job_id: 'arl-job-7' }
          : url === '/api/deezer/playlist-load/arl-job-7'
            ? {
                status: 'complete',
                playlist: { name: 'Deep Cuts', tracks: [{ id: 'a' }, { id: 'b' }] },
              }
            : {};
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    // Nothing is seeded just by loading — the vanilla shims at modal time.
    expect(seeded).toHaveLength(0);

    fireEvent.click(document.querySelector('#action-btn-deezer_arl_7') as Element);
    await waitFor(() =>
      expect(document.querySelector('#deezer-arl-playlist-details-modal')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('📥 Download Missing Tracks'));

    expect(seeded).toEqual([
      {
        id: 'deezer_arl_7',
        name: 'Deep Cuts',
        track_count: 2,
        image_url: '',
        owner: '',
      },
    ]);
    // Seeded BEFORE the engine is asked to open it.
    expect(openDownloadMissingModal).toHaveBeenCalledWith('deezer_arl_7');
  });
});

describe('the header count is per-tab drift (1901 vs 2592)', () => {
  const zeroCount = { id: 9, track_count: 0 };
  const withTracks = { tracks: [{ id: 'a' }, { id: 'b' }] };

  it('Spotify prints a zero count as zero', () => {
    render(
      <AccountDetailsModal
        modalId="playlist-details-modal"
        playlistId="9"
        row={zeroCount}
        detail={withTracks}
        trackCount={zeroCount.track_count ?? 0}
        onClose={vi.fn()}
        closeBeforeDownload={false}
        onDownloadMissing={vi.fn()}
        onSync={vi.fn()}
      />,
    );
    expect(screen.getByText('0 tracks')).toBeInTheDocument();
  });

  it('ARL falls through a zero count to the fetched track list', () => {
    render(
      <AccountDetailsModal
        modalId="deezer-arl-playlist-details-modal"
        playlistId="deezer_arl_9"
        row={zeroCount}
        detail={withTracks}
        trackCount={(zeroCount.track_count ?? 0) || (withTracks.tracks.length ?? 0)}
        onClose={vi.fn()}
        closeBeforeDownload
        onDownloadMissing={vi.fn()}
        onSync={vi.fn()}
      />,
    );
    expect(screen.getByText('2 tracks')).toBeInTheDocument();
  });
});

describe('SpotifyTab', () => {
  it('registers the RENDERED ids, in display order', async () => {
    // This is the sequential sync's queue order. It must be what is on screen:
    // the engine's own spotifyPlaylists is never pruned and also holds virtual
    // playlists, so queueing from it could sync a card that is gone.
    const seen: string[][] = [];
    responder = () => [SPOTIFY_ROW, { id: 99, name: 'Second', track_count: 3 }];
    render(<SpotifyTab registerRows={(ids) => seen.push(ids)} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    // Ids are coerced to strings, matching what the selection store holds.
    expect(seen.at(-1)).toEqual(['p1', '99']);
  });

  it('registers an EMPTY list before anything has loaded', async () => {
    // Otherwise the page would hold a stale order from a previous mount and
    // Start Sync could queue against playlists no longer listed.
    const seen: string[][] = [];
    responder = () => [];
    render(<SpotifyTab registerRows={(ids) => seen.push(ids)} />);
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0]).toEqual([]);
  });

  it('does NOT re-register when the caller passes a fresh function each render', async () => {
    // registerRows is a prop; an inline arrow is a new function every render.
    // Held in a ref so only `rows` decides — the same trap useAutoSync's `now`
    // fell into.
    let calls = 0;
    responder = () => [SPOTIFY_ROW];
    function Wrapper({ tick }: { tick: number }) {
      return (
        <div>
          <span data-testid="tick">{tick}</span>
          <SpotifyTab
            registerRows={() => {
              calls += 1;
            }}
          />
        </div>
      );
    }
    const { rerender } = render(<Wrapper tick={0} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    const after = calls;
    rerender(<Wrapper tick={1} />);
    rerender(<Wrapper tick={2} />);
    expect(calls).toBe(after);
  });

  it('loads, renders cards, and keeps the vanilla container + button ids', async () => {
    responder = () => [SPOTIFY_ROW];
    render(<SpotifyTab registerRows={() => {}} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(document.querySelector('#spotify-playlist-container')).not.toBeNull();
    expect(document.querySelector('#spotify-refresh-btn')).not.toBeNull();
    expect(document.querySelector('#progress-p1')).not.toBeNull();
  });

  it('shows the empty copy, not an empty list', async () => {
    responder = () => [];
    render(<SpotifyTab registerRows={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText('No Spotify playlists found.')).toBeInTheDocument(),
    );
  });

  it('an error paints the container AND toasts, then re-enables refresh (1624-1629)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    render(<SpotifyTab registerRows={() => {}} />);
    await waitFor(() => expect(screen.getByText('❌ Error: network down')).toBeInTheDocument());
    expect(window.showToast).toHaveBeenCalledWith('Error loading playlists: network down', 'error');
    // The finally arm — a failed load must not leave the button stuck.
    expect((document.querySelector('#spotify-refresh-btn') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('opening a card fetches its tracks and shows the details modal', async () => {
    responder = (url) =>
      url === '/api/spotify/playlists' ? [SPOTIFY_ROW] : { name: 'Road Trip', tracks: [] };
    render(<SpotifyTab registerRows={() => {}} />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    fireEvent.click(document.querySelector('#action-btn-p1') as Element);
    await waitFor(() => expect(document.querySelector('#playlist-details-modal')).not.toBeNull());
    expect(calls.some((c) => c.url === '/api/spotify/playlist/p1')).toBe(true);
  });
});

describe('DeezerArlTab', () => {
  it('prefixes every id and adds the extra card class', async () => {
    responder = (url) => (url === '/api/deezer/arl-playlists' ? [ARL_ROW] : {});
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    expect(document.querySelector('#deezer-arl-playlist-container')).not.toBeNull();
    expect(document.querySelector('#deezer-arl-refresh-btn')).not.toBeNull();
    expect(document.querySelector('#progress-deezer_arl_7')).not.toBeNull();
    expect(document.querySelector('.deezer-arl-playlist-card')).not.toBeNull();
  });

  it('rehydrates an in-flight sync through the engine (2462-2479)', async () => {
    const updateCardToSyncing = vi.fn();
    const startSyncPolling = vi.fn();
    vi.stubGlobal('updateCardToSyncing', updateCardToSyncing);
    vi.stubGlobal('startSyncPolling', startSyncPolling);
    responder = (url) =>
      url === '/api/deezer/arl-playlists'
        ? [ARL_ROW]
        : { status: 'syncing', progress: { progress: 42 } };
    render(<DeezerArlTab />);
    await waitFor(() => expect(updateCardToSyncing).toHaveBeenCalled());
    expect(calls.some((c) => c.url === '/api/sync/status/deezer_arl_7')).toBe(true);
    expect(updateCardToSyncing).toHaveBeenCalledWith('deezer_arl_7', 42, { progress: 42 });
    expect(startSyncPolling).toHaveBeenCalledWith('deezer_arl_7');
  });

  it('a syncing row with no progress payload starts at 0, not at 100 (2473)', async () => {
    const updateCardToSyncing = vi.fn();
    vi.stubGlobal('updateCardToSyncing', updateCardToSyncing);
    vi.stubGlobal('startSyncPolling', vi.fn());
    responder = (url) => (url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { status: 'syncing' });
    render(<DeezerArlTab />);
    await waitFor(() => expect(updateCardToSyncing).toHaveBeenCalled());
    expect(updateCardToSyncing).toHaveBeenCalledWith('deezer_arl_7', 0, undefined);
  });

  it('a playlist with no active sync is left alone, not treated as an error', async () => {
    const startSyncPolling = vi.fn();
    vi.stubGlobal('startSyncPolling', startSyncPolling);
    responder = (url) => (url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { status: 'idle' });
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    expect(startSyncPolling).not.toHaveBeenCalled();
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('opens its own modal id, off the RAW-id endpoint (2557, 2576)', async () => {
    responder = (url) =>
      url === '/api/deezer/arl-playlists'
        ? [ARL_ROW]
        : url === '/api/deezer/arl-playlist/7?async=1'
          ? { pending: true, job_id: 'arl-job-7' }
          : url === '/api/deezer/playlist-load/arl-job-7'
            ? { status: 'complete', playlist: { name: 'Deep Cuts', tracks: [] } }
            : {};
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    fireEvent.click(document.querySelector('#action-btn-deezer_arl_7') as Element);
    await waitFor(() =>
      expect(document.querySelector('#deezer-arl-playlist-details-modal')).not.toBeNull(),
    );
    // The PATH takes the raw id; the ids around it are prefixed.
    expect(calls.some((c) => c.url === '/api/deezer/arl-playlist/7?async=1')).toBe(true);
  });

  it('narrates the resolve-albums progress and ignores a frame for another playlist', async () => {
    responder = (url) =>
      url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { name: 'Deep Cuts', tracks: [] };
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    fireEvent.click(document.querySelector('#action-btn-deezer_arl_7') as Element);
    // Fired synchronously — the listener is attached before the details fetch
    // is awaited, so both dispatches land while the modal is still loading.
    fireEvent(
      window,
      new CustomEvent(DEEZER_PLAYLIST_PROGRESS_EVENT, {
        detail: { playlist_id: '99', done: 1, total: 2, phase: 'release dates' },
      }),
    );
    fireEvent(
      window,
      new CustomEvent(DEEZER_PLAYLIST_PROGRESS_EVENT, {
        detail: { playlist_id: '7', done: 3, total: 10, phase: 'track numbers' },
      }),
    );
    expect(window.showLoadingOverlay).not.toHaveBeenCalledWith(expect.stringContaining('99'));
    expect(window.showLoadingOverlay).toHaveBeenCalledWith(
      expect.stringContaining('track numbers 3/10 (30%)'),
    );
    await waitFor(() =>
      expect(document.querySelector('#deezer-arl-playlist-details-modal')).not.toBeNull(),
    );
  });
});
