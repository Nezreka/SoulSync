import { describe, expect, it } from 'vitest';

import type { LibraryV2ArtistPlaybackFile } from './-library-v2.api';
import { albumQueueRows, artistQueueRows } from './-library-v2.play';
import type { LibraryV2AlbumDetail, LibraryV2Track } from './-library-v2.types';

/**
 * Why Library v2 does NOT need the ownership round-trip upstream added.
 *
 * Upstream's release card lists a PROVIDER tracklist — rows from Spotify or
 * iTunes that never carry a file_path — so its play button first asks
 * /api/library/check-tracks which of them the library owns, or the player
 * reads every row as "download this first" (#1213). Library v2's album and
 * artist views read lib2_track_files directly: the path is already on the row
 * it just rendered. Asking the server what it already sent would be a second
 * source of truth, and one that can disagree.
 *
 * What still has to be true is the part the player cares about: only rows with
 * a real file, carrying is_library so the download flow is skipped.
 */

function track(over: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return {
    id: 1,
    title: 'Xtal',
    track_number: 1,
    disc_number: 1,
    duration: 292000,
    bpm: null,
    explicit: null,
    style: null,
    mood: null,
    isrc: null,
    monitored: true,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [{ id: 7, name: 'Aphex Twin', role: 'primary' }],
    file: {
      file_id: 11,
      path: '/music/Aphex Twin/SAW/01 Xtal.flac',
      format: 'FLAC',
      bitrate: 900,
      sample_rate: 44100,
      bit_depth: 16,
      size: 30000000,
      quality_tier: 'lossless',
      import_status: null,
      verification_status: null,
      source: null,
      file_state: 'active',
    },
    ...over,
  } as LibraryV2Track;
}

function album(over: Partial<LibraryV2AlbumDetail> = {}): LibraryV2AlbumDetail {
  return {
    id: 5,
    title: 'Selected Ambient Works',
    album_type: 'album',
    release_date: null,
    year: 1992,
    image_url: '/art/saw.jpg',
    genres: [],
    explicit: null,
    label: null,
    style: null,
    mood: null,
    monitored: true,
    origin: 'library',
    quality_profile: null,
    primary_artist: { id: 7, name: 'Aphex Twin' },
    tracks: [track()],
    track_count: 1,
    tracks_present: 1,
    tracks_missing: 0,
    total_size_bytes: 30000000,
    user_overrides: {},
    ...over,
  } as LibraryV2AlbumDetail;
}

describe('albumQueueRows', () => {
  it('hands the player a row it can play without a download', () => {
    const [row] = albumQueueRows(album(), 'Aphex Twin');
    expect(row).toMatchObject({
      title: 'Xtal',
      name: 'Xtal',
      artist: 'Aphex Twin',
      album: 'Selected Ambient Works',
      file_path: '/music/Aphex Twin/SAW/01 Xtal.flac',
      is_library: true,
    });
  });

  it('carries the typed Library v2 ids the player and its endpoints need', () => {
    // iss29-B08 / the shell contract: `id` is what the media server understands
    // (server or legacy), lib2_track_id addresses the v2 row, and lib2_artist_id
    // is what "Go to artist" routes on for a V2-native artist with no legacy id.
    const [row] = albumQueueRows(
      album({
        tracks: [track({ id: 42, legacy_track_id: 'L9', server_track_id: 'S3' })],
      }),
      'Aphex Twin',
    );
    expect(row).toMatchObject({
      id: 'S3',
      lib2_track_id: 42,
      legacy_track_id: 'L9',
      server_track_id: 'S3',
      lib2_artist_id: 7,
    });
  });

  it('leaves out a track with no file rather than queueing a dead row', () => {
    // A "missing" placeholder is an expected track we do not have. Queueing it
    // is what made upstream's button fail on an album owned in part.
    const rows = albumQueueRows(
      album({
        track_count: 2,
        tracks_present: 1,
        tracks_missing: 1,
        tracks: [track(), track({ id: 2, title: 'Tha', file: null })],
      }),
      'Aphex Twin',
    );
    expect(rows.map((r) => r.title)).toEqual(['Xtal']);
  });

  it('skips a file that is on the row but not active', () => {
    const rows = albumQueueRows(
      album({
        tracks: [track({ file: { ...track().file!, file_state: 'deleted' } })],
      }),
      'Aphex Twin',
    );
    expect(rows).toEqual([]);
  });

  it('plays in disc then track order, whatever order the payload arrived in', () => {
    const rows = albumQueueRows(
      album({
        tracks: [
          track({ id: 3, title: 'D2T1', disc_number: 2, track_number: 1 }),
          track({ id: 2, title: 'D1T2', disc_number: 1, track_number: 2 }),
          track({ id: 1, title: 'D1T1', disc_number: 1, track_number: 1 }),
        ],
      }),
      'Aphex Twin',
    );
    expect(rows.map((r) => r.title)).toEqual(['D1T1', 'D1T2', 'D2T1']);
  });

  it('prefers the track credit over the page it was opened from', () => {
    // A compilation row is filed under the album artist but performed by
    // someone else; the queue should say who is actually playing.
    const [row] = albumQueueRows(
      album({
        tracks: [track({ artists: [{ id: 9, name: 'Polygon Window', role: 'primary' }] })],
      }),
      'Various Artists',
    );
    expect(row.artist).toBe('Polygon Window');
    expect(row.lib2_artist_id).toBe(9);
  });

  it('falls back to the page artist when the track carries no credit', () => {
    const [row] = albumQueueRows(album({ tracks: [track({ artists: [] })] }), 'Aphex Twin');
    expect(row.artist).toBe('Aphex Twin');
    expect(row.lib2_artist_id).toBeNull();
  });

  it('gives every row the album cover so the queue is not a list of blanks', () => {
    const [row] = albumQueueRows(album(), 'Aphex Twin');
    expect(row.image_url).toBe('/art/saw.jpg');
  });
});

describe('artistQueueRows', () => {
  const file = (
    over: Partial<LibraryV2ArtistPlaybackFile> = {},
  ): LibraryV2ArtistPlaybackFile => ({
    file_id: 1,
    track_id: 100,
    track_title: 'Xtal',
    track_number: 1,
    disc_number: 1,
    duration: 292000,
    album_id: 5,
    album_title: 'Selected Ambient Works',
    album_image_url: '/art/saw.jpg',
    artist_id: 7,
    artist_name: 'Aphex Twin',
    path: '/music/x.flac',
    format: 'FLAC',
    bitrate: 900,
    file_state: 'active',
    is_primary: true,
    ...over,
  });

  it('queues one row per track, ordered by album then disc then track', () => {
    const rows = artistQueueRows(
      [
        file({ file_id: 3, track_id: 3, album_id: 9, album_title: 'B', track_number: 1 }),
        file({ file_id: 2, track_id: 2, album_id: 5, track_number: 2, track_title: 'Tha' }),
        file({ file_id: 1, track_id: 1, album_id: 5, track_number: 1 }),
      ],
      'Aphex Twin',
    );
    expect(rows.map((r) => r.lib2_track_id)).toEqual([1, 2, 3]);
  });

  it('keeps only the primary copy so a retained MP3 is not queued twice', () => {
    // The Files tab lists EVERY physical copy: a lossless master and its
    // intentional lossy companion are two rows for one recording. Queueing
    // both plays the same song twice in a row.
    const rows = artistQueueRows(
      [
        file({ file_id: 1, track_id: 1, is_primary: true, format: 'FLAC' }),
        file({ file_id: 2, track_id: 1, is_primary: false, format: 'MP3' }),
      ],
      'Aphex Twin',
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].format).toBe('FLAC');
  });

  it('still queues a track whose only copy is not flagged primary', () => {
    // primary_manual can be unset on an older row; dropping it would silently
    // shorten the queue rather than play what is there.
    const rows = artistQueueRows([file({ is_primary: false })], 'Aphex Twin');
    expect(rows).toHaveLength(1);
  });

  it('leaves out a file the catalogue no longer counts as present', () => {
    const rows = artistQueueRows([file({ file_state: 'deleted' })], 'Aphex Twin');
    expect(rows).toEqual([]);
  });

  it("names each track's own credit, not the page's artist", () => {
    // A compilation this artist appears on: labelling every row with the page
    // artist claims they performed songs they did not.
    const [own, guest] = artistQueueRows(
      [
        file({ track_id: 1 }),
        file({
          track_id: 2,
          album_id: 9,
          album_title: 'Various: Artificial Intelligence',
          artist_id: 12,
          artist_name: 'Autechre',
        }),
      ],
      'Aphex Twin',
    );
    expect(own.artist).toBe('Aphex Twin');
    expect(own.lib2_artist_id).toBe(7);
    expect(guest.artist).toBe('Autechre');
    // "Go to artist" from the queue must reach whoever actually made it.
    expect(guest.lib2_artist_id).toBe(12);
  });

  it('falls back to the page artist when a row carries no credit', () => {
    const [row] = artistQueueRows(
      [file({ artist_id: null, artist_name: null })],
      'Aphex Twin',
    );
    expect(row.artist).toBe('Aphex Twin');
    expect(row.lib2_artist_id).toBeNull();
  });

  it('carries the release artwork into the queue', () => {
    const [row] = artistQueueRows([file()], 'Aphex Twin');
    expect(row.image_url).toBe('/art/saw.jpg');
  });
});
