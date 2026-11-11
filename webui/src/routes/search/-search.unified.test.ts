import { describe, expect, it } from 'vitest';

import type { SearchAlbum, SearchArtist, SearchTrack, SourceResults } from './-search.types';

import {
  foldFirstArtist,
  foldText,
  foldTitle,
  mergeUnified,
  sameAlbum,
  sameArtist,
  sameTrack,
  sourcePriority,
  unifiedMembers,
  unifiedProgress,
} from './-search.unified';

function results(over: Partial<SourceResults> = {}): SourceResults {
  return {
    db_artists: [],
    artists: [],
    albums: [],
    tracks: [],
    playlists: [],
    videos: [],
    ...over,
  };
}

const album = (name: string, artist: string, over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  name,
  artist,
  album_type: 'album',
  ...over,
});

const track = (name: string, artist: string, over: Partial<SearchTrack> = {}): SearchTrack => ({
  name,
  artist,
  ...over,
});

describe('folding text', () => {
  it('drops what sources spell differently, and nothing else', () => {
    expect(foldText('Beyoncé')).toBe('beyonce');
    expect(foldText('AC/DC')).toBe('ac dc');
    expect(foldText('Simon & Garfunkel')).toBe('simon and garfunkel');
    expect(foldText('  OK   Computer ')).toBe('ok computer');
  });

  it('keeps the words that make an edition or a remix a different thing', () => {
    expect(foldTitle('OK Computer')).not.toBe(foldTitle('OK Computer OKNOTOK 1997 2017'));
    expect(foldTitle('Geogaddi (Deluxe Edition)')).toBe('geogaddi deluxe edition');
    expect(foldTitle('One More Time (Radio Edit)')).not.toBe(foldTitle('One More Time'));
  });

  it('reads past a credit one source appends and another leaves off', () => {
    expect(foldTitle('edamame (feat. Rich Brian)')).toBe('edamame');
    expect(foldTitle('edamame [ft. Rich Brian]')).toBe('edamame');
    expect(foldTitle('edamame feat. Rich Brian')).toBe('edamame');
  });

  it('takes the first credited artist', () => {
    expect(foldFirstArtist('bbno$, Rich Brian')).toBe('bbno');
    expect(foldFirstArtist('Daft Punk feat. Pharrell Williams')).toBe('daft punk');
    expect(foldFirstArtist('Simon & Garfunkel')).toBe('simon');
    expect(foldFirstArtist('')).toBe('');
  });
});

describe('what counts as the same thing', () => {
  it('artists: the same name', () => {
    expect(sameArtist({ name: 'Sigur Rós' }, { name: 'sigur ros' })).toBe(true);
    expect(sameArtist({ name: 'Nirvana' }, { name: 'Nirvana UK' })).toBe(false);
    expect(sameArtist({ name: '' }, { name: '' })).toBe(false);
  });

  it('albums: artist, title, kind and year agree', () => {
    const a = album('Kid A', 'Radiohead', { release_date: '2000-10-02' });
    expect(sameAlbum(a, album('Kid A', 'Radiohead', { release_date: '2000' }))).toBe(true);
    // a year either side is the same release dated two ways
    expect(sameAlbum(a, album('Kid A', 'Radiohead', { release_date: '2001-01-01' }))).toBe(true);
    // unknown on one side is not a disagreement
    expect(sameAlbum(a, album('Kid A', 'Radiohead'))).toBe(true);
    expect(sameAlbum(a, album('Kid A', 'Radiohead', { release_date: '2009' }))).toBe(false);
    expect(sameAlbum(a, album('Kid A', 'Someone Else', { release_date: '2000' }))).toBe(false);
    expect(sameAlbum(a, album('Kid A Mnesia', 'Radiohead', { release_date: '2000' }))).toBe(false);
  });

  it('albums: a single is not the album named after it', () => {
    const lp = album('Legacy', 'Mendo Dope', { total_tracks: 15 });
    expect(sameAlbum(lp, album('Legacy', 'Mendo Dope', { album_type: 'single' }))).toBe(false);
    // filed as an album by a source that only says how small it is
    expect(sameAlbum(lp, album('Legacy', 'Mendo Dope', { total_tracks: 1 }))).toBe(false);
    // an EP on one source and a single on another is one short release
    expect(
      sameAlbum(
        album('Short', 'A', { album_type: 'ep', total_tracks: 4 }),
        album('Short', 'A', { album_type: 'single', total_tracks: 4 }),
      ),
    ).toBe(true);
  });

  it('albums: one with no artist cannot be matched to a namesake', () => {
    expect(sameAlbum(album('Greatest Hits', ''), album('Greatest Hits', ''))).toBe(false);
  });

  it('albums: reads the artist list before the joined string', () => {
    const a = album('Kid A', 'Radiohead, Someone', { artists: [{ name: 'Radiohead' }] });
    expect(sameAlbum(a, album('Kid A', 'Radiohead'))).toBe(true);
  });

  it('tracks: artist, title and length agree', () => {
    const t = track('Idioteque', 'Radiohead', { duration_ms: 309_000 });
    expect(sameTrack(t, track('Idioteque', 'Radiohead', { duration_ms: 311_000 }))).toBe(true);
    expect(sameTrack(t, track('Idioteque', 'Radiohead'))).toBe(true);
    // a live take or an edit runs a different length
    expect(sameTrack(t, track('Idioteque', 'Radiohead', { duration_ms: 250_000 }))).toBe(false);
    expect(sameTrack(t, track('Idioteque (Live)', 'Radiohead'))).toBe(false);
    expect(sameTrack(t, track('Idioteque', 'A Cover Band'))).toBe(false);
  });
});

describe('unifiedMembers', () => {
  it('asks every catalog source that is set up, and none that is not', () => {
    const visible = ['spotify', 'itunes', 'deezer', 'musicbrainz', 'youtube_videos', 'soulseek'];
    expect(unifiedMembers(visible, { deezer: false })).toEqual([
      'spotify',
      'itunes',
      'musicbrainz',
    ]);
    // a source config-status never mentioned needs no credentials
    expect(unifiedMembers(['amazon', 'musicbrainz'], {})).toEqual(['amazon', 'musicbrainz']);
  });
});

describe('sourcePriority', () => {
  it('leads with the user’s own source', () => {
    expect(sourcePriority('deezer').slice(0, 3)).toEqual(['deezer', 'spotify', 'itunes']);
    // spotify without a login is still the spotify icon
    expect(sourcePriority('spotify_free')[0]).toBe('spotify');
    // an unknown one changes nothing
    expect(sourcePriority('made-up')[0]).toBe('spotify');
    expect(sourcePriority(undefined)[0]).toBe('spotify');
  });
});

describe('mergeUnified', () => {
  const members = ['spotify', 'deezer', 'musicbrainz'];

  it('shows a release every source has once, saying which sources', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({ albums: [album('Kid A', 'Radiohead', { id: 'sp1' })] }),
        deezer: results({ albums: [album('Kid A', 'Radiohead', { id: 'dz1' })] }),
        musicbrainz: results({ albums: [album('Kid A', 'Radiohead', { id: 'mb1' })] }),
      },
      {},
    );
    expect(merged.albums).toHaveLength(1);
    expect(merged.albums[0]).toMatchObject({
      id: 'sp1',
      source: 'spotify',
      sources: ['spotify', 'deezer', 'musicbrainz'],
    });
  });

  it('opens a shared card from the user’s own source', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({ artists: [{ id: 'sp', name: 'Radiohead' }] }),
        deezer: results({ artists: [{ id: 'dz', name: 'Radiohead' }] }),
      },
      {},
      'deezer',
    );
    expect(merged.artists[0]).toMatchObject({ id: 'dz', source: 'deezer' });
    expect(merged.artists[0].sources).toEqual(['deezer', 'spotify']);
  });

  it('never folds two results of one source together', () => {
    // two artists called Nirvana on the same source are two artists
    const merged = mergeUnified(
      members,
      {
        spotify: results({
          artists: [
            { id: 'a', name: 'Nirvana' },
            { id: 'b', name: 'Nirvana' },
          ],
        }),
        deezer: results({ artists: [{ id: 'c', name: 'Nirvana' }] }),
      },
      {},
    );
    expect(merged.artists.map((a) => [a.id, a.sources])).toEqual([
      ['a', ['spotify', 'deezer']],
      ['b', ['spotify']],
    ]);
  });

  it('keeps an edition, and a single, apart from the album', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({ albums: [album('Geogaddi', 'Boards of Canada', { id: 1 })] }),
        deezer: results({
          albums: [
            album('Geogaddi (Deluxe Edition)', 'Boards of Canada', { id: 2 }),
            album('Geogaddi', 'Boards of Canada', { id: 3, album_type: 'single' }),
          ],
        }),
      },
      {},
    );
    expect(merged.albums.map((a) => a.id)).toEqual([1, 2, 3]);
  });

  it('leads with what the sources agree on, over one source’s first hit', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({
          tracks: [track('One', 'A', { id: 's1' }), track('Shared', 'A', { id: 's2' })],
        }),
        deezer: results({
          tracks: [track('Two', 'A', { id: 'd1' }), track('Shared', 'A', { id: 'd2' })],
        }),
        musicbrainz: results({ tracks: [track('Three', 'A', { id: 'm1' })] }),
      },
      {},
    );
    // two sources' second hit outweighs any one source's first; the lone
    // first hits follow in source order
    expect(merged.tracks.map((t) => t.id)).toEqual(['s2', 's1', 'd1', 'm1']);
    expect(merged.tracks[0].sources).toEqual(['spotify', 'deezer']);
  });

  it('keeps one source’s first hit above a match far down two lists', () => {
    const filler = (prefix: string) =>
      Array.from({ length: 12 }, (_, i) => track(`${prefix} ${i}`, 'A', { id: `${prefix}${i}` }));
    const merged = mergeUnified(
      members,
      {
        spotify: results({ tracks: [...filler('s'), track('Deep', 'A', { id: 'sd' })] }),
        deezer: results({ tracks: [...filler('d'), track('Deep', 'A', { id: 'dd' })] }),
      },
      {},
    );
    const ids = merged.tracks.map((t) => t.id);
    expect(ids[0]).toBe('s0');
    expect(ids.indexOf('sd')).toBeGreaterThan(ids.indexOf('d0'));
  });

  it('a result two sources both rank first outranks one only a single source does', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({ artists: [{ id: 's-solo', name: 'Solo' }] }),
        deezer: results({ artists: [{ id: 'd-both', name: 'Both' }] }),
        musicbrainz: results({ artists: [{ id: 'm-both', name: 'Both' }] }),
      },
      {},
    );
    expect(merged.artists.map((a) => a.name)).toEqual(['Both', 'Solo']);
  });

  it('leaves out a source that was answered from somewhere else', () => {
    // discogs had no client and the server answered with deezer's results:
    // counting them would list deezer twice, under the wrong name
    const merged = mergeUnified(
      ['deezer', 'discogs'],
      {
        deezer: results({ albums: [album('Kid A', 'Radiohead', { id: 'dz' })] }),
        discogs: results({ albums: [album('Kid A', 'Radiohead', { id: 'dz' })] }),
      },
      { discogs: 'deezer' },
    );
    expect(merged.albums).toHaveLength(1);
    expect(merged.albums[0].sources).toEqual(['deezer']);
  });

  it('still counts spotify when its no-login client answered', () => {
    const merged = mergeUnified(
      ['spotify'],
      { spotify: results({ artists: [{ id: 's', name: 'A' }] }) },
      { spotify: 'spotify_free' },
    );
    expect(merged.artists).toHaveLength(1);
  });

  it('works on whatever has arrived so far', () => {
    const merged = mergeUnified(
      members,
      { deezer: results({ artists: [{ id: 'd', name: 'A' }] }) },
      {},
    );
    expect(merged.artists.map((a) => a.sources)).toEqual([['deezer']]);
    expect(mergeUnified(members, {}, {})).toEqual(results());
  });

  it('takes the library’s own artists from whoever brought them', () => {
    const lib: SearchArtist[] = [{ id: 7, name: 'Radiohead', library_v2_id: 7 }];
    const merged = mergeUnified(
      members,
      { spotify: results(), deezer: results({ db_artists: lib }) },
      {},
    );
    expect(merged.db_artists).toBe(lib);
  });

  it('lists each playlist once and says where it is from', () => {
    const merged = mergeUnified(
      members,
      {
        spotify: results({ playlists: [{ id: 'p1', name: 'Mix' }] }),
        deezer: results({
          playlists: [
            { id: 'p1', name: 'Mix' },
            { id: 'p1', name: 'Mix' },
          ],
        }),
      },
      {},
    );
    expect(merged.playlists.map((p) => p.source)).toEqual(['spotify', 'deezer']);
  });

  it('does not change the source lists it was given', () => {
    const spotify = results({ artists: [{ id: 's', name: 'A' }] });
    const before = JSON.stringify(spotify);
    mergeUnified(members, { spotify }, {});
    expect(JSON.stringify(spotify)).toBe(before);
  });
});

describe('unifiedProgress', () => {
  it('says who answered, who is still searching and who never did', () => {
    const progress = unifiedProgress(
      ['spotify', 'deezer', 'itunes', 'musicbrainz'],
      { spotify: results(), deezer: results() },
      new Set(['itunes', 'deezer']),
      new Set(['musicbrainz', 'spotify']),
    );
    expect(progress).toEqual({
      total: 4,
      answered: ['spotify', 'deezer'],
      // an answer outranks a spinner or a failure left over from before it
      pending: ['itunes'],
      failed: ['musicbrainz'],
    });
  });
});
