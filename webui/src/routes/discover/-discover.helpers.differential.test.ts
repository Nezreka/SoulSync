import { describe, expect, it } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import { advColor, advState, advWaveY } from './-discover.adventurousness';
import {
  cleanArtistName,
  discoverTrackToSpotifyShape,
  listeningRecommendationReason,
  listeningRecommendationReasonTitle,
  normalizeTrack,
  recommendationReason,
  recommendationReasonTitle,
  whyIcon,
} from './-discover.helpers';
import { buildDiscoverArtistContext } from './-discover.seasonal';
import { yourAlbumsPickSource } from './-discover.your-albums-actions';
import { pickArtistDetailSource } from './-discover.your-artists';

/**
 * Differential parity: run MY port and the REAL vanilla side by side.
 *
 * The extraction machinery lives in `src/test/vanilla-extract` — three
 * differential suites share it now (helpers, artist map, artist web). See that
 * module for why the vanilla is read live and why escapeHtml is neutralised.
 */
const V = loadVanilla<{
  cleanArtistName: (s: unknown) => unknown;
  _normalizeTrack: (t: unknown) => unknown;
  _discoverTrackToSpotifyShape: (t: unknown) => unknown;
  _recommendationReason: (a: unknown) => string;
  _recommendationReasonTitle: (a: unknown) => string;
  _whyIcon: (t: unknown) => string;
  _listeningRecommendationReason: (a: unknown) => string;
  _listeningRecommendationReasonTitle: (a: unknown) => string;
  _pickArtistDetailSource: (a: unknown) => unknown;
  _yourAlbumsPickSource: (a: unknown) => unknown;
}>([
  'cleanArtistName',
  '_normalizeTrack',
  '_discoverTrackToSpotifyShape',
  '_recommendationReason',
  '_recommendationReasonTitle',
  '_whyIcon',
  '_listeningRecommendationReason',
  '_listeningRecommendationReasonTitle',
  '_pickArtistDetailSource',
  '_yourAlbumsPickSource',
]);

describe('cleanArtistName', () => {
  const cases = [
    'Aphex Twin',
    'Drake feat. Rihanna',
    'Drake feat Rihanna',
    'Drake ft. Future',
    'Drake ft Future',
    'Calvin Harris featuring Dua Lipa',
    'Jack Ü with Justin Bieber',
    'Skrillex x Diplo',
    'Sixty x Nine x Ten',
    'FEAT. leading',
    // Every pattern is case-insensitive, so each needs an UPPERCASE case with
    // real leading whitespace. Without these, dropping /i from a pattern
    // survives the whole suite — a mutation caught exactly that.
    'Drake FEAT. Rihanna',
    'Artist FEATURING Someone',
    'Drake FT. Future',
    'Jack Ü WITH Justin Bieber',
    'Skrillex X Diplo',
    'Artist Feat. Mixed Case',
    'Max Ferguson', //          contains "x " inside a word — must NOT be cut
    'Jimi Hendrix Experience', // "x " inside a word again
    '  padded  ',
    'a x b feat. c ft. d',
    '',
    'x',
  ];
  for (const input of cases) {
    it(`matches the vanilla for ${JSON.stringify(input)}`, () => {
      expect(cleanArtistName(input)).toBe(V.cleanArtistName(input));
    });
  }

  it('passes falsy values through unchanged, exactly as the vanilla does', () => {
    // Not normalised to a string — null stays null, undefined stays undefined.
    expect(cleanArtistName(null)).toBe(V.cleanArtistName(null));
    expect(cleanArtistName(undefined)).toBe(V.cleanArtistName(undefined));
    expect(cleanArtistName('')).toBe(V.cleanArtistName(''));
  });
});

describe('normalizeTrack', () => {
  const cases: Record<string, unknown>[] = [
    { track_name: 'Xtal', artist_name: 'Aphex Twin', album_name: 'SAW', duration_ms: 1000 },
    { name: 'Top level name', artists: [{ name: 'Nested Artist' }] },
    { name: 'String artists', artists: ['Plain String Artist'] },
    {
      track_data_json: {
        name: 'From json',
        artists: [{ name: 'JSON Artist' }],
        album: { name: 'JSON Album', images: [{ url: '/art.jpg' }] },
        duration_ms: 42,
      },
    },
    { track_data_json: { name: 'No album' } },
    { track_data_json: { album: { name: 'Album only' } } },
    { track_data_json: { album: { images: [] } }, album_cover_url: '/fallback.jpg' },
    {}, //                                       everything defaults
    { track_data_json: null, track_name: 'Null json falls back to the row' },
    { artists: [], track_name: 'Empty artists array' },
    { artists: [null], track_name: 'Null first artist' },
    { duration_ms: 0, track_name: 'Zero duration' },
  ];
  for (const [i, input] of cases.entries()) {
    it(`matches the vanilla for case ${i}`, () => {
      expect(normalizeTrack(input)).toEqual(V._normalizeTrack(input));
    });
  }
});

describe('discoverTrackToSpotifyShape', () => {
  const cases: Record<string, unknown>[] = [
    { spotify_track_id: 'abc', track_name: 'T', artist_name: 'A', album_name: 'Al' },
    { spotify_track_id: 'abc', name: 'via name', artist_name: 'A' },
    { track_name: 'T', artist_name: 'A', album_cover_url: '/c.jpg', duration_ms: 5 },
    { track_data_json: { id: 'x', name: 'J', artists: [{ name: 'JA' }] } },
    { track_data_json: { id: 'x', name: 'J', artists: ['plain'] } },
    { track_data_json: { id: 'x', name: 'J', artists: [] } },
    { track_data_json: { id: 'x', name: 'J' } }, //   no artists key at all
    { track_data_json: { id: 'x', artists: 'not-an-array' } },
    {},
  ];
  for (const [i, input] of cases.entries()) {
    it(`matches the vanilla for case ${i}`, () => {
      expect(discoverTrackToSpotifyShape(input)).toEqual(V._discoverTrackToSpotifyShape(input));
    });
  }

  it('does not mutate the incoming track_data_json', () => {
    const json = { id: 'x', artists: [{ name: 'A' }] };
    const track = { track_data_json: json };
    discoverTrackToSpotifyShape(track);
    expect(json.artists).toEqual([{ name: 'A' }]);
  });
});

describe('the recommendation reason strings', () => {
  const cases: unknown[] = [
    { because: ['One'] },
    { because: ['One', 'Two'] },
    { because: ['One', 'Two', 'Three'] },
    { because: ['One', 'Two', 'Three', 'Four', 'Five'] },
    { because: [] },
    { because: [], occurrence_count: 1 },
    { because: [], occurrence_count: 5 },
    { occurrence_count: 0 },
    {},
    null,
    undefined,
    // The escaping case: raw here, escaped by React at render.
    { because: ['AC/DC & Friends', '<b>bold</b>'] },
  ];
  for (const [i, input] of cases.entries()) {
    it(`recommendationReason matches for case ${i}`, () => {
      expect(recommendationReason(input as never)).toBe(V._recommendationReason(input));
    });
    it(`recommendationReasonTitle matches for case ${i}`, () => {
      expect(recommendationReasonTitle(input as never)).toBe(V._recommendationReasonTitle(input));
    });
    it(`listeningRecommendationReason matches for case ${i}`, () => {
      expect(listeningRecommendationReason(input as never)).toBe(
        V._listeningRecommendationReason(input),
      );
    });
    it(`listeningRecommendationReasonTitle matches for case ${i}`, () => {
      expect(listeningRecommendationReasonTitle(input as never)).toBe(
        V._listeningRecommendationReasonTitle(input),
      );
    });
  }
});

describe('whyIcon', () => {
  for (const t of ['genre', 'obscure', 'consensus', 'explore', 'unknown', '', null, undefined]) {
    it(`matches the vanilla for ${JSON.stringify(t)}`, () => {
      expect(whyIcon(t)).toBe(V._whyIcon(t));
    });
  }
});

describe('pickArtistDetailSource', () => {
  // Argument-pure, so it is compared against the REAL vanilla rather than
  // against expectations typed from reading it.
  const cases: unknown[] = [
    null,
    undefined,
    {},
    { active_source: 'spotify', spotify_artist_id: 'sp1' },
    // active source set but ITS field empty -> falls through to declaration order
    { active_source: 'spotify', deezer_artist_id: 'dz1' },
    { active_source: 'itunes', itunes_artist_id: 'it1', spotify_artist_id: 'sp1' },
    // no active source at all -> first populated field wins, in order
    { deezer_artist_id: 'dz1', discogs_artist_id: 'dc1' },
    { discogs_artist_id: 'dc1', spotify_artist_id: 'sp1' },
    { musicbrainz_artist_id: 'mb1' },
    { soul_id: 'hb1' },
    { amazon_artist_id: 'am1' },
    // active_source_id fallback, which requires a KNOWN active source
    { active_source: 'spotify', active_source_id: 'as1' },
    { active_source: 'nonsense', active_source_id: 'as1' },
    { active_source_id: 'as1' },
    // `source` is the alias for active_source
    { source: 'deezer', deezer_artist_id: 'dz1' },
    { source: 'SPOTIFY', spotify_artist_id: 'sp1' }, //  case-insensitive
    // numeric ids must come back as strings
    { active_source: 'spotify', spotify_artist_id: 12345 },
  ];
  for (const [i, input] of cases.entries()) {
    it(`matches the vanilla for case ${i}`, () => {
      expect(pickArtistDetailSource(input as never)).toEqual(V._pickArtistDetailSource(input));
    });
  }
});

describe('_yourAlbumsPickSource', () => {
  const cases: unknown[] = [
    {},
    // the priority order, each source alone
    { spotify_album_id: 'sp1' },
    { deezer_album_id: 'dz1' },
    { tidal_album_id: 'td1' },
    { discogs_release_id: 'dc1' },
    { discogs_id: 'dc2' },
    // ...and each pair, so a reordered branch is caught
    { spotify_album_id: 'sp1', deezer_album_id: 'dz1' },
    { deezer_album_id: 'dz1', tidal_album_id: 'td1' },
    { tidal_album_id: 'td1', discogs_release_id: 'dc1' },
    { spotify_album_id: 'sp1', discogs_id: 'dc2' },
    { spotify_album_id: 'sp1', deezer_album_id: 'dz1', tidal_album_id: 'td1', discogs_id: 'dc2' },
    // discogs_release_id wins over discogs_id
    { discogs_release_id: 'dc1', discogs_id: 'dc2' },
    // numeric ids must come back as STRINGS — deezer and discogs send numbers
    { deezer_album_id: 123456 },
    { discogs_id: 987654 },
    // falsy-but-present ids must not be picked
    { spotify_album_id: '', deezer_album_id: 'dz1' },
    { spotify_album_id: 0, tidal_album_id: 'td1' },
    { spotify_album_id: null, discogs_id: 'dc2' },
    { discogs_release_id: 0, discogs_id: 'dc2' },
    { spotify_album_id: '', deezer_album_id: '', tidal_album_id: '', discogs_id: '' },
  ];
  for (const [i, input] of cases.entries()) {
    it(`matches the vanilla for case ${i}`, () => {
      expect(yourAlbumsPickSource(input as never)).toEqual(V._yourAlbumsPickSource(input));
    });
  }
});

/**
 * The dial's maths, lifted separately because `_advWaveY` closes over the
 * module-level `_advWave` for its animation phase. The port takes `phase` as an
 * argument instead, so the harness supplies a real `_advWave` and the tests set
 * its phase before each comparison.
 */
const ADV = loadVanilla<{
  _advState: (v: number) => string;
  _advColor: (v: number, light?: number, alpha?: number | null) => string;
  _advWaveY: (u: number, v: number) => number;
  _advWave: { value: number; phase: number };
}>(
  ['_advState', '_advColor', '_advWaveY'],
  'const _advWave = { value: 0.3, phase: 0, raf: null, dragging: false };',
  ['_advWave'],
);

describe('_advState', () => {
  // Every band, both sides of every boundary — the bounds are exclusive, so
  // 0.40 is "Adventurous" and a `<=` slip would put it in "Balanced".
  const cases = [
    -1, 0, 0.0001, 0.119, 0.12, 0.1201, 0.3, 0.399, 0.4, 0.4001, 0.5, 0.699, 0.7, 0.7001, 0.9, 1, 2,
  ];
  for (const v of cases) {
    it(`matches the vanilla at ${v}`, () => {
      expect(advState(v)).toBe(ADV._advState(v));
    });
  }
});

describe('_advColor', () => {
  const values = [-0.5, 0, 0.25, 0.333333, 0.5, 0.75, 1, 1.5];
  for (const v of values) {
    it(`matches the vanilla hue at ${v}`, () => {
      expect(advColor(v)).toBe(ADV._advColor(v));
    });
  }

  it('matches for each lightness the page actually uses', () => {
    for (const v of values) {
      expect(advColor(v, 55)).toBe(ADV._advColor(v, 55));
      expect(advColor(v, 62)).toBe(ADV._advColor(v, 62));
      expect(advColor(v, 50, 0.16)).toBe(ADV._advColor(v, 50, 0.16));
    }
  });

  it('matches the falsy-lightness fallback, quirk and all', () => {
    // `light || 55` — 0 yields 55, not black. Transcribed, not "fixed".
    expect(advColor(0.5, 0)).toBe(ADV._advColor(0.5, 0));
    expect(advColor(0.5, undefined)).toBe(ADV._advColor(0.5, undefined));
  });

  it('matches an alpha of ZERO, which `!= null` keeps as hsla', () => {
    expect(advColor(0.5, 55, 0)).toBe(ADV._advColor(0.5, 55, 0));
  });
});

describe('_advWaveY', () => {
  const phases = [0, 0.5, 1.3, 3.14159, 12.5];
  const values = [0, 0.001, 0.3, 0.5, 1];
  const positions = [0, 0.25, 0.5, 0.75, 1];
  for (const phase of phases) {
    for (const v of values) {
      it(`matches the vanilla at phase ${phase}, v ${v}`, () => {
        ADV._advWave.phase = phase;
        for (const u of positions) {
          expect(advWaveY(u, v, phase)).toBeCloseTo(ADV._advWaveY(u, v), 12);
        }
      });
    }
  }

  it('matches at v=0, where the second harmonic is SKIPPED entirely', () => {
    // `if (v > 0)` — at rest there is one sine, not a sum. Dropping the guard
    // changes nothing numerically (the term is multiplied by v) but the port
    // keeps it so the shapes stay identical line for line.
    ADV._advWave.phase = 2.2;
    for (const u of positions) {
      expect(advWaveY(u, 0, 2.2)).toBeCloseTo(ADV._advWaveY(u, 0), 12);
    }
  });
});

/**
 * `_buildDiscoverArtistContext` is argument-pure and is the single hardest
 * fallback chain on the page — three fallbacks per provider id, plus an id that
 * is computed and then deliberately overwritten. It is shared by Seasonal and
 * both cache-section open paths, so a drift here misroutes every album download.
 */
const CTX = loadVanilla<{
  _buildDiscoverArtistContext: (s: unknown, n: unknown, sd?: unknown, ad?: unknown) => unknown;
}>(['_buildDiscoverArtistContext']);

describe('_buildDiscoverArtistContext', () => {
  const albumWith = (id?: string, name?: string) => ({ artists: [{ id, name }] });
  const cases: [string, unknown, unknown, unknown, unknown][] = [
    ['bare', 'spotify', 'Aphex Twin', {}, {}],
    ['no source at all', '', '', {}, {}],
    ['source from active_source', '', 'A', { active_source: 'deezer', deezer_artist_id: 'dz' }, {}],
    ['source from source alias', '', 'A', { source: 'itunes', itunes_artist_id: 'it' }, {}],
    ['explicit source beats the row', 'spotify', 'A', { active_source: 'deezer' }, {}],
    ['UPPERCASE source is lowercased', 'SPOTIFY', 'A', { spotify_artist_id: 'sp' }, {}],
    // the artist_-prefixed aliases
    ['spotify alias', 'spotify', 'A', { artist_spotify_id: 'sp2' }, {}],
    ['itunes alias', 'itunes', 'A', { artist_itunes_id: 'it2' }, {}],
    ['deezer alias', 'deezer', 'A', { artist_deezer_id: 'dz2' }, {}],
    ['deezer bare id', 'deezer', 'A', { deezer_id: 'dz3' }, {}],
    ['discogs aliases', 'discogs', 'A', { discogs_id: 'dc' }, {}],
    ['amazon aliases', 'amazon', 'A', { amazon_id: 'am' }, {}],
    ['hydrabase via soul_id', 'hydrabase', 'A', { soul_id: 'hb' }, {}],
    ['hydrabase via alias', 'hydrabase', 'A', { hydrabase_artist_id: 'hb2' }, {}],
    // the album-artist fallback, which applies ONLY to the active provider
    [
      'album artist fills the ACTIVE provider',
      'spotify',
      '',
      {},
      albumWith('alb1', 'Album Artist'),
    ],
    ['album artist does NOT fill an inactive one', 'deezer', '', {}, albumWith('alb1', 'X')],
    ['album artist supplies the name', 'spotify', '', {}, albumWith('alb1', 'From Album')],
    [
      'explicit name beats the album artist',
      'spotify',
      'Explicit',
      {},
      albumWith('a', 'From Album'),
    ],
    // id resolution + the deliberate overwrite
    ['id from active_source_id', 'spotify', 'A', { active_source_id: 'as1' }, {}],
    [
      'active provider id OVERWRITES active_source_id',
      'spotify',
      'A',
      { active_source_id: 'as1', spotify_artist_id: 'sp9' },
      {},
    ],
    [
      'unknown active source keeps the generic id',
      'bandcamp',
      'A',
      { active_source_id: 'as1', spotify_artist_id: 'sp9' },
      {},
    ],
    ['id from artist_id', 'spotify', 'A', { artist_id: 'ar1' }, {}],
    // extra keys must survive the spread
    ['unknown keys pass through', 'spotify', 'A', { custom_field: 'keep me' }, {}],
    // awkward shapes
    ['artists not an array', 'spotify', 'A', {}, { artists: 'nope' }],
    // The Array.isArray guard only shows itself on an ARRAY-LIKE non-array:
    // `'nope'?.[0]` is 'n', whose .id is undefined, so a string cannot tell the
    // guarded and unguarded versions apart. An object with a numeric key can —
    // dropping the guard would hand back that artist's id.
    [
      'artists is an array-LIKE object',
      'spotify',
      'A',
      {},
      { artists: { 0: { id: 'fake', name: 'Fake' } } },
    ],
    ['artists is a plain object', 'spotify', 'A', {}, { artists: { id: 'x' } }],
    ['artists is a number', 'spotify', 'A', {}, { artists: 42 }],
    ['empty artists array', 'spotify', 'A', {}, { artists: [] }],
    ['no albumData at all', 'spotify', 'A', {}, undefined],
    ['no sourceData at all', 'spotify', 'A', undefined, undefined],
  ];
  for (const [label, src, name, sd, ad] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const mine =
        sd === undefined
          ? buildDiscoverArtistContext(src as string, name as string)
          : ad === undefined
            ? buildDiscoverArtistContext(src as string, name as string, sd as never)
            : buildDiscoverArtistContext(src as string, name as string, sd as never, ad as never);
      const theirs =
        sd === undefined
          ? CTX._buildDiscoverArtistContext(src, name)
          : ad === undefined
            ? CTX._buildDiscoverArtistContext(src, name, sd)
            : CTX._buildDiscoverArtistContext(src, name, sd, ad);
      expect(mine).toEqual(theirs);
    });
  }
});
