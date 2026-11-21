/**
 * One scenario on a page of its own. A test loads this module afresh for
 * each scenario (vi.resetModules), so the tabs' own modules start as a page
 * load starts them: several keep state outside React that nothing resets
 * (whether the video side was ever shown, which chain the widget is on).
 */

import type { Adapter } from './adapters';

import { backend } from './backend';
import {
  type CompositeRecord,
  type Review,
  type Scenario,
  type ScenarioRun,
  runComposite,
  runScenario,
  spent,
  timing,
} from './engine';
import { installWorld, uninstallWorld } from './harness';

export interface Result {
  run: ScenarioRun;
  composites: Record<string, CompositeRecord>;
  /** GETs no fixture answered */
  unanswered: string[];
  timing: typeof timing;
  spent: typeof spent;
}

export async function runOne(
  scenario: Scenario,
  known: string[],
  review: Review,
  mode: 'slots' | 'page',
  adapters: Record<string, Adapter>,
): Promise<Result> {
  installWorld();
  try {
    const run = await runScenario(scenario, new Set(known), review, mode, adapters);
    const composites: Record<string, CompositeRecord> = {};
    for (const [name, adapter] of Object.entries(adapters))
      composites[name] = await runComposite(adapter, scenario, run.slots[name]);
    return { run, composites, unanswered: [...backend.unanswered].sort(), timing, spent };
  } finally {
    uninstallWorld();
  }
}
