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
});

describe('the host and its entry points', () => {
  it('opens and closes through the shared host', async () => {
    vi.stubGlobal('fetch', stream({ source: 'soulseek', candidates: [ACCEPTED] }));
    render(<CandidateInspectorHost />);
    expect(screen.queryByText('Search every source')).toBeNull();
    act(() =>
      openWishlistInspector({ id: 'sp-fade', name: 'Fade Into You', artist: 'Mazzy Star' }),
    );
    await screen.findByText('Search every source');
    fireEvent.click(screen.getByLabelText('Close'));
    expect(screen.queryByText('Search every source')).toBeNull();
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
