import type { LibraryV2Track, LibraryV2TrackFile } from './-library-v2.types';

/** A present file with nothing measured yet. */
export function lib2TrackFile(overrides: Partial<LibraryV2TrackFile> = {}): LibraryV2TrackFile {
  return {
    file_id: 1,
    path: '/music/track.flac',
    size: null,
    bitrate: null,
    sample_rate: null,
    bit_depth: null,
    format: null,
    quality_tier: 'unknown',
    verification_status: null,
    import_status: null,
    source: null,
    file_state: null,
    has_replaygain: false,
    has_lyrics: false,
    ...overrides,
  };
}

/** A monitored track on disc 1 with that file. */
export function lib2Track(overrides: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return {
    id: 7,
    title: 'Track',
    track_number: 1,
    disc_number: 1,
    duration: null,
    bpm: null,
    explicit: null,
    style: null,
    mood: null,
    isrc: null,
    monitored: true,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [],
    file: lib2TrackFile(),
    file_status: 'present',
    metadata_gaps: [],
    meets_profile: null,
    upgrade_candidate: null,
    ...overrides,
  };
}
