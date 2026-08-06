/**
 * The server tab's pure core, pinned against pages-extra.js 59-94 and 484.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CompareTrack } from './-sync.server';

import {
  COMPARE_SERVER_ICONS,
  COMPARE_SOURCE_ICONS,
  addServerTrack,
  addTrackPosition,
  applyPickedTrack,
  applyRemovedTrack,
  removeConfirmOptions,
  removeServerTrack,
  replaceServerTrack,
  searchBitrateText,
  searchFormatBadge,
  searchLibraryTracks,
  searchModalTitle,
  searchResultsHeaderText,
  searchSeed,
  compareConfidenceBadge,
  compareFilterLabel,
  compareFooterText,
  compareMetaText,
  compareMissingHint,
  compareServerIcon,
  compareServerLabel,
  compareSourceIcon,
  compareSourceLabel,
  compareStats,
  fetchComparePlaylist,
  fetchMirroredMatches,
  fetchMirroredPlaylistById,
  fetchServerPlaylistData,
  formatDurationMs,
  serverCardHue,
  serverTabTitle,
  splitServerPlaylists,
} from './-sync.server';

const PL = (id: string, name: string) => ({ id, name, track_count: 10 });

describe('splitServerPlaylists (59-73)', () => {
  it('marks a playlist synced from EITHER the mirrored list or the history', () => {
    const { synced, unsynced } = splitServerPlaylists(
      [PL('1', 'Road Trip'), PL('2', 'From History'), PL('3', 'Neither')],
      ['Road Trip'],
      ['From History'],
    );
    expect(synced.map((p) => p.name)).toEqual(['Road Trip', 'From History']);
    expect(unsynced.map((p) => p.name)).toEqual(['Neither']);
    expect(synced[0]._synced).toBe(true);
    expect(unsynced[0]._synced).toBe(false);
  });

  it('matches trimmed and case-insensitively, on BOTH sides', () => {
    const { synced } = splitServerPlaylists([PL('1', '  ROAD trip  ')], ['road TRIP'], []);
    expect(synced).toHaveLength(1);
  });

  it('is not fooled by a partial name', () => {
    const { unsynced } = splitServerPlaylists([PL('1', 'Road')], ['Road Trip'], []);
    expect(unsynced).toHaveLength(1);
  });

  it('does not mutate the rows it was given', () => {
    const rows = [PL('1', 'Road Trip')];
    splitServerPlaylists(rows, ['Road Trip'], []);
    expect('_synced' in rows[0]).toBe(false);
  });
});

describe('serverTabTitle (76-78)', () => {
  it('upper-cases only the first letter', () => {
    expect(serverTabTitle('plex')).toBe('Server Playlists (Plex)');
    expect(serverTabTitle('navidrome')).toBe('Server Playlists (Navidrome)');
  });

  it('leaves the parens empty when the type is unknown', () => {
    expect(serverTabTitle(undefined)).toBe('Server Playlists ()');
    expect(serverTabTitle('')).toBe('Server Playlists ()');
  });
});

describe('serverCardHue (94)', () => {
  it('steps by 37 from 200 and wraps at 360', () => {
    expect(serverCardHue(0)).toBe(200);
    expect(serverCardHue(1)).toBe(237);
    // 4*37+200 = 348; the next one wraps.
    expect(serverCardHue(4)).toBe(348);
    expect(serverCardHue(5)).toBe(25);
  });
});

describe('formatDurationMs (484)', () => {
  it('ROUNDS to the nearest second — not floor', () => {
    expect(formatDurationMs(1500)).toBe('0:02');
    expect(formatDurationMs(1400)).toBe('0:01');
  });

  it('pads the seconds and does not roll into hours', () => {
    expect(formatDurationMs(219000)).toBe('3:39');
    expect(formatDurationMs(61000)).toBe('1:01');
    expect(formatDurationMs(3600000)).toBe('60:00');
  });

  it('is empty for nothing', () => {
    expect(formatDurationMs(0)).toBe('');
    expect(formatDurationMs(null)).toBe('');
    expect(formatDurationMs(undefined)).toBe('');
  });
});

describe('fetchServerPlaylistData (41-52)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads all three in parallel and returns the names', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url === '/api/server/playlists') {
          return new Response(
            JSON.stringify({ success: true, server_type: 'plex', playlists: [PL('1', 'A')] }),
          );
        }
        if (url === '/api/mirrored-playlists') {
          return new Response(JSON.stringify([{ name: 'A' }]));
        }
        return new Response(JSON.stringify(['B']));
      }),
    );
    const result = await fetchServerPlaylistData();
    expect(urls).toEqual([
      '/api/server/playlists',
      '/api/mirrored-playlists',
      '/api/sync/history/names',
    ]);
    expect(result.mirroredNames).toEqual(['A']);
    expect(result.historyNames).toEqual(['B']);
  });

  it('a broken mirrored or history response still yields the playlists (48, 51)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/server/playlists') {
          return new Response(JSON.stringify({ success: true, playlists: [PL('1', 'A')] }));
        }
        // Not JSON — .json() rejects, which the vanilla swallows.
        return new Response('<html>502</html>');
      }),
    );
    const result = await fetchServerPlaylistData();
    expect(result.data.playlists).toHaveLength(1);
    expect(result.mirroredNames).toEqual([]);
    expect(result.historyNames).toEqual([]);
  });

  it('a non-array mirrored payload is ignored, not spread', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/server/playlists'
          ? new Response(JSON.stringify({ success: true, playlists: [] }))
          : new Response(JSON.stringify({ error: 'nope' })),
      ),
    );
    const result = await fetchServerPlaylistData();
    expect(result.mirroredNames).toEqual([]);
  });
});

describe('fetchMirroredMatches (158-171)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps only exact name matches, trimmed and case-insensitive', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              { id: 1, name: 'Road Trip' },
              { id: 2, name: '  road trip ' },
              { id: 3, name: 'Road Trip 2' },
            ]),
          ),
      ),
    );
    expect((await fetchMirroredMatches('Road Trip')).map((p) => p.id)).toEqual([1, 2]);
  });

  it('SWALLOWS a failure and answers empty — the server-only path (168-170)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    await expect(fetchMirroredMatches('Road Trip')).resolves.toEqual([]);
  });
});

describe('fetchMirroredPlaylistById (237-239)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches the ONE row by id, not the whole list', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        return new Response(JSON.stringify({ id: 10, name: 'Road Trip', source: 'tidal' }));
      }),
    );
    expect(await fetchMirroredPlaylistById(10)).toMatchObject({ id: 10, source: 'tidal' });
    expect(urls).toEqual(['/api/mirrored-playlists/10']);
  });
});

/* ── The compare editor's core ────────────────────────────────────────────── */

const T = (status: string, extra: Partial<CompareTrack> = {}): CompareTrack => ({
  match_status: status,
  ...extra,
});

describe('compareStats + the labels derived from it (356-382)', () => {
  const tracks = [T('matched'), T('matched'), T('missing'), T('extra')];

  it('counts each status', () => {
    expect(compareStats(tracks)).toEqual({ matched: 2, missing: 1, extra: 1, total: 4 });
  });

  it('the footer denominator EXCLUDES extras — they get their own clause', () => {
    expect(compareFooterText(compareStats(tracks))).toBe('2/3 matched · 1 extra on server');
  });

  it('drops the extra clause when there are none', () => {
    expect(compareFooterText(compareStats([T('matched'), T('missing')]))).toBe('1/2 matched');
  });

  it('labels every pill with its own count', () => {
    const stats = compareStats(tracks);
    expect(compareFilterLabel('all', stats)).toBe('All (4)');
    expect(compareFilterLabel('matched', stats)).toBe('Matched (2)');
    expect(compareFilterLabel('missing', stats)).toBe('Missing (1)');
    expect(compareFilterLabel('extra', stats)).toBe('Extra (1)');
  });
});

describe('the three compare labels + icons (298, 312-314, 323-326)', () => {
  it("are THREE distinct tables — source, server, and the mirrored tab's", () => {
    // 313: the same six the disambiguation modal uses (193), deliberately one
    // table. Spotify is a green circle, NOT the mirrored tab's musical note.
    expect(COMPARE_SOURCE_ICONS).toEqual({
      spotify: '🟢',
      tidal: '🌊',
      youtube: '▶️',
      beatport: '🎛️',
      deezer: '🟣',
      file: '📄',
    });
    // 314: three keys only, and deezer's purple circle here means JELLYFIN.
    expect(COMPARE_SERVER_ICONS).toEqual({ plex: '🟠', jellyfin: '🟣', navidrome: '🔵' });
    expect('plex' in COMPARE_SOURCE_ICONS).toBe(false);
    expect('spotify' in COMPARE_SERVER_ICONS).toBe(false);
  });

  it('has DIFFERENT fallbacks per column — clipboard vs laptop', () => {
    expect(compareSourceIcon('spotify')).toBe('🟢');
    expect(compareSourceIcon('navidrome')).toBe('📋');
    expect(compareServerIcon('navidrome')).toBe('🔵');
    expect(compareServerIcon('spotify')).toBe('💻');
  });

  it("falls back to 'Server' and 'Source', not empty strings", () => {
    expect(compareServerLabel(undefined)).toBe('Server');
    expect(compareServerLabel('plex')).toBe('Plex');
    expect(compareSourceLabel(undefined, false)).toBe('Source');
    // 312: with a mirrored playlist but no source name, 'source' is capitalised.
    expect(compareSourceLabel(undefined, true)).toBe('Source');
    expect(compareSourceLabel('tidal', true)).toBe('Tidal');
  });

  it('builds the meta line with zero fallbacks (301)', () => {
    expect(
      compareMetaText({ server_type: 'plex', server_track_count: 5, source_track_count: 7 }),
    ).toBe('Plex · 5 server tracks · 7 source tracks');
    expect(compareMetaText({})).toBe('Server · 0 server tracks · 0 source tracks');
  });
});

describe('compareConfidenceBadge (534-540)', () => {
  it('bands at 100 and 90', () => {
    expect(compareConfidenceBadge(T('matched', { confidence: 1 }))).toEqual({
      percent: 100,
      className: 'exact',
    });
    expect(compareConfidenceBadge(T('matched', { confidence: 0.95 }))?.className).toBe('high');
    expect(compareConfidenceBadge(T('matched', { confidence: 0.9 }))?.className).toBe('high');
    expect(compareConfidenceBadge(T('matched', { confidence: 0.89 }))?.className).toBe('fuzzy');
  });

  it('renders ONLY for a matched row', () => {
    expect(compareConfidenceBadge(T('extra', { confidence: 1 }))).toBeNull();
    expect(compareConfidenceBadge(T('missing', { confidence: 1 }))).toBeNull();
  });

  it('distinguishes a ZERO confidence from an absent one', () => {
    // 534 tests `!= null`, so 0 still renders — as 0%, class fuzzy.
    expect(compareConfidenceBadge(T('matched', { confidence: 0 }))).toEqual({
      percent: 0,
      className: 'fuzzy',
    });
    expect(compareConfidenceBadge(T('matched', { confidence: null }))).toBeNull();
    expect(compareConfidenceBadge(T('matched'))).toBeNull();
  });
});

describe('compareMissingHint (566)', () => {
  it('joins artist and name with an em dash', () => {
    expect(
      compareMissingHint(T('missing', { source_track: { name: 'Alright', artist: 'Kendrick' } })),
    ).toBe('Kendrick — Alright');
  });

  it('keeps the dash when the artist is missing, and is empty with no source', () => {
    expect(compareMissingHint(T('missing', { source_track: { name: 'Alright' } }))).toBe(
      ' — Alright',
    );
    expect(compareMissingHint(T('extra'))).toBe('');
  });
});

describe('fetchComparePlaylist (276-279)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('encodes the name and appends the mirrored id only when there is one', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    await fetchComparePlaylist('7', 'Road Trip & More', 9);
    await fetchComparePlaylist('7', 'Road Trip & More');
    expect(urls[0]).toBe(
      '/api/server/playlist/7/tracks?name=Road%20Trip%20%26%20More&mirrored_playlist_id=9',
    );
    expect(urls[1]).toBe('/api/server/playlist/7/tracks?name=Road%20Trip%20%26%20More');
  });
});

/* ── Slice C: search / replace / remove (746-1020) ─────────────────────────── */

const MATCHED: CompareTrack = {
  match_status: 'matched',
  confidence: 0.9,
  source_track: { name: 'Nights', artist: 'Frank Ocean' },
  server_track: { id: 's1', title: 'Nights (Album)', artist: 'Frank Ocean' },
};

describe('searchSeed (750-759)', () => {
  it('searches the TITLE alone and keeps the artist as a separate hint (752, 756-758)', () => {
    expect(searchSeed(MATCHED)).toEqual({
      query: 'Nights',
      contextArtist: 'Frank Ocean',
      contextName: 'Nights',
    });
  });

  it('trims the query', () => {
    expect(
      searchSeed({ match_status: 'missing', source_track: { name: '  Nights  ' } }).query,
    ).toBe('Nights');
  });

  it('the SOURCE artist wins when the two sides disagree (754)', () => {
    // The hint must describe the track the user is matching FROM, not the one
    // already sitting on the server.
    expect(
      searchSeed({
        match_status: 'matched',
        source_track: { name: 'Nights', artist: 'Frank Ocean' },
        server_track: { id: 's', title: 'Nights', artist: 'Frank Ocean Tribute Band' },
      }).contextArtist,
    ).toBe('Frank Ocean');
  });

  it('falls back to the server side field by field (753-755)', () => {
    const extra: CompareTrack = {
      match_status: 'extra',
      source_track: null,
      server_track: { id: 's3', title: 'Bonus', artist: 'Someone' },
    };
    expect(searchSeed(extra)).toEqual({
      query: 'Bonus',
      contextArtist: 'Someone',
      contextName: 'Bonus',
    });
  });

  it('an EMPTY source name falls back too — the guard is truthiness, not presence (753)', () => {
    const seed = searchSeed({
      match_status: 'matched',
      source_track: { name: '', artist: 'A' },
      server_track: { id: 's', title: 'Server Title' },
    });
    expect(seed.query).toBe('Server Title');
    // …but the artist still comes from the source, which HAS one.
    expect(seed.contextArtist).toBe('A');
  });

  it('leaves everything empty when neither side names the track', () => {
    expect(searchSeed({ match_status: 'missing' })).toEqual({
      query: '',
      contextArtist: '',
      contextName: '',
    });
  });
});

describe('searchModalTitle (771)', () => {
  it('names the mode', () => {
    expect(searchModalTitle('replace')).toBe('Swap Track');
    expect(searchModalTitle('add')).toBe('Add Track to Server');
  });
});

describe('searchFormatBadge (859)', () => {
  it('shows M4A as AAC', () => {
    expect(searchFormatBadge('/music/a.m4a')).toBe('AAC');
  });

  it('shows the other six as themselves, upper-cased', () => {
    expect(searchFormatBadge('/music/a.flac')).toBe('FLAC');
    expect(searchFormatBadge('/music/a.MP3')).toBe('MP3');
    expect(searchFormatBadge('/music/a.opus')).toBe('OPUS');
    expect(searchFormatBadge('/music/a.ogg')).toBe('OGG');
    expect(searchFormatBadge('/music/a.aac')).toBe('AAC');
    expect(searchFormatBadge('/music/a.wav')).toBe('WAV');
  });

  it('shows nothing for an unlisted extension, or none at all', () => {
    expect(searchFormatBadge('/music/a.wma')).toBe('');
    expect(searchFormatBadge('/music/noextension')).toBe('');
    expect(searchFormatBadge('')).toBe('');
    expect(searchFormatBadge(null)).toBe('');
  });
});

describe('searchBitrateText (861)', () => {
  it('suffixes a k, and says nothing for 0 or missing', () => {
    expect(searchBitrateText(320)).toBe('320k');
    expect(searchBitrateText(0)).toBe('');
    expect(searchBitrateText(undefined)).toBe('');
  });
});

describe('searchResultsHeaderText (855)', () => {
  it('pluralises on anything but one', () => {
    expect(searchResultsHeaderText(1)).toBe('1 result');
    expect(searchResultsHeaderText(2)).toBe('2 results');
    expect(searchResultsHeaderText(0)).toBe('0 results');
  });
});

describe('addTrackPosition (908-911)', () => {
  const tracks: CompareTrack[] = [
    { match_status: 'matched', server_track: { id: 'a' } },
    { match_status: 'missing', server_track: null },
    { match_status: 'matched', server_track: { id: 'b' } },
    { match_status: 'missing', server_track: null },
  ];

  it('counts the SERVER tracks before the row, not the rows', () => {
    // Row 3 is the fourth row but only two rows before it are on the server.
    expect(addTrackPosition(tracks, 3)).toBe(2);
    expect(addTrackPosition(tracks, 1)).toBe(1);
    expect(addTrackPosition(tracks, 0)).toBe(0);
  });
});

describe('applyPickedTrack (946-967)', () => {
  const picked = {
    id: 42,
    title: 'Nights (Remaster)',
    artist_name: 'Frank Ocean',
    album_title: 'Blonde',
    duration: 307000,
    album_thumb_url: 'http://art/1.jpg',
  };

  it('fills the server side and pins the row to a 100% override', () => {
    const tracks: CompareTrack[] = [{ match_status: 'missing', source_track: { name: 'Nights' } }];
    const [row] = applyPickedTrack(tracks, 0, '42', picked);
    expect(row.match_status).toBe('matched');
    expect(row.confidence).toBe(1.0);
    expect(row.override).toBe(true);
    expect(row.server_track).toEqual({
      id: '42',
      title: 'Nights (Remaster)',
      artist: 'Frank Ocean',
      album: 'Blonde',
      duration: 307000,
      thumb: 'http://art/1.jpg',
    });
    // The source side is untouched — the columns stay paired.
    expect(row.source_track).toEqual({ name: 'Nights' });
  });

  it('empties every absent field rather than dropping it', () => {
    const tracks: CompareTrack[] = [{ match_status: 'missing' }];
    const [row] = applyPickedTrack(tracks, 0, '42', { id: 42 });
    expect(row.server_track).toEqual({
      id: '42',
      title: '',
      artist: '',
      album: '',
      duration: 0,
      thumb: '',
    });
  });

  it('drops the extra row the backend LINKED rather than duplicated (960-966)', () => {
    const tracks: CompareTrack[] = [
      { match_status: 'missing', source_track: { name: 'Nights' } },
      { match_status: 'extra', source_track: null, server_track: { id: '42', title: 'Nights' } },
    ];
    const next = applyPickedTrack(tracks, 0, '42', picked);
    expect(next).toHaveLength(1);
    expect(next[0].match_status).toBe('matched');
  });

  it('keeps a row that merely SHARES the id but has a source side (963)', () => {
    const tracks: CompareTrack[] = [
      { match_status: 'missing', source_track: { name: 'Nights' } },
      {
        match_status: 'matched',
        source_track: { name: 'Elsewhere' },
        server_track: { id: '42', title: 'Nights' },
      },
    ];
    // Only a source-less row is the link artefact; a matched pair is real data.
    expect(applyPickedTrack(tracks, 0, '42', picked)).toHaveLength(2);
  });

  it('never removes the row it just patched (963: p !== track)', () => {
    const tracks: CompareTrack[] = [
      { match_status: 'extra', source_track: null, server_track: { id: '42' } },
    ];
    const next = applyPickedTrack(tracks, 0, '42', picked);
    expect(next).toHaveLength(1);
    expect(next[0].match_status).toBe('matched');
  });

  it('leaves the input array alone', () => {
    const tracks: CompareTrack[] = [{ match_status: 'missing' }];
    applyPickedTrack(tracks, 0, '42', picked);
    expect(tracks[0].match_status).toBe('missing');
  });

  it('returns the list unchanged for an index with no row', () => {
    const tracks: CompareTrack[] = [{ match_status: 'missing' }];
    expect(applyPickedTrack(tracks, 5, '42', picked)).toEqual(tracks);
  });
});

describe('applyRemovedTrack (1006-1012)', () => {
  it('turns a matched pair into a missing one, keeping the row', () => {
    const tracks: CompareTrack[] = [MATCHED];
    const next = applyRemovedTrack(tracks, 0);
    expect(next).toHaveLength(1);
    expect(next[0].match_status).toBe('missing');
    expect(next[0].server_track).toBeNull();
    expect(next[0].confidence).toBe(0);
    expect(next[0].source_track).toEqual({ name: 'Nights', artist: 'Frank Ocean' });
  });

  it('drops an extra row entirely — it has no source side to keep', () => {
    const tracks: CompareTrack[] = [
      MATCHED,
      { match_status: 'extra', source_track: null, server_track: { id: 's3' } },
    ];
    const next = applyRemovedTrack(tracks, 1);
    expect(next).toHaveLength(1);
    expect(next[0].match_status).toBe('matched');
  });

  it('leaves the input array alone', () => {
    const tracks: CompareTrack[] = [MATCHED];
    applyRemovedTrack(tracks, 0);
    expect(tracks[0].match_status).toBe('matched');
  });

  it('returns the list unchanged for an index with no row', () => {
    const tracks: CompareTrack[] = [MATCHED];
    expect(applyRemovedTrack(tracks, 9)).toEqual(tracks);
  });
});

describe('removeConfirmOptions (988)', () => {
  it('names the SERVER track and marks the dialog destructive', () => {
    expect(removeConfirmOptions(MATCHED)).toEqual({
      title: 'Remove Track',
      message: 'Remove "Nights (Album)" from this playlist?',
      confirmText: 'Remove',
      destructive: true,
    });
  });

  it('falls back to a pronoun when there is no title', () => {
    expect(removeConfirmOptions(undefined).message).toBe('Remove "this track" from this playlist?');
    expect(removeConfirmOptions({ match_status: 'missing' }).message).toBe(
      'Remove "this track" from this playlist?',
    );
  });
});

describe('the three write calls + the search call', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stub() {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    return seen;
  }

  it('searchLibraryTracks fixes the limit at 20 and omits an empty hint (837-838)', async () => {
    const seen = stub();
    await searchLibraryTracks('bad guy', 'Billie Eilish');
    await searchLibraryTracks('bad guy', '');
    expect(seen[0].url).toBe(
      '/api/library/search-tracks?q=bad%20guy&limit=20&artist=Billie%20Eilish',
    );
    expect(seen[1].url).toBe('/api/library/search-tracks?q=bad%20guy&limit=20');
  });

  it('replaceServerTrack posts both ids and the name (896-904)', async () => {
    const seen = stub();
    await replaceServerTrack('7', 'Road Trip', 's1', '42');
    expect(seen[0].url).toBe('/api/server/playlist/7/replace-track');
    expect(seen[0].init?.method).toBe('POST');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      old_track_id: 's1',
      new_track_id: '42',
      playlist_name: 'Road Trip',
    });
  });

  it('addServerTrack carries the durable-match fields (917-931)', async () => {
    const seen = stub();
    await addServerTrack(
      '7',
      'Road Trip',
      '42',
      3,
      { name: 'Nights', artist: 'Frank Ocean', source_track_id: 'spot123', source: 'spotify' },
      'tidal',
    );
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      track_id: '42',
      playlist_name: 'Road Trip',
      position: 3,
      source_track_id: 'spot123',
      source_title: 'Nights',
      source_artist: 'Frank Ocean',
      // The source track named its own provider, so the mirrored one loses.
      source: 'spotify',
    });
  });

  it("addServerTrack falls back to the mirrored playlist's source, then to '' (929)", async () => {
    const seen = stub();
    await addServerTrack('7', 'Road Trip', '42', 0, { name: 'Nights' }, 'tidal');
    await addServerTrack('7', 'Road Trip', '42', 0, null, null);
    expect(JSON.parse(seen[0].init?.body as string).source).toBe('tidal');
    const bare = JSON.parse(seen[1].init?.body as string);
    expect(bare.source).toBe('');
    expect(bare.source_track_id).toBe('');
    expect(bare.source_title).toBe('');
    expect(bare.source_artist).toBe('');
  });

  it('removeServerTrack posts the id and the name (991-998)', async () => {
    const seen = stub();
    await removeServerTrack('7', 'Road Trip', 's1');
    expect(seen[0].url).toBe('/api/server/playlist/7/remove-track');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      track_id: 's1',
      playlist_name: 'Road Trip',
    });
  });
});
