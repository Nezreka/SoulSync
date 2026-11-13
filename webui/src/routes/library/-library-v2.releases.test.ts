import { describe, expect, it } from 'vitest';

import type { LibraryV2AlbumSummary } from './-library-v2.types';

import {
  copiesNote,
  releaseLeads,
  standsForItsRelease,
  withoutShadowEntries,
} from './-library-v2.releases';

function row(id: number, extra: Partial<LibraryV2AlbumSummary> = {}): LibraryV2AlbumSummary {
  return {
    id,
    title: 'bbno$',
    album_type: 'album',
    release_date: '2025-10-17',
    year: 2025,
    image_url: null,
    monitored: false,
    quality_profile_id: 1,
    origin: 'library',
    spotify_id: null,
    explicit: null,
    label: null,
    style: null,
    mood: null,
    track_count: 21,
    tracks_present: 0,
    tracks_missing: 0,
    total_size_bytes: 0,
    user_overrides: {},
    ...extra,
  };
}

// the live page: a stray track filed as its own album, the real album, and
// the provider's entry for it, all one release
const stray = row(9430, {
  track_count: 22,
  tracks_present: 1,
  release_lead_id: 68930,
  release_copies: 2,
});
const full = row(68930, { tracks_present: 21, release_lead_id: 68930, release_copies: 2 });
const entry = row(72802, {
  origin: 'discography',
  monitored: true,
  release_lead_id: 68930,
  release_copies: 2,
});
const other = row(5, {
  title: 'eat ya veggies',
  tracks_present: 12,
  release_lead_id: 5,
  release_copies: 1,
});

describe('one release, one card', () => {
  it('a discography lists the release once, as its best copy', () => {
    expect(releaseLeads([stray, full, entry, other]).map((e) => e.id)).toEqual([68930, 5]);
  });

  it('what is on disk keeps every owned copy and drops only the entry behind them', () => {
    expect(withoutShadowEntries([stray, full, entry, other]).map((e) => e.id)).toEqual([
      9430, 68930, 5,
    ]);
  });

  it('a provider entry for a release you do not have stands for itself', () => {
    const missing = row(7, { origin: 'discography', release_lead_id: 7, release_copies: 0 });
    expect(releaseLeads([missing])).toEqual([missing]);
    expect(withoutShadowEntries([missing])).toEqual([missing]);
  });

  it('a row from a server that sends no lead stands for itself', () => {
    expect(standsForItsRelease(row(1))).toBe(true);
    expect(releaseLeads([row(1), row(2)])).toHaveLength(2);
  });

  it('says how many copies only when there is more than one', () => {
    expect(copiesNote(full)).toBe('2 copies');
    expect(copiesNote(other)).toBe('');
    expect(copiesNote(row(1))).toBe('');
  });
});
