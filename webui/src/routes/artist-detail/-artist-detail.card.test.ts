import { describe, expect, it } from 'vitest';

import {
  completionOverlay,
  isExplicit,
  musicbrainzReleaseUrl,
  releaseBackgroundSrc,
  releaseCardClassName,
  releaseYearText,
} from './-artist-detail.card';

describe('releaseCardClassName', () => {
  it('keeps BOTH classes — .release-card for the filters, .album-card for the look', () => {
    expect(releaseCardClassName({ owned: true })).toBe('release-card album-card');
  });

  it('adds checking while ownership is unresolved, missing when absent', () => {
    expect(releaseCardClassName({ owned: null })).toBe('release-card album-card checking');
    expect(releaseCardClassName({ owned: false })).toBe('release-card album-card missing');
  });
});

describe('completionOverlay', () => {
  it('is omitted entirely for a source artist', () => {
    // No library to be complete against — the card is just artwork + title.
    expect(completionOverlay({ owned: true }, true)).toBeNull();
    expect(completionOverlay({ owned: null }, true)).toBeNull();
  });

  it('shows Checking while ownership is unresolved', () => {
    expect(completionOverlay({ owned: null }, false)).toEqual({
      className: 'checking',
      label: 'Checking...',
    });
  });

  it('also shows Checking when track_completion says so, even if owned', () => {
    expect(completionOverlay({ owned: true, track_completion: 'checking' }, false)?.className).toBe(
      'checking',
    );
  });

  it('shows Missing when not owned', () => {
    expect(completionOverlay({ owned: false }, false)).toEqual({
      className: 'missing',
      label: 'Missing',
    });
  });

  describe('object track_completion', () => {
    it('is complete only when NOTHING is missing', () => {
      const overlay = completionOverlay(
        {
          owned: true,
          track_completion: { owned_tracks: 12, total_tracks: 12, missing_tracks: 0 },
        },
        false,
      );
      expect(overlay).toEqual({ className: 'completed', label: '✓ Owned' });
    });

    it('shows owned/total and splits nearly_complete at 75%', () => {
      const near = completionOverlay(
        { owned: true, track_completion: { owned_tracks: 9, total_tracks: 12, missing_tracks: 3 } },
        false,
      );
      expect(near).toEqual({ className: 'nearly_complete', label: '9/12' }); // 75%

      const partial = completionOverlay(
        { owned: true, track_completion: { owned_tracks: 8, total_tracks: 12, missing_tracks: 4 } },
        false,
      );
      expect(partial).toEqual({ className: 'partial', label: '8/12' }); // 67%
    });

    it('does not divide by zero when total_tracks is 0', () => {
      const overlay = completionOverlay(
        { owned: true, track_completion: { owned_tracks: 0, total_tracks: 0, missing_tracks: 2 } },
        false,
      );
      expect(overlay).toEqual({ className: 'partial', label: '0/0' });
    });

    it('stays partial when total is 0 but owned is not — Infinity must not pass 75%', () => {
      // 0/0 is NaN and fails >= 75 anyway, so it cannot detect a missing guard.
      // owned/0 is Infinity, which WOULD pass and mislabel this as nearly complete.
      const overlay = completionOverlay(
        { owned: true, track_completion: { owned_tracks: 5, total_tracks: 0, missing_tracks: 1 } },
        false,
      );
      expect(overlay).toEqual({ className: 'partial', label: '5/0' });
    });
  });

  describe('numeric track_completion', () => {
    it('treats 100 as complete', () => {
      expect(completionOverlay({ owned: true, track_completion: 100 }, false)).toEqual({
        className: 'completed',
        label: '✓ Owned',
      });
    });

    it('treats a missing completion on an owned release as complete', () => {
      // `|| 100` in the vanilla — absence means "no partial info", not "0%".
      expect(completionOverlay({ owned: true }, false)).toEqual({
        className: 'completed',
        label: '✓ Owned',
      });
      expect(completionOverlay({ owned: true, track_completion: 0 }, false)?.className).toBe(
        'completed',
      );
    });

    it('splits nearly_complete at 75% and labels with a percent', () => {
      expect(completionOverlay({ owned: true, track_completion: 75 }, false)).toEqual({
        className: 'nearly_complete',
        label: '75%',
      });
      expect(completionOverlay({ owned: true, track_completion: 74 }, false)).toEqual({
        className: 'partial',
        label: '74%',
      });
    });
  });
});

describe('releaseYearText', () => {
  const now = new Date('2026-07-27T00:00:00Z');

  it('takes a leading 4-digit year from release_date', () => {
    expect(releaseYearText({ release_date: '1994-04-08' }, now)).toBe('1994');
  });

  it('parses a non-leading-year date format', () => {
    expect(releaseYearText({ release_date: 'April 8, 1994' }, now)).toBe('1994');
  });

  it('rejects a year outside 1900 < y <= currentYear + 1', () => {
    expect(releaseYearText({ release_date: '1899-01-01' }, now)).toBe('');
    expect(releaseYearText({ release_date: '2099-01-01' }, now)).toBe('');
    // next year IS allowed — announced but unreleased
    expect(releaseYearText({ release_date: '2027-01-01' }, now)).toBe('2027');
  });

  it('falls back to the year field when the date is unusable', () => {
    expect(releaseYearText({ release_date: 'nonsense', year: 1994 }, now)).toBe('1994');
    expect(releaseYearText({ year: 1994 }, now)).toBe('1994');
  });

  it('renders nothing when there is neither', () => {
    expect(releaseYearText({}, now)).toBe('');
  });

  it('does NOT range-check the year fallback', () => {
    // Matches the vanilla: `if (!yearText && release.year) yearText = release.year.toString()`
    // is unguarded. Reproduced 1:1 rather than tightened.
    expect(releaseYearText({ year: 3000 }, now)).toBe('3000');
  });
});

describe('releaseBackgroundSrc', () => {
  it('skips empty and whitespace-only urls so no observer work is queued', () => {
    expect(releaseBackgroundSrc({ image_url: '  ' })).toBeNull();
    expect(releaseBackgroundSrc({ image_url: '' })).toBeNull();
    expect(releaseBackgroundSrc({})).toBeNull();
    expect(releaseBackgroundSrc({ image_url: 'a.jpg' })).toBe('a.jpg');
  });
});

describe('isExplicit', () => {
  it('requires exactly true, not merely truthy', () => {
    expect(isExplicit({ explicit: true })).toBe(true);
    expect(isExplicit({ explicit: 1 as never })).toBe(false);
    expect(isExplicit({})).toBe(false);
  });
});

describe('musicbrainzReleaseUrl', () => {
  it('links to the RELEASE, not the release-group', () => {
    expect(musicbrainzReleaseUrl({ musicbrainz_release_id: 'abc' })).toBe(
      'https://musicbrainz.org/release/abc',
    );
    expect(musicbrainzReleaseUrl({})).toBeNull();
  });
});
