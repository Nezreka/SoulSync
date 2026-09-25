import { fireEvent, render, screen, act, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { resetPendingArtworkWatchers } from './artwork-pending';
import { Artwork } from './library-v2-page';

/** rev25-02: a cold cover 404s while the server builds it in the background.
 *  The client used to answer with three fixed retries (14.5s total) that
 *  regularly expired before the build finished, leaving a resolvable cover as
 *  a placeholder until the next full page load. The wait is now driven by the
 *  server's own build state. */
describe('Artwork background-build delivery (rev25-02)', () => {
  const statusCalls: string[] = [];

  beforeEach(() => {
    statusCalls.length = 0;
    resetPendingArtworkWatchers();
  });
  afterEach(() => {
    resetPendingArtworkWatchers();
    vi.useRealTimers();
  });

  function serveStatus(sequence: Array<Record<string, unknown>>) {
    let call = 0;
    server.use(
      http.get('/api/library/v2/artwork/status', ({ request }) => {
        statusCalls.push(new URL(request.url).search);
        const states = sequence[Math.min(call, sequence.length - 1)];
        call += 1;
        return HttpResponse.json({ success: true, states });
      }),
    );
  }

  it('renders the cover once the server reports the build finished', async () => {
    serveStatus([{ '7': { state: 'pending' } }, { '7': { state: 'ready', version: 1234 } }]);

    render(<Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />);
    fireEvent.error(screen.getByAltText('Artist'));

    // While the background build runs the user sees the placeholder, not a
    // broken image.
    expect(screen.getByLabelText('Artist').textContent).toBe('♪');

    await waitFor(
      () => {
        const image = screen.queryByAltText('Artist') as HTMLImageElement | null;
        expect(image?.getAttribute('src')).toBe('/api/library/v2/artwork/artist/7?v=1234');
      },
      { timeout: 15000 },
    );
  }, 20000);

  it('stops waiting when the server says there is nothing to build', async () => {
    serveStatus([{ '3': { state: 'unavailable' } }]);

    render(<Artwork src="/api/library/v2/artwork/album/3" alt="Album" className="c" />);
    fireEvent.error(screen.getByAltText('Album'));

    await waitFor(() => expect(statusCalls.length).toBe(1), { timeout: 15000 });
    // A settled "nothing to wait for" must not keep polling.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(statusCalls.length).toBe(1);
    expect(screen.queryByAltText('Album')).toBeNull();
    expect(screen.getByLabelText('Album').textContent).toBe('♪');
  }, 20000);

  it('asks for every pending cover in one batched request', async () => {
    serveStatus([{}]);

    render(
      <>
        <Artwork src="/api/library/v2/artwork/artist/7" alt="A" className="c" />
        <Artwork src="/api/library/v2/artwork/artist/8" alt="B" className="c" />
      </>,
    );
    fireEvent.error(screen.getByAltText('A'));
    fireEvent.error(screen.getByAltText('B'));

    await waitFor(() => expect(statusCalls.length).toBeGreaterThan(0), { timeout: 15000 });
    expect(statusCalls[0]).toContain('ids=7%2C8');
  }, 20000);

  it('does not poll for remote provider images', async () => {
    serveStatus([{}]);
    render(<Artwork src="https://cdn.test/cover.jpg" alt="Remote" className="c" />);

    fireEvent.error(screen.getByAltText('Remote'));
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(statusCalls).toEqual([]);
    expect(screen.getByLabelText('Remote').textContent).toBe('♪');
  });

  it('rev25-12: does not commit a stale cache-bust suffix onto a new base', async () => {
    serveStatus([{ '7': { state: 'ready', version: 1234 } }]);

    const { rerender } = render(
      <Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />,
    );
    fireEvent.error(screen.getByAltText('Artist'));
    await waitFor(
      () => {
        const image = screen.queryByAltText('Artist') as HTMLImageElement | null;
        expect(image?.getAttribute('src')).toContain('v=1234');
      },
      { timeout: 15000 },
    );

    // A real `<img>` starts loading whatever `src` it is given the instant the
    // attribute is set, before any later effect corrects it — so what matters
    // is every value it was ever pointed at, not just the last one.
    const image = screen.getByAltText('Artist') as HTMLImageElement;
    const observedSrc: string[] = [];
    const originalSetAttribute = image.setAttribute.bind(image);
    image.setAttribute = ((name: string, value: string) => {
      if (name === 'src') observedSrc.push(value);
      return originalSetAttribute(name, value);
    }) as typeof image.setAttribute;

    rerender(<Artwork src="/api/library/v2/artwork/artist/7?v=99" alt="Artist" className="c" />);

    expect(observedSrc.some((value) => value.includes('v=1234'))).toBe(false);
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/7?v=99');
  }, 20000);

  it('rev25-12: never points the element at a truthy garbage src when src goes empty', async () => {
    serveStatus([{ '7': { state: 'ready', version: 1234 } }]);

    const { rerender } = render(
      <Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />,
    );
    fireEvent.error(screen.getByAltText('Artist'));
    await waitFor(
      () => expect(screen.queryByAltText('Artist')?.getAttribute('src')).toContain('v=1234'),
      { timeout: 15000 },
    );

    const image = screen.getByAltText('Artist') as HTMLImageElement;
    const observedSrc: string[] = [];
    const originalSetAttribute = image.setAttribute.bind(image);
    image.setAttribute = ((name: string, value: string) => {
      if (name === 'src') observedSrc.push(value);
      return originalSetAttribute(name, value);
    }) as typeof image.setAttribute;

    rerender(<Artwork src="" alt="Artist" className="c" />);

    expect(observedSrc.some((value) => value.startsWith('?'))).toBe(false);
    expect(screen.queryByAltText('Artist')).toBeNull();
    expect(screen.getByLabelText('Artist').textContent).toBe('♪');
  }, 20000);

  it('replaces the stale cache-bust marker rather than duplicating it', async () => {
    serveStatus([{ '9': { state: 'ready', version: 77 } }]);

    render(<Artwork src="/api/library/v2/artwork/artist/9?v=42" alt="Thumb" className="c" thumb />);

    const image = screen.getByAltText('Thumb') as HTMLImageElement;
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/9?v=42&size=thumb');

    fireEvent.error(image);
    await waitFor(
      () =>
        expect(
          (screen.queryByAltText('Thumb') as HTMLImageElement | null)?.getAttribute('src'),
        ).toBe('/api/library/v2/artwork/artist/9?v=77&size=thumb'),
      { timeout: 15000 },
    );
  }, 20000);

  it('keeps waiting when a poll fails instead of nailing the placeholder', async () => {
    let call = 0;
    server.use(
      http.get('/api/library/v2/artwork/status', () => {
        call += 1;
        statusCalls.push(String(call));
        if (call === 1) return HttpResponse.error();
        return HttpResponse.json({
          success: true,
          states: { '7': { state: 'ready', version: 5 } },
        });
      }),
    );

    render(<Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />);
    fireEvent.error(screen.getByAltText('Artist'));

    await waitFor(
      () => expect(screen.queryByAltText('Artist')?.getAttribute('src')).toContain('v=5'),
      { timeout: 15000 },
    );
    expect(statusCalls.length).toBeGreaterThan(1);
  }, 20000);

  it('act(): mounting alone never polls', () => {
    vi.useFakeTimers();
    render(<Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />);
    act(() => void vi.advanceTimersByTime(30000));
    expect(statusCalls).toEqual([]);
  });
});

/** ldp-07: the legacy artist page painted covers straight from the provider
 *  CDN and never involved the server, which is exactly why it felt faster.
 *  Library V2 keeps its own cached copy as the truth (a manual cover pick, an
 *  embedded cover, offline/NAS) but must not make the user stare at a
 *  placeholder while that copy is still being built. */
describe('Artwork provider fallback while a local cover is pending (ldp-07)', () => {
  beforeEach(() => {
    resetPendingArtworkWatchers();
    server.use(
      http.get('/api/library/v2/artwork/status', () =>
        HttpResponse.json({ success: true, states: { '7': { state: 'pending' } } }),
      ),
    );
  });
  afterEach(() => resetPendingArtworkWatchers());

  it('shows the provider cover instead of the placeholder', () => {
    render(
      <Artwork
        src="/api/library/v2/artwork/album/7"
        remote="https://cdn.test/cover.jpg"
        alt="Album"
        className="c"
      />,
    );
    fireEvent.error(screen.getByAltText('Album'));

    expect(screen.getByAltText('Album').getAttribute('src')).toBe('https://cdn.test/cover.jpg');
  });

  it('falls through to the placeholder when the provider cover fails too', () => {
    render(
      <Artwork
        src="/api/library/v2/artwork/album/7"
        remote="https://cdn.test/gone.jpg"
        alt="Album"
        className="c"
      />,
    );
    fireEvent.error(screen.getByAltText('Album'));
    fireEvent.error(screen.getByAltText('Album'));

    expect(screen.getByLabelText('Album').textContent).toBe('♪');
  });

  it('still prefers the local copy on the first paint', () => {
    render(
      <Artwork
        src="/api/library/v2/artwork/album/7"
        remote="https://cdn.test/cover.jpg"
        alt="Album"
        className="c"
      />,
    );

    expect(screen.getByAltText('Album').getAttribute('src')).toBe(
      '/api/library/v2/artwork/album/7',
    );
  });
});
