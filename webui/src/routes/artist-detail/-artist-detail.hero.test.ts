import { afterEach, describe, expect, it } from 'vitest';

import { audioDbLogoUrl, audioDbSlug, buildHeroBadges } from './-artist-detail.hero';

afterEach(() => {
  document.body.innerHTML = '';
});

describe('buildHeroBadges', () => {
  it('renders nothing for an unenriched artist', () => {
    expect(buildHeroBadges({ name: 'Nobody' })).toEqual([]);
  });

  it('keeps the vanilla declaration order, which is visible in the hero', () => {
    const artist = {
      name: 'A',
      spotify_artist_id: 's',
      musicbrainz_id: 'm',
      deezer_id: 1,
      audiodb_id: 2,
      itunes_artist_id: 3,
      lastfm_url: 'https://last.fm/a',
      genius_url: 'https://genius.com/a',
      tidal_id: 4,
      qobuz_id: 5,
      discogs_id: 6,
      amazon_id: 'az',
      bandcamp_url: 'https://a.bandcamp.com',
      soul_id: 'soul_123',
    };
    expect(buildHeroBadges(artist).map((b) => b.key)).toEqual([
      'spotify',
      'musicbrainz',
      'deezer',
      'audiodb',
      'itunes',
      'lastfm',
      'genius',
      'tidal',
      'qobuz',
      'discogs',
      'amazon',
      'bandcamp',
      'soulsync',
    ]);
  });

  it('includes Bandcamp — the library CARD badge list does not', () => {
    // The two lists genuinely differ; merging them would change one page.
    const badges = buildHeroBadges({ name: 'A', bandcamp_url: 'https://a.bandcamp.com' });
    expect(badges).toHaveLength(1);
    expect(badges[0]).toMatchObject({ key: 'bandcamp', fallback: 'BC', title: 'Bandcamp' });
  });

  it('deep-links each provider', () => {
    const url = (artist: Parameters<typeof buildHeroBadges>[0]) => buildHeroBadges(artist)[0].url;
    expect(url({ spotify_artist_id: 'sp' })).toBe('https://open.spotify.com/artist/sp');
    expect(url({ musicbrainz_id: 'mb' })).toBe('https://musicbrainz.org/artist/mb');
    expect(url({ deezer_id: 7 })).toBe('https://www.deezer.com/artist/7');
    expect(url({ itunes_artist_id: 8 })).toBe('https://music.apple.com/artist/8');
    expect(url({ tidal_id: 9 })).toBe('https://tidal.com/browse/artist/9');
    expect(url({ qobuz_id: 10 })).toBe('https://www.qobuz.com/artist/10');
    expect(url({ discogs_id: 11 })).toBe('https://www.discogs.com/artist/11');
  });

  it('passes Last.fm, Genius and Bandcamp urls through unchanged', () => {
    // These are stored as full urls, not ids.
    expect(buildHeroBadges({ lastfm_url: 'https://last.fm/x' })[0].url).toBe('https://last.fm/x');
    expect(buildHeroBadges({ genius_url: 'https://genius.com/x' })[0].url).toBe(
      'https://genius.com/x',
    );
    expect(buildHeroBadges({ bandcamp_url: 'https://x.bandcamp.com' })[0].url).toBe(
      'https://x.bandcamp.com',
    );
  });

  it('gives Amazon and SoulID no link — neither has a public artist page', () => {
    expect(buildHeroBadges({ amazon_id: 'az' })[0].url).toBeNull();
    expect(buildHeroBadges({ soul_id: 'soul_1' })[0].url).toBeNull();
  });

  it('titles the SoulID badge with the id itself', () => {
    expect(buildHeroBadges({ soul_id: 'soul_42' })[0].title).toBe('SoulID: soul_42');
  });

  it('hides a placeholder soul id', () => {
    expect(buildHeroBadges({ soul_id: 'soul_unnamed_9' })).toEqual([]);
    expect(buildHeroBadges({ soul_id: 'soul_9' })).toHaveLength(1);
  });

  it('treats a zero id as absent', () => {
    expect(buildHeroBadges({ deezer_id: 0, tidal_id: 0 })).toEqual([]);
  });

  it('builds the AudioDB url from the slugified name', () => {
    const badge = buildHeroBadges({ name: 'Sigur Rós', audiodb_id: 5 })[0];
    expect(badge.url).toBe('https://www.theaudiodb.com/artist/5-Sigur-Rs');
  });
});

describe('audioDbSlug', () => {
  it('hyphenates spaces BEFORE stripping non-alphanumerics', () => {
    // Order matters: doing it the other way round gives "SigurRs".
    expect(audioDbSlug('Sigur Rós')).toBe('Sigur-Rs');
    expect(audioDbSlug('AC/DC')).toBe('ACDC');
    expect(audioDbSlug('!!!')).toBe('');
    expect(audioDbSlug(undefined)).toBe('');
  });
});

describe('audioDbLogoUrl', () => {
  it('reads the logo off an existing img.audiodb-logo', () => {
    document.body.innerHTML = '<img class="audiodb-logo" src="/static/adb.png">';
    expect(audioDbLogoUrl()).toContain('/static/adb.png');
  });

  it('returns empty when there is none, so the badge falls back to its text', () => {
    expect(audioDbLogoUrl()).toBe('');
    expect(buildHeroBadges({ audiodb_id: 1, name: 'A' })[0]).toMatchObject({
      logo: '',
      fallback: 'ADB',
    });
  });
});
