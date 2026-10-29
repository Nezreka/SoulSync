/**
 * The small tabs (ListenBrainz, Last.fm Radio, SoulSync Discovery) import
 * into SoulSync, and the modal host still serves the Mirrored tab's
 * identification. Rendered against a captured fetch, the tabs inside an
 * import landing, so the whole path is exercised: list → import → land.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { LastfmSyncTab } from './lastfm-sync-tab';
import { ListenBrainzSyncTab, forgetLbTargets } from './lb-sync-tab';
import { SoulsyncDiscoveryTab } from './soulsync-discovery-tab';
import { SourceModals } from './source-modals';
import { type ImportLanding, ImportLandingProvider } from './use-importer';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});
let mirrors: { id: number; source: string; source_playlist_id: string }[] = [];

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
      if (url === '/api/mirrored-playlists') return new Response(JSON.stringify(mirrors));
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

function renderTab(tab: React.ReactNode) {
  const land = vi.fn<ImportLanding['land']>();
  const showList = vi.fn();
  render(<ImportLandingProvider value={{ land, showList }}>{tab}</ImportLandingProvider>);
  return { land, showList };
}

const mirrorBody = () =>
  calls.find((c) => c.url === '/api/mirror-playlist')?.body as Record<string, unknown> | undefined;

beforeEach(() => {
  mirrors = [];
  forgetLbTargets();
});

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

const WEEKLY = {
  playlist: {
    identifier: 'https://lb.org/playlist/mbid-1',
    title: 'Weekly Jams for boulder, week of 2026-10-26',
    creator: 'troi-bot',
    annotation: { track_count: 25 },
  },
};
const ONE_OFF = {
  playlist: {
    identifier: 'https://lb.org/playlist/mbid-2',
    title: 'Road Trip',
    creator: 'boulder',
    annotation: { track_count: 2 },
  },
};

function lbResponder(url: string) {
  if (url.includes('created-for')) return { success: true, playlists: [WEEKLY] };
  if (url.includes('user-playlists')) return { success: true, playlists: [ONE_OFF] };
  if (url.includes('series-detect')) {
    return url.includes('Weekly%20Jams')
      ? {
          matched: true,
          source: 'listenbrainz',
          series_id: 'lb_weekly_jams_boulder',
          canonical_name: 'Weekly Jams',
        }
      : { matched: false };
  }
  if (url.includes('/playlist/mbid-')) {
    return {
      tracks: [
        {
          track_name: 'T1',
          artist_name: 'A1',
          album_name: 'Al',
          duration_ms: 9,
          recording_mbid: 'rec-1',
          album_cover_url: 'http://c',
        },
      ],
    };
  }
  if (url === '/api/mirror-playlist') return { success: true, playlist_id: 33 };
  return { success: true, playlists: [] };
}

describe('ListenBrainzSyncTab', () => {
  it('loads the three categories, switches sub-tabs, shows the per-tab empty copy', async () => {
    responder = (url) =>
      url.includes('user-playlists') ? { success: true, playlists: [] } : lbResponder(url);
    stubFetch();
    renderTab(<ListenBrainzSyncTab />);
    await waitFor(() =>
      expect(screen.getByText('Weekly Jams for boulder, week of 2026-10-26')).toBeInTheDocument(),
    );
    expect(screen.getByText('25 tracks · by troi-bot')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Playlists'));
    expect(
      screen.getByText("You haven't created any ListenBrainz playlists yet."),
    ).toBeInTheDocument();
  });

  it('a rotating series imports under its series key and name, raw tracks for the mirror to identify', async () => {
    responder = lbResponder;
    stubFetch();
    const { land } = renderTab(<ListenBrainzSyncTab />);
    const label = 'Import Weekly Jams for boulder, week of 2026-10-26 into SoulSync';
    await waitFor(() => expect(screen.getByLabelText(label)).toBeInTheDocument());
    // the key resolves before the card can say whether it's in
    await waitFor(() => expect(screen.getByLabelText(label)).toHaveAttribute('data-status', 'new'));
    fireEvent.click(screen.getByLabelText(label));
    await waitFor(() => expect(land).toHaveBeenCalledWith(33, 'Weekly Jams', false));
    expect(mirrorBody()).toMatchObject({
      source: 'listenbrainz',
      source_playlist_id: 'lb_weekly_jams_boulder',
      name: 'Weekly Jams',
      owner: 'troi-bot',
    });
    expect((mirrorBody()!.tracks as Record<string, unknown>[])[0]).toEqual({
      track_name: 'T1',
      artist_name: 'A1',
      album_name: 'Al',
      duration_ms: 9,
      image_url: 'http://c',
      source_track_id: 'rec-1',
      extra_data: null,
    });
  });

  it('a one-off playlist files under its own mbid', async () => {
    responder = lbResponder;
    stubFetch();
    const { land } = renderTab(<ListenBrainzSyncTab />);
    await waitFor(() =>
      expect(screen.getByText('Weekly Jams for boulder, week of 2026-10-26')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Playlists'));
    await waitFor(() =>
      expect(screen.getByLabelText('Import Road Trip into SoulSync')).toHaveAttribute(
        'data-status',
        'new',
      ),
    );
    fireEvent.click(screen.getByLabelText('Import Road Trip into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(33, 'Road Trip', false));
    expect(mirrorBody()).toMatchObject({ source: 'listenbrainz', source_playlist_id: 'mbid-2' });
  });

  it("this week's playlist of a series already in opens it, never overwrites it", async () => {
    responder = lbResponder;
    mirrors = [{ id: 9, source: 'listenbrainz', source_playlist_id: 'lb_weekly_jams_boulder' }];
    stubFetch();
    const { land } = renderTab(<ListenBrainzSyncTab />);
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    fireEvent.click(
      screen.getByLabelText('Open Weekly Jams for boulder, week of 2026-10-26 in SoulSync'),
    );
    await waitFor(() => expect(land).toHaveBeenCalledWith(9, 'Weekly Jams', true));
    expect(mirrorBody()).toBeUndefined();
  });

  it('the unauthenticated answer shows the connect prompt', async () => {
    responder = (url) =>
      url.includes('created-for')
        ? { success: false, error: 'Not authenticated with ListenBrainz' }
        : { success: true, playlists: [] };
    stubFetch();
    renderTab(<ListenBrainzSyncTab />);
    await waitFor(() => expect(screen.getByText(/ListenBrainz not connected/)).toBeInTheDocument());
  });

  it('the refresh button POSTs the cache refresh before re-loading', async () => {
    responder = () => ({ success: true, playlists: [] });
    stubFetch();
    renderTab(<ListenBrainzSyncTab />);
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

describe('LastfmSyncTab', () => {
  it('lists radios with the Last.fm defaults, and imports one under lastfm', async () => {
    responder = (url) => {
      if (url.includes('lastfm-radio')) {
        return {
          success: true,
          playlists: [
            {
              playlist: {
                identifier: 'u/m9',
                title: 'Last.fm Radio: Kavinsky',
                annotation: { track_count: 1 },
              },
            },
          ],
        };
      }
      if (url.includes('series-detect')) return { matched: false };
      if (url.includes('/playlist/m9'))
        return { tracks: [{ track_name: 'Nightcall', artist_name: 'Kavinsky' }] };
      if (url === '/api/mirror-playlist') return { success: true, playlist_id: 12 };
      return {};
    };
    stubFetch();
    const { land } = renderTab(<LastfmSyncTab />);
    await waitFor(() => expect(screen.getByText('1 track · by Last.fm')).toBeInTheDocument());
    const label = 'Import Last.fm Radio: Kavinsky into SoulSync';
    await waitFor(() => expect(screen.getByLabelText(label)).toHaveAttribute('data-status', 'new'));
    fireEvent.click(screen.getByLabelText(label));
    await waitFor(() => expect(land).toHaveBeenCalledWith(12, 'Last.fm Radio: Kavinsky', false));
    // a 'Last.fm Radio:' title files under lastfm, so the board groups it right
    expect(mirrorBody()).toMatchObject({
      source: 'lastfm',
      source_playlist_id: 'm9',
      owner: 'Last.fm',
    });
  });

  it('a radio with name but no title falls to the DEFAULT, not the name (sync-lastfm.js 68)', async () => {
    stubFetch();
    responder = (url) =>
      url.includes('lastfm-radio')
        ? { playlists: [{ playlist: { identifier: 'x/rad1', name: 'named-not-titled' } }] }
        : {};
    renderTab(<LastfmSyncTab />);
    // LB would title this 'named-not-titled'; Last.fm must not.
    await waitFor(() => expect(screen.getByText(/by Last\.fm/)).toBeInTheDocument());
    expect(screen.queryByText('named-not-titled')).not.toBeInTheDocument();
  });
});

describe('SoulsyncDiscoveryTab', () => {
  it('lists unbuilt variants under section headings, described, not keyed', async () => {
    responder = (url) => {
      if (url.includes('/kinds'))
        return {
          success: true,
          kinds: [
            {
              kind: 'daily_mix',
              name_template: 'Daily Mix {variant}',
              description: 'Built from what you listen to',
              requires_variant: true,
              variants: ['1', '2'],
            },
            { kind: 'hidden_gems', name_template: 'Hidden Gems', description: 'Obscure picks' },
          ],
        };
      return {
        success: true,
        playlists: [{ kind: 'daily_mix', variant: '1', name: 'Daily Mix 1', track_count: 40 }],
      };
    };
    stubFetch();
    renderTab(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('Daily Mix 2')).toBeInTheDocument());
    const headings = [...document.querySelectorAll('.ssd-group-title')].map((h) => h.textContent);
    expect(headings).toEqual(['Daily Mix', 'Discovery playlists']);
    expect(screen.getByText('40 tracks · Built from what you listen to')).toBeInTheDocument();
    expect(screen.getByText('Not built yet · Built from what you listen to')).toBeInTheDocument();
    expect(screen.getByText('Not built yet · Obscure picks')).toBeInTheDocument();
    expect(screen.queryByText(/daily_mix · 1/)).toBeNull();
  });

  it('import: generator POST → mirror POST with inline matches → land, card shows its count', async () => {
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
    const { land } = renderTab(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('Hidden Gems')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Hidden Gems into SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(42, 'Hidden Gems', false));
    expect((mirrorBody() as { source_playlist_id: string }).source_playlist_id).toBe(
      'ssd_hidden_gems',
    );
    expect((mirrorBody() as { source: string }).source).toBe('soulsync_discovery');
    await waitFor(() => expect(screen.getByText(/^1 track · /)).toBeInTheDocument());
    expect(screen.getByText('In SoulSync')).toBeInTheDocument();
  });

  it('one already in opens without regenerating', async () => {
    mirrors = [{ id: 5, source: 'soulsync_discovery', source_playlist_id: 'ssd_hidden_gems' }];
    responder = (url) => {
      if (url.includes('/kinds')) return { success: true, kinds: [] };
      if (url.includes('/personalized/playlists'))
        return {
          success: true,
          playlists: [{ kind: 'hidden_gems', variant: '', name: 'Hidden Gems' }],
        };
      return {};
    };
    stubFetch();
    const { land } = renderTab(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('In SoulSync')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Open Hidden Gems in SoulSync'));
    await waitFor(() => expect(land).toHaveBeenCalledWith(5, 'Hidden Gems', true));
    expect(calls.some((c) => c.url.includes('/refresh'))).toBe(false);
  });

  it('a 0-track generation says why and imports nothing', async () => {
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
      return {};
    };
    stubFetch();
    const { land } = renderTab(<SoulsyncDiscoveryTab />);
    await waitFor(() => expect(screen.getByText('Fresh Tape')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Fresh Tape into SoulSync'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        "'Fresh Tape' generated 0 tracks. Try widening the playlist's config in Discover.",
        'error',
      ),
    );
    expect(mirrorBody()).toBeUndefined();
    expect(land).not.toHaveBeenCalled();
  });
});

describe('SourceModals', () => {
  it('renders NOTHING while closed', () => {
    // The page keeps the mirrored host permanently mounted
    // (-ui/sync-modals.tsx). A closed host returns null; its one effect (a
    // download waiting on a retry, #1912) returns at once with nothing pending.
    const { container } = render(
      <SourceModals
        config={SYNC_SOURCES.tidal}
        vertical={{ states: {} } as never}
        openId={null}
        onClose={() => {}}
        standalone={false}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

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
    expect(screen.getByText('🎵 ListenBrainz Playlist Identification')).toBeInTheDocument();
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
