import { expect, test } from '@playwright/test';
import { http, HttpResponse } from 'msw';

import { shellHandlers } from './visual/fixtures';
import { installShell, ORIGIN } from './visual/harness';

// Production bundles with local API fixtures; no service or music files are touched.
// Run npm run build before this spec. Screenshots are inspection artifacts, not
// host-dependent pixel baselines.
function reviewFixture(ready: boolean) {
  const finding = {
    id: 1,
    job_id: 'library_reconciliation',
    finding_type: 'reconciliation_metadata',
    title: 'Update metadata: Home',
    status: 'pending',
    severity: 'warning',
    evidence_token: 'fixture',
    file_path: '/music/Album/01 - Home.flac',
    review_tier: ready ? 'ready' : 'manual',
    review_reason: ready
      ? 'The assignment was confirmed during review'
      : 'Confirm removing the incorrect recording ID',
    details: {
      album_id: 1,
      album_title: 'Album',
      artist_name: 'Artist',
      expected_title: 'Home',
      current_title: 'Home',
      source: 'musicbrainz',
      release_id: 'release',
      confidence: 'clear',
      assignment_evidence: ready ? 'user' : 'metadata',
      metadata_review_reason: ready ? '' : 'Confirm removing the incorrect recording ID',
      recording_id_conflict: ready ? null : { title: 'Different song', artist: 'Artist' },
      acoustid: { status: 'no_match', recordings: [] },
      changes: {
        filename: { old: '01 - HOME.flac', new: '01 - Home.flac' },
        musicbrainz_recording_id: { old: 'incorrect-recording-id', new: '' },
        library_recording_mbid: { old: 'incorrect-recording-id', new: '' },
        musicbrainz_release_id: { old: 'old-release-id', new: 'selected-release-id' },
      },
    },
  };
  const decision = {
    id: 'metadata',
    category: 'metadata',
    label: 'Metadata changes',
    risk: ready ? 'ready' : 'manual',
    summary: finding.review_reason,
    finding_ids: [1],
    action_count: 1,
  };
  const plan = {
    ready: ready ? 1 : 0,
    manual: ready ? 0 : 1,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: ready ? 'ready' : 'needs_review',
    blocker_reasons: [],
    can_apply_ready: ready,
    can_organize: false,
    decision_count: 1,
    decisions: [decision],
  };
  const assignment = {
    status: 'change',
    file: '01 - Home.flac',
    file_path: finding.file_path,
    finding_id: 1,
    current_title: 'Home',
    slot_title: 'Home',
    current_track_number: 1,
    slot_track_number: 1,
    slot_disc_number: 1,
    current_disc_number: 1,
    file_duration_ms: 200000,
    slot_duration_ms: 200000,
    confidence: 'clear',
    review_tier: finding.review_tier,
    action_state: finding.review_tier,
    assignment_evidence: finding.details.assignment_evidence,
    changes: finding.details.changes,
    ...(ready ? { planned_choice: { action: 'fix', category: 'metadata' } } : {}),
  };
  const reviewCase = {
    case_id: 'album:1:fixture',
    album: 'Album',
    artist: 'Artist',
    album_id: 1,
    folder: '/music/Album',
    count: 1,
    pending: 1,
    priority: 1,
    error_count: 0,
    finding_types: [finding.finding_type],
    status: 'pending',
    plan,
    decisions: [decision],
    items: [finding],
    album_plan: {
      state: 'changes',
      source: 'musicbrainz',
      release_id: 'release',
      intent_scope: 'complete',
      counts: { confirmed: 0, changed: 1, uncertain: 0, missing: 0, extra: 0 },
      numbering_mode: 'digital',
      categories: ['metadata'],
      assignments: [assignment],
    },
  };
  return [
    ...shellHandlers,
    http.get('*/api/repair/review-queue/summary', () =>
      HttpResponse.json({
        all: 1,
        review: ready ? 0 : 1,
        ready: ready ? 1 : 0,
        files: 0,
        waiting: 0,
        ready_total: ready ? 1 : 0,
        ready_albums_total: ready ? 1 : 0,
      }),
    ),
    http.get('*/api/repair/review-queue', () =>
      HttpResponse.json({
        items: [reviewCase],
        total: 1,
        page: 0,
        limit: 30,
      }),
    ),
    http.get('*/api/repair/review-queue/:id', () => HttpResponse.json(reviewCase)),
    http.get('*/api/repair/finding-types', () =>
      HttpResponse.json({
        types: [
          {
            type: finding.finding_type,
            label: 'Metadata repair',
            fixable: true,
          },
        ],
      }),
    ),
    http.get('*/api/repair/status', () => HttpResponse.json({ current_job: null })),
    http.get('*/api/repair/history', () => HttpResponse.json({ runs: [] })),
    http.get('*/api/repair/bulk-fix/status', () => HttpResponse.json({ running: false })),
  ];
}

for (const width of [1800, 1280, 390]) {
  for (const ready of [false, true]) {
    test(`${width}px ${ready ? 'saved' : 'manual'} metadata decisions expose identity effects`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height: 1050 });
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.addInitScript(() => {
        localStorage.setItem('sidebarCollapsed', '1');
        localStorage.setItem('tutorialShown', 'true');
      });
      const missing = await installShell(page, reviewFixture(ready));
      await page.goto(`${ORIGIN}/review?mode=all`);
      await expect(page.getByRole('heading', { name: 'Album', exact: true })).toBeVisible();
      if (ready) {
        await expect(page.getByText(/All required decisions are complete/)).toBeVisible();
        await expect(
          page.getByText('Remove MusicBrainz recording IDs on 1 track').first(),
        ).toBeVisible();
        await expect(
          page.getByText(
            /passed the album and audio checks|supported by the album and audio evidence/,
          ),
        ).toHaveCount(0);
        await page.getByText(/All required decisions are complete/).scrollIntoViewIfNeeded();
        const summary = page.getByLabel('Ready change summary', { exact: true });
        expect(
          await summary.evaluate((element) => element.scrollWidth - element.clientWidth),
        ).toBeLessThanOrEqual(1);
        await page.screenshot({ path: info.outputPath('ready-plan.png') });
      }
      await page.getByRole('tab', { name: /^Tracks/ }).click();
      const row = page.locator('[data-review-assignment]').first();
      await expect(row).toContainText(ready ? 'Change planned' : 'Review metadata');
      await expect(row).toHaveAttribute('data-status', ready ? 'change' : 'uncertain');
      await expect(
        page.getByText(ready ? '1 proposed change' : '1 metadata decision', { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole('button', {
          name: ready ? /1 proposed track change/ : /1 track needing review/,
        }),
      ).toBeVisible();
      await row.scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath('track-group.png') });
      await page.getByRole('button', { name: 'Release order', exact: true }).click();
      await expect(row).toHaveAttribute('data-status', ready ? 'change' : 'uncertain');
      await row.click();
      for (const label of [
        'Remove Recording ID · file and library',
        'Replace MusicBrainz release ID · file tag',
      ]) {
        const field = page.getByRole('rowheader', { name: label, exact: true });
        await field.scrollIntoViewIfNeeded();
        await expect(field).toBeVisible();
        expect(await field.locator('xpath=ancestor::details').count()).toBe(0);
      }
      await expect(page.getByText('incorrect-recording-id', { exact: true })).toHaveCount(1);
      await page.getByText('Proposed changes', { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath('identity-edits.png') });
      if (!ready) {
        const accept = page.getByRole('button', { name: 'Use proposed change', exact: true });
        await accept.scrollIntoViewIfNeeded();
        await expect(accept).toBeEnabled();
      }
      expect(errors).toEqual([]);
      expect(missing).toEqual([]);
    });
  }
}
