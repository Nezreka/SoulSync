import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { YouTubePlayerOptions } from '../-artist-detail.youtube-api';

import { ArtistVideosSection } from './artist-videos-section';

/**
 * a stand-in for youtube's iframe player. records what the section asked
 * for and lets a test fire the events a real player would: ended, blocked.
 */
class FakePlayer {
  static instances: FakePlayer[] = [];
  options: YouTubePlayerOptions;
  loaded: string[] = [];
  paused = 0;
  destroyed = false;

  constructor(_element: HTMLElement, options: YouTubePlayerOptions) {
    this.options = options;
    FakePlayer.instances.push(this);
  }

  loadVideoById(id: string) {
    this.loaded.push(id);
  }

  playVideo() {}

  pauseVideo() {
    this.paused += 1;
  }

  destroy() {
    this.destroyed = true;
  }

  fire(event: 'onStateChange' | 'onError', data: number) {
    this.options.events?.[event]?.({ data, target: this });
  }
}

const fakeApi = {
  Player: FakePlayer,
  PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2 },
};

const apiLoader = vi.fn(() => Promise.resolve<typeof fakeApi | null>(fakeApi));

vi.mock('../-artist-detail.youtube-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../-artist-detail.youtube-api')>();
  return { ...actual, loadYouTubeIframeApi: () => apiLoader() };
});

function ndjson(lines: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
    { status: 200 },
  );
}

function clip(id: string, title = `Aphex Twin - ${id} (Official Video)`) {
  return {
    video_id: id,
    title,
    channel: 'Aphex Twin',
    duration: 200,
    view_count: 1000,
    thumbnail: `/${id}.jpg`,
    url: `https://youtube.com/watch?v=${id}`,
  };
}

/** serve `count` clips, and record every request body. */
function serveClips(count: number, bodies: unknown[] = []) {
  server.use(
    http.post('/api/enhanced-search/source/youtube_videos', async ({ request }) => {
      const body = (await request.json()) as { limit?: number };
      bodies.push(body);
      const n = Math.min(count, body.limit ?? 20);
      const data = Array.from({ length: n }, (_, i) => clip(`v${i + 1}`));
      return ndjson([`${JSON.stringify({ type: 'videos', data })}\n`]);
    }),
  );
  return bodies;
}

async function playFeatured() {
  await screen.findByText('Aphex Twin - v1 (Official Video)');
  // the poster and the primary button both play; either is fine
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Play Aphex Twin - v1 (Official Video)' })[0],
  );
  await waitFor(() => expect(FakePlayer.instances).toHaveLength(1));
  return FakePlayer.instances[0];
}

beforeEach(() => {
  window.open = vi.fn();
  FakePlayer.instances = [];
  apiLoader.mockImplementation(() => Promise.resolve(fakeApi));
  globalThis.audioPlayer = null;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  globalThis.audioPlayer = null;
});

describe('ArtistVideosSection', () => {
  it('streams YouTube videos for the artist and presents a featured card', async () => {
    let body: unknown;
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', async ({ request }) => {
        body = await request.json();
        return ndjson([
          '{"type":"videos","data":[{"video_id":"v1","title":"Aphex Twin - Windowlicker (Official Video)","channel":"Aphex Twin","duration":215,"view_count":1500000,"thumbnail":"/v1.jpg","url":"https://youtube.com/watch?v=v1"}]}\n',
        ]);
      }),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await screen.findByText('Aphex Twin - Windowlicker (Official Video)');
    // the first ask is the search page's number, so the page load is no slower
    expect(body).toEqual({ query: 'Aphex Twin', limit: 20 });
    expect(document.querySelector('.artist-video-spotlight.featured')).not.toBeNull();
    expect(screen.getAllByText('3:35')).toHaveLength(2);
    expect(document.getElementById('artist-videos-count')?.textContent).toBe('1 video');
  });

  it('hides itself when YouTube returns no videos', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson(['{"type":"videos","data":[]}\n']),
      ),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await waitFor(() => expect(document.getElementById('artist-videos-section')).toBeNull());
  });

  it('hides itself when the video search endpoint fails', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        HttpResponse.json({}, { status: 500 }),
      ),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await waitFor(() => expect(document.getElementById('artist-videos-section')).toBeNull());
  });

  it('still offers the video on YouTube itself', async () => {
    serveClips(1);
    render(<ArtistVideosSection artistName="Aphex Twin" />);
    await screen.findByText('Aphex Twin - v1 (Official Video)');

    fireEvent.click(
      screen.getByRole('button', { name: 'Open Aphex Twin - v1 (Official Video) on YouTube' }),
    );
    expect(window.open).toHaveBeenCalledWith(
      'https://youtube.com/watch?v=v1',
      '_blank',
      'noopener,noreferrer',
    );
  });

  describe('playing in the page', () => {
    it('plays the featured video in place through the nocookie host and silences the app player', async () => {
      const pause = vi.fn();
      globalThis.audioPlayer = {
        paused: false,
        pause,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      } as unknown as HTMLAudioElement;
      serveClips(3);
      render(<ArtistVideosSection artistName="Aphex Twin" />);

      const player = await playFeatured();

      expect(window.open).not.toHaveBeenCalled();
      expect(player.options.videoId).toBe('v1');
      expect(player.options.host).toBe('https://www.youtube-nocookie.com');
      expect(player.options.playerVars?.autoplay).toBe(1);
      expect(pause).toHaveBeenCalled();
      expect(document.querySelector('.artist-video-player')).not.toBeNull();
      expect(document.querySelector('.artist-video-spotlight')).toBeNull();
      // the queue now lists every video, with the playing one marked
      expect(screen.getByText('Queue')).toBeTruthy();
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(3);
      expect(document.querySelector('.artist-video-rail-item.playing')?.textContent).toContain(
        'Aphex Twin - v1',
      );
    });

    it('steps to the next video when one ends, and reveals a hidden page to keep going', async () => {
      serveClips(9);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      const player = await playFeatured();

      act(() => player.fire('onStateChange', 0));
      // the same player, a new id: no teardown between queue steps
      expect(player.loaded).toEqual(['v2']);
      expect(FakePlayer.instances).toHaveLength(1);
      expect(document.querySelector('.artist-video-rail-item.playing')?.textContent).toContain(
        'Aphex Twin - v2',
      );
      expect(screen.getByText('2 of 9')).toBeTruthy();

      // run the queue past the first page of eight
      for (let i = 0; i < 7; i += 1) act(() => player.fire('onStateChange', 0));
      expect(player.loaded.at(-1)).toBe('v9');
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(9);
      expect(screen.getByText('9 of 9')).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Next video' })).toBeDisabled();
    });

    it('does not advance when autoplay is switched off', async () => {
      serveClips(3);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      const player = await playFeatured();

      fireEvent.click(screen.getByRole('button', { name: 'Autoplay' }));
      act(() => player.fire('onStateChange', 0));
      expect(player.loaded).toEqual([]);
    });

    it('offers YouTube when the owner blocks embedding', async () => {
      serveClips(2);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      const player = await playFeatured();

      act(() => player.fire('onError', 150));
      expect(screen.getByRole('alert').textContent).toContain("can't be played here");
      fireEvent.click(screen.getByRole('button', { name: 'Open on YouTube' }));
      expect(window.open).toHaveBeenCalledWith(
        'https://youtube.com/watch?v=v1',
        '_blank',
        'noopener,noreferrer',
      );

      // skipping leaves the blocked one behind and plays the next
      fireEvent.click(screen.getByRole('button', { name: 'Skip to next' }));
      expect(screen.queryByRole('alert')).toBeNull();
      await waitFor(() => expect(FakePlayer.instances).toHaveLength(2));
      expect(FakePlayer.instances[1].options.videoId).toBe('v2');
    });

    it('closes on escape and brings the spotlight back', async () => {
      serveClips(2);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      const player = await playFeatured();

      fireEvent.keyDown(document, { key: 'Escape' });
      expect(document.querySelector('.artist-video-player')).toBeNull();
      expect(document.querySelector('.artist-video-spotlight')).not.toBeNull();
      expect(player.destroyed).toBe(true);
    });

    it('pauses the video when the app player starts', async () => {
      const listeners: Record<string, () => void> = {};
      globalThis.audioPlayer = {
        paused: true,
        pause: vi.fn(),
        addEventListener: (name: string, fn: () => void) => {
          listeners[name] = fn;
        },
        removeEventListener: vi.fn(),
      } as unknown as HTMLAudioElement;
      serveClips(1);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      const player = await playFeatured();

      act(() => listeners.play?.());
      expect(player.paused).toBe(1);
    });

    it('falls back to a plain iframe when the api cannot load', async () => {
      apiLoader.mockImplementation(() => Promise.resolve(null));
      serveClips(1);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      await screen.findByText('Aphex Twin - v1 (Official Video)');
      fireEvent.click(
        screen.getAllByRole('button', { name: 'Play Aphex Twin - v1 (Official Video)' })[0],
      );

      const frame = await waitFor(() => {
        const el = document.querySelector<HTMLIFrameElement>('.artist-video-embed iframe');
        if (!el) throw new Error('no iframe yet');
        return el;
      });
      expect(frame.src).toContain('https://www.youtube-nocookie.com/embed/v1?');
      expect(frame.src).toContain('autoplay=1');
      expect(FakePlayer.instances).toHaveLength(0);
    });
  });

  describe('show more', () => {
    it('opens with eight and reveals the rest of the pool before asking the server again', async () => {
      const bodies = serveClips(20);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      await screen.findByText('Aphex Twin - v1 (Official Video)');

      // 1 featured + 7 in the rail
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(7);
      expect(screen.getByText('8 of 20')).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(15);
      expect(bodies).toHaveLength(1);

      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(19);
      expect(screen.getByText('20 of 20+')).toBeTruthy();
      expect(bodies).toHaveLength(1);
    });

    it('asks yt-dlp for the big pool once the first one is spent, and merges without repeats', async () => {
      const bodies = serveClips(30);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      await screen.findByText('Aphex Twin - v1 (Official Video)');

      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      expect(screen.getByText('20 of 20+')).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      await waitFor(() => expect(bodies).toHaveLength(2));
      expect(bodies[1]).toEqual({ query: 'Aphex Twin', limit: 60 });

      // the server returned 30, twenty of them repeats of the first pool: ten
      // new ones, and the click that fetched them also reveals them
      await waitFor(() =>
        expect(document.getElementById('artist-videos-count')?.textContent).toBe('30 videos'),
      );
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(29);
      // the biggest pool is fetched and fully shown: nothing left to offer
      expect(screen.queryByRole('button', { name: 'Show more videos' })).toBeNull();
    });

    it('stops offering more when the big pool has nothing new', async () => {
      const bodies = serveClips(8);
      render(<ArtistVideosSection artistName="Aphex Twin" />);
      await screen.findByText('Aphex Twin - v1 (Official Video)');
      expect(screen.getByText('8 of 8+')).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Show more videos' }));
      await waitFor(() => expect(bodies).toHaveLength(2));
      await waitFor(() =>
        expect(screen.queryByRole('button', { name: 'Show more videos' })).toBeNull(),
      );
      expect(document.querySelectorAll('.artist-video-rail-item')).toHaveLength(7);
    });
  });
});
