/**
 * The unified transform — fixture tests per payload dialect, with vanilla
 * anchors on every source-track field name. The vanilla's fourteen mappers are
 * inline in polling closures and cannot be lifted, so parity is pinned by
 * literals transcribed from the verbatim reads (file:line per describe), and
 * the three documented unifications are asserted AS divergences.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  countMatches,
  sourceTrackFields,
  spotifyArtistText,
  spotifyAlbumText,
  toDiscoveryRow,
  toDiscoveryRows,
} from './-sync.transform';

const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');

describe('source-track dialects (fields anchored to the vanilla)', () => {
  it('object-track sources read <source>_track {name, artists[]}', () => {
    expect(
      sourceTrackFields('tidal', { tidal_track: { name: 'HUMBLE.', artists: ['Kendrick Lamar'] } }),
    ).toEqual({ track: 'HUMBLE.', artist: 'Kendrick Lamar' });
    expect(sourceTrackFields('qobuz', { qobuz_track: { name: 'X', artists: ['A', 'B'] } })).toEqual(
      { track: 'X', artist: 'A, B' },
    );
    expect(sourceTrackFields('deezer', { deezer_track: { name: 'Y' } })).toEqual({
      track: 'Y',
      artist: 'Unknown', // artists missing → Unknown, exactly the vanilla ternary
    });
    expect(sourceTrackFields('spotify_public', {})).toEqual({
      track: 'Unknown',
      artist: 'Unknown',
    });
    expect(
      sourceTrackFields('itunes_link', { itunes_link_track: { name: 'Z', artists: [] } }),
    ).toEqual({ track: 'Z', artist: '' }); // [].join(', ') is '' in the vanilla too
  });

  it('beatport reads title/artist strings; LB reads lb_* with track_name fallbacks', () => {
    expect(
      sourceTrackFields('beatport', { beatport_track: { title: 'Track', artist: 'DJ' } }),
    ).toEqual({ track: 'Track', artist: 'DJ' });
    expect(sourceTrackFields('listenbrainz', { lb_track: 'L', lb_artist: 'M' })).toEqual({
      track: 'L',
      artist: 'M',
    });
    expect(
      sourceTrackFields('listenbrainz', { track_name: 'Fallback', artist_name: 'Artist' }),
    ).toEqual({ track: 'Fallback', artist: 'Artist' });
    expect(sourceTrackFields('youtube', { yt_track: 'V', yt_artist: 'W' })).toEqual({
      track: 'V',
      artist: 'W',
    });
    expect(sourceTrackFields('mirrored', {})).toEqual({ track: 'Unknown', artist: 'Unknown' });
  });

  it('anchors every dialect field name in the live vanilla', () => {
    expect(SYNC_SERVICES).toContain('r.tidal_track ? r.tidal_track.name');
    expect(SYNC_SERVICES).toContain('r.qobuz_track ? r.qobuz_track.name');
    expect(SYNC_SERVICES).toContain('r.deezer_track ? r.deezer_track.name');
    expect(SYNC_SERVICES).toContain('r.spotify_public_track ? r.spotify_public_track.name');
    expect(SYNC_SERVICES).toContain('r.itunes_link_track ? r.itunes_link_track.name');
    expect(SYNC_SERVICES).toContain('r.beatport_track ? r.beatport_track.title');
    expect(SYNC_SERVICES).toContain('r.lb_track || r.track_name');
  });
});

describe('spotify field extraction (the tidal socket mapper, 641-650)', () => {
  it('joins object artists, drops blanks, dashes when empty', () => {
    expect(
      spotifyArtistText({
        spotify_data: { artists: [{ name: 'A' }, { name: '' }, { name: 'B' }] },
      }),
    ).toBe('A, B');
    expect(spotifyArtistText({ spotify_data: { artists: ['A', 'B'] } })).toBe('A, B');
    expect(spotifyArtistText({ spotify_data: { artists: [] } })).toBe('-');
    expect(spotifyArtistText({ spotify_data: { artists: 'Solo' } })).toBe('Solo');
    expect(spotifyArtistText({ spotify_artist: 'Flat' })).toBe('Flat');
    expect(spotifyArtistText({})).toBe('-');
  });

  it('album name from object or string, dash fallback', () => {
    expect(spotifyAlbumText({ spotify_data: { album: { name: 'DAMN.' } } })).toBe('DAMN.');
    expect(spotifyAlbumText({ spotify_data: { album: '25' } })).toBe('25');
    expect(spotifyAlbumText({ spotify_data: {} })).toBe('-');
    expect(spotifyAlbumText({ spotify_album: 'Flat' })).toBe('Flat');
    expect(spotifyAlbumText({})).toBe('-');
  });
});

describe('the unified status ladder', () => {
  it('wing-it beats everything, error beats found (both unified across sources)', () => {
    const wing = toDiscoveryRow('tidal', 'lenient', { wing_it_fallback: true, status: 'found' }, 0);
    expect(wing.status).toBe('🎯 Wing It');
    expect(wing.status_class).toBe('wing-it');
    expect(wing.wing_it_fallback).toBe(true);

    const err = toDiscoveryRow('tidal', 'lenient', { status: 'error', spotify_track: 'X' }, 0);
    expect(err.status).toBe('❌ Error');
    expect(err.status_class).toBe('error');

    // UNIFICATION #1: beatport rows get wing-it too (vanilla's didn't).
    const bpWing = toDiscoveryRow('beatport', 'lenient', { status_class: 'wing-it' }, 0);
    expect(bpWing.status).toBe('🎯 Wing It');
  });

  it('found via the lenient union, Qobuz literal only for Qobuz', () => {
    expect(toDiscoveryRow('tidal', 'lenient', { spotify_data: { name: 'X' } }, 0).status).toBe(
      '✅ Found',
    );
    expect(toDiscoveryRow('tidal', 'lenient', { spotify_track: 'X' }, 0).status_class).toBe(
      'found',
    );
    expect(toDiscoveryRow('qobuz', 'qobuz', { status: 'Found' }, 0).status).toBe('✅ Found');
    expect(toDiscoveryRow('tidal', 'lenient', { status: 'Found' }, 0).status).toBe('❌ Not Found');
    expect(toDiscoveryRow('tidal', 'lenient', {}, 0).status).toBe('❌ Not Found');
  });

  it('keeps LB authoritative indexes and gives LB rows a duration', () => {
    const lb = toDiscoveryRow('listenbrainz', 'lenient', { index: 7, lb_track: 'T' }, 3);
    expect(lb.index).toBe(7);
    expect(lb.duration).toBe('0:00');
    const positional = toDiscoveryRow('tidal', 'lenient', {}, 3);
    expect(positional.index).toBe(3);
    expect(positional.duration).toBeUndefined();
  });
});

describe('toDiscoveryRows + countMatches', () => {
  it('maps a whole payload and counts found rows the actualMatches way', () => {
    const rows = toDiscoveryRows('tidal', 'lenient', [
      { tidal_track: { name: 'A', artists: ['X'] }, spotify_data: { name: 'A2', artists: ['X'] } },
      { tidal_track: { name: 'B', artists: ['Y'] }, status: 'not_found' },
      { wing_it_fallback: true, tidal_track: { name: 'C', artists: ['Z'] } },
    ]);
    expect(rows.map((r) => r.status_class)).toEqual(['found', 'not-found', 'wing-it']);
    expect(rows[0].spotify_track).toBe('A2');
    expect(rows[1].spotify_track).toBe('-');
    // Wing-it rows do NOT count as matches (they need a Fix).
    expect(countMatches(rows)).toBe(1);
    expect(toDiscoveryRows('tidal', 'lenient', null)).toEqual([]);
  });
});
