/**
 * The two account tabs and the two components they render. The card's ids and
 * classes are the ADOPTED-REGION contract — the vanilla engine finds them by
 * selector — so they are asserted as literals, not derived.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountDetailsModal } from './account-details-modal';
import { AccountPlaylistCard } from './account-playlist-card';
import { DeezerArlTab, SpotifyTab } from './account-tabs';

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
  window.showToast = vi.fn() as typeof window.showToast;
  window.showLoadingOverlay = vi.fn() as typeof window.showLoadingOverlay;
  window.hideLoadingOverlay = vi.fn() as typeof window.hideLoadingOverlay;
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

  it('a click on either BUTTON never toggles selection (1799)', () => {
    const onToggleSelect = vi.fn();
    const onOpenDetails = vi.fn();
    render(
      <AccountPlaylistCard
        cardId="p1"
        row={SPOTIFY_ROW}
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
      />,
    );
    expect(document.querySelector('.playlist-description')).toBeNull();
  });

  it('ARL closes BEFORE handing off; Spotify does not (2639 vs 1948)', () => {
    const arl = { onClose: vi.fn(), onDownloadMissing: vi.fn() };
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

    const spotify = { onClose: vi.fn(), onDownloadMissing: vi.fn() };
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
      />,
    );
    expect(screen.getByText('2 tracks')).toBeInTheDocument();
  });
});

describe('SpotifyTab', () => {
  it('loads, renders cards, and keeps the vanilla container + button ids', async () => {
    responder = () => [SPOTIFY_ROW];
    render(<SpotifyTab />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());
    expect(document.querySelector('#spotify-playlist-container')).not.toBeNull();
    expect(document.querySelector('#spotify-refresh-btn')).not.toBeNull();
    expect(document.querySelector('#progress-p1')).not.toBeNull();
  });

  it('shows the empty copy, not an empty list', async () => {
    responder = () => [];
    render(<SpotifyTab />);
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
    render(<SpotifyTab />);
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
    render(<SpotifyTab />);
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
    window.updateCardToSyncing = updateCardToSyncing as typeof window.updateCardToSyncing;
    window.startSyncPolling = startSyncPolling as typeof window.startSyncPolling;
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
    window.updateCardToSyncing = updateCardToSyncing as typeof window.updateCardToSyncing;
    window.startSyncPolling = vi.fn() as typeof window.startSyncPolling;
    responder = (url) => (url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { status: 'syncing' });
    render(<DeezerArlTab />);
    await waitFor(() => expect(updateCardToSyncing).toHaveBeenCalled());
    expect(updateCardToSyncing).toHaveBeenCalledWith('deezer_arl_7', 0, undefined);
  });

  it('a playlist with no active sync is left alone, not treated as an error', async () => {
    const startSyncPolling = vi.fn();
    window.startSyncPolling = startSyncPolling as typeof window.startSyncPolling;
    responder = (url) => (url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { status: 'idle' });
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    expect(startSyncPolling).not.toHaveBeenCalled();
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('opens its own modal id, off the RAW-id endpoint (2557, 2576)', async () => {
    responder = (url) =>
      url === '/api/deezer/arl-playlists' ? [ARL_ROW] : { name: 'Deep Cuts', tracks: [] };
    render(<DeezerArlTab />);
    await waitFor(() => expect(screen.getByText('Deep Cuts')).toBeInTheDocument());
    fireEvent.click(document.querySelector('#action-btn-deezer_arl_7') as Element);
    await waitFor(() =>
      expect(document.querySelector('#deezer-arl-playlist-details-modal')).not.toBeNull(),
    );
    // The PATH takes the raw id; the ids around it are prefixed.
    expect(calls.some((c) => c.url === '/api/deezer/arl-playlist/7')).toBe(true);
  });
});
