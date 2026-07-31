import { describe, expect, it } from 'vitest';

import {
  LB_CACHE_GLOBALS,
  clearLbCacheInPlace,
  lbCacheHit,
  lbPlaylistTracksUrl,
  normalizeLbTrack,
  publishLbCaches,
} from './-discover.listenbrainz-cache';

describe('the published globals', () => {
  it('names all three the other files reference', () => {
    expect([...LB_CACHE_GLOBALS]).toEqual([
      'listenbrainzPlaylistsCache',
      'listenbrainzTracksCache',
      'listenbrainzPlaylistsLoaded',
    ]);
  });

  it('creates both caches and the loaded flag', () => {
    const target: Record<string, unknown> = {};
    publishLbCaches(target);
    expect(target.listenbrainzPlaylistsCache).toEqual({});
    expect(target.listenbrainzTracksCache).toEqual({});
    expect(target.listenbrainzPlaylistsLoaded).toBe(false);
  });

  it('REUSES an existing cache rather than replacing it', () => {
    // init.js clears through its own reference; replacing the object orphans
    // that reference and its clear silently stops working.
    const existing = { recommendations: [{}] };
    const target: Record<string, unknown> = { listenbrainzTracksCache: existing };
    publishLbCaches(target);
    expect(target.listenbrainzTracksCache).toBe(existing);
    expect(target.listenbrainzTracksCache).toEqual({ recommendations: [{}] });
  });

  it('is idempotent — a second publish keeps the same objects', () => {
    const target: Record<string, unknown> = {};
    const first = publishLbCaches(target);
    const second = publishLbCaches(target);
    expect(second.listenbrainzPlaylistsCache).toBe(first.listenbrainzPlaylistsCache);
    expect(second.listenbrainzTracksCache).toBe(first.listenbrainzTracksCache);
  });

  it('does not clobber a loaded flag that is already true', () => {
    const target: Record<string, unknown> = { listenbrainzPlaylistsLoaded: true };
    publishLbCaches(target);
    expect(target.listenbrainzPlaylistsLoaded).toBe(true);
  });

  it('gives sync-listenbrainz.js a defined cache so it does NOT fork its own', () => {
    // `if (typeof listenbrainzTracksCache === 'undefined') window.… = {}` —
    // if it wins that race the Sync tab and Discover stop sharing entirely,
    // silently, and both re-fetch.
    const target: Record<string, unknown> = {};
    publishLbCaches(target);
    expect(typeof target.listenbrainzTracksCache).not.toBe('undefined');
  });
});

describe('clearing', () => {
  it('empties IN PLACE, preserving identity', () => {
    // `= {}` would look equivalent and would break init.js's reference.
    const cache: Record<string, unknown> = { a: [1], b: [2] };
    clearLbCacheInPlace(cache);
    expect(cache).toEqual({});
  });

  it('leaves the same object for the other file to keep using', () => {
    const cache: Record<string, unknown> = { a: [1] };
    const ref = cache;
    clearLbCacheInPlace(cache);
    ref.b = [2];
    expect(cache.b).toEqual([2]);
  });

  it('copes with an already-empty cache', () => {
    const cache: Record<string, unknown> = {};
    expect(() => clearLbCacheInPlace(cache)).not.toThrow();
  });
});

describe('cache hits', () => {
  it('reuses only a NON-EMPTY entry', () => {
    // An empty array means a previous fetch found nothing; treating it as a hit
    // would cache the emptiness forever.
    expect(lbCacheHit({ m1: [{}] }, 'm1')).toBe(true);
    expect(lbCacheHit({ m1: [] }, 'm1')).toBe(false);
    expect(lbCacheHit({}, 'm1')).toBe(false);
  });

  it('ignores a non-array value', () => {
    expect(lbCacheHit({ m1: 'nope' as unknown as unknown[] }, 'm1')).toBe(false);
  });

  it('encodes the mbid into the url', () => {
    expect(lbPlaylistTracksUrl('a b/c')).toBe('/api/discover/listenbrainz/playlist/a%20b%2Fc');
  });
});

describe('the normalised track shape', () => {
  it('renames recording_mbid to mbid', () => {
    expect(normalizeLbTrack({ recording_mbid: 'rec1' }).mbid).toBe('rec1');
  });

  it('falls back to an existing mbid', () => {
    expect(normalizeLbTrack({ mbid: 'm1' }).mbid).toBe('m1');
  });

  it('prefers recording_mbid when both are present', () => {
    expect(normalizeLbTrack({ recording_mbid: 'rec1', mbid: 'm1' }).mbid).toBe('rec1');
  });

  it('defaults every field rather than leaving undefined', () => {
    expect(normalizeLbTrack({})).toEqual({
      track_name: '',
      artist_name: '',
      album_name: '',
      duration_ms: 0,
      mbid: '',
      release_mbid: '',
      album_cover_url: '',
    });
  });
});
