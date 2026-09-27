import { describe, expect, it } from 'vitest';

import {
  INBOX_EMPTY,
  INBOX_PREVIEW,
  inboxArtistRef,
  inboxKindLabel,
  inboxReleaseAlbum,
  inboxWhen,
  isRelease,
  unansweredLine,
} from './-discover.inbox';

const TODAY = new Date(2026, 8, 26, 15, 0);
const item = (over: Record<string, unknown>) => ({
  id: 1,
  kind: 'new_release',
  title: 'X',
  ...over,
});

describe('inboxWhen', () => {
  it('says how long ago a release came out', () => {
    expect(inboxWhen(item({ item_date: '2026-09-26' }), TODAY)).toBe('Out today');
    expect(inboxWhen(item({ item_date: '2026-09-25' }), TODAY)).toBe('Out yesterday');
    expect(inboxWhen(item({ item_date: '2026-09-20' }), TODAY)).toBe('Out 6 days ago');
    expect(inboxWhen(item({ item_date: '' }), TODAY)).toBe('New release');
  });

  it('counts down to what is coming, then gives the date', () => {
    const up = (d: string) => inboxWhen(item({ kind: 'upcoming', item_date: d }), TODAY);
    expect(up('2026-09-27')).toBe('Out tomorrow');
    expect(up('2026-10-01')).toBe('Out in 5 days');
    expect(up('2026-11-20')).toMatch(/^Out \D+ ?20|^Out 20/);
  });

  it('dates a concert and explains a saved rec', () => {
    expect(inboxWhen(item({ kind: 'concert', item_date: '2026-10-26' }), TODAY)).toMatch(/^Live /);
    expect(
      inboxWhen(
        item({
          kind: 'saved_rec',
          payload: { explanation: { kind: 'listened', seeds: [{ name: 'Tool' }] } },
        }),
        TODAY,
      ),
    ).toBe('Because you listen to Tool');
    expect(inboxWhen(item({ kind: 'saved_rec' }), TODAY)).toBe('Saved');
  });
});

describe('the rest', () => {
  it('says which source did not answer, never blanking the inbox', () => {
    expect(unansweredLine([])).toBe('');
    expect(unansweredLine(['concerts'])).toBe("Ticketmaster didn't answer, showing the rest");
    expect(unansweredLine(['releases', 'concerts'])).toBe(
      "Your release scan and Ticketmaster didn't answer, showing the rest",
    );
  });

  it('turns a release into the album opener shape', () => {
    expect(
      inboxReleaseAlbum(
        item({
          title: 'Memorial',
          artist_name: 'Soen',
          image_url: 'c.jpg',
          payload: { source: 'deezer', ids: { deezer: 'dz1' } },
        }),
      ),
    ).toEqual({
      album_name: 'Memorial',
      artist_name: 'Soen',
      album_cover_url: 'c.jpg',
      source: 'deezer',
      album_spotify_id: undefined,
      album_deezer_id: 'dz1',
      album_itunes_id: undefined,
    });
    expect(isRelease(item({ kind: 'upcoming' }))).toBe(true);
    expect(isRelease(item({ kind: 'concert' }))).toBe(false);
  });

  it('finds an artist page only for a saved artist with an id', () => {
    const saved = (payload: Record<string, unknown>) => item({ kind: 'saved_rec', payload });
    expect(inboxArtistRef(saved({ entity_type: 'artist', ids: { deezer: 'dz' } }))).toEqual({
      id: 'dz',
      source: 'deezer',
    });
    expect(inboxArtistRef(saved({ entity_type: 'artist', ids: {} }))).toBeNull();
    expect(inboxArtistRef(saved({ entity_type: 'track', ids: { deezer: 'dz' } }))).toBeNull();
  });

  it('labels and caps', () => {
    expect(inboxKindLabel('upcoming')).toBe('Coming soon');
    expect(inboxKindLabel('nope')).toBe('');
    expect(INBOX_PREVIEW).toBe(6);
    expect(INBOX_EMPTY.saved).toBe('Nothing saved. Use ⋯ → Save for later on any recommendation.');
  });
});
