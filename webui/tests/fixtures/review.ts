import { http, HttpResponse } from 'msw';

import type { ReviewCase } from '../../src/routes/review/-review.api';

import { reviewCaseFixture } from '../../src/routes/review/-test/fixtures';

import { shellHandlers } from '../visual/fixtures';

// Production bundles with local API fixtures; no service or music files are touched.
// Run npm run build before this spec. Screenshots are inspection artifacts, not
// host-dependent pixel baselines.
export function reviewFixture(ready: boolean, configure?: (review: ReviewCase) => void) {
  const reviewCase = reviewCaseFixture(ready);
  const finding = reviewCase.items[0];
  configure?.(reviewCase as unknown as ReviewCase);
  return [
    http.get('*/api/repair/review-queue/:caseId/playlist-references', () =>
      HttpResponse.json({ playlists: [] }),
    ),
    ...shellHandlers,
    http.get('*/api/repair/review-queue/summary', () =>
      HttpResponse.json({
        all: 1,
        review: reviewCase.plan!.can_apply_ready ? 0 : 1,
        ready: reviewCase.plan!.can_apply_ready ? 1 : 0,
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

export function ambiguousReview(review: ReviewCase) {
  const item = review.items[0];
  item.finding_type = 'reconciliation_unresolved';
  item.title = 'Confirm assignment: Home';
  item.details = {
    ...item.details,
    metadata_review_reason: '',
    recording_id_conflict: null,
    ambiguity_reasons: ['Two recording identities have equal fingerprint scores'],
    acoustid: {
      status: 'ok',
      best_score: 0.99,
      recordings: [
        { title: 'Home', artist: 'Artist', score: 0.99 },
        { title: 'Different song', artist: 'Artist', score: 0.99 },
      ],
    },
  };
  item.review_reason = 'The track assignment is not decisive';
  const row = review.album_plan!.assignments[0];
  row.status = 'uncertain';
  row.confidence = 'uncertain';
  review.album_plan!.counts = { confirmed: 0, changed: 0, uncertain: 1, missing: 0, extra: 0 };
  review.decisions![0].category = 'audio_conflicts';
  review.decisions![0].label = 'Confirm assignment';
  review.decisions![0].summary = item.review_reason;
}
