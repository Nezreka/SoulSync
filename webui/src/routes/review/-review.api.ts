import type { BulkFixStatus, RepairFinding } from '../tools/-tools.types';

import { fetchRepairStatus, fetchBulkFixStatus } from '../tools/-tools.api';

export interface ReviewPlan {
  ready: number;
  manual: number;
  destructive: number;
  blocked: number;
  total: number;
  status: 'ready' | 'needs_review' | 'waiting';
  blocker_reasons: string[];
  can_apply_ready: boolean;
  can_organize: boolean;
  decision_count?: number;
  categories?: string[];
  decisions?: ReviewDecision[];
}

export interface ReviewDecision {
  id: string;
  category: string;
  label: string;
  risk: 'ready' | 'manual' | 'destructive' | 'blocked';
  summary: string;
  finding_ids: number[];
  action_count: number;
}

export interface AlbumAssignment {
  status:
    | 'confirmed'
    | 'change'
    | 'uncertain'
    | 'missing'
    | 'not_owned'
    | 'extra'
    | 'duplicate'
    | 'settled';
  evidence_status?: string;
  assignment_evidence?: 'audio' | 'metadata' | 'user' | 'conflicting';
  companion_of?: string;
  tool_checks?: Record<string, unknown>[];
  review_state?: 'pending' | 'reviewed' | 'obsolete';
  historical_finding_id?: number;
  planned_choice?: { category: string; action: 'fix' | 'dismiss'; fix_action?: string };
  action_state?:
    | 'ready'
    | 'manual'
    | 'destructive'
    | 'blocked'
    | 'preview'
    | 'resolved'
    | 'waiting';
  fulfillment_state?: 'wishlisted' | 'downloading' | 'downloaded' | 'cancelled';
  numbering_preview?: boolean;
  preview_reason?: string;
  relationship?: 'version_conflict';
  relationship_key?: string;
  counterpart_title?: string;
  counterpart_file?: string;
  counterpart_file_path?: string;
  counterpart_track_number?: number | string | null;
  counterpart_display_track_number?: number | string | null;
  counterpart_disc_number?: number | string | null;
  audio_conflict?: 'different_recording' | '';
  file_path?: string;
  track_id?: string | number;
  file?: string;
  finding_id?: number;
  review_tier?: string;
  current_title?: string;
  current_track_number?: number | string | null;
  current_disc_number?: number | string | null;
  slot_title?: string;
  slot_track_number?: number | string | null;
  display_track_number?: number | string | null;
  slot_disc_number?: number | string | null;
  title_difference?: string;
  confidence?: string;
  changes?: Record<string, { old?: unknown; new?: unknown }>;
  audio_title?: string;
  audio_artist?: string;
  audio_score?: number | null;
  duration_delta_ms?: number | null;
  file_duration_ms?: number | null;
  slot_artist?: string;
  slot_duration_ms?: number | null;
  slot_preview_url?: string;
  playlists?: string[];
  current_tags?: Record<string, unknown>;
  acoustid?: Record<string, unknown>;
  reasons?: string[];
  ambiguity_reasons?: string[];
}

export interface AlbumReconciliationPlan {
  state: 'confirmed' | 'changes' | 'review' | 'release_conflict';
  source?: string;
  release_id?: string;
  intent_scope?: string;
  counts: {
    confirmed: number;
    changed: number;
    uncertain: number;
    missing: number;
    not_owned?: number;
    extra: number;
    settled?: number;
  };
  numbering_mode?: string;
  categories: string[];
  assignments: AlbumAssignment[];
  candidates?: Array<{
    source: string;
    release_id: string;
    clear: number;
    matched: number;
    total_slots: number;
    missing: number;
    extra: number;
    continuous_numbering: boolean;
    release_title?: string;
    image_url?: string;
    country?: string;
    disambiguation?: string;
    format?: string;
    date?: string;
    pairs?: Array<{
      file: string;
      file_path?: string;
      track_id?: string | number;
      local_title: string;
      file_duration_ms?: number;
      slot_title: string;
      slot_duration_ms?: number;
      slot_preview_url?: string;
      disc_number?: number;
      track_number?: number;
      display_track_number?: number;
      confidence: string;
    }>;
    missing_tracks?: string[];
    extra_files?: string[];
  }>;
}

export interface ReviewCaseSummary {
  case_id: string;
  album: string | null;
  artist: string | null;
  album_thumb_url?: string | null;
  pending: number;
  priority: number;
  finding_types: string[];
  error_count: number;
  plan?: ReviewPlan;
  refreshing?: boolean;
}

export interface ReviewQueuePage {
  items: ReviewCaseSummary[];
  total: number;
  ready_total?: number;
  page: number;
  limit: number;
}

export interface ReviewQueueModeTotals {
  ready_albums_total?: number;
  ready_total?: number;
  all: number;
  review: number;
  ready: number;
  files: number;
  waiting: number;
}

export interface ReviewFinding extends RepairFinding {
  evidence_token: string;
  updated_at?: string | null;
  review_tier?: 'ready' | 'manual' | 'destructive' | 'blocked';
  review_reason?: string;
}

export interface ReviewCase {
  case_id: string;
  plan_signature?: string;
  saved_for_later?: boolean;
  album: string | null;
  artist: string | null;
  album_thumb_url?: string | null;
  folder: string | null;
  count: number;
  status: string;
  album_id?: string | number | null;
  plan?: ReviewPlan;
  decisions?: ReviewDecision[];
  album_plan?: AlbumReconciliationPlan | null;
  refresh?: { state: 'idle' | 'running' | 'complete' | 'error'; error?: string };
  items: ReviewFinding[];
}

export interface ReconciliationScanStatus {
  current_job: {
    job_id: string;
    progress?: { scanned: number; total: number; percent: number };
  } | null;
}

export interface ReconciliationRun {
  job_id: string;
  status: string;
  error_text?: string | null;
  items_scanned: number;
  finished_at?: string | null;
}

async function readJson<T>(response: Response): Promise<T> {
  if (typeof response.text !== 'function') {
    const body = (await response.json()) as T & { error?: string };
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }
  const raw = await response.text();
  if (!raw.trim()) throw new Error(`The server returned an empty response (${response.status})`);
  let body: T & { error?: string };
  try {
    body = JSON.parse(raw) as T & { error?: string };
  } catch {
    throw new Error(`The server returned an invalid response (${response.status})`);
  }
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

export async function fetchReviewQueue(
  page = 0,
  q = '',
  category = '',
  risk = '',
  limit = 30,
): Promise<ReviewQueuePage> {
  const params = new URLSearchParams({
    page: String(page),
    q,
    job_id: 'library_reconciliation',
    limit: String(limit),
  });
  if (category) params.set('category', category);
  if (risk) params.set('risk', risk);
  return readJson<ReviewQueuePage>(await fetch(`/api/repair/review-queue?${params}`));
}

export async function fetchReviewQueueModeTotals(
  q = '',
  category = '',
): Promise<ReviewQueueModeTotals> {
  const params = new URLSearchParams({ q, category, job_id: 'library_reconciliation' });
  return readJson<ReviewQueueModeTotals>(await fetch(`/api/repair/review-queue/summary?${params}`));
}

export async function applyReviewDecisionGroup(
  caseId: string,
  category: string,
  action: 'fix' | 'dismiss' | 'reset',
): Promise<{
  success: boolean;
  started?: boolean;
  operation_id?: string;
  applied?: number;
  total?: number;
  error?: string;
  refresh_queued?: boolean;
}> {
  const response = await fetch(
    `/api/repair/review-queue/${encodeURIComponent(caseId)}/decisions/${encodeURIComponent(category)}/apply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    },
  );
  // Grouped decisions only save draft choices; they do not write files. A missing acknowledgement proves neither success nor failure.
  if (typeof response.text !== 'function') return response.json();
  const raw = await response.text();
  if (!raw.trim()) {
    return {
      success: false,
      error: `No operation acknowledgement was received (${response.status}). Check operation progress before retrying.`,
    };
  }
  return JSON.parse(raw) as {
    success: boolean;
    started?: boolean;
    operation_id?: string;
    applied?: number;
    total?: number;
    error?: string;
    refresh_queued?: boolean;
  };
}

export async function fetchReviewCase(caseId: string): Promise<ReviewCase> {
  return readJson<ReviewCase>(
    await fetch(
      `/api/repair/review-queue/${encodeURIComponent(caseId)}?job_id=library_reconciliation`,
    ),
  );
}

export async function pinReviewRelease(
  caseId: string,
  candidate: { source: string; release_id: string },
  current: { source?: string; release_id?: string },
  options: { manual?: boolean; preview?: boolean } = {},
): Promise<{
  success: boolean;
  message?: string;
  error?: string;
  refresh_queued?: boolean;
  release_title?: string;
  track_count?: number;
  tracks?: string[];
  candidate?: Record<string, unknown>;
}> {
  const response = await fetch(`/api/repair/review-queue/${encodeURIComponent(caseId)}/release`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: candidate.source,
      release_id: candidate.release_id,
      expected_source: current.source,
      expected_release_id: current.release_id,
      ...options,
    }),
  });
  return readJson(response);
}

export async function runReconciliationScan(): Promise<void> {
  await readJson<{ success: boolean }>(
    await fetch('/api/repair/jobs/library_reconciliation/run', { method: 'POST' }),
  );
}

export async function fetchReconciliationScanStatus(): Promise<ReconciliationScanStatus> {
  return fetchRepairStatus(readJson<ReconciliationScanStatus>);
}

export async function fetchLastReconciliationRun(): Promise<ReconciliationRun | null> {
  const data = await readJson<{ runs: ReconciliationRun[] }>(
    await fetch('/api/repair/history?limit=50'),
  );
  return data.runs.find((run) => run.job_id === 'library_reconciliation') || null;
}

export async function applyReviewDecision(
  caseId: string,
  finding: ReviewFinding,
  action: 'fix' | 'dismiss',
  fixAction?: string | null,
): Promise<{
  success: boolean;
  message?: string;
  error?: string;
  stale?: boolean;
  partial?: boolean;
  refresh_queued?: boolean;
}> {
  const response = await fetch(
    `/api/repair/review-queue/${encodeURIComponent(caseId)}/findings/${finding.id}/apply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        fix_action: fixAction,
        evidence_token: finding.evidence_token,
      }),
    },
  );
  return response.json();
}

export async function reopenReviewAssignment(
  caseId: string,
  findingId: number,
): Promise<{
  success: boolean;
  message?: string;
  error?: string;
}> {
  return readJson(
    await fetch(
      `/api/repair/review-queue/${encodeURIComponent(caseId)}/findings/${findingId}/reopen`,
      { method: 'POST' },
    ),
  );
}

export async function startReadyChanges(
  signature: string,
  caseId?: string,
  organize = false,
): Promise<{
  started: boolean;
  operation_id?: string;
  total?: number;
  error?: string;
  already_running?: boolean;
}> {
  return readJson(
    await fetch('/api/repair/review-queue/apply-ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId, organize, signature }),
    }),
  );
}

export async function startMissingTrackRequests(
  caseId: string,
  findingIds?: number[],
): Promise<{ started: boolean; operation_id?: string; total?: number; error?: string }> {
  return readJson(
    await fetch(`/api/repair/review-queue/${encodeURIComponent(caseId)}/request-missing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(findingIds ? { finding_ids: findingIds } : {}),
    }),
  );
}

export async function reanalyzeReviewCase(caseId: string, refreshCatalogue = false): Promise<void> {
  await readJson<{ queued: boolean }>(
    await fetch(`/api/repair/review-queue/${encodeURIComponent(caseId)}/reanalyze`, {
      method: 'POST',
      ...(refreshCatalogue
        ? {
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_catalogue: true }),
          }
        : {}),
    }),
  );
}

export async function fetchReviewApplyStatus(operationId?: string): Promise<BulkFixStatus> {
  return fetchBulkFixStatus(readJson<BulkFixStatus>, operationId);
}

export async function resetAlbumScope(caseId: string) {
  return readJson<{ success: boolean }>(
    await fetch(`/api/repair/review-queue/${encodeURIComponent(caseId)}/reset-scope`, {
      method: 'POST',
    }),
  );
}
