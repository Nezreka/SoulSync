import { describe, expect, it } from 'vitest';

import type { SearchVideo } from '../search/-search.types';

import { artistVideoScore, curateArtistVideos, videoWatchUrl } from './-artist-detail.videos';

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
});
