/**
 * The Settings key guard, the pages the baselines hide (scenarios.ts; see
 * settings-guard.test.tsx).
 */

import { describe, expect, it } from 'vitest';

import { SCENARIOS } from './scenarios';
import { WRITE, checkScenario } from './suite';

describe.skipIf(WRITE)('settings guard', () => {
  it.each(SCENARIOS.slice(2).map((scenario) => [scenario.name, scenario] as const))(
    'the %s page saves and shows what the manifest says',
    async (_name, scenario) => {
      expect(await checkScenario(scenario)).toEqual([]);
    },
    240_000,
  );
});
