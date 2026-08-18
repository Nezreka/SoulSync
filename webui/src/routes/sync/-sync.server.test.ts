/**
 * The server tab's pure core, pinned against pages-extra.js 59-94 and 484.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CompareTrack } from './-sync.server';

import {
  ALIGNABLE_SERVERS,
  COMPARE_SERVER_ICONS,
  COMPARE_SOURCE_ICONS,
  addServerTrack,
  alignMatchedIds,
  alignServerPlaylist,
  canAlignServer,
  downloadM3u,
  exportServerM3u,
  m3uExportNote,
  m3uFileName,
  orderModalTitle,
  serverM3uTracks,
  addTrackPosition,
  applyPickedTrack,
  applyRemovedTrack,
  removeConfirmOptions,
  deleteServerPlaylist,
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

  it('replaceServerTrack carries the durable-match fields too (#1159)', async () => {
    // Same source_* payload as addServerTrack. Without them the backend had
    // nothing to persist, so a corrected bad match reverted on the next load —
    // the sync's cached auto-match kept winning (#1159, AfonsoG6).
    const seen = stub();
    await replaceServerTrack(
      '7',
      'Road Trip',
      's1',
      '42',
      { name: 'Nights', artist: 'Frank Ocean', source_track_id: 'spot123', source: 'spotify' },
      'tidal',
    );
    expect(seen[0].url).toBe('/api/server/playlist/7/replace-track');
    expect(seen[0].init?.method).toBe('POST');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      old_track_id: 's1',
      new_track_id: '42',
      playlist_name: 'Road Trip',
      source_track_id: 'spot123',
      source_title: 'Nights',
      source_artist: 'Frank Ocean',
      source: 'spotify',
    });
  });

  it('replaceServerTrack degrades to empty source fields without a source track', async () => {
    // Non-mirrored compares have no source row — the edit must still post,
    // with nothing for the backend to persist.
    const seen = stub();
    await replaceServerTrack('7', 'Road Trip', 's1', '42', null, undefined);
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      old_track_id: 's1',
      new_track_id: '42',
      playlist_name: 'Road Trip',
      source_track_id: '',
      source_title: '',
      source_artist: '',
      source: '',
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

  it('deleteServerPlaylist posts the name so a stale id can be re-resolved', async () => {
    const seen = stub();
    await deleteServerPlaylist('7', 'Road Trip');
    expect(seen[0].url).toBe('/api/server/playlist/7/delete');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({ playlist_name: 'Road Trip' });
  });
});

/* ── Slice D: the order view + align (385-482) ─────────────────────────────── */

describe('canAlignServer (412)', () => {
  it('covers exactly the three servers the backend accepts', () => {
    // web_server.py 22014 gates on the same three — checked, not assumed.
    expect([...ALIGNABLE_SERVERS].sort()).toEqual(['jellyfin', 'navidrome', 'plex']);
    expect(canAlignServer('plex')).toBe(true);
    expect(canAlignServer('jellyfin')).toBe(true);
    expect(canAlignServer('navidrome')).toBe(true);
  });

  it('refuses anything else, including nothing at all', () => {
    expect(canAlignServer('subsonic')).toBe(false);
    expect(canAlignServer('')).toBe(false);
    expect(canAlignServer(undefined)).toBe(false);
    // Case matters: the server type arrives lower-cased.
    expect(canAlignServer('Plex')).toBe(false);
  });
});

describe('orderModalTitle (390-391, 436)', () => {
  it('capitalises the server and falls back to Server', () => {
    expect(orderModalTitle('plex')).toBe('Plex playlist order');
    expect(orderModalTitle('navidrome')).toBe('Navidrome playlist order');
    expect(orderModalTitle(undefined)).toBe('Server playlist order');
    expect(orderModalTitle('')).toBe('Server playlist order');
  });
});

describe('alignMatchedIds (454-456)', () => {
  it('takes MATCHED rows only, in source order, as strings', () => {
    const tracks: CompareTrack[] = [
      { match_status: 'matched', server_track: { id: 'a' } },
      { match_status: 'missing', server_track: null },
      { match_status: 'extra', source_track: null, server_track: { id: 'b' } },
      { match_status: 'matched', server_track: { id: 'c' } },
    ];
    // The extra is governed by keep_extras, not by this list.
    expect(alignMatchedIds(tracks)).toEqual(['a', 'c']);
  });

  it('drops a matched row with no server track or no id', () => {
    const tracks: CompareTrack[] = [
      { match_status: 'matched', server_track: null },
      { match_status: 'matched', server_track: {} },
      { match_status: 'matched', server_track: { id: 'a' } },
    ];
    expect(alignMatchedIds(tracks)).toEqual(['a']);
  });

  it("keeps an id of '' or 0 — the guard is != null, not truthiness (455)", () => {
    const tracks = [
      { match_status: 'matched', server_track: { id: '' } },
      { match_status: 'matched', server_track: { id: 0 } },
    ] as unknown as CompareTrack[];
    expect(alignMatchedIds(tracks)).toEqual(['', '0']);
  });

  it('is empty for a playlist with nothing matched', () => {
    expect(alignMatchedIds([{ match_status: 'missing' }])).toEqual([]);
  });
});

describe('alignServerPlaylist (462-470)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts the ids, the name and the extras choice', async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify({ success: true, track_count: 3 }));
      }),
    );
    await alignServerPlaylist('7', 'Road Trip', ['a', 'b'], true);
    expect(seen[0].url).toBe('/api/server/playlist/7/align');
    expect(seen[0].init?.method).toBe('POST');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      playlist_name: 'Road Trip',
      matched_ids: ['a', 'b'],
      keep_extras: true,
    });
  });

  it('coerces a missing name to an empty string rather than dropping the key (466)', async () => {
    // The guard looks redundant against the type, but the name comes from an
    // untyped payload: JSON.stringify OMITS an undefined value entirely, so
    // without it the backend would see no playlist_name at all rather than the
    // empty one it answers 400 to.
    const seen: { init?: RequestInit }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        seen.push({ init });
        return new Response(JSON.stringify({ success: false }));
      }),
    );
    await alignServerPlaylist('7', undefined as unknown as string, ['a'], false);
    const body = JSON.parse(seen[0].init?.body as string) as Record<string, unknown>;
    expect('playlist_name' in body).toBe(true);
    expect(body.playlist_name).toBe('');
  });

  it('sends an empty string for a nameless playlist, which the backend rejects (466)', async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify({ success: false }));
      }),
    );
    await alignServerPlaylist('7', '', ['a'], false);
    const body = JSON.parse(seen[0].init?.body as string);
    expect(body.playlist_name).toBe('');
    expect(body.keep_extras).toBe(false);
  });
});

/* ── Slice E: M3U export (632-696) ─────────────────────────────────────────── */

describe('serverM3uTracks (645-651)', () => {
  it('exports what is physically ON the server — matched and extra, never missing', () => {
    const tracks: CompareTrack[] = [
      {
        match_status: 'matched',
        source_track: { name: 'Source Name' },
        server_track: { id: 'a', title: 'Alright', artist: 'Kendrick', duration: 219000 },
      },
      { match_status: 'missing', source_track: { name: 'Nights' }, server_track: null },
      {
        match_status: 'extra',
        source_track: null,
        server_track: { id: 'b', title: 'Bonus', artist: 'Someone', duration: 100 },
      },
    ];
    // The names come from the SERVER side, not the source side — this file
    // describes the server's playlist.
    expect(serverM3uTracks(tracks)).toEqual([
      { name: 'Alright', artist: 'Kendrick', duration_ms: 219000 },
      { name: 'Bonus', artist: 'Someone', duration_ms: 100 },
    ]);
  });

  it('defaults a missing artist and duration but leaves the name alone', () => {
    const tracks: CompareTrack[] = [{ match_status: 'matched', server_track: { id: 'a' } }];
    expect(serverM3uTracks(tracks)).toEqual([{ name: undefined, artist: '', duration_ms: 0 }]);
  });

  it('is empty for a playlist with nothing on the server', () => {
    expect(serverM3uTracks([{ match_status: 'missing', server_track: null }])).toEqual([]);
  });
});

describe('m3uFileName (676)', () => {
  it('flattens every character a filesystem refuses', () => {
    expect(m3uFileName('a/b\\c?d%e*f:g|h"i<j>k')).toBe('a-b-c-d-e-f-g-h-i-j-k.m3u');
  });

  it('leaves an ordinary name alone and falls back when there is none', () => {
    expect(m3uFileName('Road Trip')).toBe('Road Trip.m3u');
    expect(m3uFileName('')).toBe('Playlist.m3u');
    expect(m3uFileName(undefined)).toBe('Playlist.m3u');
  });
});

describe('m3uExportNote (689)', () => {
  it('calls out the shortfall only when the library could not resolve every track', () => {
    expect(m3uExportNote(8, 10)).toBe(' (8/10 in library)');
    expect(m3uExportNote(10, 10)).toBe(' (10 tracks)');
    // More found than sent cannot happen, but it reads as the plain form.
    expect(m3uExportNote(11, 10)).toBe(' (11 tracks)');
  });
});

describe('exportServerM3u (659-673)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubM3u(body: unknown, ok = true) {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify(body), { status: ok ? 200 : 500 });
      }),
    );
    return seen;
  }

  it('posts the tracks with save_to_disk and force set', async () => {
    const seen = stubM3u({ success: true, m3u_content: '#EXTM3U' });
    const tracks = [{ name: 'Alright', artist: 'Kendrick', duration_ms: 219000 }];
    await exportServerM3u('Road Trip', tracks);
    expect(seen[0].url).toBe('/api/generate-playlist-m3u');
    expect(JSON.parse(seen[0].init?.body as string)).toEqual({
      playlist_name: 'Road Trip',
      tracks,
      context_type: 'playlist',
      // force bypasses the auto-save gate; this export is on demand.
      save_to_disk: true,
      force: true,
    });
  });

  it("names a nameless playlist 'Playlist' in the body (663)", async () => {
    const seen = stubM3u({ success: true });
    await exportServerM3u('', []);
    expect(JSON.parse(seen[0].init?.body as string).playlist_name).toBe('Playlist');
  });

  it('throws the error the backend named, and a default when it named none', async () => {
    stubM3u({ success: false, error: 'no writer configured' });
    await expect(exportServerM3u('Road Trip', [])).rejects.toThrow('no writer configured');
    stubM3u({ success: false });
    await expect(exportServerM3u('Road Trip', [])).rejects.toThrow('Export failed');
  });

  it('throws on a non-ok response even when the body claims success', async () => {
    stubM3u({ success: true, m3u_content: '#EXTM3U' }, false);
    await expect(exportServerM3u('Road Trip', [])).rejects.toThrow('Export failed');
  });

  it('treats a response that OMITS success as a success (671)', async () => {
    // The check is `success === false`, not falsiness.
    stubM3u({ m3u_content: '#EXTM3U' });
    await expect(exportServerM3u('Road Trip', [])).resolves.toMatchObject({
      m3u_content: '#EXTM3U',
    });
  });
});

describe('downloadM3u (677-685)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('clicks a real anchor, then cleans up after itself', () => {
    const createObjectURL = vi.fn(() => 'blob:m3u');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    const clicked: HTMLAnchorElement[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        // Captured mid-flight: the anchor must still be IN the document when
        // it is clicked, or the download never starts in some browsers.
        expect(document.body.contains(this)).toBe(true);
        clicked.push(this);
      });

    downloadM3u('#EXTM3U', 'Road Trip.m3u');

    expect(click).toHaveBeenCalledTimes(1);
    expect(clicked[0].download).toBe('Road Trip.m3u');
    expect(clicked[0].href).toContain('blob:m3u');
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect((createObjectURL.mock.calls[0] as unknown[])[0]).toBeInstanceOf(Blob);
    // …and neither the node nor the object URL is left behind.
    expect(document.body.contains(clicked[0])).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:m3u');
    click.mockRestore();
  });
});
