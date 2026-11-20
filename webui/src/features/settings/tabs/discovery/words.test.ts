/**
 * The Discovery tab's hero sentences (settings-bic phase 5), from sample
 * settings: what each pill reads back.
 */

import { describe, expect, it } from 'vitest';

import {
  adventureWord,
  type HeroPart,
  importListsSentence,
  notificationsSentence,
  playlistsSentence,
  recommendationsSentence,
} from './words';

const read = (parts: HeroPart[]) => parts.map((p) => p.text).join('');
const warned = (parts: HeroPart[]) => parts.filter((p) => p.kind === 'warn').map((p) => p.text);
const named = (parts: HeroPart[]) => parts.filter((p) => p.kind === 'name').map((p) => p.text);

describe('the Discovery heroes', () => {
  it('reads the dial on its own 0 to 1 scale (the old summary always said "plays it safe")', () => {
    expect(adventureWord(0)).toBe('safe');
    expect(adventureWord(0.25)).toBe('safe');
    expect(adventureWord(0.3)).toBe('balanced');
    expect(adventureWord(0.6)).toBe('balanced');
    expect(adventureWord(0.65)).toBe('adventurous');
    expect(adventureWord(1)).toBe('adventurous');
  });

  it('Recommendations: the dial, play history, and on the music side the pool', () => {
    const base = { adventurousness: 0.1, statsEnabled: false, statsInterval: 30 };
    expect(read(recommendationsSentence(base))).toBe(
      'Recommendations play it safe, close to what you already know. Play history is not collected.',
    );
    const full = recommendationsSentence({
      adventurousness: 0.3,
      statsEnabled: true,
      statsInterval: 1,
      lookback: 'all',
      hemisphere: 'northern',
    });
    expect(read(full)).toBe(
      'Recommendations are balanced between what you know and new finds. Play history is read from your media server every 1 minute. New artists are scanned back over their whole discography, and seasonal playlists follow the northern hemisphere.',
    );
    expect(named(full)).toEqual([
      'balanced',
      '1 minute',
      'their whole discography',
      'northern hemisphere',
    ]);
    // a lookback with no hemisphere yet still ends its sentence
    expect(read(recommendationsSentence({ ...base, lookback: '180' }))).toMatch(
      /back over the last 6 months\.$/,
    );
  });

  it('Playlists: what a sync does, the backup, and which M3U files are written', () => {
    const base = {
      syncMode: 'reconcile',
      wishlistMissing: true,
      createBackup: true,
      m3uExport: false,
      libraryM3u: false,
    };
    // the backup only counts for Replace
    expect(read(playlistsSentence(base))).toBe(
      'A playlist sync edits the playlist on your server in place and adds missing tracks to the wishlist. No M3U files are written.',
    );
    expect(read(playlistsSentence({ ...base, syncMode: 'replace' }))).toContain(
      'A backup copy is kept on the server first.',
    );
    expect(
      read(playlistsSentence({ ...base, syncMode: 'append', m3uExport: true, libraryM3u: true })),
    ).toBe(
      'A playlist sync only adds new tracks to the playlist on your server and adds missing tracks to the wishlist. An M3U file is saved with each playlist download, and a whole-library M3U is kept up to date.',
    );
    expect(read(playlistsSentence({ ...base, libraryM3u: true }))).toMatch(
      /A whole-library M3U is kept up to date\.$/,
    );
  });

  it('Import Lists: what is watched, what is off, and nothing yet', () => {
    expect(read(importListsSentence(null))).toBe('Loading your import lists.');
    expect(read(importListsSentence([]))).toMatch(/^No import lists yet\./);
    const two = importListsSentence([
      { name: 'Trending', enabled: true },
      { name: 'Mine', enabled: false },
      { name: 'Top shows', enabled: true },
    ]);
    expect(read(two)).toBe(
      'SoulSync watches Trending and Top shows. Anything new on them is wishlisted if it is a movie and followed if it is a show. 1 more list is switched off.',
    );
    expect(named(two)).toEqual(['Trending', 'Top shows']);
    const off = importListsSentence([{ name: 'Mine', enabled: false }]);
    expect(warned(off)).toEqual(['Every list is switched off']);
    expect(read(off)).toBe('Every list is switched off, so nothing is added from it.');
  });

  it('Notifications: who is told, a connection that hears nothing, the ones off', () => {
    expect(read(notificationsSentence(null))).toBe('Loading your notification connections.');
    expect(read(notificationsSentence([]))).toContain('No connections yet.');
    expect(warned(notificationsSentence([{ name: 'D', enabled: false, events: 2 }]))).toEqual([
      'Every connection is switched off',
    ]);
    const some = notificationsSentence([
      { name: 'Discord', enabled: true, events: 2 },
      { name: 'TG', enabled: true, events: 0 },
      { name: 'Old', enabled: false, events: 1 },
    ]);
    expect(read(some)).toBe(
      'SoulSync tells Discord and TG when things happen. 1 connection hears no events, so nothing goes there yet. 1 more is switched off.',
    );
    expect(warned(some)).toEqual(['1 connection hears no events']);
  });
});
