import { renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { SearchArtist } from './-search.types';

import { artistsNeedingImages, useArtistImages } from './-search.use-artist-images';

afterEach(() => {
  document.body.innerHTML = '';
  delete window.extractImageColors;
  delete window.applyDynamicGlow;
});

describe('artistsNeedingImages', () => {
  it('takes library artists first, then source ones', () => {
    // Document order, so the images fill in from the top of the list the user is
    // actually reading.
    const pending = artistsNeedingImages([{ id: 'db1' }], [{ id: 'sp1' }], {});
    expect(pending.map((a) => a.id)).toEqual(['db1', 'sp1']);
  });

  it('skips an artist that already has art, from either field', () => {
    const pending = artistsNeedingImages(
      [],
      [
        { id: 'has-url', image_url: 'https://cdn/a.jpg' },
        { id: 'has-images', images: [{ url: 'https://cdn/b.jpg' }] },
        { id: 'needs' },
      ],
      {},
    );
    expect(pending.map((a) => a.id)).toEqual(['needs']);
  });

  it('skips one already resolved, so a re-render does not re-fetch it', () => {
    expect(artistsNeedingImages([], [{ id: 'sp1' }], { sp1: 'https://cdn/a.jpg' })).toEqual([]);
  });

  it('skips an artist with no id, which nothing could be looked up by', () => {
    expect(artistsNeedingImages([], [{ name: 'Nameless only' }], {})).toEqual([]);
  });
});

describe('useArtistImages', () => {
  it('resolves one image at a time, in order', async () => {
    // Sequential on purpose: the resolver falls back to third-party lookups
    // server-side, so ten parallel requests are ten parallel iTunes hits.
    const started: string[] = [];
    let inFlight = 0;
    let peak = 0;
    server.use(
      http.get('/api/artist/:id/image', async ({ params }) => {
        started.push(String(params.id));
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((r) => setTimeout(r, 5));
        inFlight -= 1;
        return HttpResponse.json({ success: true, image_url: `https://cdn/${params.id}.jpg` });
      }),
    );

    const artists: SearchArtist[] = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const { result } = renderHook(() => useArtistImages([], artists, 'spotify'));

    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(3));
    expect(started).toEqual(['a', 'b', 'c']);
    expect(peak).toBe(1);
    expect(result.current.a).toBe('https://cdn/a.jpg');
  });

  it('passes the artist name, for sources that store no art at all', async () => {
    // MusicBrainz has MBIDs and nothing else; the name is what the resolver
    // searches iTunes/Deezer with.
    let url = '';
    server.use(
      http.get('/api/artist/:id/image', ({ request }) => {
        url = request.url;
        return HttpResponse.json({ success: true, image_url: 'https://cdn/x.jpg' });
      }),
    );

    const artists: SearchArtist[] = [{ id: 'mb1', name: 'Aphex Twin' }];
    const { result } = renderHook(() => useArtistImages([], artists, 'musicbrainz'));
    await waitFor(() => expect(result.current.mb1).toBeTruthy());

    expect(new URL(url).searchParams.get('name')).toBe('Aphex Twin');
    expect(new URL(url).searchParams.get('source')).toBe('musicbrainz');
  });

  it('omits the source param for spotify, as the vanilla did', async () => {
    let url = '';
    server.use(
      http.get('/api/artist/:id/image', ({ request }) => {
        url = request.url;
        return HttpResponse.json({ success: true, image_url: 'https://cdn/x.jpg' });
      }),
    );

    const artists: SearchArtist[] = [{ id: 'sp1', name: 'A' }];
    const { result } = renderHook(() => useArtistImages([], artists, 'spotify'));
    await waitFor(() => expect(result.current.sp1).toBeTruthy());
    expect(new URL(url).searchParams.has('source')).toBe(false);
  });

  it('keeps going after one artist has no image', async () => {
    server.use(
      http.get('/api/artist/:id/image', ({ params }) =>
        params.id === 'a'
          ? HttpResponse.json({ success: false, image_url: null })
          : HttpResponse.json({ success: true, image_url: 'https://cdn/b.jpg' }),
      ),
    );

    const artists: SearchArtist[] = [{ id: 'a' }, { id: 'b' }];
    const { result } = renderHook(() => useArtistImages([], artists, 'spotify'));
    await waitFor(() => expect(result.current.b).toBe('https://cdn/b.jpg'));
    expect(result.current.a).toBeUndefined();
  });

  it('does not trust a url that came back alongside success:false', async () => {
    server.use(
      http.get('/api/artist/:id/image', () =>
        HttpResponse.json({ success: false, image_url: 'https://cdn/stale.jpg' }),
      ),
    );
    const { result } = renderHook(() => useArtistImages([], [{ id: 'a' }], 'spotify'));
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current.a).toBeUndefined();
  });

  it('leaves the glow to the card, which glows ANY artwork', async () => {
    // Not this hook's job: renderCompactSection glowed every card with an image,
    // so CompactItem owns it and fires on the resolved url like any other.
    server.use(
      http.get('/api/artist/:id/image', () =>
        HttpResponse.json({ success: true, image_url: 'https://cdn/x.jpg' }),
      ),
    );
    window.extractImageColors = vi.fn() as never;

    const { result } = renderHook(() => useArtistImages([], [{ id: 'sp1' }], 'spotify'));
    await waitFor(() => expect(result.current.sp1).toBe('https://cdn/x.jpg'));
    expect(window.extractImageColors).not.toHaveBeenCalled();
  });

  it('abandons the run when the results are replaced mid-loop', async () => {
    // The old query's first request is held open, so the loop is provably parked
    // on it when the results change — timing-free, unlike waiting a few ms and
    // hoping.
    const seen: string[] = [];
    const release: (() => void)[] = [];
    server.use(
      http.get('/api/artist/:id/image', ({ params }) => {
        const id = String(params.id);
        seen.push(id);
        if (!id.startsWith('old')) {
          return HttpResponse.json({ success: true, image_url: `https://cdn/${id}.jpg` });
        }
        return new Promise<Response>((resolve) => {
          release.push(() => resolve(HttpResponse.json({ success: true, image_url: 'https://x' })));
        });
      }),
    );

    const { result, rerender, unmount } = renderHook(
      ({ artists }: { artists: SearchArtist[] }) => useArtistImages([], artists, 'spotify'),
      { initialProps: { artists: [{ id: 'old1' }, { id: 'old2' }, { id: 'old3' }] } },
    );
    await waitFor(() => expect(seen).toEqual(['old1']));

    rerender({ artists: [{ id: 'new1' }] });
    await waitFor(() => expect(result.current.new1).toBe('https://cdn/new1.jpg'));

    // Let the abandoned request land: it must not resume the old queue, and must
    // not write an image for a card nobody is looking at.
    release[0]();
    await new Promise((r) => setTimeout(r, 20));
    expect(seen).toEqual(['old1', 'new1']);
    expect(result.current.old1).toBeUndefined();
    unmount();
  });

  it('requests each artist ONCE, however often the caller re-renders', async () => {
    // The loop writes state as it goes, so a dep on the arrays (or on the
    // resolved map) restarts it after every image and re-requests the rest of
    // the queue. The empty `[]` below is the trap in miniature: a fresh literal
    // on every render.
    const seen: string[] = [];
    server.use(
      http.get('/api/artist/:id/image', async ({ params }) => {
        seen.push(String(params.id));
        await new Promise((r) => setTimeout(r, 5));
        return HttpResponse.json({ success: true, image_url: `https://cdn/${params.id}.jpg` });
      }),
    );

    const artists: SearchArtist[] = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const { result } = renderHook(() => useArtistImages([], artists, 'spotify'));

    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(3));
    await new Promise((r) => setTimeout(r, 20));
    expect(seen).toEqual(['a', 'b', 'c']);
  });

  it('asks for nothing when every artist already has art', async () => {
    let asked = false;
    server.use(
      http.get('/api/artist/:id/image', () => {
        asked = true;
        return HttpResponse.json({ success: true, image_url: 'https://cdn/x.jpg' });
      }),
    );
    renderHook(() => useArtistImages([], [{ id: 'a', image_url: 'https://cdn/a.jpg' }], 'spotify'));
    await new Promise((r) => setTimeout(r, 20));
    expect(asked).toBe(false);
  });
});
