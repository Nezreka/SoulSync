/** #2332: the Holiday music picker on an artist, album or track */
import { describe, expect, it } from 'vitest';

import type { LibraryV2HolidayState } from '../-library-v2.api';

import { holidayChoice, holidaySays } from './holiday-flag';

const state = (over: Partial<LibraryV2HolidayState>): LibraryV2HolidayState => ({
  holiday: false,
  decided_by: 'auto',
  override: null,
  detected: false,
  season: { mode: 'season', in_season: false, allows_holiday: false },
  ...over,
});

describe('the holiday picker', () => {
  it('shows the hand flag on this row, else automatic', () => {
    expect(holidayChoice(undefined)).toBe('auto');
    expect(holidayChoice(state({}))).toBe('auto');
    expect(holidayChoice(state({ override: true, holiday: true }))).toBe('on');
    expect(holidayChoice(state({ override: false }))).toBe('off');
  });

  it('says what it is and why, in plain words', () => {
    expect(holidaySays('track', state({ holiday: true, detected: true }))).toBe(
      'Holiday music, from its genres or title. Mixes play it only in season.',
    );
    expect(holidaySays('track', state({}))).toBe(
      'Not holiday music, going by its genres and title.',
    );
    expect(holidaySays('track', state({ holiday: true, decided_by: 'album' }))).toBe(
      'Holiday music, from what you set on its album.',
    );
    expect(holidaySays('album', state({ override: false }))).toBe(
      'Not holiday music, set by hand. Mixes play it all year.',
    );
    for (const line of [holidaySays('artist', state({ override: true, holiday: true }))])
      expect(line).not.toContain('—');
  });
});
