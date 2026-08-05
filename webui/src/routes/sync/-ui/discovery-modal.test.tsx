/**
 * The shared discovery modal — rendered across the phase machine with real
 * configs, pinning the day-one fixes as regression tests: Deezer sync_complete
 * gets a real Sync button (bug #1), track names render as TEXT (the XSS fix),
 * headers use the live metadata label (bug #5), the vanilla's gates
 * (results-presence, counter-only matches, converted-id download fallback) and
 * the engine argument shapes for the wing-it actions.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SourcePlaylistState } from '../-sync.state';
import type { DiscoveryRow } from '../-sync.transform';

import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { DiscoveryModal, pendingSourceRows, wingItSourceWord } from './discovery-modal';

const FOUND_ROW: DiscoveryRow = {
  index: 0,
  yt_track: 'A',
  yt_artist: 'B',
  status: '✅ Found',
  status_class: 'found',
  spotify_track: 'A2',
  spotify_artist: 'C',
  spotify_album: 'Alb',
};

function makeState(
  configId: keyof typeof SYNC_SOURCES,
  overrides: Partial<SourcePlaylistState>,
): SourcePlaylistState {
  return {
    ...freshSourceState(SYNC_SOURCES[configId], overrides.sourceId ?? '1'),
    playlist: { name: 'My List', tracks: [{}, {}] },
    ...overrides,
  };
}

const noopHandlers = {
  onClose: () => {},
  onStartDiscovery: () => {},
  onStartSync: () => {},
  onCancelSync: () => {},
  onDownloadMissing: () => {},
};

afterEach(() => {
  vi.restoreAllMocks();
  delete (window as { wingItDownload?: unknown }).wingItDownload;
  delete (window as { _wingItSyncFromModal?: unknown })._wingItSyncFromModal;
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { getActiveMetadataSource?: unknown }).getActiveMetadataSource;
  delete (window as { getMetadataSourceLabel?: unknown }).getMetadataSourceLabel;
});

describe('phase footers', () => {
  it('fresh: Start Discovery + Wing It; the start button fires the action', () => {
    const onStartDiscovery = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', {})}
        standalone={false}
        {...noopHandlers}
        onStartDiscovery={onStartDiscovery}
      />,
    );
    fireEvent.click(screen.getByText('🔍 Start Discovery'));
    expect(onStartDiscovery).toHaveBeenCalledOnce();
    expect(screen.getByText('⚡ Wing It')).toBeInTheDocument();
    expect(screen.getByText('Click Start Discovery to begin...')).toBeInTheDocument();
  });

  it('discovering: the vanilla info line, no action buttons', () => {
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', { phase: 'discovering' })}
        standalone={false}
        {...noopHandlers}
      />,
    );
    expect(screen.getByText('🔍 Discovering Spotify matches...')).toBeInTheDocument();
    expect(screen.queryByText('🔍 Download Missing Tracks')).not.toBeInTheDocument();
  });

  it('DEEZER sync_complete renders a real Sync button (live bug #1 regression)', () => {
    const onStartSync = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.deezer}
        state={makeState('deezer', {
          phase: 'sync_complete',
          spotifyMatches: 2,
          rows: [FOUND_ROW],
        })}
        standalone={false}
        {...noopHandlers}
        onStartSync={onStartSync}
      />,
    );
    fireEvent.click(screen.getByText('🔄 Sync This Playlist'));
    expect(onStartSync).toHaveBeenCalledOnce();
  });

  it('the discovered group needs RESULTS: without rows it warns instead (9628)', () => {
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', { phase: 'discovered', spotifyMatches: 3 })}
        standalone={false}
        {...noopHandlers}
      />,
    );
    expect(
      screen.getByText('⚠️ No discovery results available. Try starting discovery again.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('🔄 Sync This Playlist')).not.toBeInTheDocument();
  });

  it('matches are COUNTER-only, and a converted id keeps Download available (9604-9605)', () => {
    // Found rows but counter 0 → no sync button; converted id → Download stays.
    // (No info line: the vanilla's "No matches" prepend is unreachable dead
    // code — its startsWith guard is defeated by the wing-it wrap.)
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', {
          phase: 'discovered',
          spotifyMatches: 0,
          rows: [FOUND_ROW],
          convertedSpotifyPlaylistId: 'tidal_1',
        })}
        standalone={false}
        {...noopHandlers}
      />,
    );
    expect(screen.queryByText('🔄 Sync This Playlist')).not.toBeInTheDocument();
    expect(screen.getByText('🔍 Download Missing Tracks')).toBeInTheDocument();
    expect(screen.queryByText(/No .* matches found/)).not.toBeInTheDocument();
  });

  it('standalone hides sync everywhere and gives spotify_public the folder download', () => {
    const onDownloadMissing = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.spotify_public}
        state={makeState('spotify_public', {
          sourceId: 'h4sh',
          phase: 'discovered',
          spotifyMatches: 1,
          rows: [FOUND_ROW],
        })}
        standalone={true}
        {...noopHandlers}
        onDownloadMissing={onDownloadMissing}
      />,
    );
    expect(screen.queryByText('🔄 Sync This Playlist')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('📁 Download to Playlist Folder'));
    expect(onDownloadMissing).toHaveBeenCalledWith({ forcePlaylistFolder: true });
  });

  it('syncing: cancel first, then the ♪/✓/✗ status row with the config formula', () => {
    const onCancelSync = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.beatport}
        state={makeState('beatport', {
          sourceId: 'ch4rt',
          phase: 'syncing',
          lastSyncProgress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
        })}
        standalone={false}
        {...noopHandlers}
        onCancelSync={onCancelSync}
      />,
    );
    expect(screen.getByText('♪ 10')).toBeInTheDocument();
    expect(screen.getByText('✓ 4')).toBeInTheDocument();
    expect(screen.getByText('✗ 1')).toBeInTheDocument();
    // beatport = matched/total → 40%, NOT the processed 50%.
    expect(screen.getByText('(40%)')).toBeInTheDocument();
    fireEvent.click(screen.getByText('❌ Cancel Sync'));
    expect(onCancelSync).toHaveBeenCalledOnce();
  });

  it('rediscover only for reset-capable sources; Retry Failed counts every NOT-found row (9684)', () => {
    const { unmount } = render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', { phase: 'discovered', spotifyMatches: 1, rows: [FOUND_ROW] })}
        standalone={false}
        {...noopHandlers}
        onRediscover={() => {}}
        onRetryFailed={() => {}}
      />,
    );
    expect(screen.queryByText('🔄 Rediscover')).not.toBeInTheDocument(); // tidal has no reset
    unmount();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.mirrored}
        state={makeState('mirrored', {
          sourceId: 'mirrored_5',
          phase: 'discovered',
          spotifyMatches: 1,
          rows: [
            FOUND_ROW,
            { ...FOUND_ROW, index: 1, status_class: 'not-found' },
            { ...FOUND_ROW, index: 2, status_class: 'wing-it' }, // counted too — != 'found'
          ],
        })}
        standalone={false}
        {...noopHandlers}
        onRediscover={() => {}}
        onRetryFailed={() => {}}
      />,
    );
    expect(screen.getByText('🔄 Rediscover')).toBeInTheDocument();
    expect(screen.getByText('🔄 Retry Failed (2)')).toBeInTheDocument();
  });
});

describe('the table', () => {
  it('renders rows as TEXT (the XSS fix) with the vanilla cell classes and the #863 fallback', () => {
    const onFixTrack = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', {
          sourceId: '77',
          phase: 'discovered',
          spotifyMatches: 1,
          rows: [
            {
              index: 0,
              yt_track: '<img src=x onerror=alert(1)>',
              yt_artist: 'Unknown Artist',
              status: '✅ Found',
              status_class: 'found',
              spotify_track: 'Clean',
              spotify_artist: 'Real Artist',
              spotify_album: 'Alb',
            },
            {
              index: 1,
              yt_track: 'B',
              yt_artist: 'C',
              status: '❌ Not Found',
              status_class: 'not-found',
              spotify_track: '-',
              spotify_artist: '-',
              spotify_album: '-',
            },
          ],
        })}
        standalone={false}
        {...noopHandlers}
        onFixTrack={onFixTrack}
        onUnmatchTrack={() => {}}
      />,
    );
    // The payload renders as literal text — no element injected.
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img')).toBe(null);
    // The vanilla cell classes wishlist-tools repaints and style.css styles.
    expect(document.querySelector('td.yt-track')).not.toBe(null);
    expect(document.querySelector('td.spotify-album')).not.toBe(null);
    expect(document.querySelector('td.discovery-actions')).not.toBe(null);
    // #863: Unknown Artist falls back to the matched artist (both cells show it).
    expect(screen.getAllByText('Real Artist')).toHaveLength(2);
    // Found row: rematch + unmatch; not-found row: Fix → the intent props.
    expect(screen.getByTitle('Change this match')).toBeInTheDocument();
    fireEvent.click(screen.getByText('🔧 Fix'));
    expect(onFixTrack).toHaveBeenCalledWith(expect.objectContaining({ index: 1 }));
    expect(document.getElementById('sync-discovery-row-tidal_77-0')).not.toBe(null);
  });

  it('a modal with no results pre-renders every playlist track as Pending (10001)', () => {
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', {
          playlist: {
            name: 'My List',
            tracks: [
              { name: 'T1', artists: ['A1'] },
              { name: 'T2', artists: ['A2', 'A3'] },
            ],
          },
        })}
        standalone={false}
        {...noopHandlers}
      />,
    );
    expect(screen.getAllByText('🔍 Pending...')).toHaveLength(2);
    expect(screen.getByText('T1')).toBeInTheDocument();
    expect(screen.getByText('A2, A3')).toBeInTheDocument();
  });

  it('pendingSourceRows speaks the vanilla dialects exactly (10007-10020)', () => {
    expect(
      pendingSourceRows(SYNC_SOURCES.listenbrainz, [{ track_name: 'L', artist_name: 'M' }]),
    ).toEqual([{ index: 0, track: 'L', artist: 'M' }]);
    expect(pendingSourceRows(SYNC_SOURCES.tidal, [{}])).toEqual([
      { index: 0, track: 'Unknown Track', artist: 'Unknown Artist' },
    ]);
    // A non-array artists value passes through; empty array joins to ''.
    expect(pendingSourceRows(SYNC_SOURCES.tidal, [{ name: 'S', artists: 'Solo' }])).toEqual([
      { index: 0, track: 'S', artist: 'Solo' },
    ]);
    expect(pendingSourceRows(SYNC_SOURCES.tidal, [{ name: 'E', artists: [] }])).toEqual([
      { index: 0, track: 'E', artist: '' },
    ]);
  });
});

describe('the wing-it dropdown', () => {
  it('Download closes the modal and calls the engine with the vanilla shape', () => {
    const wingIt = vi.fn();
    const onClose = vi.fn();
    window.wingItDownload = wingIt;
    const tracks = [{ id: 't1' }, { id: 't2' }];
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.qobuz}
        state={makeState('qobuz', { playlist: { name: 'Q List', tracks } })}
        standalone={false}
        {...noopHandlers}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByText('⚡ Wing It'));
    fireEvent.click(screen.getByText('Download'));
    expect(onClose).toHaveBeenCalledOnce();
    expect(wingIt).toHaveBeenCalledWith(tracks, 'Q List', 'Qobuz', null, true);
  });

  it('Sync to Server keeps the modal open and passes the LB flag', () => {
    const wingItSync = vi.fn();
    const onClose = vi.fn();
    window._wingItSyncFromModal = wingItSync;
    const tracks = [{ id: 't1' }];
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.listenbrainz}
        state={makeState('listenbrainz', {
          sourceId: 'mbid-1',
          playlist: { name: 'Jams', tracks },
        })}
        standalone={false}
        {...noopHandlers}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByText('⚡ Wing It'));
    fireEvent.click(screen.getByText('Sync to Server'));
    expect(onClose).not.toHaveBeenCalled();
    expect(wingItSync).toHaveBeenCalledWith('mbid-1', tracks, 'Jams', true);
  });

  it('empty tracks toast instead of calling the engine (downloads.js 83)', () => {
    const toast = vi.fn();
    const wingIt = vi.fn();
    window.showToast = toast;
    window.wingItDownload = wingIt;
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', { playlist: { name: 'Empty', tracks: [] } })}
        standalone={false}
        {...noopHandlers}
      />,
    );
    fireEvent.click(screen.getByText('⚡ Wing It'));
    fireEvent.click(screen.getByText('Download'));
    expect(toast).toHaveBeenCalledWith('No tracks available for Wing It', 'error');
    expect(wingIt).not.toHaveBeenCalled();
  });

  it('wingItSourceWord speaks the engine dialect', () => {
    expect(wingItSourceWord(SYNC_SOURCES.listenbrainz)).toBe('ListenBrainz');
    expect(wingItSourceWord(SYNC_SOURCES.youtube)).toBe('YouTube');
    expect(wingItSourceWord(SYNC_SOURCES.spotify_public)).toBe('YouTube'); // the engine default
  });
});

describe('chrome', () => {
  it('Close fires onClose; the footer actions sit inside the vanilla wrapper', () => {
    const onClose = vi.fn();
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.tidal}
        state={makeState('tidal', {})}
        standalone={false}
        {...noopHandlers}
        onClose={onClose}
      />,
    );
    expect(document.querySelector('.modal-footer-left > .modal-footer-actions')).not.toBe(null);
    fireEvent.click(screen.getByText('🏠 Close'));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
