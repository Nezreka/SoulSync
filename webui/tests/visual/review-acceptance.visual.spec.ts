import { expect, test, type Locator, type Page } from '@playwright/test';
import { http, HttpResponse, type RequestHandler } from 'msw';

import { acceptanceApi, acceptanceSnapshot } from '../fixtures/review-acceptance';
import { installShell, ORIGIN } from './harness';

// These are independent product expectations, not assertions derived from the
// current UI or regenerated Python output. The backend verifies exact edits.
const scenarios = [
  { name: 'supported', ready: true, title: 'Home', status: 'change', reason: 'Proposed changes' },
  {
    name: 'metadata_only',
    ready: true,
    title: 'Open Water',
    status: 'change',
    reason: 'Proposed changes',
  },
  {
    name: 'recording_conflict',
    ready: false,
    title: 'Home',
    status: 'extra',
    reason: 'Unrelated Song',
  },
  {
    name: 'compatible_qualifier',
    ready: true,
    title: 'Home (live at Wembley, 12 July 1986)',
    status: 'change',
    reason: 'Proposed changes',
  },
  {
    name: 'different_performance',
    ready: false,
    title: 'Home (live at Wembley, 12 July 1986)',
    status: 'extra',
    reason: 'The local audio and release track may be different versions.',
  },
  {
    name: 'repeated_recording',
    ready: false,
    title: 'Home',
    status: 'uncertain',
    reason: 'Why this needs review',
  },
  {
    name: 'missing_metadata',
    ready: true,
    title: 'Open Water',
    status: 'change',
    reason: 'Proposed changes',
  },
  {
    name: 'contradictory_metadata',
    ready: false,
    title: 'Open Water',
    status: 'uncertain',
    reason: 'Album metadata differs between files',
  },
  {
    name: 'duplicate',
    ready: false,
    title: 'Home',
    status: 'duplicate',
    reason: 'Duplicate copies',
  },
  { name: 'companion', ready: true, title: 'Home', status: 'change', reason: 'Proposed changes' },
  {
    name: 'unmatched_companion',
    ready: false,
    title: 'Home',
    status: 'extra',
    reason: 'Audio does not fit a slot on the selected release.',
  },
  { name: 'partial', ready: false, chooser: 'Choose album completeness' },
  { name: 'selective', ready: false, chooser: 'Choose missing tracks' },
  { name: 'waiting', ready: true, plan: 'Missing tracks being acquired' },
  { name: 'cancelled', ready: false, chooser: 'Choose missing tracks' },
  { name: 'saved', ready: true, plan: 'Ready change summary' },
  { name: 'duplicate_saved', ready: true, plan: 'Ready change summary' },
  { name: 'relocated', ready: true, plan: 'Ready change summary' },
  {
    name: 'stale',
    ready: false,
    title: 'Open Water',
    status: 'uncertain',
    reason: 'Album metadata differs between files',
  },
] as const;

async function settle(page: Page) {
  await page.mouse.move(0, 0);
  await page.waitForLoadState('networkidle');
  await page.evaluate(async () => {
    await document.fonts.ready;
    for (const animation of document.getAnimations()) {
      if (animation.effect?.getComputedTiming().iterations === Infinity) animation.cancel();
      else animation.finish();
    }
  });
}

async function expectSeparated(first: Locator, second: Locator) {
  const a = await first.boundingBox();
  const b = await second.boundingBox();
  expect(a).not.toBeNull();
  expect(b).not.toBeNull();
  expect(b!.y >= a!.y + a!.height || b!.x >= a!.x + a!.width).toBe(true);
}

async function openAcceptance(
  page: Page,
  width: number,
  api: ReturnType<typeof acceptanceApi>,
  handlers: RequestHandler[] = [],
) {
  await page.setViewportSize({ width, height: 1100 });
  await page.clock.setFixedTime(new Date('2025-01-13T10:00:00Z'));
  await page.addInitScript(() => {
    localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
    localStorage.setItem('soulsync_helper_discovered', '1');
  });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const missing = await installShell(page, [...handlers, ...api.handlers]);
  await page.goto(`${ORIGIN}/review?mode=all`);
  await expect(page.getByRole('heading', { name: 'Northbound', exact: true })).toBeVisible();
  return { errors, missing };
}

for (const width of [1800, 1280, 390]) {
  test(`ready plan with organization ${width}px`, async ({ page }) => {
    const api = acceptanceApi('supported');
    const { errors, missing } = await openAcceptance(page, width, api, [
      http.post('*/api/library/album/:id/reorganize/preview', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              file_exists: true,
              matched: true,
              current_path: '/music/Acceptance Artist/Northbound/01 - Home.flac',
              new_path: '/music/Acceptance Artist/[2001] Northbound/01 - Home.flac',
            },
          ],
        }),
      ),
    ]);
    const apply = page.getByLabel('Apply album changes', { exact: true });
    await expect(
      apply.getByRole('button', {
        name: 'Apply ready edits, then organize folder',
        exact: true,
      }),
    ).toBeVisible();
    const description = apply.locator(':scope > div').first();
    const controls = apply.locator(':scope > div').nth(1);
    const card = await apply.boundingBox();
    const text = await description.boundingBox();
    // Buttons must not steal nearly all the width of the primary explanation.
    expect(text!.width).toBeGreaterThanOrEqual(Math.min(240, card!.width - 42));
    await expectSeparated(description, controls);
    await apply.evaluate((element) => element.scrollIntoView({ block: 'center' }));
    await settle(page);
    expect(errors).toEqual([]);
    expect(missing).toEqual([]);
    expect(api.unexpected).toEqual([]);
    await expect(apply).toHaveScreenshot(['review-acceptance', `organize-${width}.png`]);
  });
}

for (const width of [1800, 390, 320]) {
  for (const scenario of scenarios) {
    // These three panels exposed collisions; also protect the narrowest phone
    // width supported by the existing review layout suite.
    if (width === 320 && !['supported', 'duplicate', 'waiting'].includes(scenario.name)) continue;
    test(`${scenario.name} acceptance ${width}px`, async ({ page }) => {
      const api = acceptanceApi(scenario.name);
      const { errors, missing } = await openAcceptance(page, width, api);
      await expect(page.getByRole('button', { name: /Apply \d+ ready change/ })).toHaveCount(
        scenario.ready ? 1 : 0,
      );
      await expect(
        page.getByText(
          /passed the album and audio checks|supported by the album and audio evidence/,
        ),
      ).toHaveCount(0);

      if ('plan' in scenario) {
        await page.getByLabel(scenario.plan, { exact: true }).scrollIntoViewIfNeeded();
        await expect(page.getByLabel('Ready change summary', { exact: true })).toHaveCount(1);
        if (scenario.name === 'duplicate_saved') {
          await expect(page.getByLabel('Planned file actions')).toContainText(
            /Keep .*01 - Home.flac; move to recoverable quarantine: .*01 - Home.copy.flac/,
          );
          await page.getByLabel('Planned file actions').scrollIntoViewIfNeeded();
        }
        if (scenario.name === 'waiting') {
          const waiting = page.getByLabel('Missing tracks being acquired');
          await expectSeparated(
            waiting.getByText('1 requested', { exact: true }),
            waiting.getByText('Waiting for missing tracks', { exact: true }),
          );
          await expectSeparated(waiting.locator('p'), waiting.getByRole('link'));
        }
      } else {
        await page.getByRole('tab', { name: /^Tracks/ }).click();
        await page.getByRole('button', { name: 'Release order', exact: true }).click();
        const rows = page.locator('[data-review-assignment]');
        // A version conflict intentionally combines its missing slot and local
        // file into one comparison row. Every local file must still be present.
        const assignments = api.current.album_plan!.assignments;
        await expect(rows).toHaveCount(
          assignments.filter(
            (row) => !(row.status === 'missing' && row.relationship === 'version_conflict'),
          ).length,
        );
        if ('chooser' in scenario) {
          if (scenario.chooser === 'Choose missing tracks')
            await page.getByText(/missing track.*Choose tracks to request/).click();
          const chooser = page.getByLabel(scenario.chooser, { exact: true });
          await chooser.scrollIntoViewIfNeeded();
          await expect(chooser.getByRole('checkbox', { name: 'Open Water' })).toBeVisible();
          await expect(chooser.getByRole('checkbox', { name: 'Afterglow' })).toHaveCount(
            scenario.name === 'partial' ? 1 : 0,
          );
          if (scenario.name === 'cancelled')
            await expect(rows.filter({ hasText: 'Request cancelled · still wanted' })).toHaveCount(
              1,
            );
        } else {
          const matching = rows
            .filter({
              has: page.locator('[class*="compactLocal"] strong', { hasText: scenario.title }),
            })
            .and(page.locator(`[data-status="${scenario.status}"]`));
          const row = scenario.name.includes('companion') ? matching.last() : matching.first();
          if (scenario.name.includes('companion'))
            await expect(row.getByText('Intentional companion', { exact: true })).toBeVisible();
          await row.click();
          await expect(row).toHaveAttribute('aria-pressed', 'true');
          const detail = page.getByRole('complementary', { name: 'Selected track details' });
          await expect(detail.getByText(scenario.reason, { exact: false }).first()).toBeVisible();
          // A proposed replacement/removal must be available before acceptance.
          if (scenario.name === 'contradictory_metadata' || scenario.name === 'stale') {
            const change = detail.getByRole('rowheader', {
              name: 'Replace MusicBrainz release ID · file tag',
            });
            await expect(change).toBeVisible();
            expect(await change.locator('xpath=ancestor::details').count()).toBe(0);
          }
          if (scenario.name === 'metadata_only') {
            await expect(detail.getByRole('cell', { name: /Northbound/ })).toBeVisible();
            await expect(
              detail.getByText(
                'Metadata supports this repair; audio identity is not independently confirmed',
                { exact: true },
              ),
            ).toBeVisible();
          }
          if (scenario.name === 'unmatched_companion') {
            await expect(detail.getByRole('link', { name: /Search Spotify/ })).toHaveAttribute(
              'href',
              'https://open.spotify.com/search/Acceptance%20Artist%20Home',
            );
          }
          if (scenario.name === 'duplicate') {
            await expect(detail.getByText('01 - Home.flac', { exact: true }).first()).toBeVisible();
            await expect(
              detail.getByText('01 - Home.copy.flac', { exact: true }).first(),
            ).toBeVisible();
            for (const copy of await detail.locator('article').all()) {
              await expectSeparated(copy.locator(':scope > div'), copy.getByRole('button'));
            }
          }
          await detail.scrollIntoViewIfNeeded();
          await detail.evaluate((element) => {
            element.scrollTop = 0;
          });
        }
      }
      await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/);
      await settle(page);
      if (width < 769 && scenario.ready && 'title' in scenario) {
        const text = await page
          .getByText('All decisions are planned. This album is ready to apply.', { exact: true })
          .boundingBox();
        const menu = await page.getByRole('button', { name: 'Open navigation' }).boundingBox();
        expect(text!.x).toBeGreaterThan(menu!.x + menu!.width);
      }
      expect(
        await page.locator('[class*="headerTools"]').evaluate((e) => e.scrollWidth - e.clientWidth),
      ).toBeLessThanOrEqual(1);
      expect(errors).toEqual([]);
      expect(missing).toEqual([]);
      expect(api.unexpected).toEqual([]);
      await expect(page).toHaveScreenshot(['review-acceptance', `${scenario.name}-${width}.png`]);
    });
  }

  if (width === 320) continue;

  test(`save, relocate and reject stale apply ${width}px`, async ({ page }) => {
    const api = acceptanceApi('contradictory_metadata');
    const requests: unknown[] = [];
    const { errors, missing } = await openAcceptance(page, width, api, [
      http.post('*/api/repair/review-queue/:id/decisions/metadata/apply', async ({ request }) => {
        requests.push(await request.json());
        api.setCase('saved');
        return HttpResponse.json({ success: true, saved: 2 });
      }),
      http.post('*/api/repair/review-queue/apply-ready', async ({ request }) => {
        requests.push(await request.json());
        api.setCase('stale');
        return HttpResponse.json({
          started: false,
          stale: true,
          error: 'The ready album plans changed; review the updated batch before applying.',
        });
      }),
    ]);
    await page.getByRole('button', { name: 'Use 2 proposed changes', exact: true }).click();
    await expect(page.getByText(/All required decisions are planned and saved/)).toBeVisible();
    expect(requests).toEqual([{ action: 'fix' }]);
    // Opening the old case URL after a verified move follows the backend redirect.
    api.setCase('relocated');
    await page.reload();
    await expect(page).toHaveURL(/case=album%3A10%3Arelocated/);
    await expect(page.getByText(/All required decisions are planned and saved/)).toBeVisible();
    await page.getByRole('button', { name: 'Apply 2 ready changes', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/01 - Home.flac/)).toBeVisible();
    await expect(dialog.getByText(/02 - Open Water.flac/)).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(requests).toHaveLength(1);
    await page.getByRole('button', { name: 'Apply 2 ready changes', exact: true }).click();
    await dialog.getByRole('button', { name: 'Apply plans', exact: true }).click();
    await expect(page.getByText(/The ready album plans changed/)).toBeVisible();
    expect(requests[1]).toEqual({
      case_id: 'album:10:relocated',
      organize: false,
      signature: acceptanceSnapshot('relocated').ready_preview.signature,
    });
    await page.getByRole('button', { name: 'Reload queue', exact: true }).click();
    await expect(
      page.getByRole('button', { name: 'Apply 2 ready changes', exact: true }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
    expect(missing).toEqual([]);
    expect(api.unexpected).toEqual([]);
  });

  test(`selective request and cancellation ${width}px`, async ({ page }) => {
    const api = acceptanceApi('partial');
    const requests: unknown[] = [];
    let requested = false;
    const { errors, missing } = await openAcceptance(page, width, api, [
      http.post('*/api/repair/review-queue/:id/findings/:finding/apply', async ({ request }) => {
        requests.push(await request.json());
        api.setCase('selective');
        return HttpResponse.json({ success: true });
      }),
      http.post('*/api/repair/review-queue/:id/request-missing', async ({ request }) => {
        requests.push(await request.json());
        requested = true;
        api.setCase('waiting');
        return HttpResponse.json({ started: true, operation_id: 'acceptance-request', total: 1 });
      }),
      http.get('*/api/repair/bulk-fix/status', () =>
        HttpResponse.json(
          requested
            ? {
                operation_id: 'acceptance-request',
                running: false,
                fixed: 1,
                failed: 0,
                total: 1,
                review_scope: 'album',
                review_case_ids: ['album:10:acceptance'],
              }
            : { running: false },
        ),
      ),
    ]);
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    await page.getByRole('button', { name: 'Release order', exact: true }).click();
    const scope = page.getByLabel('Choose album completeness', { exact: true });
    await scope.getByRole('checkbox', { name: 'Open Water', exact: true }).check();
    await scope.getByRole('button', { name: 'Keep as partial · 1 wanted', exact: true }).click();
    const intent = acceptanceSnapshot('partial').case.items.find(
      (i) => i.finding_type === 'reconciliation_intent',
    )!;
    expect(requests).toEqual([
      {
        action: 'fix',
        evidence_token: intent.evidence_token,
        fix_action: 'partial:["id:00000000-0000-4000-8000-000000000002:1:2"]',
      },
    ]);
    await page.getByText(/missing track.*Choose tracks to request/).click();
    const chooser = page.getByLabel('Choose missing tracks', { exact: true });
    await expect(chooser.getByRole('checkbox', { name: 'Afterglow' })).toHaveCount(0);
    await chooser.getByRole('checkbox', { name: 'Open Water', exact: true }).check();
    await chooser.getByRole('button', { name: 'Request selected tracks (1)', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(requests).toHaveLength(1);
    await chooser.getByRole('button', { name: 'Request selected tracks (1)', exact: true }).click();
    await dialog.getByRole('button', { name: 'Request 1 track', exact: true }).click();
    await expect(page.getByText('1 album action completed.', { exact: true })).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.locator('[data-review-assignment]').filter({ hasText: 'Open Water' }),
    ).toContainText('Waiting for download');
    const selected = acceptanceSnapshot('selective').case.items.find(
      (i) => i.finding_type === 'reconciliation_missing',
    )!;
    expect(requests[1]).toEqual({ finding_ids: [selected.id] });
    // Cancellation happens in Wishlist; reloading Review must retain partial intent.
    api.setCase('cancelled');
    await page.getByRole('button', { name: 'Reload queue', exact: true }).click();
    await expect(page.getByText('Request cancelled · still wanted').first()).toBeVisible();
    await expect(
      page.locator('[data-review-assignment]').filter({ hasText: 'Afterglow' }),
    ).toContainText('Not collected');
    expect(errors).toEqual([]);
    expect(missing).toEqual([]);
    expect(api.unexpected).toEqual([]);
  });

  test(`duplicate keeper saves without applying and confirmation names files ${width}px`, async ({
    page,
  }) => {
    const api = acceptanceApi('duplicate');
    const requests: unknown[] = [];
    const { errors, missing } = await openAcceptance(page, width, api, [
      http.post('*/api/repair/review-queue/:id/findings/:finding/apply', async ({ request }) => {
        requests.push(await request.json());
        api.setCase('duplicate_saved');
        return HttpResponse.json({ success: true, saved: 1 });
      }),
    ]);
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    await page.getByRole('button', { name: 'Release order', exact: true }).click();
    await page.locator('[data-review-assignment][data-status="duplicate"]').click();
    const detail = page.getByRole('complementary', { name: 'Selected track details' });
    const copy = detail.locator('article').filter({ hasText: '01 - Home.flac' });
    await copy.getByRole('button', { name: 'Plan to keep this copy', exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    const finding = acceptanceSnapshot('duplicate').case.items.find(
      (i) => i.finding_type === 'reconciliation_duplicate',
    )!;
    expect(requests).toEqual([
      { action: 'fix', fix_action: '1', evidence_token: finding.evidence_token },
    ]);
    await page.getByRole('tab', { name: /^Plan/ }).click();
    await page.getByRole('button', { name: 'Review file decision', exact: true }).click();
    await expect(page.getByRole('tab', { name: /^Tracks/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(detail.getByRole('heading', { name: 'Duplicate copies: Home' })).toBeVisible();
    await page.getByRole('tab', { name: /^Plan/ }).click();
    await page.getByRole('button', { name: 'Apply 2 ready changes', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(
      dialog.getByText(
        /Keep .*01 - Home.flac; move to recoverable quarantine: .*01 - Home.copy.flac/,
      ),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(requests).toHaveLength(1);
    expect(errors).toEqual([]);
    expect(missing).toEqual([]);
    expect(api.unexpected).toEqual([]);
  });
}
