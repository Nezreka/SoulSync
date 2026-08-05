/**
 * The small-tab wave — the shared card, the three tabs, and the modal host,
 * rendered against a captured fetch and the REAL useSourceVertical hook so
 * the wiring (seed → open → discovery modal → fix/unmatch/download) is
 * exercised end to end.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LbCardData } from '../-sync.lb-tabs';

import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { LastfmSyncTab } from './lastfm-sync-tab';
import { LbCardList, ListenBrainzSyncTab, useLbCardOpen } from './lb-sync-tab';
import { SoulsyncDiscoveryTab } from './soulsync-discovery-tab';
import { SourceCard } from './source-card';
import { SourceModals } from './source-modals';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showLoadingOverlay?: unknown }).showLoadingOverlay;
  delete (window as { hideLoadingOverlay?: unknown }).hideLoadingOverlay;
  delete (window as { openMirroredPlaylistModal?: unknown }).openMirroredPlaylistModal;
  delete (window as { openDownloadMissingModalForYouTube?: unknown })
    .openDownloadMissingModalForYouTube;
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
});

describe('SourceCard', () => {
  it('phase-driven text/color/button from the shared maps, with overrides', () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <SourceCard
        cardClassName="listenbrainz-playlist-card"
        icon="🎧"
        name="Weekly Jams"
        countText="25 tracks"
        owner="by troi-bot"
        phase="discovered"
        progressLine="♪ 25 / ✓ 20 / ✗ 5 / 80%"
        onClick={onClick}
      />,
    );
    expect(screen.getByText('Discovery Complete')).toBeInTheDocument();
    expect(screen.getByText('View Results')).toBeInTheDocument();
    expect(screen.getByText('♪ 25 / ✓ 20 / ✗ 5 / 80%')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Weekly Jams'));
    expect(onClick).toHaveBeenCalledOnce();
    rerender(
      <SourceCard
        cardClassName="soulsync-discovery-playlist-card"
        icon="✨"
        name="Hidden Gems"
        countText="0 tracks"
        owner="hidden_gems"
        phase="fresh"
        statusText="Ready"
        statusColor="#14b8a6"
        buttonText="Refresh & Mirror"
        progressLine={null}
        onClick={onClick}
      />,
    );
    expect(screen.getByText('Refresh & Mirror')).toBeInTheDocument();
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(document.querySelector('.playlist-card-progress')?.classList.contains('hidden')).toBe(
      true,
    );
  });
});

describe('ListenBrainzSyncTab', () => {
  it('loads the three categories, switches sub-tabs, shows the per-tab empty copy', async () => {
    responder = (url) =>
      url.includes('created-for')
        ? {
            success: true,
            playlists: [
              {
                playlist: {
                  identifier: 'https://lb.org/playlist/mbid-1',
                  title: 'Weekly Jams',
                  creator: 'troi-bot',
                  annotation: { track_count: 25 },
                },
              },
            ],
          }
        : { success: true, playlists: [] };
    stubFetch();
    const Harness = () => {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      return <ListenBrainzSyncTab vertical={vertical} onOpen={() => {}} />;
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('Weekly Jams')).toBeInTheDocument());
    expect(screen.getByText('25 tracks')).toBeInTheDocument();
    fireEvent.click(screen.getByText('My Playlists'));
    expect(
      screen.getByText("You haven't created any ListenBrainz playlists yet."),
    ).toBeInTheDocument();
  });

  it('the unauthenticated answer shows the connect prompt (41-47)', async () => {
    responder = (url) =>
      url.includes('created-for')
        ? { success: false, error: 'Not authenticated with ListenBrainz' }
        : { success: true, playlists: [] };
    stubFetch();
    const Harness = () => {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      return <ListenBrainzSyncTab vertical={vertical} onOpen={() => {}} />;
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText(/ListenBrainz not connected/)).toBeInTheDocument());
  });

  it('the refresh button POSTs the cache refresh before re-loading (348-356)', async () => {
    responder = () => ({ success: true, playlists: [] });
    stubFetch();
    const Harness = () => {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      return <ListenBrainzSyncTab vertical={vertical} onOpen={() => {}} />;
    };
    render(<Harness />);
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(3));
    calls = [];
    await act(async () => {
      fireEvent.click(screen.getByText('🔄 Refresh'));
    });
    await waitFor(() =>
      expect(calls[0]).toEqual(
        expect.objectContaining({ url: '/api/discover/listenbrainz/refresh', method: 'POST' }),
      ),
    );
  });
});

describe('useLbCardOpen', () => {
  it('fetches + caches tracks, seeds the vertical, opens; empty playlists toast (159-218)', async () => {
    responder = (url) =>
      url.includes('/playlist/mbid-1')
        ? { tracks: [{ track_name: 'T', artist_name: 'A' }] }
        : { tracks: [] };
    stubFetch();
    const opened: string[] = [];
    let openCard: ((card: LbCardData) => Promise<void>) | undefined;
    const Harness = () => {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      openCard = useLbCardOpen(vertical, (id) => opened.push(id));
      return (
        <LbCardList
          cards={[{ mbid: 'mbid-1', title: 'Jams', creator: 'x', count: 1 }]}
          vertical={vertical}
          icon="🎧"
          cardClassName="listenbrainz-playlist-card"
          idPrefix="listenbrainz-sync-card"
          onOpen={(card) => void openCard?.(card)}
        />
      );
    };
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('Jams'));
    });
    expect(opened).toEqual(['mbid-1']);
    // Second open reuses the cache — no second tracks fetch.
    calls = [];
    await act(async () => {
      fireEvent.click(screen.getByText('Jams'));
    });
    expect(calls).toEqual([]);

    const toast = vi.fn();
    window.showToast = toast;
    await act(async () => {
      await openCard?.({ mbid: '', title: 'NoId', creator: '', count: 0 });
    });
    expect(toast).toHaveBeenCalledWith('Missing playlist ID', 'error');
  });
});

describe('LastfmSyncTab', () => {
  it('loads radios with the Last.fm defaults and the generate-first empty copy', async () => {
    responder = () => ({ success: true, playlists: [{ playlist: { identifier: 'u/m9' } }] });
    stubFetch();
    const Harness = () => {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      return <LastfmSyncTab vertical={vertical} onOpen={() => {}} />;
    };
    render(<Harness />);
    await waitFor(() => expect(screen.getByText('by Last.fm')).toBeInTheDocument());
  });
});

describe('LastfmSyncTab — the nameFallback wiring', () => {
  it('a radio with name but no title falls to the DEFAULT, not the name (sync-lastfm.js 68)', async () => {
    stubFetch();
    responder = (url) =>
      url.includes('lastfm-radio')
        ? { playlists: [{ playlist: { identifier: 'x/rad1', name: 'named-not-titled' } }] }
        : {};
    function Harness() {
      const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
      return <LastfmSyncTab vertical={vertical} onOpen={() => undefined} />;
    }
    render(<Harness />);
    // LB would title this 'named-not-titled'; Last.fm must not.
    await waitFor(() => expect(screen.getByText('by Last.fm')).toBeInTheDocument());
    expect(screen.queryByText('named-not-titled')).not.toBeInTheDocument();
  });
});

describe('SoulsyncDiscoveryTab', () => {
  it('Refresh & Mirror: generator POST → mirror POST → toast + detail modal + card update', async () => {
    const toast = vi.fn();
    const openMirrored = vi.fn();
    window.showToast = toast;
    window.openMirroredPlaylistModal = openMirrored;
    responder = (url) => {
      if (url.includes('/kinds')) return { success: true, kinds: [] };
      if (url.includes('/personalized/playlists'))
        return {
          success: true,
          playlists: [{ kind: 'hidden_gems', variant: '', name: 'Hidden Gems', is_stale: true }],
        };
      if (url.includes('/refresh'))
        return {
          success: true,
          playlist: { kind: 'hidden_gems', name: 'Hidden Gems' },
          tracks: [{ track_name: 'T', artist_name: 'A', spotify_track_id: 'sp1' }],
        };
      if (url.includes('/mirror-playlist')) return { success: true, playlist_id: 42 };
      return {};
    };
    stubFetch();
    render(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('Hidden Gems')).toBeInTheDocument());
    expect(screen.getByText('Stale — refresh to regenerate')).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByText('Hidden Gems'));
    });
    const mirror = calls.find((c) => c.url === '/api/mirror-playlist');
    expect(mirror).toBeDefined();
    expect((mirror!.body as { source_playlist_id: string }).source_playlist_id).toBe(
      'ssd_hidden_gems',
    );
    expect(toast).toHaveBeenCalledWith("Mirrored 'Hidden Gems' with 1 tracks", 'success');
    expect(openMirrored).toHaveBeenCalledWith(42);
    await waitFor(() => expect(screen.getByText('Ready')).toBeInTheDocument());
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
  });

  it('a 0-track generation warns but still mirrors (160-164)', async () => {
    const toast = vi.fn();
    window.showToast = toast;
    responder = (url) => {
      if (url.includes('/kinds')) return { success: true, kinds: [] };
      if (url.includes('/personalized/playlists'))
        return {
          success: true,
          playlists: [{ kind: 'fresh_tape', variant: '', name: 'Fresh Tape' }],
        };
      if (url.includes('/refresh')) return { success: true, playlist: {}, tracks: [] };
      if (url.includes('/mirror-playlist')) return { success: true, playlist_id: 7 };
      return {};
    };
    stubFetch();
    render(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('Fresh Tape')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByText('Fresh Tape'));
    });
    expect(toast).toHaveBeenCalledWith(
      "'Fresh Tape' generated 0 tracks. Try widening the playlist's config in Discover.",
      'warning',
    );
    expect(calls.some((c) => c.url === '/api/mirror-playlist')).toBe(true);
  });
});

describe('SourceModals', () => {
  function Harness({ withRows = true }: { withRows?: boolean }) {
    const vertical = useSourceVertical(SYNC_SOURCES.listenbrainz);
    const [openId, setOpenId] = useState<string | null>(null);
    return (
      <div>
        <button
          type="button"
          onClick={() => {
            vertical.hydrate('mbid-1', {
              phase: 'discovered',
              playlist: { name: 'Jams', tracks: [{}] },
              spotify_matches: withRows ? 1 : 0,
              discovery_results: withRows
                ? [
                    {
                      lb_track: 'Src',
                      lb_artist: 'Artist',
                      status_class: 'found',
                      spotify_data: { id: 'sp1', name: 'Matched' },
                    },
                  ]
                : [],
            });
            setOpenId('mbid-1');
          }}
        >
          open-it
        </button>
        <SourceModals
          config={SYNC_SOURCES.listenbrainz}
          vertical={vertical}
          openId={openId}
          onClose={() => setOpenId(null)}
          standalone={false}
        />
      </div>
    );
  }

  it('renders the discovery modal, hands downloads to the engine with the LB vpid', async () => {
    const engine = vi.fn(async () => {});
    window.openDownloadMissingModalForYouTube = engine;
    responder = () => ({});
    stubFetch();
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    expect(screen.getByText('🎵 ListenBrainz Playlist Discovery')).toBeInTheDocument();
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));
    expect(engine).toHaveBeenCalledWith('listenbrainz_mbid-1', 'Jams', [
      { id: 'sp1', name: 'Matched' },
    ]);
  });

  it('the fix modal is NESTED inside the discovery overlay, not a sibling (9426)', async () => {
    responder = () => ({});
    stubFetch();
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByTitle('Change this match'));
    const overlay = document.querySelector('.modal-overlay')!;
    const fix = document.querySelector('.discovery-fix-modal-overlay')!;
    expect(fix).toBeTruthy();
    // .discovery-fix-modal-overlay is z-index 1000 "Above parent modal
    // content"; .modal-overlay is 10000. As a sibling it paints underneath.
    expect(overlay.contains(fix)).toBe(true);
  });

  it('the hand-off does NOT run the close-reset (no update_phase POST)', async () => {
    window.openDownloadMissingModalForYouTube = vi.fn(async () => {});
    responder = () => ({});
    stubFetch();
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));
    await act(async () => {});
    // startYouTubeDownloadMissing only hides (10768-10772); the reset +
    // update_phase POST belong to the Close button (10253-10455).
    expect(
      calls.some((c) => c.url.includes('update-phase') || c.url.includes('update_phase')),
    ).toBe(false);
  });

  it('unmatch POSTs, toasts, and flips the row back to Not Found', async () => {
    const toast = vi.fn();
    window.showToast = toast;
    responder = () => ({ success: true });
    stubFetch();
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    await act(async () => {
      fireEvent.click(screen.getByTitle('Remove this match'));
    });
    expect(calls[0]).toMatchObject({
      url: '/api/listenbrainz/discovery/unmatch',
      method: 'POST',
      body: { identifier: 'mbid-1', track_index: 0 },
    });
    expect(toast).toHaveBeenCalledWith('Match removed', 'success');
    expect(screen.getByText('❌ Not Found')).toBeInTheDocument();
  });

  it('the Fix intent mounts the FixModal for the clicked row', async () => {
    responder = () => ({ tracks: [] });
    stubFetch();
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    await act(async () => {
      fireEvent.click(screen.getByTitle('Change this match'));
    });
    expect(screen.getByText('Fix Track Match')).toBeInTheDocument();
    // The prefill reads the row's source fields.
    expect(screen.getByDisplayValue('Src')).toBeInTheDocument();
  });
});
