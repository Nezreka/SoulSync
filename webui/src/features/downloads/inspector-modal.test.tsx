import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { InspectorTarget } from './inspector-modal';

import {
  CandidateInspectorHost,
  CandidateInspectorModal,
  closeCandidateInspector,
  openDownloadTaskInspector,
  openWishlistInspector,
} from './inspector-modal';

/** The standalone candidate inspector (wishlist, failed downloads). */

function ndjson(lines: unknown[]) {
  return new Response(lines.map((l) => `${JSON.stringify(l)}\n`).join(''));
}

const ACCEPTED = {
  display_name: 'Fade Into You.flac',
  filename: 'Mazzy Star/So Tonight/04 - Fade Into You.flac',
  username: 'vinylhead',
  confidence: 0.97,
  size: 33_000_000,
  size_display: '31.5 MB',
  quality: 'FLAC',
  decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.97 },
};
const TORRENT = {
  display_name: 'So Tonight That I Might See [FLAC]',
  filename: 't||1',
  username: 'torrent',
  confidence: 0.99,
  size_display: '400 MB',
  decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.99 },
};
const LIVE = {
  display_name: 'Fade Into You (Live).flac',
  filename: 'x/live.flac',
  username: 'bootlegz',
  confidence: 0.2,
  size_display: '30 MB',
  decision: {
    accepted: false,
    code: 'version_conflict',
    stage: 'version',
    detail: 'live version, asked for the original',
    score: 0.2,
  },
};

function stream(...lines: unknown[]) {
  return vi.fn(async () => ndjson([...lines, { done: true }]));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
  act(() => closeCandidateInspector());
});

function target(over: Partial<InspectorTarget> = {}): InspectorTarget {
  return {
    name: 'Fade Into You',
    artist: 'Mazzy Star',
    album: 'So Tonight That I Might See',
    searchUrl: '/api/wishlist/inspect',
    searchBody: { track_id: 'sp-fade' },
    onGrab: vi.fn(async () => {}),
    ...over,
  };
}

describe('CandidateInspectorModal', () => {
  it('streams every source, preselects the best, and grabs it', async () => {
    const fetchSpy = stream({
      source: 'soulseek',
      candidates: [ACCEPTED],
      rejected: [LIVE],
      rejected_total: 1,
      rejected_counts: { version_conflict: 1 },
    });
    vi.stubGlobal('fetch', fetchSpy);
    window.showToast = vi.fn();
    const onClose = vi.fn();
    const t = target();
    render(<CandidateInspectorModal target={t} onClose={onClose} />);

    await screen.findByText('Fade Into You.flac');
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/wishlist/inspect',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ track_id: 'sp-fade' }) }),
    );
    expect(screen.getByText('Best')).toBeTruthy();
    expect(screen.getByText('Show 1 rejected')).toBeTruthy();

    fireEvent.click(screen.getByText('Download Selected'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(t.onGrab).toHaveBeenCalledWith(expect.objectContaining({ username: 'vinylhead' }));
    expect(window.showToast).toHaveBeenCalledWith('Downloading Fade Into You.flac', 'success');
  });

  it('never preselects or grabs a hit this surface refuses', async () => {
    vi.stubGlobal(
      'fetch',
      stream({ source: 'torrent', candidates: [TORRENT], rejected: [], rejected_total: 0 }),
    );
    const t = target({ cannotGrab: (c) => (c.username === 'torrent' ? 'whole release' : null) });
    render(<CandidateInspectorModal target={t} onClose={vi.fn()} />);
    await screen.findByText('So Tonight That I Might See [FLAC]');
    expect(document.querySelector('input[type="radio"]')).toBeNull();
    expect(screen.getByText("Can't pick here")).toBeTruthy();
    const button = screen.getByText('No match to download') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('keeps the modal open and says why when the start fails', async () => {
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [ACCEPTED] }));
    const onClose = vi.fn();
    const t = target({
      onGrab: vi.fn(async () => {
        throw new Error('That track isn’t on your wishlist');
      }),
    });
    render(<CandidateInspectorModal target={t} onClose={onClose} />);
    await screen.findByText('Fade Into You.flac');
    fireEvent.click(screen.getByText('Download Selected'));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert').textContent).toContain('isn’t on your wishlist');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows the server error instead of an empty column', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: false, error: 'Task not found' }), {
            status: 404,
          }),
      ),
    );
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    await screen.findByText('Error: Task not found');
  });

  it('a rejected row grabbed anyway goes through the confirm', async () => {
    vi.stubGlobal(
      'fetch',
      stream({ source: 'soulseek', candidates: [], rejected: [LIVE], rejected_total: 1 }),
    );
    window.showConfirmDialog = vi.fn(async () => true);
    const t = target();
    render(<CandidateInspectorModal target={t} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText('Show 1 rejected'));
    fireEvent.click(screen.getByText('Grab anyway'));
    await waitFor(() => expect(t.onGrab).toHaveBeenCalled());
    expect(window.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ destructive: true }),
    );
  });

  it('shows the length the server judged against in the wanted column', async () => {
    vi.stubGlobal(
      'fetch',
      stream({
        source: 'tidal',
        expected_duration_ms: 212_000,
        candidates: [],
        rejected: [{ ...LIVE, duration: 206_000 }],
        rejected_total: 1,
      }),
    );
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText('Show 1 rejected'));
    fireEvent.click(screen.getByText('Why?'));
    const row = screen.getByText('Length').closest('tr')!;
    expect(row.textContent).toContain('3:32');
    expect(row.textContent).toContain('3:26');
  });

  it('a retyped title and artist search again, and the pick downloads as typed', async () => {
    const fetchSpy = stream({ source: 'soulseek', candidates: [ACCEPTED] });
    vi.stubGlobal('fetch', fetchSpy);
    const t = target({ editable: true });
    render(<CandidateInspectorModal target={t} onClose={vi.fn()} />);
    await screen.findByText('Fade Into You.flac');
    fireEvent.click(screen.getByText('Edit search'));
    fireEvent.change(screen.getByLabelText('Artist'), { target: { value: 'Hope Sandoval' } });
    fireEvent.click(screen.getByText('Search'));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    const [, init] = fetchSpy.mock.calls[1] as unknown as [string, { body: string }];
    const body = JSON.parse(init.body);
    expect(body).toEqual({ track_id: 'sp-fade', name: 'Fade Into You', artist: 'Hope Sandoval' });
    expect(await screen.findByText('Hope Sandoval · downloads as typed')).toBeTruthy();

    await screen.findByText('Fade Into You.flac');
    fireEvent.click(screen.getByText('Download Selected'));
    await waitFor(() =>
      expect(t.onGrab).toHaveBeenCalledWith(expect.anything(), {
        name: 'Fade Into You',
        artist: 'Hope Sandoval',
      }),
    );
  });

  it('has no edit button unless the target allows it', async () => {
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [ACCEPTED] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    await screen.findByText('Fade Into You.flac');
    expect(screen.queryByText('Edit search')).toBeNull();
  });

  it('shows the inspection date and the ladder rung from the streamed payload', async () => {
    vi.stubGlobal(
      'fetch',
      stream({
        source: 'soulseek',
        candidates: [ACCEPTED],
        provenance: {
          search_mode: 'interactive',
          searched_at: '2026-09-26 15:00:00',
          policy_run_id: 'abc123def456',
        },
        policy: {
          target_index: 0,
          target_label: 'FLAC 24-bit/96kHz',
          target_count: 3,
          tier_score: null,
          fallback_enabled: true,
        },
      }),
    );
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);

    await screen.findByText('Interactive Search');
    expect(
      screen.getByText('Searched 2026-09-26 · ladder rung 1 of 3: FLAC 24-bit/96kHz'),
    ).toBeTruthy();
  });

  it("shows each candidate's ladder rung from its row policy facet", async () => {
    const WITH_RUNG = {
      ...ACCEPTED,
      policy: {
        target_index: 1,
        target_label: 'FLAC 16-bit',
        target_count: 3,
        tier_score: 0.8,
        fallback_enabled: true,
      },
    };
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [WITH_RUNG, ACCEPTED] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);

    const chip = await screen.findByText('rung 2/3');
    expect(chip.getAttribute('title')).toBe('Ladder rung: FLAC 16-bit');
    // the row without a facet renders no rung chip
    expect(screen.queryAllByText(/rung \d+\/\d+/)).toHaveLength(1);
  });
});

describe('the host and its entry points', () => {
  it('opens and closes through the shared host', async () => {
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [ACCEPTED] }));
    render(<CandidateInspectorHost />);
    expect(screen.queryByText('Interactive Search')).toBeNull();
    act(() =>
      openWishlistInspector({ id: 'sp-fade', name: 'Fade Into You', artist: 'Mazzy Star' }),
    );
    await screen.findByText('Interactive Search');
    fireEvent.click(screen.getByLabelText('Close'));
    expect(screen.queryByText('Interactive Search')).toBeNull();
  });

  it('wishlist picks post the track, the file and any override', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        calls.push({ url, body: JSON.parse(typeof init?.body === 'string' ? init.body : '{}') });
        if (url === '/api/wishlist/inspect') {
          return ndjson([
            {
              source: 'soulseek',
              candidates: [],
              rejected: [
                {
                  ...LIVE,
                  decision: { ...LIVE.decision, code: 'below_profile', stage: 'quality' },
                },
              ],
              rejected_total: 1,
            },
            { done: true },
          ]);
        }
        return new Response(JSON.stringify({ success: true, batch_id: 'b', task_id: 't' }));
      }),
    );
    window.showConfirmDialog = vi.fn(async () => true);
    window.updateWishlistCount = vi.fn();
    render(<CandidateInspectorHost />);
    act(() => openWishlistInspector({ id: 'sp-fade', name: 'Fade Into You' }));
    fireEvent.click(await screen.findByText('Show 1 rejected'));
    fireEvent.click(screen.getByText('Grab anyway'));
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/wishlist/inspect/download')).toBe(true),
    );
    const start = calls.find((c) => c.url === '/api/wishlist/inspect/download')!;
    expect(start.body).toMatchObject({
      track_id: 'sp-fade',
      candidate: { username: 'bootlegz', filename: 'x/live.flac' },
      override: { code: 'below_profile', stage: 'quality' },
    });
    expect(window.updateWishlistCount).toHaveBeenCalled();
    delete window.updateWishlistCount;
  });

  it('a failed download retries with the pick on its own task', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        calls.push({ url, body: JSON.parse(typeof init?.body === 'string' ? init.body : '{}') });
        if (url.endsWith('/inspect')) {
          return ndjson([{ source: 'soulseek', candidates: [ACCEPTED] }, { done: true }]);
        }
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    render(<CandidateInspectorHost />);
    act(() => openDownloadTaskInspector('task 1', { name: 'Fade Into You' }));
    await screen.findByText('Fade Into You.flac');
    fireEvent.click(screen.getByText('Download Selected'));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0].url).toBe('/api/downloads/task/task%201/inspect');
    expect(calls[1].url).toBe('/api/downloads/task/task%201/download-candidate');
    expect(calls[1].body).toMatchObject({
      username: 'vinylhead',
      filename: 'Mazzy Star/So Tonight/04 - Fade Into You.flac',
      size: 33_000_000,
    });
    expect(calls[1].body.override).toBeUndefined();
  });
});

describe('row name hover text', () => {
  it('shows the song name, not the dispatch key, for a keyed row', async () => {
    const KEYED = {
      display_name: 'Christmas in July',
      filename: '1207415400||https://soundcloud.com/u/christmas-in-july||Christmas in July',
      username: 'soundcloud',
      source_service: 'soundcloud',
      confidence: 0.8,
      size_display: '0.0 MB',
      decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.8 },
    };
    vi.stubGlobal('fetch', stream({ source: 'soundcloud', candidates: [KEYED] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    const name = await screen.findByText('Christmas in July');
    expect(name.getAttribute('title')).toBe('Christmas in July');
  });

  it('still shows the full remote path for a peer row whose folder contains ||', async () => {
    const PEER_KEYED_PATH = {
      ...ACCEPTED,
      filename: 'Music||Flac/04 - Fade Into You.flac',
      source_service: 'soulseek',
    };
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [PEER_KEYED_PATH] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    const name = await screen.findByText('Fade Into You.flac');
    expect(name.getAttribute('title')).toBe('Music||Flac/04 - Fade Into You.flac');
  });

  it('still shows the full remote path for a peer row', async () => {
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [ACCEPTED] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    const name = await screen.findByText('Fade Into You.flac');
    expect(name.getAttribute('title')).toBe('Mazzy Star/So Tonight/04 - Fade Into You.flac');
  });
});

describe('row name hover for release sources', () => {
  it('shows the release title, not the store token, whatever the source label says', async () => {
    const RELEASE = {
      display_name: 'So Tonight That I Might See [FLAC]',
      filename: 'tok123||So Tonight That I Might See [FLAC]',
      username: 'torrent',
      source_service: 'hybrid',
      confidence: 0.9,
      size_display: '400 MB',
      decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.9 },
    };
    vi.stubGlobal('fetch', stream({ source: 'torrent', candidates: [RELEASE] }));
    render(<CandidateInspectorModal target={target()} onClose={vi.fn()} />);
    const name = await screen.findByText('So Tonight That I Might See [FLAC]');
    expect(name.getAttribute('title')).toBe('So Tonight That I Might See [FLAC]');
  });
});
