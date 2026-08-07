/**
 * The Beatport pure core, pinned against beatport-ui.js.
 *
 * Several of these assertions look like they are enshrining a bug. They are:
 * the read established that these behaviours reach the download engine and
 * change what lands on disk, so the port transcribes them and the tests say so
 * out loud.
 */

import { describe, expect, it } from 'vitest';

import {
  BEATPORT_COMPILATION_ARTIST,
  absoluteBeatportUrl,
  beatportChartAlbumId,
  beatportChartPlaylistId,
  beatportDownloadContext,
  beatportEnrichmentId,
  beatportRandomToken,
  beatportReleasePlaylistId,
  releaseBubbleImage,
  BEATPORT_SLIDERS,
  EXCLUDED_GENRE_NAMES,
  beatportCardBackground,
  buildChartAlbum,
  buildChartTrackName,
  buildChartTracks,
  cleanTrackText,
  filterBeatportGenres,
  isExcludedGenre,
  normaliseReleaseTrackArtists,
  parseBeatportDuration,
  slideCount,
  slidePosition,
  splitBeatportArtists,
  upscaleBeatportArtwork,
  wrapSlideIndex,
} from './-beatport.core';

describe('cleanTrackText (1638-1649)', () => {
  it('splits the concatenation it exists for', () => {
    expect(cleanTrackText('DeepHouseAnthem')).toBe('Deep House Anthem');
    expect(cleanTrackText('artistName')).toBe('artist Name');
  });

  it('spaces after a comma only between letters', () => {
    expect(cleanTrackText('Artist,Other')).toBe('Artist, Other');
    // A comma next to a digit or a space is left alone.
    expect(cleanTrackText('Track 1,2')).toBe('Track 1,2');
  });

  it('spaces the four mix keywords', () => {
    expect(cleanTrackText('SongExtended')).toBe('Song Extended');
    expect(cleanTrackText('SongRemix')).toBe('Song Remix');
    expect(cleanTrackText('SongVersion')).toBe('Song Version');
    expect(cleanTrackText('SongMixed')).toBe('Song Mixed');
  });

  it('needs an ALL-CAPS prefix to show rule three doing anything', () => {
    // The cases above all pass with rule three deleted, because rule one has
    // already split them on the lower→upper transition. Rule three earns its
    // place only where rule one cannot fire — after an uppercase letter.
    expect(cleanTrackText('SUMMERMix')).toBe('SUMMER Mix');
    expect(cleanTrackText('IBIZARemix')).toBe('IBIZA Remix');
    expect(cleanTrackText('CLUBExtended')).toBe('CLUB Extended');
    expect(cleanTrackText('RADIOVersion')).toBe('RADIO Version');
  });

  it('honours the word boundary — Mixed is not Mix', () => {
    // Same all-caps setup, so only rule three is in play. With \b the keyword
    // must end the word; without it, 'Mixed' would be split too.
    expect(cleanTrackText('SUMMERMixed')).toBe('SUMMERMixed');
    expect(cleanTrackText('SUMMERVersions')).toBe('SUMMERVersions');
  });

  it('collapses runs of whitespace and trims', () => {
    expect(cleanTrackText('  a   b  ')).toBe('a b');
  });

  it('splits legitimate internal capitals too — the accepted cost', () => {
    // Documented, not desired: rule one cannot tell a real name from a
    // concatenation. Changing this changes folder names on disk.
    expect(cleanTrackText('McCartney')).toBe('Mc Cartney');
    expect(cleanTrackText('MoBlack')).toBe('Mo Black');
  });

  it('leaves an all-caps or already-spaced name alone', () => {
    expect(cleanTrackText('ARTIST NAME')).toBe('ARTIST NAME');
    expect(cleanTrackText('Deep House')).toBe('Deep House');
  });

  it('returns a falsy argument UNCHANGED rather than coercing it', () => {
    expect(cleanTrackText('')).toBe('');
    expect(cleanTrackText(undefined)).toBeUndefined();
    expect(cleanTrackText(null)).toBeNull();
  });
});

describe('parseBeatportDuration (1990-1997)', () => {
  it('reads m:ss', () => {
    expect(parseBeatportDuration('3:45')).toBe(225000);
    expect(parseBeatportDuration('0:30')).toBe(30000);
    expect(parseBeatportDuration('10:05')).toBe(605000);
  });

  it('reads a bare seconds count, string or number', () => {
    expect(parseBeatportDuration(225)).toBe(225000);
    expect(parseBeatportDuration('225')).toBe(225000);
  });

  it('is 0 for nothing and for nonsense', () => {
    expect(parseBeatportDuration(0)).toBe(0);
    expect(parseBeatportDuration('')).toBe(0);
    expect(parseBeatportDuration(null)).toBe(0);
    expect(parseBeatportDuration(undefined)).toBe(0);
    expect(parseBeatportDuration('abc')).toBe(0);
    // NaN * 1000 is NaN, and the vanilla's `|| 0` catches it.
    expect(parseBeatportDuration('x:y')).toBe(0);
  });
});

describe('artwork (1824-1827)', () => {
  it('upscales only the exact Beatport size segment', () => {
    expect(upscaleBeatportArtwork('https://cdn/image_size/95x95/abc.jpg')).toBe(
      'https://cdn/image_size/500x500/abc.jpg',
    );
  });

  it('passes anything else through untouched', () => {
    expect(upscaleBeatportArtwork('https://cdn/other/abc.jpg')).toBe('https://cdn/other/abc.jpg');
    expect(upscaleBeatportArtwork('')).toBe('');
  });

  it('bakes the gradient into the background value', () => {
    expect(beatportCardBackground('https://cdn/image_size/95x95/a.jpg')).toBe(
      "linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.8)), url('https://cdn/image_size/500x500/a.jpg')",
    );
  });
});

describe('the five slider configurations', () => {
  it('keeps five distinct autoplay delays', () => {
    const delays = Object.values(BEATPORT_SLIDERS).map((s) => s.autoPlayDelay);
    expect(delays).toEqual([5000, 8000, 4000, 10000, 12000]);
    // The point of the table: no two sliders agree, so a shared component must
    // be driven by config rather than by any one slider's behaviour.
    expect(new Set(delays).size).toBe(5);
  });

  it('keeps the layouts that differ', () => {
    expect(BEATPORT_SLIDERS.hero.cardsPerSlide).toBe(1);
    expect(BEATPORT_SLIDERS.dj.cardsPerSlide).toBe(3);
    expect(BEATPORT_SLIDERS.releases.cardsPerSlide).toBe(10);
  });

  it('records the three different failure behaviours', () => {
    expect(BEATPORT_SLIDERS.hero.onFailure).toBe('keep-static-markup');
    expect(BEATPORT_SLIDERS.releases.onFailure).toBe('error-block');
    // charts and DJ render nothing — which is why they are the only two that
    // retry the fetch on re-entry.
    expect(BEATPORT_SLIDERS.charts.onFailure).toBe('nothing');
    expect(BEATPORT_SLIDERS.dj.onFailure).toBe('nothing');
  });

  it('records which sliders pad their last slide', () => {
    expect(BEATPORT_SLIDERS.releases.padsLastSlide).toBe(true);
    expect(BEATPORT_SLIDERS.hypePicks.padsLastSlide).toBe(true);
    expect(BEATPORT_SLIDERS.charts.padsLastSlide).toBe(false);
  });
});

describe('slide maths', () => {
  it('pages by ceiling', () => {
    expect(slideCount(25, 10)).toBe(3);
    expect(slideCount(20, 10)).toBe(2);
    expect(slideCount(1, 10)).toBe(1);
    expect(slideCount(0, 10)).toBe(0);
    // The hero slider is one item per slide.
    expect(slideCount(4, 1)).toBe(4);
  });

  it('does not divide by zero', () => {
    expect(slideCount(10, 0)).toBe(0);
  });

  it('wraps in both directions', () => {
    expect(wrapSlideIndex(-1, 3)).toBe(2);
    expect(wrapSlideIndex(3, 3)).toBe(0);
    expect(wrapSlideIndex(1, 3)).toBe(1);
  });

  it('wraps safely with no slides', () => {
    expect(wrapSlideIndex(1, 0)).toBe(0);
    expect(wrapSlideIndex(-1, 0)).toBe(0);
  });

  it('gives every slide a DIRECTION, not just an active flag', () => {
    // The CSS transition needs prev vs next; an is-active boolean loses it.
    expect(slidePosition(0, 1)).toBe('prev');
    expect(slidePosition(1, 1)).toBe('active');
    expect(slidePosition(2, 1)).toBe('next');
  });
});

describe('genre filtering (2380-2399)', () => {
  it('drops the nine section headings Beatport returns as genres', () => {
    expect([...EXCLUDED_GENRE_NAMES].sort()).toEqual([
      'browse',
      'charts',
      'electronic',
      'featured',
      'genres',
      'new releases',
      'open format',
      'popular',
      'trending',
    ]);
  });

  it('matches lower-cased and trimmed', () => {
    expect(isExcludedGenre('  Open Format ')).toBe(true);
    expect(isExcludedGenre('ELECTRONIC')).toBe(true);
  });

  it('is exact equality, so a real genre containing the word survives', () => {
    expect(isExcludedGenre('Electronica')).toBe(false);
    expect(isExcludedGenre('Tech House')).toBe(false);
    expect(isExcludedGenre('Trending Now')).toBe(false);
  });

  it('filters a list without disturbing the rest', () => {
    const kept = filterBeatportGenres([
      { name: 'Tech House' },
      { name: 'Electronic' },
      { name: 'Techno' },
      { name: 'Charts' },
    ]);
    expect(kept.map((g) => g.name)).toEqual(['Tech House', 'Techno']);
  });
});

describe('the chart → download-modal bridge (1999-2064)', () => {
  const album = buildChartAlbum('album_1', 'Beatport Top 100', 'http://art.jpg', 2);

  it('builds a COMPILATION, not an album', () => {
    expect(album).toEqual({
      id: 'album_1',
      name: 'Beatport Top 100',
      album_type: 'compilation',
      images: [{ url: 'http://art.jpg' }],
      total_tracks: 2,
    });
  });

  it('omits the images array entirely when there is no chart image', () => {
    expect(buildChartAlbum('a', 'n', null, 0).images).toEqual([]);
  });

  it('credits every chart to the same synthetic artist', () => {
    expect(BEATPORT_COMPILATION_ARTIST).toEqual({
      id: 'beatport_various',
      name: 'Various Artists',
    });
  });

  it('appends the mix name unless it is the original mix', () => {
    expect(buildChartTrackName({ title: 'Song', mix_name: 'Extended Mix' })).toBe(
      'Song (Extended Mix)',
    );
    expect(buildChartTrackName({ title: 'Song', mix_name: 'Original Mix' })).toBe('Song');
    // Case-insensitive, because Beatport is inconsistent about it.
    expect(buildChartTrackName({ title: 'Song', mix_name: 'ORIGINAL MIX' })).toBe('Song');
    expect(buildChartTrackName({ title: 'Song' })).toBe('Song');
  });

  it('defaults an untitled track the way the vanilla does', () => {
    expect(buildChartTrackName({})).toBe('Unknown Title');
  });

  it('splits the artist string so the folder structure comes out right', () => {
    expect(splitBeatportArtists('Artist One, Artist Two')).toEqual(['Artist One', 'Artist Two']);
    expect(splitBeatportArtists('Solo Artist')).toEqual(['Solo Artist']);
    // cleanTrackText runs first, so an unspaced comma is already normalised.
    expect(splitBeatportArtists('One,Two')).toEqual(['One', 'Two']);
    expect(splitBeatportArtists(undefined)).toEqual(['Unknown Artist']);
  });

  it('drops empty segments from a trailing comma', () => {
    expect(splitBeatportArtists('One, ')).toEqual(['One']);
  });

  it('gives each track the compilation album when the scrape found no release', () => {
    const tracks = buildChartTracks([{ title: 'A' }, { title: 'B' }], album);
    expect(tracks[0].album).toBe(album);
    expect(tracks[1].album).toBe(album);
    expect(tracks.map((t) => t.id)).toEqual(['beatport_chart_0', 'beatport_chart_1']);
    expect(tracks.map((t) => t.track_number)).toEqual([1, 2]);
    expect(tracks[0].disc_number).toBe(1);
  });

  it('prefers per-track release metadata when the scrape found it', () => {
    const [track] = buildChartTracks(
      [
        {
          title: 'A',
          release_name: 'TheRelease',
          release_id: 'r9',
          release_image: 'http://r.jpg',
          release_date: '2024-01-01',
        },
      ],
      album,
    );
    expect(track.album).toEqual({
      id: 'beatport_release_r9',
      // The release name is cleaned like every other scraped string.
      name: 'The Release',
      album_type: 'single',
      images: [{ url: 'http://r.jpg' }],
      release_date: '2024-01-01',
      total_tracks: 1,
    });
  });

  it('falls back to the index when a release has no id', () => {
    const tracks = buildChartTracks([{ title: 'A' }, { title: 'B', release_name: 'R' }], album);
    expect(tracks[1].album.id).toBe('beatport_release_1');
  });

  it('carries the duration through the parser', () => {
    const [track] = buildChartTracks([{ title: 'A', duration: '3:45' }], album);
    expect(track.duration_ms).toBe(225000);
  });
});

describe('normaliseReleaseTrackArtists (1891-1894)', () => {
  it('flattens artist objects to names and leaves strings alone', () => {
    const out = normaliseReleaseTrackArtists([
      { id: 1, artists: [{ name: 'A' }, 'B'] },
      { id: 2, artists: [] },
    ]);
    expect(out[0].artists).toEqual(['A', 'B']);
    expect(out[1].artists).toEqual([]);
  });

  it('keeps every other field on the track', () => {
    const out = normaliseReleaseTrackArtists([{ id: 7, name: 'x', artists: ['A'] }]);
    expect(out[0]).toMatchObject({ id: 7, name: 'x' });
  });
});

describe('the download-modal context type (1900-1907 vs 2052-2060)', () => {
  it('sends charts as a playlist and releases as an artist_album', () => {
    // The release call passes only six arguments, so it takes the callee's
    // default (shared-helpers.js 1763). The difference between the two call
    // sites is an argument that ISN'T THERE, which is why it is easy to miss.
    expect(beatportDownloadContext('chart')).toBe('playlist');
    expect(beatportDownloadContext('release')).toBe('artist_album');
  });
});

describe('releaseBubbleImage (1910)', () => {
  it("prefers the metadata endpoint's album art", () => {
    expect(releaseBubbleImage({ images: [{ url: 'http://album.jpg' }] }, 'http://card.jpg')).toBe(
      'http://album.jpg',
    );
  });

  it("falls back to the card's own thumbnail, then to nothing", () => {
    expect(releaseBubbleImage({ images: [] }, 'http://card.jpg')).toBe('http://card.jpg');
    expect(releaseBubbleImage(null, 'http://card.jpg')).toBe('http://card.jpg');
    expect(releaseBubbleImage(undefined, undefined)).toBe('');
    expect(releaseBubbleImage({ images: [] }, '')).toBe('');
  });
});

describe('the four synthetic ids (1896, 1939, 2005, 2047)', () => {
  const now = () => 1700000000000;

  it("matches substr(2, n) on the vanilla's random suffix", () => {
    // 0.5.toString(36) is '0.i', so substr(2, 9) is 'i' — the suffix is
    // shorter than n whenever the fraction is short, and that is fine.
    expect(beatportRandomToken(() => 0.5, 9)).toBe('i');
    expect(beatportRandomToken(() => 0.123456789, 9)).toBe((0.123456789).toString(36).substr(2, 9));
    expect(beatportRandomToken(() => 0.123456789, 6)).toBe((0.123456789).toString(36).substr(2, 6));
  });

  it('gives releases and charts DIFFERENT prefixes and the same 9-char suffix', () => {
    const random = () => 0.123456789;
    const suffix = (0.123456789).toString(36).substr(2, 9);
    expect(suffix).toHaveLength(9);
    expect(beatportReleasePlaylistId(now, random)).toBe(`beatport_release_1700000000000_${suffix}`);
    expect(beatportChartPlaylistId(now, random)).toBe(`beatport_chart_1700000000000_${suffix}`);
  });

  it("gives the chart's ALBUM id no random suffix at all", () => {
    // 2005. Two charts opened in the same millisecond would share it. Kept as
    // the vanilla has it: this is the id the download engine keys on.
    expect(beatportChartAlbumId(now)).toBe('beatport_chart_1700000000000');
    expect(beatportChartAlbumId(now)).toBe(beatportChartAlbumId(now));
  });

  it('gives the enrichment id a SHORTER suffix than the playlist ids', () => {
    const random = () => 0.123456789;
    const enrich = beatportEnrichmentId(now, random).split('_')[2];
    const playlist = beatportChartPlaylistId(now, random).split('_')[3];
    expect(enrich).toHaveLength(6);
    expect(playlist).toHaveLength(9);
    expect(beatportEnrichmentId(now, random)).toMatch(/^enrich_1700000000000_/);
  });
});

describe('absoluteBeatportUrl (2869-2871)', () => {
  it('prefixes the host onto a relative path', () => {
    // The genre hero endpoint is the ONLY one that returns relative urls, and
    // the url is POSTed to the release-metadata scraper — so a relative one
    // would arrive with no host at all.
    expect(absoluteBeatportUrl('/release/nights/1234')).toBe(
      'https://www.beatport.com/release/nights/1234',
    );
  });

  it('leaves an absolute url alone, http or https', () => {
    expect(absoluteBeatportUrl('https://www.beatport.com/x')).toBe('https://www.beatport.com/x');
    // The test is startsWith('http'), so plain http passes through too.
    expect(absoluteBeatportUrl('http://other/x')).toBe('http://other/x');
  });

  it('is the empty string for nothing', () => {
    expect(absoluteBeatportUrl('')).toBe('');
    expect(absoluteBeatportUrl(undefined)).toBe('');
    expect(absoluteBeatportUrl(null)).toBe('');
  });
});
