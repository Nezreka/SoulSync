import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

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
import { pickArtistDetailSource } from './-discover.your-artists';

/**
 * Differential parity: run MY port and the REAL vanilla side by side.
 *
 * Reading a function and re-implementing it is how the search port shipped an
 * artist link that went nowhere. So the parity claim here is checkable rather
 * than asserted: the vanilla functions are lifted out of discover.js by
 * brace-matching (a regex cannot — the bodies contain nested braces, template
 * literals and regex literals with braces in them), evaluated, and compared
 * against this port over a matrix of awkward inputs.
 *
 * SOURCE NOTE: this reads the LIVE webui/static/discover.js on purpose. The
 * file still exists during the port PR, so there is no reason to commit a
 * 12,319-line copy of it. When the cleanup PR deletes discover.js, this switches
 * to a frozen `__fixtures__/-vanilla-discover.js` — the same way the downloads
 * port did it — because converting to hand-written expectations would swap
 * "matches the code it replaced" for "matches what I believed it did", which is
 * the exact failure this test exists to rule out.
 */
const SOURCE = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');

/** Lift one function out by brace-matching, string- and regex-literal aware. */
function extractFunction(name: string): string {
  const decl = new RegExp(`^(?:async )?function ${name}\\s*\\(`, 'm');
  const m = decl.exec(SOURCE);
  if (!m) throw new Error(`vanilla function ${name} not found in discover.js`);

  let i = SOURCE.indexOf('{', m.index);
  let depth = 0;
  let inString: string | null = null;
  let escaped = false;

  for (; i < SOURCE.length; i++) {
    const c = SOURCE[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (c === '\\') escaped = true;
      else if (c === inString) inString = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') inString = c;
    else if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return SOURCE.slice(m.index, i + 1);
    }
  }
  throw new Error(`unbalanced braces extracting ${name}`);
}

/**
 * Evaluate the named vanilla functions.
 *
 * `escapeHtml` is supplied as IDENTITY on purpose. The vanilla reason-strings
 * escape inline because they are destined for innerHTML; the React port returns
 * raw text and lets React escape at render. Neutralising escapeHtml makes the
 * vanilla return raw text too, so the comparison comes down to the LOGIC — which
 * is the thing that has to match. (Escaping itself is covered by React.)
 */
function loadVanilla<T>(names: string[]): T {
  const preamble = 'const escapeHtml = (s) => s;';
  const body = names.map(extractFunction).join('\n');
  return new Function(`${preamble}\n${body}\nreturn { ${names.join(', ')} };`)() as T;
}

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
