import { describe, expect, it } from 'vitest';

import {
  applyMusicBrainzDeclutter,
  classifyReleaseContent,
  DEFAULT_DISCOGRAPHY_FILTERS,
  groupReleases,
  passesDiscographyFilters,
  releaseSection,
  type DiscographyFilterState,
} from './discography-filters';

function filters(patch: Partial<DiscographyFilterState>): DiscographyFilterState {
  return { ...DEFAULT_DISCOGRAPHY_FILTERS, ...patch };
}

describe('classifyReleaseContent (ldp-04, ported from library.js #877)', () => {
  it('detects live releases from the title in every legacy spelling', () => {
    expect(classifyReleaseContent({ title: 'Live at Wembley' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Nevermind (Live in Rome)' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Nevermind [Live]' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Alive' }).isLive).toBe(false);
  });

  it('treats the compilation album type as authoritative on top of the title', () => {
    expect(classifyReleaseContent({ title: 'Greatest Hits' }).isCompilation).toBe(true);
    expect(
      classifyReleaseContent({ title: 'Untitled', album_type: 'compilation' }).isCompilation,
    ).toBe(true);
    expect(classifyReleaseContent({ title: 'Untitled', album_type: 'album' }).isCompilation).toBe(
      false,
    );
  });

  it('reads either title or name, since the two payload shapes differ', () => {
    expect(classifyReleaseContent({ name: 'Song (feat. Someone)' }).isFeatured).toBe(true);
  });
});

describe('releaseSection: where a release is listed (#2055)', () => {
  it('leaves a plain album, EP and single where their type puts them', () => {
    expect(releaseSection({ title: 'Dummy' }, 'albums')).toBe('albums');
    expect(releaseSection({ title: 'Glory Box' }, 'eps')).toBe('eps');
    expect(releaseSection({ title: 'Sour Times' }, 'singles')).toBe('singles');
  });

  it.each([
    ['Roseland NYC Live', 'live'],
    ['HAARP (Live from Wembley Stadium)', 'live'],
    ['Hullabaloo Soundtrack', 'soundtracks'],
    ['Legend (Original Motion Picture Soundtrack)', 'soundtracks'],
    ['Tron: Legacy (Original Score)', 'soundtracks'],
    ['Akira OST', 'soundtracks'],
    ['Greatest Hits', 'compilations'],
    ['Anti-Hero (Remixes)', 'remixes'],
    ['Fabric 55 (DJ Mix)', 'remixes'],
    ['The Complete Interviews', 'spoken'],
  ] as const)('guesses %j from its title when nobody has said', (title, section) => {
    expect(releaseSection({ title }, 'albums')).toBe(section);
    expect(releaseSection({ title }, 'eps')).toBe(section);
  });

  it('does not read a kind into an ordinary word', () => {
    for (const title of ['Alive', 'Lost in the Post', 'Remixing the Stars', 'Costello']) {
      expect(releaseSection({ title }, 'albums')).toBe('albums');
    }
  });

  it('a single stays a single, whatever else it is', () => {
    expect(releaseSection({ title: 'Uprising (Live)' }, 'singles')).toBe('singles');
    expect(releaseSection({ title: 'X', release_types: ['remix'] }, 'singles')).toBe('singles');
  });

  it('goes by the known types over the title', () => {
    // known to be a studio album: the title is not asked
    expect(releaseSection({ title: 'Live Through This', release_types: [] }, 'albums')).toBe(
      'albums',
    );
    expect(releaseSection({ title: 'Plain Name', release_types: ['live'] }, 'albums')).toBe('live');
    expect(releaseSection({ title: 'Plain Name', release_types: ['Soundtrack'] }, 'eps')).toBe(
      'soundtracks',
    );
  });

  it('a MusicBrainz listing has always said, even with no types', () => {
    expect(
      releaseSection({ title: 'Live Through This', secondary_types: [] }, 'albums', true),
    ).toBe('albums');
    expect(releaseSection({ title: 'Live Through This' }, 'albums', true)).toBe('albums');
    expect(releaseSection({ title: 'X', secondary_types: ['dj-mix'] }, 'albums', true)).toBe(
      'remixes',
    );
  });

  it('another source that sends an empty list has said nothing', () => {
    expect(releaseSection({ title: 'Live at Wembley', secondary_types: [] }, 'albums')).toBe(
      'live',
    );
  });

  it.each([
    [['live', 'compilation'], 'live'],
    [['soundtrack', 'compilation'], 'soundtracks'],
    [['dj-mix', 'compilation'], 'remixes'],
    [['compilation'], 'compilations'],
    [['spokenword', 'live'], 'spoken'],
    [['audiobook'], 'spoken'],
    [['demo'], 'demos'],
    [['mixtape/street'], 'albums'],
  ] as const)('files %j under the more telling kind', (types, section) => {
    expect(releaseSection({ title: 'X', release_types: [...types] }, 'albums')).toBe(section);
  });

  it('the compilation and live album types are an answer too', () => {
    expect(releaseSection({ title: 'X', album_type: 'compilation' }, 'albums')).toBe(
      'compilations',
    );
    expect(releaseSection({ title: 'X', album_type: 'live', release_types: [] }, 'albums')).toBe(
      'live',
    );
  });
});

describe('groupReleases', () => {
  it('sorts three lists into every section, keeping their order', () => {
    const grouped = groupReleases(
      {
        albums: [{ title: 'Dummy' }, { title: 'Roseland NYC Live' }, { title: 'Third' }],
        eps: [{ title: 'Numb (Remixes)' }, { title: 'Live at Glastonbury' }],
        singles: [{ title: 'Sour Times (Live)' }],
      },
      (item) => item,
    );
    expect(grouped.albums.map((r) => r.title)).toEqual(['Dummy', 'Third']);
    expect(grouped.live.map((r) => r.title)).toEqual(['Roseland NYC Live', 'Live at Glastonbury']);
    expect(grouped.remixes.map((r) => r.title)).toEqual(['Numb (Remixes)']);
    expect(grouped.singles.map((r) => r.title)).toEqual(['Sour Times (Live)']);
    expect(grouped.spoken).toEqual([]);
  });
});

describe('passesDiscographyFilters', () => {
  it('keeps everything under the default show-all state', () => {
    expect(
      passesDiscographyFilters(
        { title: 'Live at Wembley' },
        DEFAULT_DISCOGRAPHY_FILTERS,
        false,
        'live',
      ),
    ).toBe(true);
  });

  it('hides a section whose Show pill is off, and only that one', () => {
    const noLive = filters({
      categories: { ...DEFAULT_DISCOGRAPHY_FILTERS.categories, live: false },
    });
    expect(passesDiscographyFilters({ title: 'Live at Wembley' }, noLive, true, 'live')).toBe(
      false,
    );
    expect(passesDiscographyFilters({ title: 'Studio Record' }, noLive, true, 'albums')).toBe(true);
  });

  it('hides featured releases when Featured is off', () => {
    const noFeat = filters({ content: { featured: false } });
    expect(passesDiscographyFilters({ title: 'Song (feat. X)' }, noFeat, true, 'albums')).toBe(
      false,
    );
    expect(passesDiscographyFilters({ title: 'Song' }, noFeat, true, 'albums')).toBe(true);
  });

  it('applies the single-select ownership status', () => {
    const owned = filters({ ownership: 'owned' });
    const missing = filters({ ownership: 'missing' });
    expect(passesDiscographyFilters({ title: 'A' }, owned, true, 'albums')).toBe(true);
    expect(passesDiscographyFilters({ title: 'A' }, owned, false, 'albums')).toBe(false);
    expect(passesDiscographyFilters({ title: 'A' }, missing, false, 'albums')).toBe(true);
    expect(passesDiscographyFilters({ title: 'A' }, missing, true, 'albums')).toBe(false);
  });

  it('never hides a release whose ownership is still undetermined', () => {
    expect(
      passesDiscographyFilters({ title: 'A' }, filters({ ownership: 'owned' }), null, 'albums'),
    ).toBe(true);
  });
});

describe('MusicBrainz declutter (3.6.1 parity, third #1062 review G15-4)', () => {
  const mb = applyMusicBrainzDeclutter(DEFAULT_DISCOGRAPHY_FILTERS, 'musicbrainz');

  it('starts with every non-studio section hidden, and compilations shown', () => {
    for (const section of ['live', 'soundtracks', 'remixes', 'demos', 'spoken'] as const) {
      expect(mb.categories[section]).toBe(false);
      expect(passesDiscographyFilters({ title: 'X' }, mb, false, section)).toBe(false);
    }
    for (const section of ['albums', 'eps', 'singles', 'compilations'] as const) {
      expect(mb.categories[section]).toBe(true);
    }
  });

  it('never hides a non-studio release the user owns', () => {
    expect(passesDiscographyFilters({ title: 'X' }, mb, true, 'live')).toBe(true);
  });

  it('but a section the user turned off hides what they own too', () => {
    const noAlbums = { ...mb, categories: { ...mb.categories, albums: false } };
    expect(passesDiscographyFilters({ title: 'X' }, noAlbums, true, 'albums')).toBe(false);
  });

  it('leaves every other source on show-all', () => {
    expect(applyMusicBrainzDeclutter(DEFAULT_DISCOGRAPHY_FILTERS, 'spotify')).toEqual(
      DEFAULT_DISCOGRAPHY_FILTERS,
    );
  });
});
