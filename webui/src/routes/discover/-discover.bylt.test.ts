import { describe, expect, it } from 'vitest';

import {
  BYLT_ANCHOR_ID,
  BYLT_CONTAINER_ID,
  BYLT_LOADING_MESSAGE,
  BYLT_RENDERS_EMPTY_STATE,
  BYLT_SUBTITLE,
  byltCarouselId,
  byltHasArtistImage,
  byltSections,
  byltTrackCard,
  byltTracks,
} from './-discover.bylt';

describe('the self-created container', () => {
  it('creates its own container after the release-radar anchor', () => {
    // index.html ships no placeholder for this section, so the loader inserts
    // one — and bails entirely if the anchor is missing.
    expect(BYLT_CONTAINER_ID).toBe('discover-bylt-sections');
    expect(BYLT_ANCHOR_ID).toBe('discover-release-radar');
  });

  it('opts OUT of the shared empty state', () => {
    // With nothing to show the container stays blank rather than rendering a
    // placeholder — the original no-op behaviour.
    expect(BYLT_RENDERS_EMPTY_STATE).toBe(false);
    expect(BYLT_LOADING_MESSAGE).toBe('');
  });

  it('keeps the subtitle verbatim', () => {
    expect(BYLT_SUBTITLE).toBe('Because you listen to');
  });
});

describe('reading the response', () => {
  it('extracts sections, defaulting to empty', () => {
    expect(byltSections({ sections: [{ artist_name: 'A' }] })).toHaveLength(1);
    expect(byltSections({})).toEqual([]);
    expect(byltSections(null)).toEqual([]);
  });

  it('omits the header image entirely when absent', () => {
    expect(byltHasArtistImage({ artist_image: '/a.jpg' })).toBe(true);
    expect(byltHasArtistImage({})).toBe(false);
  });

  it('ids each shelf grid by INDEX', () => {
    expect(byltCarouselId(0)).toBe('bylt-carousel-0');
    expect(byltCarouselId(3)).toBe('bylt-carousel-3');
  });
});

describe('the track card', () => {
  it('reads `name` and `artist`, NOT track_name/artist_name', () => {
    // Every other card renderer on this page uses name/artist_name. Sharing a
    // card type across them would quietly blank this one's second line.
    expect(byltTrackCard({ name: 'Xtal', artist: 'Aphex Twin' })).toMatchObject({
      title: 'Xtal',
      subtitle: 'Aphex Twin',
    });
    expect(byltTrackCard({ track_name: 'Xtal', artist_name: 'A' } as never)).toMatchObject({
      title: '',
      subtitle: '',
    });
  });

  it('shows the placeholder only without art', () => {
    expect(byltTrackCard({ image_url: '/a.jpg' })).toMatchObject({
      image: '/a.jpg',
      showPlaceholder: false,
    });
    expect(byltTrackCard({})).toMatchObject({ image: null, showPlaceholder: true });
  });
});

describe('a malformed section', () => {
  it('costs ONE shelf, not every shelf after it', () => {
    // section.tracks.map(...) at 10438 is unguarded, so a section without a
    // tracks array throws inside onRendered and aborts the loop — every later
    // shelf renders its wrapper with no cards.
    expect(byltTracks({ tracks: [{ name: 'x' }] })).toHaveLength(1);
    expect(byltTracks({})).toEqual([]);
    expect(byltTracks({ tracks: null as never })).toEqual([]);
    expect(byltTracks({ tracks: 'nope' as never })).toEqual([]);
  });
});
