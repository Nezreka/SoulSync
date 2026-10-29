/**
 * #1910 (cremonies): the playlist flow said things it didn't mean.
 *
 * - "65 not synced yet" on a playlist whose server copy held all 65: the
 *   ownership answer only a sync wrote. the detail now checks unchecked
 *   tracks on open and says "being checked…" meanwhile
 * - "Found" in identification read as "you have it". it means matched on the
 *   metadata source, so it says Identified, with a line saying so
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SourcePlaylistState } from '../-sync.state';
import type { DiscoveryRow } from '../-sync.transform';

import { trackStatusSegments } from '../-sync.mirrored';
import { discoveryStatusLabel } from '../-sync.modal-core';
import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { DiscoveryModal } from './discovery-modal';
import { MirroredDetailModal } from './mirrored-detail-modal';
import { LIBRARY_CHECK_MAX_ROUNDS } from './use-library-check';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the words', () => {
  it('an unchecked track is not checked yet, not "not synced"', () => {
    expect(trackStatusSegments({ unchecked: 65 })).toEqual([
      { status: 'unchecked', count: 65, label: 'not checked yet' },
    ]);
  });

  it('a found identification row reads Identified, everything else is untouched', () => {
    expect(discoveryStatusLabel({ status: '✅ Found', status_class: 'found' })).toBe(
      '✅ Identified',
    );
    expect(discoveryStatusLabel({ status: 'Found' })).toBe('✅ Identified');
    expect(discoveryStatusLabel({ status: '❌ Not Found', status_class: 'not-found' })).toBe(
      '❌ Not Found',
    );
    expect(discoveryStatusLabel({ status: '⚡ Wing It', status_class: 'wing-it' })).toBe(
      '⚡ Wing It',
    );
  });
});

const FOUND: DiscoveryRow = {
  index: 0,
  yt_track: 'Gives You Hell',
  yt_artist: 'AAR',
  status: '✅ Found',
  status_class: 'found',
  spotify_track: 'Gives You Hell',
  spotify_artist: 'The All-American Rejects',
  spotify_album: 'When The World Comes Down',
};

function state(over: Partial<SourcePlaylistState>): SourcePlaylistState {
  return {
    ...freshSourceState(SYNC_SOURCES.mirrored, 'mirrored_3'),
    playlist: { name: 'alternative', tracks: [{}] },
    ...over,
  };
}

describe('the identification window', () => {
  it('says Identified, and what that does and does not mean', () => {
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.mirrored}
        state={state({ phase: 'discovered', rows: [FOUND] })}
        mirroredSource="deezer"
        standalone={false}
        onClose={() => {}}
        onStartDiscovery={() => {}}
        onStartSync={() => {}}
        onCancelSync={() => {}}
        onDownloadMissing={() => {}}
      />,
    );
    expect(screen.getByText('✅ Identified')).toBeInTheDocument();
    expect(screen.queryByText('✅ Found')).toBeNull();
    expect(screen.getByRole('columnheader', { name: 'Match' })).toBeInTheDocument();
    expect(document.querySelector('.discovery-meaning-note')?.textContent).toMatch(
      /Whether you already have it is checked when you download or sync/,
    );
    // the download button says what happens to the server playlist
    expect(screen.getByText('🔍 Download Missing Tracks').getAttribute('title')).toMatch(
      /Push to server/,
    );
  });

  it('no note before there is anything identified', () => {
    render(
      <DiscoveryModal
        config={SYNC_SOURCES.mirrored}
        state={state({ phase: 'fresh', rows: [] })}
        standalone={false}
        onClose={() => {}}
        onStartDiscovery={() => {}}
        onStartSync={() => {}}
        onCancelSync={() => {}}
        onDownloadMissing={() => {}}
      />,
    );
    expect(document.querySelector('.discovery-meaning-note')).toBeNull();
  });
});

describe('the detail checks the library on open', () => {
  const NOW = Date.UTC(2026, 9, 28, 12, 0, 0);
  const handlers = () => ({
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onEditSource: vi.fn(),
    onRunPipeline: vi.fn(),
    onDiscover: vi.fn(),
    onRefresh: vi.fn(),
  });

  it('checks a slice at a time, says so meanwhile, then refetches once', async () => {
    const answers = [
      { success: true, checked: 150, remaining: 20 },
      { success: true, checked: 20, remaining: 0 },
    ];
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify(answers.shift() ?? { success: true })),
    );
    vi.stubGlobal('fetch', fetchMock);
    const on = handlers();
    render(
      <MirroredDetailModal
        playlistId={41}
        data={{
          name: 'alternative',
          source: 'deezer',
          tracks: [],
          status_counts: { unchecked: 170 },
        }}
        now={NOW}
        {...on}
      />,
    );
    expect(screen.getByText(/being checked…/)).toBeInTheDocument();
    await waitFor(() => expect(on.onRefresh).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls.map((c) => [c[0], c[1]?.method])).toEqual([
      ['/api/mirrored-playlists/41/check-library', 'POST'],
      ['/api/mirrored-playlists/41/check-library', 'POST'],
    ]);
    expect(screen.getByText(/not checked yet/)).toBeInTheDocument();
  });

  it('asks nothing when every track has been checked', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const on = handlers();
    render(
      <MirroredDetailModal
        playlistId={42}
        data={{ name: 'x', source: 'deezer', tracks: [], status_counts: { in_library: 5 } }}
        now={NOW}
        {...on}
      />,
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(on.onRefresh).not.toHaveBeenCalled();
  });

  it('re-asks its own stale misses even when nothing reads unchecked', async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ success: true, checked: 3, remaining: 0 })),
    );
    vi.stubGlobal('fetch', fetchMock);
    const on = handlers();
    render(
      <MirroredDetailModal
        playlistId={45}
        data={{
          name: 'x',
          source: 'deezer',
          tracks: [],
          status_counts: { missing: 3 },
          library_check_due: 3,
        }}
        now={NOW}
        {...on}
      />,
    );
    await waitFor(() => expect(on.onRefresh).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // a quiet re-check: the missing count keeps its own words meanwhile
    expect(screen.queryByText(/being checked/)).toBeNull();
  });

  it('a check that keeps finding work stops after its round limit', async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ success: true, checked: 150, remaining: 999 })),
    );
    vi.stubGlobal('fetch', fetchMock);
    const on = handlers();
    render(
      <MirroredDetailModal
        playlistId={43}
        data={{ name: 'big', source: 'deezer', tracks: [], status_counts: { unchecked: 5000 } }}
        now={NOW}
        {...on}
      />,
    );
    await waitFor(() => expect(on.onRefresh).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(LIBRARY_CHECK_MAX_ROUNDS);
  });

  it('a failed check still refetches, and does not retry on every render', async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error('down');
    });
    vi.stubGlobal('fetch', fetchMock);
    const on = handlers();
    const view = render(
      <MirroredDetailModal
        playlistId={44}
        data={{ name: 'x', source: 'deezer', tracks: [], status_counts: { unchecked: 3 } }}
        now={NOW}
        {...on}
      />,
    );
    await waitFor(() => expect(on.onRefresh).toHaveBeenCalledTimes(1));
    view.rerender(
      <MirroredDetailModal
        playlistId={44}
        data={{ name: 'x', source: 'deezer', tracks: [], status_counts: { unchecked: 3 } }}
        now={NOW}
        {...on}
      />,
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
