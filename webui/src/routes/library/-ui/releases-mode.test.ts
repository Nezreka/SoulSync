import { describe, expect, it } from 'vitest';

import { shouldAutoFetchDiscography } from './library-v2-page';

describe('shouldAutoFetchDiscography', () => {
  it('fetches when the artist has no discography yet and nothing is in flight', () => {
    expect(
      shouldAutoFetchDiscography({
        discographyCount: 0,
        discographyBusy: false,
        alreadyAttempted: false,
      }),
    ).toBe(true);
  });

  it('does not fetch while the artist is still loading (count undefined)', () => {
    expect(
      shouldAutoFetchDiscography({
        discographyCount: undefined,
        discographyBusy: false,
        alreadyAttempted: false,
      }),
    ).toBe(false);
  });

  it('does not fetch when the discography already has entries walked recently', () => {
    expect(
      shouldAutoFetchDiscography({
        discographyCount: 5,
        discographyBusy: false,
        alreadyAttempted: false,
        syncedAt: '2026-10-29 10:00:00',
        now: Date.parse('2026-10-29T12:00:00Z'),
      }),
    ).toBe(false);
  });

  it('re-checks a discography walked long ago, or never, for new releases', () => {
    const base = { discographyCount: 5, discographyBusy: false, alreadyAttempted: false };
    const now = Date.parse('2026-10-29T12:00:00Z');
    expect(shouldAutoFetchDiscography({ ...base, syncedAt: '2026-10-29 05:00:00', now })).toBe(
      true,
    );
    expect(shouldAutoFetchDiscography({ ...base, syncedAt: null, now })).toBe(true);
    // once per visit: the attempt guard still holds
    expect(
      shouldAutoFetchDiscography({ ...base, alreadyAttempted: true, syncedAt: null, now }),
    ).toBe(false);
  });

  it('does not fetch while a fetch is already in flight', () => {
    expect(
      shouldAutoFetchDiscography({
        discographyCount: 0,
        discographyBusy: true,
        alreadyAttempted: false,
      }),
    ).toBe(false);
  });

  it('does not re-fetch after an attempt already ran for this mode switch, even if the count is still 0', () => {
    // Regression guard: a genuinely-empty provider discography must not
    // retry forever every time `discographyBusy` flips back to false.
    expect(
      shouldAutoFetchDiscography({
        discographyCount: 0,
        discographyBusy: false,
        alreadyAttempted: true,
      }),
    ).toBe(false);
  });
});
