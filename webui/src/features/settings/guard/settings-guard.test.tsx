/**
 * The Settings key guard: the safety net under the Settings redesign.
 *
 * It renders the real page (settings-page.tsx with the real tabs, store and
 * host) on recorded API answers and holds it against manifest.json:
 *
 *  - every form control on the page, found by its id (or its own name /
 *    data-* attributes), still sends what it sent when a person changes it:
 *    the /api/settings keys and values, and every other request;
 *  - it still shows those keys when the page loads with them saved;
 *  - every key of the recorded /api/settings payload is shown by some
 *    control, or is listed in `unshownKeys` with why not;
 *  - the composite widgets (chains, ranked lists, row lists) still send what
 *    their steps sent (adapters.ts);
 *  - no control and no button is gone, and no control is new without being
 *    added to the manifest on purpose.
 *
 * It never looks at a class, a tab, a section header or a position on the
 * page (adapters.ts aside, which is the one place that does and is meant to
 * be repointed): a control that keeps its id may move anywhere.
 *
 * The files:
 *   fixtures/      the recorded payloads (GET /api/settings and every other GET)
 *   backend.ts     the fake server, and what a save changes on it
 *   harness.tsx    the page in jsdom, opened as the route opens it
 *   driver.ts      how a control is found, read and changed, by kind
 *   engine.ts      the probes (what a change saves, what a load shows)
 *   adapters.ts    the hand-written probes for the composite widgets
 *   scenarios.ts   the pages looked at (two baselines, then what they hide)
 *   review.json    everything a person wrote: reasons, and how the forms
 *                  with an explicit Save are probed
 *   manifest.json  GENERATED: what the page did when it was recorded
 *
 * The check is split over five test files so they run side by side: this
 * one (the checks that need no page, and a third of the music side),
 * settings-guard-music-2 and -3, settings-guard-video and
 * settings-guard-scenarios.
 *
 * To record the page again (a control was added, removed or changed on
 * purpose):
 *
 *   SETTINGS_GUARD=write npx vitest run src/features/settings/guard/settings-guard.test.tsx
 *
 * From WSL with Windows node the variable has to be passed across:
 *   WSLENV=SETTINGS_GUARD SETTINGS_GUARD=write npx vitest run ...
 * Then read the diff of manifest.json: every line of it is a behaviour that
 * changed. The run fails until review.json says why for anything new that
 * sends nothing, any recorded key nothing shows, and any audit id not found.
 *
 * SETTINGS_GUARD_MOUNT=page probes every control on the whole page instead
 * of slot by slot (minutes, not seconds): the two must record the same.
 */

import { describe, expect, it } from 'vitest';

import { SCENARIOS } from './scenarios';
import { WRITE, checkScenario, generate, manifest, numbers, save, staticProblems } from './suite';

describe.runIf(WRITE)('settings guard: record the page', () => {
  it('writes manifest.json', async () => {
    const { manifest: next, problems, results } = await generate();
    save(next);
    console.log(
      'settings guard wrote manifest.json',
      numbers(next),
      results.map((result) => [result.run.scenario.name, result.timing, result.spent]),
    );
    expect(problems).toEqual([]);
  }, 900_000);
});

describe.skipIf(WRITE)('settings guard', () => {
  it('the manifest accounts for the recorded payload and the audit', () => {
    console.log('settings guard', numbers(manifest));
    expect(staticProblems(manifest)).toEqual([]);
  });

  it('the music side saves and shows what the manifest says (part 1 of 3)', async () => {
    expect(await checkScenario(SCENARIOS[0], { index: 0, of: 3 })).toEqual([]);
  }, 240_000);
});
