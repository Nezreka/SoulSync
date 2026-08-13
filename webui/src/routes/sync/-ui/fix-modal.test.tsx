/**
 * The React-native Fix modal — cascade search with per-source status, the
 * MBID escape hatch, and the confirm→POST→onFixed flow, all against a
 * captured fetch and the SoulSync confirm dialog (never window.confirm).
 * Plus the applyFixedMatch/applyUnmatched reducers it feeds.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoveryRow } from '../-sync.transform';

import { SYNC_SOURCES } from '../-sync.sources';
import { applyFixedMatch, applyUnmatched, freshSourceState } from '../-sync.state';
import { FixModal } from './fix-modal';
import { OrganizeToggle } from './organize-toggle';

const ROW: DiscoveryRow = {
  index: 1,
  yt_track: 'Src Track',
  yt_artist: 'Src Artist',
  status: '❌ Not Found',
  status_class: 'not-found',
  spotify_track: '-',
  spotify_artist: '-',
  spotify_album: '-',
};

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
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  delete (window as { showToast?: unknown }).showToast;
});

describe('FixModal', () => {
  it('auto-searches on open, cascades to the next source, renders sorted results', async () => {
    responder = (url) =>
      url.startsWith('/api/spotify/')
        ? { tracks: [] }
        : url.startsWith('/api/deezer/')
          ? {
              tracks: [
                { id: 'd1', name: 'Song (Live)' },
                { id: 'd2', name: 'Song' },
              ],
            }
          : { tracks: [] };
    stubFetch();
    render(
      <FixModal
        config={SYNC_SOURCES.tidal}
        sourceId="77"
        row={ROW}
        onClose={() => {}}
        onFixed={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText('Song')).toBeInTheDocument());
    // Spotify first (the default order), then the deezer fallback answered.
    expect(calls[0].url).toContain('/api/spotify/search_tracks?track=Src+Track&artist=Src+Artist');
    expect(calls[1].url).toContain('/api/deezer/search_tracks');
    // Sorted: the standard version above the live variant.
    const titles = Array.from(document.querySelectorAll('.fix-result-title')).map(
      (el) => el.textContent,
    );
    expect(titles).toEqual(['Song', 'Song (Live)']);
  });

  it('the MBID lookup renders a single confirmable result; garbage is rejected', async () => {
    responder = (url) =>
      url.includes('/api/musicbrainz/recording/') ? { id: 'mb1', name: 'Exact' } : { tracks: [] };
    stubFetch();
    render(
      <FixModal
        config={SYNC_SOURCES.tidal}
        sourceId="77"
        row={ROW}
        onClose={() => {}}
        onFixed={() => {}}
      />,
    );
    const mbidInput = screen.getByPlaceholderText('Or paste MusicBrainz recording URL / MBID');
    fireEvent.change(mbidInput, { target: { value: 'nonsense' } });
    fireEvent.click(screen.getByText('🔗 Look up'));
    await waitFor(() =>
      expect(screen.getByText(/Not a valid MusicBrainz recording URL or MBID/)).toBeInTheDocument(),
    );
    fireEvent.change(mbidInput, {
      target: { value: 'https://musicbrainz.org/recording/b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f' },
    });
    fireEvent.click(screen.getByText('🔗 Look up'));
    await waitFor(() => expect(screen.getByText('Exact')).toBeInTheDocument());
  });

  it('selecting confirms through the SoulSync dialog, POSTs update_match, fires onFixed + onClose', async () => {
    const confirm = vi.fn(async () => true);
    const onFixed = vi.fn();
    const onClose = vi.fn();
    window.showConfirmDialog = confirm;
    responder = (url) =>
      url.startsWith('/api/spotify/')
        ? { tracks: [{ id: 'sp9', name: 'Pick Me', artists: ['A'], album: 'Alb' }] }
        : {};
    stubFetch();
    render(
      <FixModal
        config={SYNC_SOURCES.spotify_public}
        sourceId="h4sh"
        row={ROW}
        onClose={onClose}
        onFixed={onFixed}
      />,
    );
    await waitFor(() => expect(screen.getByText('Pick Me')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByText('Pick Me'));
    });
    expect(confirm).toHaveBeenCalledWith({
      title: 'Confirm Match',
      message: 'Match to "Pick Me" by A?',
      confirmText: 'Confirm',
    });
    const post = calls.find((c) => c.method === 'POST');
    expect(post).toMatchObject({
      url: '/api/spotify-public/discovery/update_match',
      body: {
        identifier: 'h4sh',
        track_index: 1,
        original_name: 'Src Track',
        original_artist: 'Src Artist',
      },
    });
    expect(onFixed).toHaveBeenCalledWith(1, expect.objectContaining({ id: 'sp9' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('a declined confirm neither POSTs nor closes', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    const onFixed = vi.fn();
    responder = (url) =>
      url.startsWith('/api/spotify/') ? { tracks: [{ id: 'sp9', name: 'Pick Me' }] } : {};
    stubFetch();
    render(
      <FixModal
        config={SYNC_SOURCES.tidal}
        sourceId="77"
        row={ROW}
        onClose={() => {}}
        onFixed={onFixed}
      />,
    );
    await waitFor(() => expect(screen.getByText('Pick Me')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByText('Pick Me'));
    });
    expect(calls.find((c) => c.method === 'POST')).toBeUndefined();
    expect(onFixed).not.toHaveBeenCalled();
  });
});

describe('the fix reducers', () => {
  const base = {
    ...freshSourceState(SYNC_SOURCES.tidal, '77'),
    phase: 'discovered',
    spotifyMatches: 1,
    spotifyTotal: 4,
    rawResults: [
      { tidal_track: { name: 'A', artists: ['X'] }, spotify_data: { name: 'A2' } },
      { tidal_track: { name: 'B', artists: ['Y'] }, status: 'not_found' },
    ],
  };

  it('applyFixedMatch: found + manual_match + album OBJECT, counter/progress on newly-found only', () => {
    const next = applyFixedMatch(base as never, SYNC_SOURCES.tidal, 1, {
      id: 'sp9',
      name: 'Fixed',
      artists: ['A'],
      album: 'Alb',
      duration_ms: 1000,
      image_url: 'http://img/x',
    });
    expect(next.rows[1].status_class).toBe('found');
    expect(next.rows[1].spotify_track).toBe('Fixed');
    expect(next.rawResults[1].manual_match).toBe(true);
    const data = next.rawResults[1].spotify_data as { album: Record<string, unknown> };
    expect(data.album).toEqual({
      name: 'Alb',
      image_url: 'http://img/x',
      images: [{ url: 'http://img/x' }],
    });
    expect(next.spotifyMatches).toBe(2);
    expect(next.discoveryProgress).toBe(50); // 2/4, the vanilla repaint formula
    // Re-fixing an already-found row does NOT bump the counter (478/524).
    const again = applyFixedMatch(next, SYNC_SOURCES.tidal, 1, { id: 'sp10', name: 'Other' });
    expect(again.spotifyMatches).toBe(2);
    // An index with no raw result is a no-op.
    expect(applyFixedMatch(base as never, SYNC_SOURCES.tidal, 9, { id: 'x' })).toBe(base);
  });

  it('applyUnmatched: reverts the row, does NOT decrement the counter (the vanilla omission)', () => {
    const fixed = applyFixedMatch(base as never, SYNC_SOURCES.tidal, 1, { id: 'sp9', name: 'F' });
    const undone = applyUnmatched(fixed, SYNC_SOURCES.tidal, 1);
    expect(undone.rows[1].status_class).toBe('not-found');
    expect(undone.rows[1].spotify_track).toBe('-');
    expect(undone.rawResults[1].spotify_data).toBe(null);
    expect(undone.rawResults[1].manual_match).toBe(false);
    expect(undone.spotifyMatches).toBe(2); // transcribed: no decrement
  });
});

describe('OrganizeToggle', () => {
  it('reads the preference on mount and reverts the checkbox when the write fails', async () => {
    const toast = vi.fn();
    window.showToast = toast;
    responder = (url) =>
      url.includes('/resolve')
        ? { found: true, playlist: { id: 9, organize_by_playlist: true } }
        : { error: 'nope' };
    stubFetch();
    render(<OrganizeToggle playlistRef="spotify_public_h" source="spotify_public" />);
    const checkbox = await waitFor(() => screen.getByRole('checkbox'));
    await waitFor(() => expect(checkbox).toBeChecked());
    // Uncheck → resolve ok but PATCH errors → reverts + toasts.
    await act(async () => {
      fireEvent.click(checkbox);
    });
    await waitFor(() => expect(checkbox).toBeChecked());
    expect(toast).toHaveBeenCalledWith(
      'Could not save playlist folder preference (mirror this playlist first)',
      'warning',
    );
  });
});
