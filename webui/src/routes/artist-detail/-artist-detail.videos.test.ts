import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchVideo } from '../search/-search.types';

import {
  ARTIST_VIDEO_FETCH_MAX,
  artistVideoScore,
  curateArtistVideos,
  formatUploadDate,
  loadMoreStep,
  nextVideoAfter,
  pauseLibraryPlayback,
  previousVideoBefore,
  videoEmbedUrl,
  videoKey,
  videoWatchUrl,
} from './-artist-detail.videos';

const video = (over: Partial<SearchVideo> = {}): SearchVideo => ({
  video_id: 'v1',
  title: 'Aphex Twin - Windowlicker (Official Video)',
  channel: 'Aphex Twin',
  url: 'https://youtube.com/watch?v=v1',
  view_count: 1_000_000,
  ...over,
});

describe('artist video curation', () => {
  it('scores official artist matches above weak non-video matches', () => {
    const strong = artistVideoScore(video(), 'Aphex Twin');
    const weak = artistVideoScore(
      video({ title: 'Aphex Twin interview reaction', channel: 'Random Channel', view_count: 10 }),
      'Aphex Twin',
    );
    expect(strong).toBeGreaterThan(weak);
  });

  it('dedupes, sorts, and caps videos for the section', () => {
    const curated = curateArtistVideos(
      [
        video({ video_id: 'weak', title: 'Aphex Twin interview reaction', view_count: 100 }),
        video({ video_id: 'best', title: 'Aphex Twin - Come To Daddy (Official Music Video)' }),
        video({ video_id: 'best', title: 'Duplicate' }),
        video({
          video_id: 'other',
          title: 'Other artist official music video',
          channel: 'Other Channel',
        }),
      ],
      'Aphex Twin',
      2,
    );
    expect(curated.map((v) => v.video_id)).toEqual(['best', 'weak']);
  });

  it('builds a watch url from the id when the API did not include one', () => {
    expect(videoWatchUrl(video({ video_id: 'a/b', url: undefined }))).toBe(
      'https://www.youtube.com/watch?v=a%2Fb',
    );
  });

  it('keeps the whole curated pool when no limit is given', () => {
    // the section owns how many to show; capping here would make "show more"
    // reveal nothing past the first page
    const pool = Array.from({ length: 30 }, (_, i) =>
      video({ video_id: `v${i}`, title: `Aphex Twin - Track ${i} (Official Video)` }),
    );
    expect(curateArtistVideos(pool, 'Aphex Twin')).toHaveLength(30);
    expect(curateArtistVideos(pool, 'Aphex Twin', 8)).toHaveLength(8);
  });
});

describe('embed url', () => {
  it('plays through the nocookie host with the api and origin switched on', () => {
    const url = new URL(videoEmbedUrl(video({ video_id: 'abc' }), 'http://localhost:8008'));
    expect(url.origin).toBe('https://www.youtube-nocookie.com');
    expect(url.pathname).toBe('/embed/abc');
    expect(url.searchParams.get('autoplay')).toBe('1');
    expect(url.searchParams.get('enablejsapi')).toBe('1');
    expect(url.searchParams.get('origin')).toBe('http://localhost:8008');
  });

  it('is empty without a video id, so nothing renders a broken frame', () => {
    expect(videoEmbedUrl(video({ video_id: undefined }))).toBe('');
  });
});

describe('load more', () => {
  it('reveals while the curated pool still has hidden videos', () => {
    expect(loadMoreStep({ curatedCount: 20, visible: 8, fetchedLimit: 20 })).toBe('reveal');
  });

  it('fetches a bigger pool once the pool is dry and the server can give more', () => {
    expect(loadMoreStep({ curatedCount: 8, visible: 8, fetchedLimit: 20 })).toBe('fetch');
    expect(loadMoreStep({ curatedCount: 5, visible: 8, fetchedLimit: 20 })).toBe('fetch');
  });

  it('stops once the biggest pool has been fetched and shown', () => {
    expect(
      loadMoreStep({ curatedCount: 30, visible: 32, fetchedLimit: ARTIST_VIDEO_FETCH_MAX }),
    ).toBe('none');
    expect(
      loadMoreStep({ curatedCount: 40, visible: 32, fetchedLimit: ARTIST_VIDEO_FETCH_MAX }),
    ).toBe('reveal');
  });
});

describe('queue', () => {
  const queue = [video({ video_id: 'a' }), video({ video_id: 'b' }), video({ video_id: 'c' })];

  it('steps forward and back by key', () => {
    expect(nextVideoAfter(queue, 'a')?.video_id).toBe('b');
    expect(nextVideoAfter(queue, 'c')).toBeNull();
    expect(nextVideoAfter(queue, null)?.video_id).toBe('a');
    expect(previousVideoBefore(queue, 'b')?.video_id).toBe('a');
    expect(previousVideoBefore(queue, 'a')).toBeNull();
    expect(previousVideoBefore(queue, null)).toBeNull();
  });

  it('treats a key that left the queue as the end', () => {
    expect(nextVideoAfter(queue, 'zzz')).toBeNull();
  });

  it('keys a video the same way curation dedupes it', () => {
    expect(videoKey(video({ video_id: 'ABC ' }))).toBe('abc');
    expect(videoKey(video({ video_id: undefined, url: undefined, title: 'T' }))).toBe('t');
  });
});

describe('pauseLibraryPlayback', () => {
  afterEach(() => {
    globalThis.audioPlayer = null;
    globalThis.setPlayingState = undefined;
  });

  it('pauses the app player when it is playing, and tells the mini player so', () => {
    const pause = vi.fn();
    const setPlayingState = vi.fn();
    globalThis.audioPlayer = { paused: false, pause } as unknown as HTMLAudioElement;
    globalThis.setPlayingState = setPlayingState;
    pauseLibraryPlayback();
    expect(pause).toHaveBeenCalledTimes(1);
    expect(setPlayingState).toHaveBeenCalledWith(false);
  });

  it('still pauses when the vanilla icon sync is not loaded', () => {
    const pause = vi.fn();
    globalThis.audioPlayer = { paused: false, pause } as unknown as HTMLAudioElement;
    pauseLibraryPlayback();
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it('leaves a paused or missing player alone', () => {
    const pause = vi.fn();
    globalThis.audioPlayer = { paused: true, pause } as unknown as HTMLAudioElement;
    pauseLibraryPlayback();
    expect(pause).not.toHaveBeenCalled();
    globalThis.audioPlayer = null;
    expect(() => pauseLibraryPlayback()).not.toThrow();
  });
});

describe('formatUploadDate', () => {
  it('turns yt-dlp YYYYMMDD into a month and year', () => {
    expect(formatUploadDate('20240101')).toBe('Jan 2024');
    expect(formatUploadDate('20231215')).toBe('Dec 2023');
  });

  it('passes anything else through untouched', () => {
    expect(formatUploadDate('')).toBe('');
    expect(formatUploadDate(undefined)).toBe('');
    expect(formatUploadDate('2024')).toBe('2024');
    expect(formatUploadDate('20241301')).toBe('20241301');
  });
});
