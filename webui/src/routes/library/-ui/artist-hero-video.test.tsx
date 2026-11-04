import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { ArtistHeroVideo } from './artist-hero-video';

let fire: (ratio: number) => void = () => {};
class FakeObserver {
  constructor(cb: (entries: { intersectionRatio: number }[]) => void) {
    fire = (ratio) => cb([{ intersectionRatio: ratio }]);
  }
  observe() {}
  disconnect() {}
}

afterEach(() => vi.unstubAllGlobals());

const TOP = ['Money Trees', 'HUMBLE.', 'DNA.', 'Alright', 'King Kunta', 'Swimming Pools'];

/** top tracks, and a video for every song except the ones in `misses` */
function serve(misses: string[] = []) {
  const asked: (string | null)[] = [];
  server.use(
    http.get('*/api/artist/:id/top-tracks', () =>
      HttpResponse.json({ success: true, source: 'deezer', tracks: TOP.map((name) => ({ name })) }),
    ),
    http.get('*/api/discover/backdrop-video', ({ request }) => {
      const title = new URL(request.url).searchParams.get('title');
      asked.push(title);
      const id = title ? (misses.includes(title) ? null : `song-${title}`) : 'artist-top';
      return HttpResponse.json({ success: true, video_id: id });
    }),
  );
  return asked;
}

function mount() {
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ArtistHeroVideo name="Kendrick Lamar" providerId="525046" />
    </QueryClientProvider>,
  );
  act(() => fire(0.9));
}

describe('the artist hero video', () => {
  it('plays one of the top five songs, picked at random', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const asked = serve();
    mount();
    // the third of the top five; never past the fifth
    await waitFor(() => expect(asked).toEqual(['DNA.']));
  });

  it("falls back to the artist's top video when the song has none", async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    const asked = serve(['Money Trees']);
    mount();
    await waitFor(() => expect(asked).toEqual(['Money Trees', null]));
  });

  it('looks nothing up before the hero has been seen', async () => {
    const asked = serve();
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ArtistHeroVideo name="Kendrick Lamar" providerId="525046" />
      </QueryClientProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(asked).toEqual([]);
  });
});
