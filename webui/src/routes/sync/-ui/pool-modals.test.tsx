/**
 * The two pool modals and the fix/rematch sub-modal, rendered against a
 * captured fetch (stats-automations.js 1217-2022).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DiscoveryPoolModal } from './discovery-pool-modal';
import { PoolFixModal } from './pool-fix-modal';
import { WingItPoolModal } from './wingit-pool-modal';

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
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      const data = responder(url, method);
      if (data instanceof Error) throw data;
      return new Response(JSON.stringify(data));
    }),
  );
}

const DISCOVERY = {
  stats: { matched: 2, failed: 1 },
  playlists: [{ id: 3, name: 'Road Trip' }],
  failed: [{ id: 11, track_name: 'Ghost', artist_name: 'Nobody', playlist_name: 'Road Trip' }],
  matched: [
    {
      id: 21,
      original_title: 'Blue Monday',
      original_artist: 'New Order',
      provider: 'spotify',
      confidence: 0.92,
      use_count: 4,
      matched_data: { name: 'Blue Monday (Remaster)', image_url: 'a.jpg' },
    },
    {
      id: 22,
      original_title: 'Temptation',
      original_artist: 'New Order',
      provider: 'spotify',
      confidence: 0.65,
      use_count: 1,
      matched_data: { name: 'Temptation' },
    },
  ],
};

const WINGIT = {
  playlists: [{ id: 3, name: 'Road Trip' }],
  tracks: [{ id: 31, track_name: 'Guessed', artist_name: 'Someone', playlist_name: 'Road Trip' }],
  matched: [
    {
      id: 32,
      track_name: 'Fixed',
      artist_name: 'Someone',
      playlist_name: 'Road Trip',
      extra_data: JSON.stringify({ matched_data: { name: 'Fixed (Real)' } }),
    },
  ],
};

beforeEach(() => {
  stubFetch();
  window.showToast = vi.fn() as typeof window.showToast;
  window.showConfirmDialog = vi.fn(async () => true) as typeof window.showConfirmDialog;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
});

describe('DiscoveryPoolModal', () => {
  const open = async () => {
    responder = (url) => (url.includes('/cache/') ? { success: true } : DISCOVERY);
    const onClose = vi.fn();
    render(<DiscoveryPoolModal onClose={onClose} />);
    await waitFor(() => expect(screen.getByText('2 Matched')).toBeInTheDocument());
    return onClose;
  };

  it('opens on the category grid with counts from `stats`', async () => {
    await open();
    expect(calls[0].url).toBe('/api/discovery-pool');
    expect(screen.getByText('1 Failed')).toBeInTheDocument();
    expect(document.querySelector('#pool-header-failed')!.className).toContain(
      'pool-header-failed-highlight',
    );
    expect(screen.getByText('tracks need attention')).toBeInTheDocument();
    expect(screen.getByText('cached matches')).toBeInTheDocument();
    // The list view is not rendered until a card is opened.
    expect(document.querySelector('.pool-list-view')).toBeNull();
  });

  it('under four covers the matched card keeps its flat gradient', async () => {
    await open();
    expect(document.querySelector('.wishlist-mosaic-background')).toBeNull();
    expect(document.querySelector('#pool-matched-bg')!.className).toBe(
      'pool-category-fallback matched',
    );
  });

  it('four or more covers build the mosaic instead', async () => {
    responder = () => ({
      ...DISCOVERY,
      matched: ['a', 'b', 'c', 'd', 'e'].map((u, i) => ({
        id: i,
        matched_data: { image_url: `${u}.jpg` },
      })),
    });
    render(<DiscoveryPoolModal onClose={vi.fn()} />);
    await waitFor(() =>
      expect(document.querySelector('.wishlist-mosaic-background')).not.toBeNull(),
    );
    expect(document.querySelectorAll('.wishlist-mosaic-row-wrapper')).toHaveLength(4);
    // ceil(5/4) * 2 tiles per row
    expect(document.querySelectorAll('.wishlist-mosaic-row')[0].children).toHaveLength(4);
  });

  it('the failed list offers a Fix Match per row and filters live', async () => {
    await open();
    fireEvent.click(screen.getByText('tracks need attention'));
    expect(screen.getByText('Failed Tracks')).toBeInTheDocument();
    expect(screen.getByText('Ghost')).toBeInTheDocument();
    expect(screen.getByText('Fix Match')).toBeInTheDocument();

    fireEvent.change(document.querySelector('.pool-list-search')!, {
      target: { value: 'zzz' },
    });
    expect(screen.getByText('No failed tracks match your filter.')).toBeInTheDocument();
    // The playlist name is a search field too.
    fireEvent.change(document.querySelector('.pool-list-search')!, {
      target: { value: 'road' },
    });
    expect(screen.getByText('Ghost')).toBeInTheDocument();
  });

  it('the matched list shows cover, confidence band, use count and provider', async () => {
    await open();
    fireEvent.click(screen.getByText('cached matches'));
    expect(screen.getByText('Matched Tracks')).toBeInTheDocument();
    const rows = [...document.querySelectorAll('.pool-track-row.pool-matched')];
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.pool-match-image')!.getAttribute('src')).toBe('a.jpg');
    expect(rows[0].querySelector('.pool-confidence-badge')!.textContent).toBe('92%');
    expect(rows[0].querySelector('.pool-confidence-badge')!.className).toContain('high');
    expect(rows[0].querySelector('.pool-use-count')!.textContent).toBe('4×');
    expect(rows[0].querySelector('.pool-match-provider')!.textContent).toBe('spotify');
    // No image_url and no album → the placeholder, and a low band.
    expect(rows[1].querySelector('.pool-match-image')).toBeNull();
    expect(rows[1].querySelector('.pool-match-image-placeholder')).not.toBeNull();
    expect(rows[1].querySelector('.pool-confidence-badge')!.className).toContain('low');
  });

  it('switching views CLEARS the search box (1610-1611)', async () => {
    await open();
    fireEvent.click(screen.getByText('tracks need attention'));
    fireEvent.change(document.querySelector('.pool-list-search')!, { target: { value: 'gho' } });
    fireEvent.click(screen.getByText('← Back'));
    fireEvent.click(screen.getByText('cached matches'));
    expect((document.querySelector('.pool-list-search') as HTMLInputElement).value).toBe('');
  });

  it('removing a cache entry confirms, DELETEs, and refetches', async () => {
    await open();
    fireEvent.click(screen.getByText('cached matches'));
    fireEvent.click(document.querySelectorAll('.pool-remove-btn')[0]);
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/discovery-pool/cache/21')).toBe(true),
    );
    expect(window.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Remove Cache Entry',
      message: 'Remove this cached match? The track will be re-discovered fresh next time.',
    });
    expect(calls.find((c) => c.url === '/api/discovery-pool/cache/21')!.method).toBe('DELETE');
    expect(window.showToast).toHaveBeenCalledWith('Cache entry removed', 'success');
  });

  it('the matched list filters too, on the original pair AND the matched name', async () => {
    await open();
    fireEvent.click(screen.getByText('cached matches'));
    expect(document.querySelectorAll('.pool-track-row.pool-matched')).toHaveLength(2);

    // Scoped to the row's own title — row 22's matched name is 'Temptation'
    // too, so a bare getByText would match twice.
    const titles = () =>
      [...document.querySelectorAll('.pool-track-row.pool-matched .pool-track-name')].map(
        (n) => n.textContent,
      );

    fireEvent.change(document.querySelector('.pool-list-search')!, {
      target: { value: 'temptation' },
    });
    expect(titles()).toEqual(['Temptation']);

    // ...and on the MATCHED name, which only the first row carries.
    fireEvent.change(document.querySelector('.pool-list-search')!, {
      target: { value: 'remaster' },
    });
    expect(titles()).toEqual(['Blue Monday']);

    fireEvent.change(document.querySelector('.pool-list-search')!, { target: { value: 'zzz' } });
    expect(screen.getByText('No matched tracks match your filter.')).toBeInTheDocument();
  });

  it('the playlist filter refetches with the id', async () => {
    await open();
    fireEvent.change(document.querySelector('.pool-playlist-filter')!, { target: { value: '3' } });
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/discovery-pool?playlist_id=3')).toBe(true),
    );
  });

  it('a first-load failure toasts and never opens; a filter failure keeps it open', async () => {
    responder = () => new Error('offline');
    const onClose = vi.fn();
    render(<DiscoveryPoolModal onClose={onClose} />);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to load discovery pool', 'error'),
    );
    expect(onClose).toHaveBeenCalled();

    // Now the other arm: open cleanly, then fail a filter.
    const second = await open();
    responder = () => new Error('offline');
    fireEvent.change(document.querySelector('.pool-playlist-filter')!, { target: { value: '3' } });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to filter discovery pool', 'error'),
    );
    expect(second).not.toHaveBeenCalled();
  });

  it('Close and the backdrop both dismiss', async () => {
    const onClose = await open();
    fireEvent.click(screen.getByText('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(document.querySelector('#discovery-pool-overlay')!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe('DiscoveryPoolModal — the row → fix-modal hand-off', () => {
  // The ids are the whole point here: a FAILED row hands a mirrored TRACK id
  // to /discovery-pool/fix, a MATCHED row hands a CACHE id to
  // /discovery-pool/rematch. Same-looking buttons, different ids, different
  // endpoints — so both are driven end to end from the row that owns them.
  const SEARCH_HIT = { name: 'Blue Monday', artists: ['New Order'] };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    responder = (url) => {
      if (url.includes('search_tracks')) return { tracks: [SEARCH_HIT] };
      if (url.includes('/discovery-pool/fix') || url.includes('/discovery-pool/rematch')) {
        return { success: true };
      }
      return DISCOVERY;
    };
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const openPool = async () => {
    render(<DiscoveryPoolModal onClose={vi.fn()} />);
    await act(async () => {});
    await waitFor(() => expect(screen.getByText('2 Matched')).toBeInTheDocument());
  };

  const chooseResult = async () => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    await act(async () => {
      fireEvent.click(document.querySelector('.pool-fix-result')!);
    });
  };

  it('Fix Match on a failed row carries THAT row’s track id', async () => {
    await openPool();
    fireEvent.click(screen.getByText('tracks need attention'));
    fireEvent.click(screen.getByText('Fix Match'));
    // The sub-modal opens prefilled from the row.
    expect(screen.getByText('Fix Track Match')).toBeInTheDocument();
    expect((document.querySelector('#pool-fix-track-input') as HTMLInputElement).value).toBe(
      'Ghost',
    );
    await chooseResult();
    const post = calls.find((c) => c.url === '/api/discovery-pool/fix')!;
    expect(post.body).toEqual({ track_id: 11, spotify_track: SEARCH_HIT });
  });

  it('Rematch on the SECOND matched row carries that row’s cache id', async () => {
    await openPool();
    fireEvent.click(screen.getByText('cached matches'));
    // Deliberately the second row, so an off-by-one lands on a real id.
    fireEvent.click(document.querySelectorAll('.pool-rematch-btn')[1]);
    expect(screen.getByText('Rematch Track')).toBeInTheDocument();
    await chooseResult();
    const post = calls.find((c) => c.url === '/api/discovery-pool/rematch')!;
    expect(post.body).toEqual({
      cache_id: 22,
      original_title: 'Temptation',
      original_artist: 'New Order',
      spotify_track: SEARCH_HIT,
    });
  });

  it('a committed fix refetches the pool', async () => {
    await openPool();
    fireEvent.click(screen.getByText('tracks need attention'));
    fireEvent.click(screen.getByText('Fix Match'));
    const before = calls.filter((c) => c.url === '/api/discovery-pool').length;
    await chooseResult();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/discovery-pool').length).toBe(before + 1),
    );
    // ...and the sub-modal is gone.
    expect(document.querySelector('#pool-fix-overlay')).toBeNull();
  });
});

describe('WingItPoolModal', () => {
  const open = async () => {
    responder = () => WINGIT;
    const onClose = vi.fn();
    render(<WingItPoolModal onClose={onClose} />);
    await waitFor(() => expect(screen.getByText('1 to review')).toBeInTheDocument());
    return onClose;
  };

  it('counts from ARRAY LENGTHS, with its own card copy', async () => {
    await open();
    expect(calls[0].url).toBe('/api/wing-it-pool');
    expect(screen.getByText('1 resolved')).toBeInTheDocument();
    expect(screen.getByText('guesses to review')).toBeInTheDocument();
    expect(screen.getByText('resolved manually')).toBeInTheDocument();
    // NEVER a mosaic on this side.
    expect(document.querySelector('.wishlist-mosaic-background')).toBeNull();
  });

  it('the attention list offers Fix Match; the matched list offers Re-match', async () => {
    await open();
    fireEvent.click(screen.getByText('guesses to review'));
    expect(screen.getByText('⚡ Guesses to review')).toBeInTheDocument();
    expect(screen.getByText('Fix Match')).toBeInTheDocument();
    expect(screen.queryByText('Re-match')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('← Back'));
    fireEvent.click(screen.getByText('resolved manually'));
    expect(screen.getByText('✓ Resolved Wing It guesses')).toBeInTheDocument();
    expect(screen.getByText('Re-match')).toBeInTheDocument();
    // The matched name comes out of the JSON STRING in extra_data.
    expect(screen.getByText('Fixed (Real)')).toBeInTheDocument();
  });

  it('shares ONE filtered-empty message across both lists', async () => {
    await open();
    fireEvent.click(screen.getByText('guesses to review'));
    fireEvent.change(document.querySelector('.pool-list-search')!, { target: { value: 'zzz' } });
    expect(screen.getByText('No tracks match your filter.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('← Back'));
    fireEvent.click(screen.getByText('resolved manually'));
    fireEvent.change(document.querySelector('.pool-list-search')!, { target: { value: 'zzz' } });
    expect(screen.getByText('No tracks match your filter.')).toBeInTheDocument();
  });

  it('a truly empty list gets its own message per view', async () => {
    responder = () => ({ playlists: [], tracks: [], matched: [] });
    render(<WingItPoolModal onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('0 to review')).toBeInTheDocument());
    fireEvent.click(screen.getByText('guesses to review'));
    expect(screen.getByText('No Wing It guesses to review.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('← Back'));
    fireEvent.click(screen.getByText('resolved manually'));
    expect(
      screen.getByText(
        'No resolved Wing It tracks yet — ones you Fix here will land in this list.',
      ),
    ).toBeInTheDocument();
  });
});

describe('PoolFixModal', () => {
  const TRACK = {
    name: 'Blue Monday',
    artists: ['New Order'],
    album: 'Power',
    duration_ms: 270000,
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const openFix = async (over: Record<string, unknown> = {}) => {
    responder = (url) => (url.includes('search_tracks') ? { tracks: [TRACK] } : { success: true });
    const onClose = vi.fn();
    const onMatched = vi.fn();
    render(
      <PoolFixModal
        target={{ mode: 'fix', trackId: 11, trackName: 'Ghost', artistName: 'Nobody', ...over }}
        onClose={onClose}
        onMatched={onMatched}
      />,
    );
    return { onClose, onMatched };
  };

  it('prefills both inputs and auto-searches after 500ms', async () => {
    const handles = await openFix();
    expect(handles.onClose).not.toHaveBeenCalled();
    expect((document.querySelector('#pool-fix-track-input') as HTMLInputElement).value).toBe(
      'Ghost',
    );
    expect((document.querySelector('#pool-fix-artist-input') as HTMLInputElement).value).toBe(
      'Nobody',
    );
    expect(screen.getByText('Searching...')).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('search_tracks'))).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(calls.some((c) => c.url.includes('search_tracks'))).toBe(true);
    expect(screen.getByText('Blue Monday')).toBeInTheDocument();
    expect(screen.getByText('New Order · Power')).toBeInTheDocument();
    expect(screen.getByText('4:30')).toBeInTheDocument();
  });

  it('a fix POSTs the TRACK id and reports back', async () => {
    const { onClose, onMatched } = await openFix();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Blue Monday'));
    });
    expect(window.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Confirm Match',
      message: 'Match to "Blue Monday" by New Order?',
      confirmText: 'Confirm',
    });
    const post = calls.find((c) => c.url === '/api/discovery-pool/fix')!;
    expect(post.body).toEqual({ track_id: 11, spotify_track: TRACK });
    expect(window.showToast).toHaveBeenCalledWith('Matched: Blue Monday', 'success');
    expect(onClose).toHaveBeenCalled();
    expect(onMatched).toHaveBeenCalled();
  });

  it('a rematch POSTs the CACHE id and the original pair instead', async () => {
    await openFix({
      mode: 'rematch',
      cacheId: 21,
      originalTitle: 'Blue Monday',
      originalArtist: 'New Order',
      trackName: 'Blue Monday',
      artistName: 'New Order',
    });
    expect(screen.getByText('Rematch Track')).toBeInTheDocument();
    expect(screen.getByText('Current Match')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    await act(async () => {
      fireEvent.click(document.querySelector('.pool-fix-result')!);
    });
    const post = calls.find((c) => c.url === '/api/discovery-pool/rematch')!;
    expect(post.body).toEqual({
      cache_id: 21,
      original_title: 'Blue Monday',
      original_artist: 'New Order',
      spotify_track: TRACK,
    });
    expect(calls.some((c) => c.url === '/api/discovery-pool/fix')).toBe(false);
  });

  it('the fix mode has its own heading and label', async () => {
    await openFix();
    expect(screen.getByText('Fix Track Match')).toBeInTheDocument();
    expect(screen.getByText('Original Track')).toBeInTheDocument();
  });

  it('a declined confirm posts nothing', async () => {
    window.showConfirmDialog = vi.fn(async () => false) as typeof window.showConfirmDialog;
    await openFix();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Blue Monday'));
    });
    expect(calls.some((c) => c.url.includes('/discovery-pool/fix'))).toBe(false);
  });

  it('surfaces a real search failure instead of "No results found"', async () => {
    responder = () => ({ error: 'spotify not authenticated' });
    render(
      <PoolFixModal
        target={{ mode: 'fix', trackId: 11, trackName: 'Ghost', artistName: '' }}
        onClose={vi.fn()}
        onMatched={vi.fn()}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(screen.getByText('Search error: spotify not authenticated')).toBeInTheDocument();
  });

  it('an empty result set says so, and an empty query asks for one', async () => {
    responder = () => ({ tracks: [] });
    render(
      <PoolFixModal
        target={{ mode: 'fix', trackId: 11, trackName: 'Ghost', artistName: '' }}
        onClose={vi.fn()}
        onMatched={vi.fn()}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(screen.getByText('No results found')).toBeInTheDocument();

    fireEvent.change(document.querySelector('#pool-fix-track-input')!, { target: { value: '  ' } });
    await act(async () => {
      fireEvent.click(screen.getByText('Search'));
    });
    expect(screen.getByText('Enter a search term')).toBeInTheDocument();
  });

  it('Enter in either input searches', async () => {
    await openFix();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    const before = calls.filter((c) => c.url.includes('search_tracks')).length;
    await act(async () => {
      fireEvent.keyDown(document.querySelector('#pool-fix-artist-input')!, { key: 'Enter' });
    });
    expect(calls.filter((c) => c.url.includes('search_tracks')).length).toBe(before + 1);
  });

  it('Cancel and the backdrop close it; a mousedown inside does not', async () => {
    const { onClose } = await openFix();
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(document.querySelector('#pool-fix-overlay')!);
    expect(onClose).toHaveBeenCalledTimes(2);
    // The dialog stops the event itself, so nothing inside can dismiss —
    // including a stray mousedown on the header or an input.
    fireEvent.mouseDown(document.querySelector('.pool-fix-modal')!);
    fireEvent.mouseDown(document.querySelector('#pool-fix-track-input')!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
