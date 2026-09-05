import { describe, expect, it } from 'vitest';

import { getServiceUrl } from './-library-v2.service-links';

/**
 * "Open this on Spotify" from the library.
 *
 * Library v2 knew every provider id it holds and could do nothing with them:
 * they were a coverage ring and a copy-to-clipboard button. Upstream's
 * artist-detail page turns them into links (`getServiceUrl`), on the page this
 * branch deleted — so the capability never arrived here.
 *
 * The rules worth pinning are the ones that are not "append the id to a URL".
 */

describe('getServiceUrl', () => {
  it('builds the obvious ones per entity type', () => {
    expect(getServiceUrl('spotify', 'artist', '4tZ')).toBe('https://open.spotify.com/artist/4tZ');
    expect(getServiceUrl('spotify', 'album', '5gQ')).toBe('https://open.spotify.com/album/5gQ');
    expect(getServiceUrl('spotify', 'track', '1cQ')).toBe('https://open.spotify.com/track/1cQ');
  });

  it('knows MusicBrainz calls an album a release and a track a recording', () => {
    expect(getServiceUrl('musicbrainz', 'album', 'mbid')).toBe(
      'https://musicbrainz.org/release/mbid',
    );
    expect(getServiceUrl('musicbrainz', 'track', 'mbid')).toBe(
      'https://musicbrainz.org/recording/mbid',
    );
  });

  describe('Discogs album ids carry their own type', () => {
    // release N and master N are DIFFERENT albums sharing one numeric
    // namespace, so the id is stored tagged (core/discogs_client.py
    // _tag_discogs_album_id). Dropping the tag sends the user to a real page
    // about the wrong record — worse than no link.
    it('routes a master id to /master', () => {
      expect(getServiceUrl('discogs', 'album', 'm12345')).toBe(
        'https://www.discogs.com/master/12345',
      );
    });

    it('routes a release id to /release', () => {
      expect(getServiceUrl('discogs', 'album', 'r12345')).toBe(
        'https://www.discogs.com/release/12345',
      );
    });

    it('treats an untagged legacy id as a release', () => {
      // Matches _discogs_album_endpoints()'s own fallback for rows written
      // before the tagging existed.
      expect(getServiceUrl('discogs', 'album', '12345')).toBe(
        'https://www.discogs.com/release/12345',
      );
    });

    it('has no per-track page to link to', () => {
      expect(getServiceUrl('discogs', 'track', 'r1')).toBeNull();
    });
  });

  it('passes through the services that store a full url instead of an id', () => {
    // Last.fm, Genius and Bandcamp hand back a URL; prefixing a host would
    // produce https://…/https://…
    expect(getServiceUrl('lastfm', 'artist', 'https://last.fm/music/Aphex')).toBe(
      'https://last.fm/music/Aphex',
    );
    expect(getServiceUrl('bandcamp', 'album', 'https://x.bandcamp.com/album/y')).toBe(
      'https://x.bandcamp.com/album/y',
    );
  });

  it('refuses a stored value that is not a link at all', () => {
    // A bare id in a url-shaped column would otherwise render as an href the
    // browser resolves against SoulSync's own origin.
    expect(getServiceUrl('lastfm', 'artist', '12345')).toBeNull();
    expect(getServiceUrl('genius', 'artist', 'not a url')).toBeNull();
  });

  it('says null rather than guessing for a combination that has no page', () => {
    expect(getServiceUrl('amazon', 'artist', 'B01')).toBeNull();
    expect(getServiceUrl('genius', 'album', '99')).toBeNull();
    expect(getServiceUrl('nonesuch', 'artist', '1')).toBeNull();
  });

  it('has nothing to offer without an id', () => {
    expect(getServiceUrl('spotify', 'artist', null)).toBeNull();
    expect(getServiceUrl('spotify', 'artist', '')).toBeNull();
    expect(getServiceUrl('spotify', 'artist', undefined)).toBeNull();
  });

  it('accepts the catalogue spelling of a service as well as the short one', () => {
    // provider_ids keys them 'musicbrainz'/'itunes'; the match chips use the
    // same words, so one spelling is enough — but an id column named
    // `musicbrainz_id` must not silently produce no link.
    expect(getServiceUrl('MusicBrainz', 'artist', 'mbid')).toBe(
      'https://musicbrainz.org/artist/mbid',
    );
  });
});
