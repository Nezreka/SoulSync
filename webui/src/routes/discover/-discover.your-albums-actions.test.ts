import { describe, expect, it } from 'vitest';

import type { BatchRow, YourAlbum } from './-discover.your-albums-actions';

import {
  ALBUM_NO_TRACKS,
  ALBUM_NOT_FOUND,
  BATCH_DONE_TEXT,
  BATCH_ENDPOINT,
  BATCH_ENVELOPE_ARTIST,
  BATCH_NO_SOURCES,
  BATCH_PROCESSING_INFO,
  BATCH_SUBMIT_IDLE,
  BATCH_WAITING_TEXT,
  DISCONNECTED_HINTS,
  MISSING_ALL_OWNED,
  MISSING_NONE,
  SOURCES_NONE_SELECTED,
  SOURCES_SAVED,
  SOURCES_SAVE_FAILED,
  YOUR_ALBUMS_DEFAULT_SOURCES,
  YOUR_ALBUMS_REFRESH_POLL_MS,
  YOUR_ALBUMS_REFRESH_TIMEOUT_MS,
  YOUR_ALBUMS_SEARCH_DEBOUNCE_MS,
  YOUR_ALBUMS_SOURCE_INFO,
  albumDownloadSources,
  albumVirtualId,
  batchAlbumsPayload,
  batchCardAnimationDelay,
  batchCardMeta,
  batchFooter,
  batchModalSubtitle,
  batchProgressKey,
  batchRequestBody,
  batchSummary,
  disconnectedHint,
  enabledSources,
  initialBatchProgress,
  initialSourcesState,
  mapAlbumObject,
  mapAlbumTracks,
  missingAlbumsOutcome,
  prepareBatchRows,
  reduceBatchEvent,
  savedSourcesSubtitle,
  selectedBatchRows,
  sourcesSavePayload,
  splitNdjson,
  toggleSource,
} from './-discover.your-albums-actions';

const row = (over: Partial<BatchRow> = {}): BatchRow =>
  ({
    album_name: 'SAW',
    artist_name: 'Aphex Twin',
    _src: { id: 'sp1', source: 'spotify' },
    _index: 0,
    ...over,
  }) as BatchRow;

describe('the search debounce', () => {
  it('waits 400ms', () => {
    expect(YOUR_ALBUMS_SEARCH_DEBOUNCE_MS).toBe(400);
  });
});

describe('the missing-albums outcome', () => {
  it('says nothing to do when the request failed or came back empty', () => {
    for (const data of [
      null,
      undefined,
      { success: false, albums: [{ in_library: false }] },
      { success: true },
      { success: true, albums: [] },
    ]) {
      const out = missingAlbumsOutcome(data as never);
      expect(out).toEqual({ kind: 'none', message: MISSING_NONE, toast: 'info' });
    }
  });

  it('re-filters on in_library even though the query already asked for missing', () => {
    // The query selects what the CACHE believes is missing; in_library is
    // stamped fresh. An album imported since the last cache write lands here.
    const out = missingAlbumsOutcome({
      success: true,
      albums: [{ in_library: true }, { in_library: true }],
    });
    expect(out).toEqual({ kind: 'all-owned', message: MISSING_ALL_OWNED, toast: 'success' });
  });

  it('opens the modal with only the genuinely missing rows', () => {
    const out = missingAlbumsOutcome({
      success: true,
      albums: [{ in_library: true, album_name: 'have' }, { album_name: 'want' }],
    });
    expect(out.kind).toBe('open');
    expect(out.kind === 'open' && out.missing.map((a) => a.album_name)).toEqual(['want']);
  });

  it('distinguishes "all owned" (success) from "nothing at all" (info)', () => {
    // Two different toasts and two different levels — collapsing them would
    // tell a user with a complete library that something went wrong.
    expect(MISSING_NONE).toBe('No missing albums to download');
    expect(MISSING_ALL_OWNED).toBe('All albums are already in your library!');
  });
});

describe('preparing the batch rows', () => {
  it('drops albums with no usable source id', () => {
    const rows = prepareBatchRows([{ spotify_album_id: 'sp1' }, {}, { deezer_album_id: 'dz1' }]);
    expect(rows.map((r) => r._src.source)).toEqual(['spotify', 'deezer']);
  });

  it('assigns _index BEFORE filtering, so it stays a stable join key', () => {
    // This is the subtle one. The checkbox carries _index and the submit
    // handler joins on it. If _index were assigned after the filter, every
    // index would shift and a user selecting one album would submit another.
    const rows = prepareBatchRows([{}, { spotify_album_id: 'sp1' }, {}, { tidal_album_id: 'td1' }]);
    expect(rows.map((r) => r._index)).toEqual([1, 3]);
  });

  it('keeps every other field on the row', () => {
    const rows = prepareBatchRows([{ spotify_album_id: 'sp1', album_name: 'SAW' }]);
    expect(rows[0].album_name).toBe('SAW');
  });

  it('produces nothing when no album has a source', () => {
    expect(prepareBatchRows([{}, {}])).toEqual([]);
    expect(BATCH_NO_SOURCES).toBe('No missing albums have a usable source ID to resolve');
  });
});

describe('the batch card', () => {
  it('joins each present segment with its own separator', () => {
    expect(batchCardMeta(row({ release_date: '1992-11-09', total_tracks: 13 }))).toBe(
      'Aphex Twin · 1992 · 13 tracks · spotify',
    );
  });

  it('takes the YEAR off the front of the release date', () => {
    expect(batchCardMeta(row({ release_date: '1992-11-09' }))).toBe('Aphex Twin · 1992 · spotify');
  });

  it('omits absent segments without leaving a dangling separator', () => {
    expect(batchCardMeta(row())).toBe('Aphex Twin · spotify');
    expect(batchCardMeta(row({ artist_name: '' }))).toBe(' · spotify');
  });

  it('omits a ZERO track count rather than printing "0 tracks"', () => {
    expect(batchCardMeta(row({ total_tracks: 0 }))).toBe('Aphex Twin · spotify');
  });

  it('staggers the animation by the FILTERED index, not the join key', () => {
    // data-row-index uses _index; the animation delay uses the render position.
    expect(batchCardAnimationDelay(0)).toBe('0s');
    expect(batchCardAnimationDelay(3)).toBe('0.09s');
  });

  it('counts the rows in the subtitle', () => {
    expect(batchModalSubtitle(7)).toBe('7 albums missing from your library');
  });
});

describe('the footer tally', () => {
  it('sums the track counts of the selected rows', () => {
    const f = batchFooter([{ total_tracks: 13 }, { total_tracks: 9 }]);
    expect([f.releases, f.tracks]).toEqual([2, 22]);
    expect(f.info).toBe('2 albums · 22 tracks');
  });

  it('singularises exactly one album', () => {
    expect(batchFooter([{ total_tracks: 3 }]).info).toBe('1 album · 3 tracks');
  });

  it('omits the track clause when the total is zero', () => {
    expect(batchFooter([{}]).info).toBe('1 album');
  });

  it('says "0 albums" plural, not "0 album"', () => {
    // `releases !== 1` rather than `> 1` — an off-by-one here is invisible
    // until the user deselects everything.
    expect(batchFooter([]).info).toBe('0 albums');
  });

  it('disables the submit button with nothing selected', () => {
    const empty = batchFooter([]);
    expect(empty.submitDisabled).toBe(true);
    expect(empty.submitText).toBe(BATCH_SUBMIT_IDLE);
  });

  it('names the count in the button once something is selected', () => {
    const f = batchFooter([{}, {}]);
    expect(f.submitDisabled).toBe(false);
    expect(f.submitText).toBe('Add 2 to Wishlist');
  });

  it('ignores a non-numeric track count rather than producing NaN', () => {
    expect(batchFooter([{ total_tracks: undefined }, { total_tracks: 5 }]).tracks).toBe(5);
  });
});

describe('the submit payload', () => {
  it('joins the checked boxes back to their rows by _index', () => {
    const rows = [row({ _index: 1, album_name: 'A' }), row({ _index: 3, album_name: 'B' })];
    expect(selectedBatchRows(rows, [3]).map((r) => r.album_name)).toEqual(['B']);
  });

  it('sends the per-album source, which is what the backend actually resolves', () => {
    const payload = batchAlbumsPayload([
      row({ _src: { id: 'dz9', source: 'deezer' }, album_name: 'X', artist_name: 'Y' }),
    ]);
    expect(payload).toEqual([{ id: 'dz9', name: 'X', artist_name: 'Y', source: 'deezer' }]);
  });

  it('defaults missing names to empty strings, not undefined', () => {
    const payload = batchAlbumsPayload([
      row({ album_name: undefined, artist_name: undefined } as Partial<BatchRow>),
    ]);
    expect(payload[0]).toMatchObject({ name: '', artist_name: '' });
  });

  it('wraps them in the placeholder envelope', () => {
    const body = batchRequestBody([row()]);
    expect(body.artist_name).toBe(BATCH_ENVELOPE_ARTIST);
    expect(body.source).toBeNull();
    expect(BATCH_ENDPOINT).toBe('/api/artist/your-albums/download-discography');
  });
});

describe('the progress stream', () => {
  const selected = [
    row({ _src: { id: '111', source: 'spotify' }, _index: 0 }),
    row({ _src: { id: '222', source: 'deezer' }, _index: 1 }),
  ];

  it('keys rows by source AND id, since one id can span sources', () => {
    expect(batchProgressKey({ id: '111', source: 'spotify' })).toBe('spotify-111');
  });

  it('starts every row waiting', () => {
    const state = initialBatchProgress(selected);
    expect(Object.keys(state.items)).toEqual(['spotify-111', 'deezer-222']);
    expect(state.items['spotify-111']).toEqual({ status: 'waiting', text: BATCH_WAITING_TEXT });
    expect([state.totalAdded, state.totalSkipped]).toEqual([0, 0]);
  });

  it('records a done row’s per-album counts', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { album_id: '111', status: 'done', tracks_added: 12, tracks_skipped: 1 },
      selected,
    );
    expect(next.items['spotify-111']).toEqual({ status: 'done', text: '12 added · 1 skipped' });
  });

  it('defaults absent counts to zero rather than undefined', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { album_id: '111', status: 'done' },
      selected,
    );
    expect(next.items['spotify-111'].text).toBe('0 added · 0 skipped');
  });

  it('shows an error row with the server’s message', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { album_id: '222', status: 'error', message: 'no match' },
      selected,
    );
    expect(next.items['deezer-222']).toEqual({ status: 'error', text: 'Error: no match' });
  });

  it('falls back to "unknown" for a message-less error', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { album_id: '222', status: 'error' },
      selected,
    );
    expect(next.items['deezer-222'].text).toBe('Error: unknown');
  });

  it('matches a NUMERIC album_id against the string id we sent', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { album_id: 111, status: 'done', tracks_added: 3 },
      selected,
    );
    expect(next.items['spotify-111'].status).toBe('done');
  });

  it('compares STRINGS, so a numerically-equal id is not treated as the same', () => {
    // `String(x) === y` and `x == y` agree on every id in this file's other
    // tests, which is why the strict form has to be pinned separately: under
    // `==`, '0111' equals 111 and '1e2' equals 100. Discogs ids are opaque
    // strings and are not safe to compare numerically.
    const padded = [row({ _src: { id: '0111', source: 'spotify' }, _index: 0 })];
    const next = reduceBatchEvent(
      initialBatchProgress(padded),
      { album_id: 111, status: 'done', tracks_added: 3 },
      padded,
    );
    expect(next.items['spotify-0111'].status).toBe('waiting');
  });

  it('ignores an album_id it did not submit', () => {
    const before = initialBatchProgress(selected);
    expect(reduceBatchEvent(before, { album_id: '999', status: 'done' }, selected)).toBe(before);
  });

  it('takes the totals off the complete event', () => {
    const next = reduceBatchEvent(
      initialBatchProgress(selected),
      { status: 'complete', total_added: 40, total_skipped: 2 },
      selected,
    );
    expect([next.totalAdded, next.totalSkipped]).toEqual([40, 2]);
  });

  it('leaves rows untouched for an unrecognised status', () => {
    const before = initialBatchProgress(selected);
    const next = reduceBatchEvent(before, { album_id: '111', status: 'searching' }, selected);
    expect(next.items['spotify-111'].status).toBe('waiting');
  });

  it('summarises with the level keyed off whether anything landed', () => {
    expect(batchSummary({ totalAdded: 40, totalSkipped: 2, items: {} })).toEqual({
      info: '40 tracks added to wishlist · 2 skipped',
      toast: '40 tracks added to wishlist',
      toastLevel: 'success',
    });
    expect(batchSummary({ totalAdded: 0, totalSkipped: 5, items: {} }).toastLevel).toBe('info');
  });

  it('keeps the in-flight copy verbatim', () => {
    expect(BATCH_PROCESSING_INFO).toBe('Processing... this may take a moment');
    expect(BATCH_DONE_TEXT).toBe('Done');
  });
});

describe('the ndjson splitter', () => {
  it('carries an incomplete trailing line into the next chunk', () => {
    // This is what stops a chunk that splits mid-JSON being parsed as two
    // broken halves.
    const { lines, rest } = splitNdjson('{"a":1}\n{"b":2}\n{"c":');
    expect(lines).toEqual(['{"a":1}', '{"b":2}']);
    expect(rest).toBe('{"c":');
  });

  it('emits nothing until the first newline arrives', () => {
    const { lines, rest } = splitNdjson('{"a":1');
    expect(lines).toEqual([]);
    expect(rest).toBe('{"a":1');
  });

  it('leaves an empty remainder when the chunk ends cleanly', () => {
    expect(splitNdjson('{"a":1}\n')).toEqual({ lines: ['{"a":1}'], rest: '' });
  });

  it('skips blank lines', () => {
    expect(splitNdjson('{"a":1}\n\n  \n{"b":2}\n').lines).toEqual(['{"a":1}', '{"b":2}']);
  });
});

describe('opening one album’s download', () => {
  it('tries streaming sources before discogs', () => {
    // Streaming tracklists carry ids the downloader can act on; a Discogs
    // collection item has a tracklist but no streaming ids.
    const sources = albumDownloadSources({
      discogs_id: 'dc1',
      tidal_album_id: 'td1',
      deezer_album_id: 'dz1',
      spotify_album_id: 'sp1',
    });
    expect(sources.map((s) => s.source)).toEqual(['spotify', 'deezer', 'tidal', 'discogs']);
  });

  it('offers EVERY populated source, not just the best one', () => {
    // Unlike the batch picker, this is a fallback chain — an empty payload
    // from the first source falls through to the next.
    expect(albumDownloadSources({ spotify_album_id: 'sp1', deezer_album_id: 'dz1' })).toHaveLength(
      2,
    );
  });

  it('prefers discogs_release_id over discogs_id', () => {
    expect(albumDownloadSources({ discogs_release_id: 'r1', discogs_id: 'd1' })[0].id).toBe('r1');
  });

  it('stringifies numeric ids', () => {
    expect(albumDownloadSources({ deezer_album_id: 42 })[0].id).toBe('42');
  });

  it('offers nothing for an album with no ids at all', () => {
    expect(albumDownloadSources({})).toEqual([]);
  });

  it('builds a virtual id that falls back to the GRID INDEX', () => {
    // Two Discogs-only albums with the same name would otherwise collide.
    expect(albumVirtualId({ spotify_album_id: 'sp1' }, 4)).toBe('discover_album_sp1');
    expect(albumVirtualId({ deezer_album_id: 'dz1' }, 4)).toBe('discover_album_dz1');
    expect(albumVirtualId({ tidal_album_id: 'td1' }, 4)).toBe('discover_album_td1');
    expect(albumVirtualId({}, 4)).toBe('discover_album_4');
  });

  it('keeps the failure copy', () => {
    expect(ALBUM_NOT_FOUND).toBe('Album data not found');
    expect(ALBUM_NO_TRACKS).toBe('No tracks found for this album');
  });
});

describe('flattening the album detail', () => {
  const album: YourAlbum = { artist_name: 'Aphex Twin' };

  it('prefers the track’s own artists', () => {
    const tracks = mapAlbumTracks(
      { artists: [{ name: 'Album Artist' }], tracks: [{ artists: [{ name: 'Track Artist' }] }] },
      album,
    );
    expect(tracks[0].artists).toEqual(['Track Artist']);
  });

  it('falls back to the album’s artists, then to the grid row', () => {
    expect(
      mapAlbumTracks({ artists: [{ name: 'Album Artist' }], tracks: [{}] }, album)[0].artists,
    ).toEqual(['Album Artist']);
    expect(mapAlbumTracks({ tracks: [{}] }, album)[0].artists).toEqual(['Aphex Twin']);
  });

  it('unwraps objects but passes plain strings through', () => {
    // `a.name || a` — without the `|| a`, a string array becomes undefineds.
    expect(mapAlbumTracks({ tracks: [{ artists: ['Plain'] }] }, album)[0].artists).toEqual([
      'Plain',
    ]);
  });

  it('defaults the album envelope on every track', () => {
    const tracks = mapAlbumTracks({ id: 'a1', name: 'SAW', tracks: [{ id: 't1' }] }, album);
    expect(tracks[0].album).toEqual({
      id: 'a1',
      name: 'SAW',
      album_type: 'album',
      total_tracks: 0,
      release_date: '',
      images: [],
    });
    expect([tracks[0].duration_ms, tracks[0].track_number]).toEqual([0, 0]);
  });

  it('survives a payload with no tracks key', () => {
    expect(mapAlbumTracks({}, album)).toEqual([]);
  });

  it('names the artist on the album object from the GRID row', () => {
    // The detail payload's own artists are deliberately not used here.
    expect(mapAlbumObject({ artists: [{ name: 'Wrong' }] }, album).artists).toEqual([
      { name: 'Aphex Twin' },
    ]);
  });
});

describe('the refresh poller', () => {
  it('checks every four seconds and gives up after a minute', () => {
    expect(YOUR_ALBUMS_REFRESH_POLL_MS).toBe(4000);
    expect(YOUR_ALBUMS_REFRESH_TIMEOUT_MS).toBe(60000);
    expect(YOUR_ALBUMS_REFRESH_TIMEOUT_MS / YOUR_ALBUMS_REFRESH_POLL_MS).toBe(15);
  });
});

describe('the sources modal', () => {
  it('offers four sources but defaults to three', () => {
    // Discogs is opt-in: it is a collection, not a streaming library, and
    // turning it on by default would flood the section with vinyl pressings.
    expect(YOUR_ALBUMS_SOURCE_INFO.map((s) => s.id)).toEqual([
      'spotify',
      'tidal',
      'deezer',
      'discogs',
    ]);
    expect(YOUR_ALBUMS_DEFAULT_SOURCES).toEqual(['spotify', 'tidal', 'deezer']);
    expect(YOUR_ALBUMS_DEFAULT_SOURCES).not.toContain('discogs');
  });

  it('gives every source a key, on or off', () => {
    expect(initialSourcesState(['spotify'])).toEqual({
      spotify: true,
      tidal: false,
      deezer: false,
      discogs: false,
    });
  });

  it('toggles a connected source', () => {
    const { state, hint } = toggleSource({ spotify: false }, 'spotify', ['spotify']);
    expect(state.spotify).toBe(true);
    expect(hint).toBeNull();
    expect(toggleSource(state, 'spotify', ['spotify']).state.spotify).toBe(false);
  });

  it('REFUSES a disconnected source and says why', () => {
    // The toggle used to bail silently, which read as a broken switch.
    const before = { discogs: false };
    const { state, hint } = toggleSource(before, 'discogs', ['spotify']);
    expect(state).toBe(before);
    expect(hint).toBe(DISCONNECTED_HINTS.discogs);
    expect(hint).toContain('Settings → Connections');
  });

  it('has a hint for every offered source', () => {
    for (const s of YOUR_ALBUMS_SOURCE_INFO) {
      expect(DISCONNECTED_HINTS[s.id]).toBeTruthy();
    }
  });

  it('falls back to a generic hint for an unlisted source', () => {
    expect(disconnectedHint('bandcamp')).toBe(
      'bandcamp not connected — set it up in Settings → Connections first',
    );
  });

  it('saves a COMMA-joined string, not an array', () => {
    expect(enabledSources({ spotify: true, tidal: false, deezer: true })).toEqual([
      'spotify',
      'deezer',
    ]);
    expect(sourcesSavePayload(['spotify', 'deezer'])).toEqual({
      discover: { your_albums_sources: 'spotify,deezer' },
    });
  });

  it('rewrites the subtitle with "and", using the RIGHT single quote', () => {
    const subtitle = savedSourcesSubtitle(['spotify', 'deezer']);
    expect(subtitle).toBe('Albums you’ve saved on Spotify and Deezer');
    expect(subtitle).not.toContain("you've"); //  an ASCII apostrophe is a regression
  });

  it('passes an unknown source id through to the subtitle', () => {
    expect(savedSourcesSubtitle(['bandcamp'])).toBe('Albums you’ve saved on bandcamp');
  });

  it('keeps the save copy', () => {
    expect(SOURCES_NONE_SELECTED).toBe('Select at least one source');
    expect(SOURCES_SAVED).toBe('Sources saved — refresh to apply');
    expect(SOURCES_SAVE_FAILED).toBe('Failed to save sources');
  });
});
