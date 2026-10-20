import { http, HttpResponse } from 'msw';

import type { ReviewCase } from '../../src/routes/review/-review.api';

import { shellHandlers } from '../visual/fixtures';

// Production bundles with local API fixtures; no service or music files are touched.
// Run npm run build before this spec. Screenshots are inspection artifacts, not
// host-dependent pixel baselines.
export function reviewFixture(ready: boolean, configure?: (review: ReviewCase) => void) {
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
      current_tags: { title: 'Home', album: 'Local album (deluxe)', artist: 'Artist' },
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
      candidates: [
        {
          source: 'musicbrainz',
          release_id: 'release',
          release_title: 'Catalogue Album',
          disambiguation: 'Japanese edition',
          country: 'JP',
          format: 'CD',
          date: '2001-01-01',
          clear: 1,
          matched: 1,
          total_slots: 1,
          missing: 0,
          extra: 0,
          continuous_numbering: false,
          pairs: [
            {
              file: assignment.file,
              local_title: 'Home',
              slot_title: 'Home',
              confidence: 'clear',
              track_number: 1,
            },
          ],
        },
      ],
    },
  };
  configure?.(reviewCase as unknown as ReviewCase);
  return [
    ...shellHandlers,
    http.get('*/api/repair/review-queue/summary', () =>
      HttpResponse.json({
        all: 1,
        review: reviewCase.plan.can_apply_ready ? 0 : 1,
        ready: reviewCase.plan.can_apply_ready ? 1 : 0,
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
