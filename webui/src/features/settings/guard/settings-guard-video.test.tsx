/**
 * The Settings key guard, the video side (see settings-guard.test.tsx).
 */

import { describe, expect, it } from 'vitest';

import { SCENARIOS } from './scenarios';
import { WRITE, checkScenario } from './suite';

describe.skipIf(WRITE)('settings guard', () => {
  it('the video side saves and shows what the manifest says', async () => {
    expect(await checkScenario(SCENARIOS[1])).toEqual([]);
  }, 240_000);
});
