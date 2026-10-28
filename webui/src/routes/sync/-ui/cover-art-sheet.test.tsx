/**
 * generate cover art: the sheet makes one on open, shows it as a preview, and
 * only "use this cover" saves it. the menu wiring lives on the detail modal.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { vibeLine } from '../../../features/playlists/cover-art';
import { CoverArtSheet, withoutRetryHint } from './cover-art-sheet';
import { MirroredDetailModal } from './mirrored-detail-modal';

type Reply = { status: number; body: Record<string, unknown> };

const PREVIEW: Reply = {
  status: 200,
  body: {
    success: true,
    preview_url: '/api/mirrored-playlists/3/cover-art/preview?v=1',
    vibe: {
      name: 'Night Drive',
      genres: ['synthwave', 'electronic'],
      moods: ['dreamy'],
      decade: 1980,
    },
    prompt: 'abstract digital painting of a retro neon grid',
    keyed: false,
  },
};

function mockFetch(replies: Record<string, Reply[]>) {
  const calls: { url: string; method: string; body?: string }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body as string | undefined });
    const key = `${method} ${url.split('?')[0]}`;
    const queue = replies[key] ?? [{ status: 200, body: { success: true } }];
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(JSON.stringify(reply.body), { status: reply.status });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

const GEN = 'POST /api/mirrored-playlists/3/cover-art';
const APPLY = 'POST /api/mirrored-playlists/3/cover-art/apply';
const NAME = 'POST /api/mirrored-playlists/3/cover-art/name';

function renderSheet() {
  const onClose = vi.fn();
  const onApplied = vi.fn();
  render(
    <CoverArtSheet
      playlistId={3}
      playlistName="Night Drive"
      onClose={onClose}
      onApplied={onApplied}
    />,
  );
  return { onClose, onApplied };
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('CoverArtSheet', () => {
  it('makes one cover on open and shows what it was made from', async () => {
    const calls = mockFetch({ [GEN]: [PREVIEW] });
    renderSheet();
    expect(screen.getByText('Painting…')).toBeInTheDocument();
    const img = await screen.findByAltText('Generated cover preview');
    expect(img).toHaveAttribute('src', '/api/mirrored-playlists/3/cover-art/preview?v=1');
    expect(screen.getByText('Made from synthwave, electronic, dreamy, 1980s')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(JSON.parse(calls[0].body ?? '{}')).toEqual({ attempt: 0, show_name: true });
  });

  it('strict mode still makes only one: a second free call inside 45s is refused', async () => {
    const calls = mockFetch({ [GEN]: [PREVIEW] });
    render(
      <StrictMode>
        <CoverArtSheet
          playlistId={3}
          playlistName="Night Drive"
          onClose={vi.fn()}
          onApplied={vi.fn()}
        />
      </StrictMode>,
    );
    await screen.findByAltText('Generated cover preview');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('use this cover saves it, refreshes the playlist and closes', async () => {
    const calls = mockFetch({
      [GEN]: [PREVIEW],
      [APPLY]: [{ status: 200, body: { image_url: '/x' } }],
    });
    const { onClose, onApplied } = renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByRole('button', { name: 'Use this cover' }));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/cover-art/apply'))).toBe(true);
    // a used preview isn't thrown away
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('closing while it saves leaves the preview to the save', async () => {
    let finish: (r: Response) => void = () => {};
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET';
        calls.push({ url, method });
        if (url.endsWith('/apply')) return new Promise<Response>((res) => (finish = res));
        return Promise.resolve(new Response(JSON.stringify(PREVIEW.body), { status: 200 }));
      }),
    );
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByRole('button', { name: 'Use this cover' }));
    fireEvent.click(screen.getByLabelText('Close'));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await act(async () =>
      finish(new Response(JSON.stringify({ image_url: '/x' }), { status: 200 })),
    );
  });

  it('closing without using it throws the preview away, the cover is untouched', async () => {
    const calls = mockFetch({ [GEN]: [PREVIEW] });
    const { onClose, onApplied } = renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalled();
    expect(onApplied).not.toHaveBeenCalled();
    const del = calls.find((c) => c.method === 'DELETE');
    expect(del?.url).toBe('/api/mirrored-playlists/3/cover-art?preview_only=1');
    expect(calls.some((c) => c.url.endsWith('/apply'))).toBe(false);
  });

  it('the free tier wait is a countdown on the button, not a dead end', async () => {
    mockFetch({ [GEN]: [PREVIEW] });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    const again = screen.getByRole('button', { name: /Try another in \d+s/ });
    expect(again).toBeDisabled();
    expect(screen.getByText(/Free images carry a small Pollinations mark/)).toBeInTheDocument();
  });

  it('a refusal shows why and counts down to the retry', async () => {
    mockFetch({
      [GEN]: [
        {
          status: 429,
          body: { error: 'Free images are one every 45 seconds. Try again in 3s.', retry_after: 3 },
        },
      ],
    });
    renderSheet();
    expect(await screen.findByText('Free images are one every 45 seconds.')).toBeInTheDocument();
    // not still "reading" once it's failed
    expect(screen.queryByText(/Reading the playlist/)).toBeNull();
    expect(screen.getByRole('button', { name: /^Try again in [1-3]s$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Use this cover' })).toBeDisabled();
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Try again' })).not.toBeDisabled(),
      { timeout: 5000 },
    );
  });

  it('try another asks for the next art style', async () => {
    const keyed = { ...PREVIEW, body: { ...PREVIEW.body, keyed: true } };
    const calls = mockFetch({ [GEN]: [keyed] });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    // a key: no wait, no mark note
    expect(screen.queryByText(/Free images carry/)).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Try another' }));
    });
    const gens = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/cover-art'));
    expect(gens.map((c) => JSON.parse(c.body ?? '{}').attempt)).toEqual([0, 1]);
  });

  it('show name is on by default and goes with the generate', async () => {
    const calls = mockFetch({ [GEN]: [PREVIEW] });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    expect(screen.getByRole('switch', { name: 'Show name on cover' })).toBeChecked();
    expect(JSON.parse(calls[0].body ?? '{}')).toEqual({ attempt: 0, show_name: true });
  });

  it('flipping show name redraws the same image instead of making a new one', async () => {
    const calls = mockFetch({
      [GEN]: [PREVIEW],
      [NAME]: [
        {
          status: 200,
          body: {
            success: true,
            preview_url: '/api/mirrored-playlists/3/cover-art/preview?v=2',
            show_name: false,
          },
        },
      ],
    });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByRole('switch', { name: 'Show name on cover' }));
    await waitFor(() =>
      expect(screen.getByAltText('Generated cover preview')).toHaveAttribute(
        'src',
        '/api/mirrored-playlists/3/cover-art/preview?v=2',
      ),
    );
    expect(screen.getByRole('switch', { name: 'Show name on cover' })).not.toBeChecked();
    expect(calls.filter((c) => `${c.method} ${c.url}` === GEN)).toHaveLength(1);
    const name = calls.find((c) => `${c.method} ${c.url}` === NAME);
    expect(JSON.parse(name?.body ?? '{}')).toEqual({ show_name: false });
  });

  it('try another keeps the show name choice', async () => {
    const keyed: Reply = { status: 200, body: { ...PREVIEW.body, keyed: true } };
    const calls = mockFetch({ [GEN]: [keyed] });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByRole('switch', { name: 'Show name on cover' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try another' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Try another' }));
    await waitFor(() =>
      expect(calls.filter((c) => `${c.method} ${c.url}` === GEN)).toHaveLength(2),
    );
    const second = calls.filter((c) => `${c.method} ${c.url}` === GEN)[1];
    expect(JSON.parse(second.body ?? '{}')).toEqual({ attempt: 1, show_name: false });
  });

  it('a failed redraw puts the switch back and says why', async () => {
    mockFetch({
      [GEN]: [PREVIEW],
      [NAME]: [{ status: 404, body: { error: 'That preview is gone. Generate a new one.' } }],
    });
    renderSheet();
    await screen.findByAltText('Generated cover preview');
    fireEvent.click(screen.getByRole('switch', { name: 'Show name on cover' }));
    expect(
      await screen.findByText('That preview is gone. Generate a new one.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Show name on cover' })).toBeChecked();
  });

  it('strips the server retry hint, the button says it', () => {
    expect(withoutRetryHint('Pollinations is busy right now. Try again in 46s.')).toBe(
      'Pollinations is busy right now.',
    );
    expect(withoutRetryHint("Couldn't reach Pollinations.")).toBe("Couldn't reach Pollinations.");
  });

  it('vibe line skips what is missing', () => {
    expect(vibeLine({ name: '', genres: [], moods: [], decade: null })).toBe('');
    expect(vibeLine({ name: '', genres: ['a', 'b', 'c', 'd'], moods: ['m'], decade: 2020 })).toBe(
      'a, b, c, m, 2020s',
    );
  });
});

describe('MirroredDetailModal cover art menu', () => {
  const handlers = () => ({
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onEditSource: vi.fn(),
    onRunPipeline: vi.fn(),
    onDiscover: vi.fn(),
    onRefresh: vi.fn(),
  });

  it('every playlist gets generate cover art, and it opens the sheet', async () => {
    mockFetch({ [GEN]: [PREVIEW] });
    render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'Night Drive', source: 'spotify', tracks: [] }}
        now={0}
        {...handlers()}
      />,
    );
    fireEvent.click(screen.getByLabelText('More actions'));
    expect(screen.queryByRole('menuitem', { name: /Restore original cover/ })).toBeNull();
    fireEvent.click(screen.getByRole('menuitem', { name: /Generate cover art/ }));
    expect(await screen.findByRole('dialog', { name: 'Cover art' })).toBeInTheDocument();
  });

  it("a mirror with a generated cover can go back to the source's", async () => {
    const calls = mockFetch({});
    const h = handlers();
    render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'Night Drive', source: 'spotify', custom_cover: true, tracks: [] }}
        now={0}
        {...h}
      />,
    );
    fireEvent.click(screen.getByLabelText('More actions'));
    const item = screen.getByRole('menuitem', { name: /Restore original cover/ });
    expect(item).toHaveTextContent("back to Spotify's cover");
    fireEvent.click(item);
    await waitFor(() => expect(h.onRefresh).toHaveBeenCalled());
    expect(calls.find((c) => c.method === 'DELETE')?.url).toBe(
      '/api/mirrored-playlists/3/cover-art',
    );
  });

  it('a user playlist has no original to restore', () => {
    mockFetch({});
    render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'Mine', source: 'soulsync', custom_cover: true, tracks: [] }}
        now={0}
        {...handlers()}
      />,
    );
    fireEvent.click(screen.getByLabelText('More actions'));
    expect(screen.getByRole('menuitem', { name: /Generate cover art/ })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /Restore original cover/ })).toBeNull();
  });
});
