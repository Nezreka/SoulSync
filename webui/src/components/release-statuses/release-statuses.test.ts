import { describe, expect, it } from 'vitest';

import {
  describeStatuses,
  normalizeOverride,
  normalizeStatuses,
  RELEASE_STATUSES,
  toggleStatus,
} from './release-statuses';

describe('#2175 release statuses', () => {
  it('reads nothing usable as every status, like the server', () => {
    expect(normalizeStatuses(undefined)).toEqual([...RELEASE_STATUSES]);
    expect(normalizeStatuses([])).toEqual([...RELEASE_STATUSES]);
    expect(normalizeStatuses(['made-up'])).toEqual([...RELEASE_STATUSES]);
  });

  it("keeps MusicBrainz's order whatever order they came in", () => {
    expect(normalizeStatuses(['Bootleg', 'official'])).toEqual(['official', 'bootleg']);
    expect(toggleStatus(['bootleg'], 'official', true)).toEqual(['official', 'bootleg']);
    expect(toggleStatus(['official', 'bootleg'], 'official', false)).toEqual(['bootleg']);
  });

  it('an artist without its own statuses follows the library', () => {
    expect(normalizeOverride(null)).toBeNull();
    expect(normalizeOverride(undefined)).toBeNull();
    expect(normalizeOverride([])).toBeNull();
    expect(normalizeOverride(['official'])).toEqual(['official']);
  });

  it('says every status, or the ones picked', () => {
    expect(describeStatuses(RELEASE_STATUSES)).toBe('every status');
    expect(describeStatuses(['official', 'pseudo-release'])).toBe('Official, Pseudo-Release');
  });
});
