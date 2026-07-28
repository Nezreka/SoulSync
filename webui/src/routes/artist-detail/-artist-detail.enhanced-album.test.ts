import { afterEach, describe, expect, it } from 'vitest';

import {
  albumEnrichServices,
  inlineEditDisplay,
  inlineEditInput,
  inlineEditUrl,
  inlineEditValue,
  albumMetaFields,
  albumMetaUpdates,
  deriveMissingTracks,
  getAlbumCanonicalSource,
  normalizeCanonicalTracks,
  normTitleForMatch,
  albumIdBadges,
  albumMatchChips,
  expandedHeaderDetails,
  getAlbumTrackRows,
  getServiceUrl,
  normalizeExpectedMissingTrack,
  serviceBadgeClass,
  trackSlotKey,
} from './-artist-detail.enhanced-album';

afterEach(() => {
  delete window.filterJiosaavnServiceEntries;
});

describe('getServiceUrl', () => {
  it('builds a url per service and entity type', () => {
    expect(getServiceUrl('spotify', 'album', 'abc')).toBe('https://open.spotify.com/album/abc');
    expect(getServiceUrl('musicbrainz', 'album', 'x')).toBe('https://musicbrainz.org/release/x');
    expect(getServiceUrl('discogs', 'album', '9')).toBe('https://www.discogs.com/release/9');
  });

  it('returns the value ITSELF for services that store a full url', () => {
    // lastfm_url / genius_url / bandcamp_url are already complete links.
    expect(getServiceUrl('lastfm', 'album', 'https://last.fm/x')).toBe('https://last.fm/x');
    expect(getServiceUrl('bandcamp', 'album', 'https://x.bandcamp.com')).toBe(
      'https://x.bandcamp.com',
    );
  });

  it('is null for a service/entity pair that has no page', () => {
    expect(getServiceUrl('discogs', 'track', '9')).toBeNull();
    expect(getServiceUrl('amazon', 'artist', '9')).toBeNull();
    expect(getServiceUrl('genius', 'album', '9')).toBeNull();
  });

  it('is null for an unknown service or a missing id', () => {
    expect(getServiceUrl('napster', 'album', '1')).toBeNull();
    expect(getServiceUrl('spotify', 'album', '')).toBeNull();
    expect(getServiceUrl('spotify', 'album', null)).toBeNull();
  });
});

describe('serviceBadgeClass', () => {
  it('abbreviates only MusicBrainz', () => {
    expect(serviceBadgeClass('musicbrainz')).toBe('mb');
    expect(serviceBadgeClass('spotify')).toBe('spotify');
  });
});

describe('albumIdBadges', () => {
  it('emits a badge only for ids the album actually has', () => {
    const badges = albumIdBadges({ spotify_album_id: 'sp1', deezer_id: '' });
    expect(badges.map((b) => b.service)).toEqual(['spotify']);
    expect(badges[0].url).toBe('https://open.spotify.com/album/sp1');
    expect(badges[0].title).toBe('Spotify: sp1 (click to open)');
    expect(badges[0].className).toBe('enhanced-id-badge spotify');
  });

  it('drops the click hint from a badge that is not a link', () => {
    // JioSaavn is the one album service with no public page, so it is also the
    // only way to reach the unlinked branch — and it only renders at all when
    // the shared helper enables it.
    window.filterJiosaavnServiceEntries = (entries) => entries as never[];
    const [badge] = albumIdBadges({ jiosaavn_id: 'js1' });
    expect(badge.url).toBeNull();
    expect(badge.title).toBe('JioSaavn: js1');
  });

  it('hides JioSaavn unless the shared helper enables it', () => {
    expect(albumIdBadges({ jiosaavn_id: 'js1' })).toEqual([]);
    window.filterJiosaavnServiceEntries = (entries) => entries as never[];
    expect(albumIdBadges({ jiosaavn_id: 'js1' }).map((b) => b.service)).toEqual(['jiosaavn']);
  });

  it('keeps the vanilla badge order', () => {
    const badges = albumIdBadges({
      itunes_album_id: 'i',
      spotify_album_id: 's',
      musicbrainz_release_id: 'm',
    });
    expect(badges.map((b) => b.service)).toEqual(['spotify', 'musicbrainz', 'itunes']);
  });
});

describe('albumMatchChips', () => {
  it('shows a chip for EVERY service, even an unmatched one', () => {
    // "we never matched this" is the information the row carries.
    const chips = albumMatchChips({});
    expect(chips).toHaveLength(9);
    expect(chips[0].status).toBe('—');
    expect(chips[0].className).toContain('pending');
  });

  it('classes matched, not_found and anything else apart', () => {
    const chips = albumMatchChips({
      spotify_match_status: 'matched',
      musicbrainz_match_status: 'not_found',
      deezer_match_status: 'queued',
    });
    expect(chips[0].className).toContain('matched');
    expect(chips[1].className).toContain('not-found');
    expect(chips[2].className).toContain('pending');
  });

  it('mentions the last attempt in the tooltip when there was one', () => {
    const [chip] = albumMatchChips({ spotify_last_attempted: '2026-01-02T03:04:05Z' });
    expect(chip.title).toContain('Last:');
    expect(chip.title).toContain('Click to rematch');
  });

  it('says only "Click to rematch" when it has never been attempted', () => {
    expect(albumMatchChips({})[0].title).toBe('Click to rematch');
  });
});

describe('albumEnrichServices', () => {
  it('lists the enrichment sources, JioSaavn excluded by default', () => {
    expect(albumEnrichServices().map((s) => s.id)).toEqual([
      'spotify',
      'musicbrainz',
      'deezer',
      'discogs',
      'audiodb',
      'itunes',
      'lastfm',
      'genius',
      'bandcamp',
    ]);
  });
});

describe('trackSlotKey', () => {
  it('defaults disc 1 and track 0', () => {
    expect(trackSlotKey({})).toBe('1:0');
    expect(trackSlotKey({ disc_number: 2, track_number: 5 })).toBe('2:5');
  });

  it('falls back to the expected_* fields of a missing row', () => {
    expect(trackSlotKey({ expected_disc_number: 2, expected_track_number: 3 })).toBe('2:3');
  });
});

describe('normalizeExpectedMissingTrack', () => {
  const album = { id: 42 };

  it('flattens a source row into the shape a track row expects', () => {
    const row = normalizeExpectedMissingTrack(
      { title: 'Xtal', track_number: 1, disc_number: 1, source: 'spotify', track_id: 'sp9' },
      album,
    );
    expect(row.id).toBe('missing-42-1-1');
    expect(row.spotify_track_id).toBe('sp9');
    expect(row._missingExpected).toBe(true);
    expect(row._hasActionableContext).toBe(true);
  });

  it('routes the source id to the field for its own source only', () => {
    const row = normalizeExpectedMissingTrack(
      { title: 'X', track_number: 1, source: 'deezer', track_id: 'dz1' },
      album,
    );
    expect(row.deezer_id).toBe('dz1');
    expect(row.spotify_track_id).toBe('');
  });

  it('is NOT actionable without a track number or any source id', () => {
    // Nothing the row's buttons could act on; the row is dropped rather than
    // rendered inert.
    expect(
      normalizeExpectedMissingTrack({ title: 'X', track_number: 1 }, album)._hasActionableContext,
    ).toBe(false);
    expect(
      normalizeExpectedMissingTrack({ title: 'X', track_id: 'a' }, album)._hasActionableContext,
    ).toBe(false);
  });

  it('names an untitled row by its track number', () => {
    expect(normalizeExpectedMissingTrack({ track_number: 3 }, album).title).toBe('Track 3');
    expect(normalizeExpectedMissingTrack({}, album).title).toBe('Track ?');
  });
});

describe('getAlbumTrackRows', () => {
  it('keys owned tracks by ID, so a shared disc:track slot cannot eat one', () => {
    // #1051: multi-disc albums whose tags all claim disc 1 make disc1-track1
    // and disc2-track1 share a slot. Keying by slot dropped one of them.
    const rows = getAlbumTrackRows({
      tracks: [
        { id: 1, track_number: 1, disc_number: 1, title: 'A' },
        { id: 2, track_number: 1, disc_number: 1, title: 'B' },
      ],
    });
    expect(rows).toHaveLength(2);
  });

  it('merges expected-missing tracks that are not already owned', () => {
    const rows = getAlbumTrackRows({
      id: 42,
      tracks: [{ id: 1, track_number: 1, disc_number: 1, title: 'Owned' }],
      missing_tracks: [
        { title: 'Missing', track_number: 2, disc_number: 1, source: 'spotify', track_id: 's2' },
      ],
    });
    expect(rows.map((r) => r.title)).toEqual(['Owned', 'Missing']);
  });

  it('does NOT re-add a missing track whose slot is already owned', () => {
    const rows = getAlbumTrackRows({
      id: 42,
      tracks: [{ id: 1, track_number: 1, disc_number: 1, title: 'Owned' }],
      missing_tracks: [
        { title: 'Dup', track_number: 1, disc_number: 1, source: 'spotify', track_id: 's1' },
      ],
    });
    expect(rows).toHaveLength(1);
  });

  it('drops a missing track with no actionable context', () => {
    const rows = getAlbumTrackRows({
      id: 42,
      tracks: [],
      missing_tracks: [{ title: 'Nothing to do', track_number: 2 }],
    });
    expect(rows).toEqual([]);
  });

  it('sorts by disc, then track, then title', () => {
    const rows = getAlbumTrackRows({
      tracks: [
        { id: 1, disc_number: 2, track_number: 1, title: 'D2T1' },
        { id: 2, disc_number: 1, track_number: 2, title: 'D1T2' },
        { id: 3, disc_number: 1, track_number: 1, title: 'B' },
        { id: 4, disc_number: 1, track_number: 1, title: 'A' },
      ],
    });
    expect(rows.map((r) => r.title)).toEqual(['A', 'B', 'D1T2', 'D2T1']);
  });

  it('survives an album with no tracks at all', () => {
    expect(getAlbumTrackRows({})).toEqual([]);
  });
});

describe('expandedHeaderDetails', () => {
  it('builds the full detail line in order', () => {
    const album = {
      year: 1992,
      label: 'Apollo',
      record_type: 'album',
      tracks: [{ duration: 300_000 }, { duration: 60_000 }],
    };
    expect(expandedHeaderDetails(album, getAlbumTrackRows(album))).toBe(
      '1992 · 2 tracks · 6:00 · Apollo · ALBUM',
    );
  });

  it('reads owned/expected only when the album is INCOMPLETE', () => {
    const incomplete = { tracks: [{ id: 1 }], api_track_count: 12 };
    expect(expandedHeaderDetails(incomplete, getAlbumTrackRows(incomplete))).toBe('1/12 tracks');

    const complete = { tracks: [{ id: 1 }], api_track_count: 1 };
    expect(expandedHeaderDetails(complete, getAlbumTrackRows(complete))).toBe('1 track');
  });

  it('never lets an under-reporting source make an album look over-full', () => {
    // Expected is the LARGEST of owned / rendered / claimed.
    const album = { tracks: [{ id: 1 }, { id: 2 }], api_track_count: 1 };
    expect(expandedHeaderDetails(album, getAlbumTrackRows(album))).toBe('2 tracks');
  });

  it('counts the missing rows separately', () => {
    const album = {
      id: 42,
      tracks: [{ id: 1, track_number: 1 }],
      api_track_count: 2,
      missing_tracks: [{ title: 'M', track_number: 2, source: 'spotify', track_id: 's' }],
    };
    expect(expandedHeaderDetails(album, getAlbumTrackRows(album))).toBe('1/2 tracks · 1 missing');
  });

  it('counts the RENDERED rows toward expected, even with no claimed count', () => {
    // A source that never reported a track count still has its missing rows
    // shown, and those rows are part of what the album is expected to contain.
    const album = {
      id: 42,
      tracks: [{ id: 1, track_number: 1 }],
      missing_tracks: [{ title: 'M', track_number: 2, source: 'spotify', track_id: 's' }],
    };
    expect(expandedHeaderDetails(album, getAlbumTrackRows(album))).toBe('1/2 tracks · 1 missing');
  });

  it('says so while the canonical tracklist is still loading', () => {
    expect(expandedHeaderDetails({ _canonicalTracksLoading: true, tracks: [] }, [])).toBe(
      'checking tracklist · 0 tracks',
    );
  });

  it('omits a zero duration rather than printing the dash sentinel', () => {
    expect(expandedHeaderDetails({ tracks: [{ id: 1 }] }, [])).toBe('1 track');
  });
});

describe('albumMetaFields', () => {
  it('joins genres into one comma string for the form', () => {
    const fields = albumMetaFields({ genres: ['ambient', 'idm'] });
    expect(fields.find((f) => f.key === 'genres')?.value).toBe('ambient, idm');
  });

  it('keeps a non-array genres value as-is', () => {
    expect(albumMetaFields({ genres: 'ambient' }).find((f) => f.key === 'genres')?.value).toBe(
      'ambient',
    );
  });

  it('renders explicit as the string 1 or 0, not a boolean', () => {
    expect(albumMetaFields({ explicit: true }).find((f) => f.key === 'explicit')?.value).toBe('1');
    expect(albumMetaFields({}).find((f) => f.key === 'explicit')?.value).toBe('0');
  });

  it('defaults the type to album, and everything else to empty', () => {
    const fields = albumMetaFields({});
    expect(fields.find((f) => f.key === 'record_type')?.value).toBe('album');
    expect(fields.find((f) => f.key === 'title')?.value).toBe('');
  });

  it('keeps the vanilla field order', () => {
    expect(albumMetaFields({}).map((f) => f.key)).toEqual([
      'title',
      'year',
      'release_date',
      'genres',
      'label',
      'style',
      'mood',
      'record_type',
      'explicit',
    ]);
  });
});

describe('albumMetaUpdates', () => {
  const album = { title: 'SAW', year: 1992, genres: ['ambient'], label: 'Apollo' };

  it('sends ONLY the fields that changed', () => {
    // Sending everything would clobber columns another process touched between
    // load and save.
    const { updates } = albumMetaUpdates(album, { title: 'SAW', label: 'Warp' });
    expect(updates).toEqual({ label: 'Warp' });
  });

  it('ALWAYS reports explicit:0 for a non-explicit album', () => {
    // `album.explicit || null` reads a falsy 0 as null, so 0 !== null every
    // time. Verbatim vanilla — a save on such an album is never a no-op.
    expect(albumMetaUpdates({ explicit: 0 }, { explicit: '0' }).updates).toEqual({ explicit: 0 });
    expect(albumMetaUpdates({}, { explicit: '0' }).updates).toEqual({ explicit: 0 });
    // An album that IS explicit compares cleanly.
    expect(albumMetaUpdates({ explicit: 1 }, { explicit: '1' }).updates).toEqual({});
  });

  it('sends null — not an empty string — for a cleared field', () => {
    expect(albumMetaUpdates(album, { label: '' }).updates).toEqual({ label: null });
  });

  it('parses numeric fields, and nulls an emptied one', () => {
    expect(albumMetaUpdates(album, { year: '1993' }).updates).toEqual({ year: 1993 });
    expect(albumMetaUpdates(album, { year: '' }).updates).toEqual({ year: null });
    expect(albumMetaUpdates(album, { year: '1992' }).updates).toEqual({});
  });

  it('compares genres as a LIST, not as the joined string', () => {
    expect(albumMetaUpdates(album, { genres: 'ambient' }).updates).toEqual({});
    expect(albumMetaUpdates(album, { genres: 'ambient, idm' }).updates).toEqual({
      genres: ['ambient', 'idm'],
    });
    expect(albumMetaUpdates(album, { genres: '' }).updates).toEqual({ genres: [] });
  });

  it('drops blank entries from a sloppy genre list', () => {
    expect(albumMetaUpdates(album, { genres: 'ambient, , idm,' }).updates).toEqual({
      genres: ['ambient', 'idm'],
    });
  });

  it('accepts a year, a year-month or a full release date (#824)', () => {
    for (const value of ['1992', '1992-11', '1992-11-08']) {
      expect(albumMetaUpdates(album, { release_date: value }).invalidDate).toBe(false);
    }
  });

  it('flags a malformed release date and saves NOTHING from that field', () => {
    const result = albumMetaUpdates(album, { release_date: '08/11/1992', label: 'Warp' });
    expect(result.invalidDate).toBe(true);
    expect(result.updates.release_date).toBeUndefined();
  });

  it('accepts an EMPTY release date as a clear', () => {
    const result = albumMetaUpdates({ release_date: '1992' }, { release_date: '' });
    expect(result.invalidDate).toBe(false);
    expect(result.updates).toEqual({ release_date: null });
  });

  it('trims before comparing, so whitespace alone is not a change', () => {
    expect(albumMetaUpdates(album, { title: '  SAW  ' }).updates).toEqual({});
  });
});

describe('normTitleForMatch', () => {
  it('collapses different separators to the same key', () => {
    expect(normTitleForMatch('X (Main Theme)')).toBe(normTitleForMatch('X - Main Theme'));
  });

  it('KEEPS bracket content — that is what makes editions line up', () => {
    expect(normTitleForMatch('X (Main Theme)')).toBe('x main theme');
  });

  it('removes a featured credit in brackets or trailing', () => {
    expect(normTitleForMatch('Song (feat. Y)')).toBe('song');
    expect(normTitleForMatch('Song [ft Y]')).toBe('song');
    expect(normTitleForMatch('Song feat. Y and Z')).toBe('song');
  });

  it('is empty for junk', () => {
    expect(normTitleForMatch(null)).toBe('');
    expect(normTitleForMatch('!!!')).toBe('');
  });
});

describe('getAlbumCanonicalSource', () => {
  it('prefers Spotify, then Deezer, then iTunes', () => {
    expect(getAlbumCanonicalSource({ deezer_id: 'd', spotify_album_id: 's' })).toEqual({
      source: 'spotify',
      id: 's',
    });
    expect(getAlbumCanonicalSource({ itunes_album_id: 'i', deezer_id: 'd' })).toEqual({
      source: 'deezer',
      id: 'd',
    });
  });

  it('is null when the album is matched to nothing', () => {
    expect(getAlbumCanonicalSource({})).toBeNull();
  });
});

describe('deriveMissingTracks', () => {
  const canonical = (n: number, title: string, disc = 1) => ({
    track_number: n,
    disc_number: disc,
    title,
    name: title,
    source: 'spotify',
    track_id: `s${n}`,
  });

  it('matches by disc:track slot first', () => {
    const album = { id: 1, tracks: [{ id: 1, track_number: 1, disc_number: 1, title: 'Owned' }] };
    expect(deriveMissingTracks(album, [canonical(1, 'Different Title')])).toEqual([]);
  });

  it('falls back to TITLE when every owned track claims disc 1 (#916)', () => {
    // The scanner does not split discs, so a strict slot match would report
    // every canonical disc-2 track as missing.
    const album = {
      id: 1,
      tracks: [{ id: 1, track_number: 1, disc_number: 1, title: 'Disc Two Opener' }],
    };
    expect(deriveMissingTracks(album, [canonical(1, 'Disc Two Opener', 2)])).toEqual([]);
  });

  it('consumes each owned track once, so a duplicate title still counts missing', () => {
    const album = { id: 1, tracks: [{ id: 1, track_number: 9, disc_number: 1, title: 'Same' }] };
    const missing = deriveMissingTracks(album, [canonical(1, 'Same'), canonical(2, 'Same')]);
    expect(missing).toHaveLength(1);
  });

  it('does NOT let two untitled tracks match each other', () => {
    // Both normalise to an empty key; matching on that would silently mark a
    // missing track as owned.
    const album = { id: 1, tracks: [{ id: 1, track_number: 9, disc_number: 1, title: '!!!' }] };
    const missing = deriveMissingTracks(album, [
      {
        track_number: 1,
        disc_number: 1,
        title: '???',
        name: '???',
        source: 'spotify',
        track_id: 's1',
      },
    ]);
    expect(missing).toHaveLength(1);
  });

  it('reports a genuinely absent track', () => {
    const album = { id: 1, tracks: [] };
    const missing = deriveMissingTracks(album, [canonical(1, 'Gone')]);
    expect(missing[0].name).toBe('Gone');
  });

  it('skips a canonical track with no usable slot or context', () => {
    const album = { id: 1, tracks: [] };
    expect(deriveMissingTracks(album, [{ title: 'No number' }])).toEqual([]);
  });

  it('carries a duration through under duration_ms', () => {
    const album = { id: 1, tracks: [] };
    const [row] = deriveMissingTracks(album, [{ ...canonical(1, 'X'), duration: 1234 }]);
    expect(row.duration_ms).toBe(1234);
  });
});

describe('normalizeCanonicalTracks', () => {
  it('fills in title, name, numbers and duration', () => {
    const [row] = normalizeCanonicalTracks(
      [{ id: 'a', name: 'X', duration_ms: 500 }],
      'spotify',
      'al1',
    );
    expect(row.title).toBe('X');
    expect(row.track_number).toBe(1);
    expect(row.disc_number).toBe(1);
    expect(row.duration).toBe(500);
    expect(row.track_id).toBe('a');
  });

  it('SYNTHESISES an id when the source gives none', () => {
    // Without a stable key the row cannot be tracked across a re-render.
    const [row] = normalizeCanonicalTracks([{ name: 'X', track_number: 4 }], 'deezer', 'al1');
    expect(row.id).toBe('deezer:al1:1:4');
  });

  it('names an untitled track by its position', () => {
    const rows = normalizeCanonicalTracks([{}, {}], 'spotify', 'al1');
    expect(rows.map((r) => r.title)).toEqual(['Track 1', 'Track 2']);
  });

  it('prefers the source the PAYLOAD reports over the one we asked for', () => {
    const [row] = normalizeCanonicalTracks([{ id: 'a' }], 'spotify', 'al1', 'spotify_free');
    expect(row.source).toBe('spotify_free');
  });
});

describe('inlineEditValue', () => {
  it('parses whole numbers for the track and disc slots', () => {
    expect(inlineEditValue('track_number', '7.9')).toBe(7);
    expect(inlineEditValue('disc_number', '2')).toBe(2);
  });

  it('keeps BPM fractional', () => {
    expect(inlineEditValue('bpm', '128.5')).toBe(128.5);
  });

  it('CLEARS a number to null so the field can be emptied', () => {
    expect(inlineEditValue('bpm', '')).toBeNull();
    expect(inlineEditValue('track_number', 'abc')).toBeNull();
  });

  it('floors explicit to 0, never null — the column is a flag', () => {
    // A flag has no "unknown" state, so an empty input means "not explicit".
    expect(inlineEditValue('explicit', '')).toBe(0);
    expect(inlineEditValue('explicit', 'x')).toBe(0);
    expect(inlineEditValue('explicit', '1')).toBe(1);
  });

  it('passes text through untouched', () => {
    expect(inlineEditValue('title', '  Xtal ')).toBe('  Xtal ');
  });
});

describe('inlineEditDisplay', () => {
  it('shows a dash for a cleared value', () => {
    expect(inlineEditDisplay(null)).toBe('-');
    expect(inlineEditDisplay('')).toBe('-');
  });

  it('shows a real zero rather than a dash', () => {
    expect(inlineEditDisplay(0)).toBe('0');
  });
});

describe('inlineEditInput', () => {
  it('is a number box for the numeric fields only', () => {
    expect(inlineEditInput('bpm').type).toBe('number');
    expect(inlineEditInput('title').type).toBe('text');
    expect(inlineEditInput('title').className).not.toContain('num');
  });
});

describe('inlineEditUrl', () => {
  it('routes each entity to its own endpoint', () => {
    expect(inlineEditUrl('track', 5)).toBe('/api/library/track/5');
    expect(inlineEditUrl('album', 9)).toBe('/api/library/album/9');
  });
});
