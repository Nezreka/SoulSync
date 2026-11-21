/**
 * The Settings key guard, the music side, part 3 of 3 (see
 * settings-guard.test.tsx).
 */

import { describe, expect, it } from 'vitest';

import { SCENARIOS } from './scenarios';
import { WRITE, checkScenario } from './suite';

describe.skipIf(WRITE)('settings guard', () => {
  it('the music side saves and shows what the manifest says (part 3 of 3)', async () => {
    expect(await checkScenario(SCENARIOS[0], { index: 2, of: 3 })).toEqual([]);
  }, 240_000);
});
