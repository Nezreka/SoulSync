/**
 * Modal pure core — literal pins transcribed from the modal builder
 * (9302-9550) and text generators (9930-9959), plus vanilla anchors for the
 * two-format download-track builder (startYouTubeDownloadMissing 10727-10753,
 * DOM-bound and unliftable).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  buildDownloadTracks,
  descriptionSourceWord,
  initialProgressText,
  isLastfmRadioHash,
  metadataSourceLabel,
  modalDescription,
  modalSourceLabel,
  modalTitle,
  progressLineText,
  seededProgress,
} from './-sync.modal-core';
import { SYNC_SOURCES } from './-sync.sources';
import { freshSourceState } from './-sync.state';

const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { getActiveMetadataSource?: unknown }).getActiveMetadataSource;
  delete (window as { getMetadataSourceLabel?: unknown }).getMetadataSourceLabel;
});

describe('metadataSourceLabel (bug #5 knowing fix)', () => {
  it('asks the live seam and falls back to Spotify without it', () => {
    expect(metadataSourceLabel()).toBe('Spotify');
    window.getActiveMetadataSource = () => 'itunes';
    window.getMetadataSourceLabel = (s: string) => (s === 'itunes' ? 'iTunes' : 'Other');
    expect(metadataSourceLabel()).toBe('iTunes');
    window.getActiveMetadataSource = () => {
      throw new Error('boom');
    };
    expect(metadataSourceLabel()).toBe('Spotify');
  });
});

describe('title/label/description ladders (9356-9379, 9930-9944)', () => {
  it('titles per source + the lastfm hash special', () => {
    expect(modalTitle('tidal', 'tidal_1')).toBe('🎵 Tidal Playlist Discovery');
    expect(modalTitle('beatport', 'h')).toBe('🎵 Beatport Chart Discovery');
    expect(modalTitle('mirrored', 'mirrored_5')).toBe('🎵 Mirrored Playlist Discovery');
    expect(modalTitle('listenbrainz', 'lastfm_radio_abc')).toBe('📻 Last.fm Radio Discovery');
    expect(modalTitle('youtube', 'h4sh')).toBe('🎵 YouTube Playlist Discovery');
    expect(isLastfmRadioHash('lastfm_radio_x')).toBe(true);
    expect(isLastfmRadioHash('mbid')).toBe(false);
  });

  it("source labels keep the vanilla's abbreviations and mirrored capitalisation", () => {
    expect(modalSourceLabel('listenbrainz', 'mbid')).toBe('LB');
    expect(modalSourceLabel('youtube', 'h')).toBe('YT');
    expect(modalSourceLabel('itunes_link', 'h')).toBe('iTunes');
    expect(modalSourceLabel('listenbrainz', 'lastfm_radio_x')).toBe('Last.fm');
    expect(modalSourceLabel('mirrored', 'mirrored_5', 'tidal')).toBe('Tidal');
    expect(modalSourceLabel('mirrored', 'mirrored_5')).toBe('Source');
  });

  it("descriptions: the source word ('mirrored' stays lowercase) + phase arms", () => {
    expect(descriptionSourceWord('mirrored', 'mirrored_5')).toBe('mirrored');
    expect(descriptionSourceWord('listenbrainz', 'lastfm_radio_x')).toBe('Last.fm Radio');
    expect(modalDescription('fresh', 'Tidal', 'Spotify')).toBe(
      'Ready to discover clean Spotify metadata for Tidal tracks...',
    );
    expect(modalDescription('discovering', 'Deezer', 'iTunes')).toBe(
      'Discovering clean iTunes metadata for Deezer tracks...',
    );
    expect(modalDescription('discovered', 'Tidal', 'Spotify')).toBe(
      'Discovery complete! View the results below.',
    );
    expect(modalDescription('syncing', 'Tidal', 'Spotify')).toBe(
      'Discovering clean Spotify metadata for Tidal tracks...', // the default arm
    );
  });

  it('initial progress text arms + the live progress line', () => {
    expect(initialProgressText('fresh')).toBe('Click Start Discovery to begin...');
    expect(initialProgressText('discovering')).toBe('Starting discovery...');
    expect(initialProgressText('download_complete')).toBe('Discovery completed!');
    expect(progressLineText(3, 10, 30)).toBe('3 / 10 tracks matched (30%)');
  });
});

describe('seededProgress (the modal-open seeding, 9512-9527)', () => {
  const base = freshSourceState(SYNC_SOURCES.tidal, '1');

  it('prefers stored progress, else computes from rows/track count', () => {
    expect(seededProgress({ ...base, discoveryProgress: 40, spotifyMatches: 2 })).toEqual({
      progress: 40,
      matches: 2,
    });
    const computed = seededProgress({
      ...base,
      playlist: { tracks: [{}, {}, {}, {}] },
      rows: [{ status_class: 'found' } as never, { status_class: 'not-found' } as never],
    });
    expect(computed.progress).toBe(50);
    expect(computed.matches).toBe(1); // falls back to counting found rows
  });
});

describe('buildDownloadTracks (the two-format builder, 10727-10753)', () => {
  it('passes spotify_data verbatim and reconstructs flat rows with an album OBJECT', () => {
    const verbatim = { id: 'sp1', name: 'A', album: { name: 'Alb' } };
    const tracks = buildDownloadTracks([
      { spotify_data: verbatim },
      {
        spotify_track: 'B',
        spotify_artist: 'Artist',
        spotify_album: 'Album B',
        spotify_id: 'sp2',
        status_class: 'found',
      },
      { spotify_track: 'skipped — not found', status_class: 'not-found' },
      { status_class: 'found' }, // no data at all → filtered out
    ]);
    expect(tracks).toHaveLength(2);
    expect(tracks[0]).toBe(verbatim);
    expect(tracks[1]).toEqual({
      id: 'sp2',
      name: 'B',
      artists: ['Artist'],
      album: { name: 'Album B', album_type: 'album', images: [] },
      duration_ms: 0,
    });
  });

  it('album objects pass through; missing fields fall to the Unknown defaults', () => {
    const albumObj = { name: 'Real', images: [{ url: 'x' }] };
    const [row] = buildDownloadTracks([
      { spotify_track: 'T', status_class: 'found', spotify_album: albumObj } as never,
    ]);
    expect((row as { album: unknown }).album).toBe(albumObj);
    const [bare] = buildDownloadTracks([{ spotify_track: 'T', status_class: 'found' }]);
    expect(bare).toEqual({
      id: 'unknown',
      name: 'T',
      artists: ['Unknown Artist'],
      album: { name: 'Unknown Album', album_type: 'album', images: [] },
      duration_ms: 0,
    });
  });

  it('anchors the vanilla filter so a silent edit there fails here', () => {
    expect(SYNC_SERVICES).toContain(
      ".filter(result => result.spotify_data || (result.spotify_track && result.status_class === 'found'))",
    );
  });
});
