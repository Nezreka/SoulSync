import { describe, expect, it } from 'vitest';

import type { ArtistAbout } from '../artist-detail/-artist-detail.types';

import { catalogueAbout } from './-library-v2.about';

const ABOUT: ArtistAbout = {
  bio: 'Massive Attack are a trip hop collective.',
  bio_source: 'wikipedia',
  bio_url: 'https://en.wikipedia.org/wiki/Massive_Attack',
  tags: ['trip hop'],
  tags_source: 'musicbrainz',
  listeners: 5,
  plays: 50,
  popularity_source: 'listenbrainz',
  fans: 7,
  fans_source: 'deezer',
};

const NO_LIVE = { listeners: null, playcount: null, followers: null, bio: null };

describe('catalogueAbout', () => {
  it('the media server summary and live last.fm come first, with no note', () => {
    expect(catalogueAbout({ summary: 'Plex bio', about: ABOUT }, NO_LIVE)).toMatchObject({
      bio: 'Plex bio',
      bioSource: null,
      bioUrl: null,
    });
    const live = { ...NO_LIVE, bio: 'Last.fm bio', listeners: 10, playcount: 99, followers: 3 };
    expect(catalogueAbout({ summary: null, about: ABOUT }, live)).toEqual({
      bio: 'Last.fm bio',
      bioSource: null,
      bioUrl: null,
      listeners: 10,
      playcount: 99,
      numbersSource: null,
      followers: 3,
      fans: null,
    });
  });

  it('without last.fm, the stored free sources fill in and say which', () => {
    expect(catalogueAbout({ summary: null, about: ABOUT }, NO_LIVE)).toEqual({
      bio: ABOUT.bio,
      bioSource: 'wikipedia',
      bioUrl: ABOUT.bio_url,
      listeners: 5,
      playcount: 50,
      numbersSource: 'listenbrainz',
      followers: null,
      fans: 7,
    });
  });

  it('a stored last.fm bio is shown without a note', () => {
    const about = { ...ABOUT, bio_source: 'lastfm' as const };
    expect(catalogueAbout({ summary: null, about }, undefined)).toMatchObject({
      bio: ABOUT.bio,
      bioSource: null,
    });
  });

  it('no about at all keeps the old behaviour', () => {
    expect(catalogueAbout({ summary: null }, undefined)).toEqual({
      bio: null,
      bioSource: null,
      bioUrl: null,
      listeners: null,
      playcount: null,
      numbersSource: null,
      followers: null,
      fans: null,
    });
  });
});
