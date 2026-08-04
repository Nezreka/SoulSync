import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { SearchAlbum } from './-search.types';

import {
  albumIdentity,
  albumMetaLine,
  albumOwnershipByIdentity,
  artistDetailPath,
  artistMetaLine,
  fallbackBannerText,
  fallbackFor,
  formatDuration,
  formatVideoDuration,
  formatViewCount,
  hasAnyResults,
  inLibraryArtistPath,
  isIdLookupQuery,
  labelDetailPath,
  labelMetaLine,
  MIN_QUERY_LENGTH,
  pickerSource,
  SEARCH_DEBOUNCE_MS,
  shouldSearch,
  sourceResultsFromResponse,
  splitAlbums,
  trackMetaLine,
  visibleSources,
} from './-search.helpers';
import { emptySourceResults } from './-search.helpers';
import { SOURCE_LABELS, SOURCE_ORDER } from './-search.types';

const album = (over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  id: 'a1',
  name: 'Drukqs',
  artist: 'Aphex Twin',
  album_type: 'album',
  ...over,
});

describe('query gating', () => {
  it('needs two characters before it fires', () => {
    expect(shouldSearch('a')).toBe(false);
    expect(shouldSearch('ap')).toBe(true);
  });

  it('ignores surrounding whitespace', () => {
    expect(shouldSearch('  a  ')).toBe(false);
    expect(shouldSearch('  ap  ')).toBe(true);
  });

  it('keeps the vanilla floor and debounce', () => {
    expect(MIN_QUERY_LENGTH).toBe(2);
    // Raised from 300ms for #751 — a shorter debounce fires a request per
    // keystroke against rate-limited providers.
    expect(SEARCH_DEBOUNCE_MS).toBe(600);
  });
});

describe('isIdLookupQuery', () => {
  it('recognises a bare MusicBrainz uuid', () => {
    expect(isIdLookupQuery('770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce')).toBe(true);
    expect(isIdLookupQuery('  770A1E6B-2D17-4BBE-A0C2-A3C4F77E9BCE  ')).toBe(true);
  });

  it('leaves a query that merely CONTAINS one as a text search', () => {
    // Anchored on purpose: "aphex 770a..." is someone searching, not pasting.
    expect(isIdLookupQuery('aphex 770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce')).toBe(false);
  });

  it('is not fooled by a near-miss', () => {
    expect(isIdLookupQuery('770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bc')).toBe(false);
    expect(isIdLookupQuery('not-a-uuid')).toBe(false);
  });
});

describe('visibleSources', () => {
  it('hides experimental sources until they are enabled', () => {
    const none = visibleSources(new Set());
    expect(none).not.toContain('jiosaavn');
    expect(none).not.toContain('bandcamp');
    expect(none).toContain('spotify');
  });

  it('shows one experimental source without showing the other', () => {
    const only = visibleSources(new Set(['bandcamp']));
    expect(only).toContain('bandcamp');
    expect(only).not.toContain('jiosaavn');
  });

  it('keeps the canonical order', () => {
    const all = visibleSources(new Set(['jiosaavn', 'bandcamp']));
    expect(all).toEqual([...SOURCE_ORDER]);
  });
});

describe('pickerSource', () => {
  it('maps spotify_free onto the spotify icon', () => {
    // spotify_free has a label but NO icon in the picker; /status can report it
    // as the primary source, and leaving it unmapped renders nothing active.
    expect(pickerSource('spotify_free')).toBe('spotify');
  });

  it('passes everything else through, and defaults to spotify', () => {
    expect(pickerSource('deezer')).toBe('deezer');
    expect(pickerSource(undefined)).toBe('spotify');
    expect(pickerSource('')).toBe('spotify');
  });
});

describe('fallbackFor', () => {
  it('reports the source that actually answered', () => {
    expect(fallbackFor('spotify', { primary_source: 'deezer' })).toBe('deezer');
  });

  it('is silent when the requested source answered', () => {
    expect(fallbackFor('deezer', { primary_source: 'deezer' })).toBeNull();
  });

  it('DOES report spotify_free as a fallback from spotify', () => {
    // A raw comparison, as the vanilla has it (shared-helpers.js:504). On a
    // no-credentials instance the server answers a 'spotify' request with
    // 'spotify_free', and the banner naming both is the point — normalising the
    // two through pickerSource would silently swallow it.
    expect(fallbackFor('spotify', { primary_source: 'spotify_free' })).toBe('spotify_free');
  });

  it('reports nothing when the server served exactly what was asked for', () => {
    expect(fallbackFor('spotify', { primary_source: 'spotify' })).toBeNull();
    expect(fallbackFor('deezer', { primary_source: 'deezer' })).toBeNull();
  });

  it('falls back to metadata_source when primary_source is absent', () => {
    expect(fallbackFor('spotify', { metadata_source: 'itunes' })).toBe('itunes');
  });

  it('is silent when the server said nothing about the source', () => {
    expect(fallbackFor('spotify', {})).toBeNull();
  });

  it('names both sources in the banner', () => {
    expect(fallbackBannerText('spotify', 'deezer')).toBe('Spotify unavailable — showing Deezer.');
  });
});

describe('splitAlbums', () => {
  it('sends singles and eps one way, everything else the other', () => {
    const rows = [
      album({ id: '1', album_type: 'album' }),
      album({ id: '2', album_type: 'single' }),
      album({ id: '3', album_type: 'ep' }),
      album({ id: '4', album_type: 'compilation' }),
    ];
    const { albums, singlesAndEps } = splitAlbums(rows);
    expect(albums.map((a) => a.id)).toEqual(['1', '4']);
    expect(singlesAndEps.map((a) => a.id)).toEqual(['2', '3']);
  });

  it('treats a missing type as an album', () => {
    // "albums" is the catch-all; an unknown type must still be rendered.
    const { albums } = splitAlbums([album({ album_type: undefined })]);
    expect(albums).toHaveLength(1);
  });
});

describe('albumOwnershipByIdentity — the badge-misalignment fix', () => {
  it('carries ownership to the right album when the response INTERLEAVES', () => {
    // The bug this replaces: the vanilla applied library-check answers by
    // indexing a document-order selector (all albums, then all singles) against
    // the request order. core/search/orchestrator.py returns the provider's
    // array unsorted, so the two disagree the moment types interleave.
    const requested = [
      album({ id: 'A1', album_type: 'album' }),
      album({ id: 'S1', album_type: 'single' }),
      album({ id: 'A2', album_type: 'album' }),
    ];
    // Only the LAST row is owned.
    const owned = albumOwnershipByIdentity(requested, [false, false, true]);

    expect(owned.has(albumIdentity(requested[2]))).toBe(true);
    // The vanilla would have marked requested[1] here, because A2 is the second
    // card in document order.
    expect(owned.has(albumIdentity(requested[1]))).toBe(false);
    expect(owned.has(albumIdentity(requested[0]))).toBe(false);
  });

  it('survives a short or missing flags array', () => {
    const requested = [album({ id: '1' }), album({ id: '2' })];
    expect(albumOwnershipByIdentity(requested, [true]).size).toBe(1);
    expect(albumOwnershipByIdentity(requested, undefined).size).toBe(0);
    expect(albumOwnershipByIdentity(requested, []).size).toBe(0);
  });

  it('identifies by id when there is one, and by name+artist when there is not', () => {
    expect(albumIdentity(album({ id: 'x' }))).toBe('id:x');
    expect(albumIdentity(album({ id: undefined }))).toBe('na:drukqs|aphex twin');
    // Case-insensitive, so a source that title-cases differently still matches.
    expect(albumIdentity(album({ id: undefined, name: 'DRUKQS' }))).toBe('na:drukqs|aphex twin');
  });

  it('does not collide two same-named albums by different artists', () => {
    const a = album({ id: undefined, name: 'Untitled', artist: 'A' });
    const b = album({ id: undefined, name: 'Untitled', artist: 'B' });
    expect(albumIdentity(a)).not.toBe(albumIdentity(b));
  });
});

describe('formatViewCount', () => {
  it('abbreviates billions, millions and thousands', () => {
    // Billions are ordinary on a music-video grid; without that branch a
    // 1.2B-view video reads "1200.0M".
    expect(formatViewCount(1_200_000_000)).toBe('1.2B');
    expect(formatViewCount(1_200_000)).toBe('1.2M');
    expect(formatViewCount(3_400)).toBe('3.4K');
    expect(formatViewCount(742)).toBe('742');
  });

  it('is empty for nothing and for nonsense', () => {
    expect(formatViewCount(0)).toBe('');
    expect(formatViewCount(undefined)).toBe('');
    expect(formatViewCount(-5)).toBe('');
  });
});

describe('formatDuration', () => {
  it('is m:ss with a zero-padded seconds field', () => {
    expect(formatDuration(215_000)).toBe('3:35');
    expect(formatDuration(65_000)).toBe('1:05');
    expect(formatDuration(600_000)).toBe('10:00');
  });

  it('is empty when there is no duration', () => {
    expect(formatDuration(0)).toBe('');
    expect(formatDuration(undefined)).toBe('');
  });
});

describe('formatVideoDuration', () => {
  it('reads its input as SECONDS', () => {
    // The same 3:35 as formatDuration(215_000) — the units are the only
    // difference between the two functions, and the reason there are two.
    expect(formatVideoDuration(215)).toBe('3:35');
    expect(formatVideoDuration(65)).toBe('1:05');
    expect(formatVideoDuration(600)).toBe('10:00');
  });

  it('does not roll over into hours', () => {
    // The vanilla printed 90:00 for a 90-minute upload rather than 1:30:00;
    // kept, because a music-video grid effectively never sees one.
    expect(formatVideoDuration(5400)).toBe('90:00');
  });

  it('floors a fractional duration instead of printing a decimal', () => {
    expect(formatVideoDuration(59.7)).toBe('0:59');
  });

  it('is empty when there is no duration', () => {
    expect(formatVideoDuration(0)).toBe('');
    expect(formatVideoDuration(undefined)).toBe('');
    expect(formatVideoDuration(-5)).toBe('');
  });
});

describe('meta lines', () => {
  it('labels an artist by which section it is in, not by a count', () => {
    // A count is tempting and wrong: _build_db_artists sends only id, name and
    // image_url (core/search/orchestrator.py:121-134), so every library artist
    // would read "0 tracks".
    expect(artistMetaLine(true)).toBe('In Your Library');
    expect(artistMetaLine(false)).toBe('Artist');
  });

  it('joins a track artist and album, both being plain strings', () => {
    // `album` is the album NAME — sources.py copies Track.album, a str.
    expect(trackMetaLine({ artist: 'Aphex Twin', album: 'Drukqs' })).toBe('Aphex Twin • Drukqs');
    expect(trackMetaLine({ artist: 'Aphex Twin' })).toBe('Aphex Twin');
    expect(trackMetaLine({})).toBe('');
  });

  it('gives an album its year, or the vanilla N/A', () => {
    expect(albumMetaLine({ artist: 'Aphex Twin', release_date: '2001-10-22' })).toBe(
      'Aphex Twin • 2001',
    );
    expect(albumMetaLine({ artist: 'Aphex Twin' })).toBe('Aphex Twin • N/A');
  });

  it('joins a label type and area, or says what it is', () => {
    expect(labelMetaLine({ type: 'Original Production', area: 'US' })).toBe(
      'Original Production • US',
    );
    expect(labelMetaLine({ type: 'Original Production' })).toBe('Original Production');
    expect(labelMetaLine({})).toBe('Record label');
  });
});

describe('response unpacking', () => {
  it('reads the historical spotify_* field names for every source', () => {
    // These names carry Deezer/iTunes/MusicBrainz results too — renaming them
    // would mean changing the API contract.
    const results = sourceResultsFromResponse({
      db_artists: [{ name: 'in library' }],
      spotify_artists: [{ name: 'from source' }],
      spotify_albums: [album()],
      spotify_tracks: [{ name: 'track' }],
    });
    expect(results.db_artists).toHaveLength(1);
    expect(results.artists).toHaveLength(1);
    expect(results.albums).toHaveLength(1);
    expect(results.tracks).toHaveLength(1);
    expect(results.videos).toEqual([]);
  });

  it('gives every list a value, so no consumer reads undefined', () => {
    expect(sourceResultsFromResponse({})).toEqual(emptySourceResults());
  });
});

describe('hasAnyResults', () => {
  it('counts videos, so a video-only search is not empty', () => {
    const results = emptySourceResults();
    results.videos = [{ video_id: 'v1' }];
    expect(hasAnyResults(results)).toBe(true);
  });

  it('is false only when every list is empty', () => {
    expect(hasAnyResults(emptySourceResults())).toBe(false);
  });
});

describe('parity with the vanilla source tables', () => {
  const vanilla = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf8');

  it('keeps the picker order identical', () => {
    // The row the user sees. Reading the vanilla's own array rather than
    // restating it, because a reorder here is invisible in review.
    const match = /const SOURCE_ORDER = \[([\s\S]*?)\];/.exec(vanilla);
    expect(match).toBeTruthy();
    const order = Array.from(match![1].matchAll(/'([a-z_]+)'/g)).map((m) => m[1]);
    expect(order).toEqual([...SOURCE_ORDER]);
  });

  it('keeps every source label and badge class', () => {
    // The badge classes are CSS hooks; a typo silently unstyles a source.
    for (const [source, label] of Object.entries(SOURCE_LABELS)) {
      expect(vanilla).toContain(`text: '${label.text}'`);
      expect(vanilla).toContain(`badgeClass: '${label.badgeClass}'`);
      expect(vanilla).toContain(`${source}: {`);
    }
  });

  it('keeps the experimental set in step', () => {
    expect(vanilla).toContain("const EXPERIMENTAL_SOURCES = new Set(['jiosaavn', 'bandcamp']);");
  });
});

describe('detail paths', () => {
  /**
   * These mirror buildArtistDetailPath / buildLabelDetailPath (init.js:2964,
   * 3003) and feed the routes /artist-detail/$source/$id and /label-detail/$id.
   *
   * Pinned because the first version of them was GUESSED — `/artist-detail/<id>`
   * with no source segment, which parseArtistDetailPath rejects outright
   * (`segs.length < 3`) and which matches no route. Clicking an artist did
   * nothing at all.
   */
  it('puts the SOURCE segment in an artist path', () => {
    expect(artistDetailPath('sp1', 'spotify')).toBe('/artist-detail/spotify/sp1');
    // Three segments. Two is not a shorter version of this — it is unroutable.
    expect(artistDetailPath('sp1', 'spotify').split('/').filter(Boolean)).toHaveLength(3);
  });

  it('files a library artist under the literal "library"', () => {
    // A library artist has no metadata source, and the slot cannot be empty.
    expect(artistDetailPath(42)).toBe('/artist-detail/library/42');
    expect(artistDetailPath(42, null)).toBe('/artist-detail/library/42');
    expect(artistDetailPath(42, '')).toBe('/artist-detail/library/42');
    expect(artistDetailPath(42, '   ')).toBe('/artist-detail/library/42');
  });

  /**
   * The "In Your Library" section is the one place a search result points at
   * something the user already owns, so it opens the page that can manage it.
   * The old vanilla search sent every one of them to the legacy artist page,
   * which is the bug this replaces (docs/library-v2-issues.md §10/§11).
   */
  it('opens an "In Your Library" hit in Library v2 when v2 knows the artist', () => {
    // ldp-05 (iss29-B05): a search arrival lands on the legacy artist page's
    // shape — full discography, cards, rich header. Without these params the
    // deep link fell back to the in-library defaults, so the same artist looked
    // different depending on whether v2 had mapped it.
    expect(inLibraryArtistPath({ id: 42, library_v2_id: 7 })).toBe(
      '/library?artist=7&releases=all&releaseView=cards&header=rich',
    );
  });

  it('falls back to artist detail when v2 has not mapped the artist', () => {
    // No v2 id means no v2 row — routing there would open an artist that does
    // not exist. Both an absent and an explicitly null id take the fallback.
    expect(inLibraryArtistPath({ id: 42 })).toBe('/artist-detail/library/42');
    expect(inLibraryArtistPath({ id: 42, library_v2_id: null })).toBe('/artist-detail/library/42');
  });

  it('lowercases the source, as _normalizeArtistDetailSource does', () => {
    expect(artistDetailPath('x', 'Deezer')).toBe('/artist-detail/deezer/x');
  });

  it('carries the name for sources with no id lookup', () => {
    // Bandcamp cannot resolve by id at all; on a cold load the name is all
    // there is to go on.
    expect(artistDetailPath('abc', 'bandcamp', 'Aphex Twin')).toBe(
      '/artist-detail/bandcamp/abc?name=Aphex%20Twin',
    );
    expect(artistDetailPath('abc', 'bandcamp')).toBe('/artist-detail/bandcamp/abc');
  });

  it('encodes ids and names that would otherwise break the path', () => {
    expect(artistDetailPath('a/b', 'spotify')).toBe('/artist-detail/spotify/a%2Fb');
    expect(artistDetailPath('1', 'spotify', 'AC/DC')).toBe('/artist-detail/spotify/1?name=AC%2FDC');
  });

  it('builds a label path with its optional name', () => {
    expect(labelDetailPath('l1')).toBe('/label-detail/l1');
    expect(labelDetailPath('l1', 'Warp')).toBe('/label-detail/l1?name=Warp');
  });
});
