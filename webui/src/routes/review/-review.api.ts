import type { BulkFixStatus, RepairFinding } from '../tools/-tools.types';

export interface ReviewPlan {
  ready: number;
  manual: number;
  destructive: number;
  blocked: number;
  total: number;
  status: 'ready' | 'needs_review';
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
  status: 'confirmed' | 'change' | 'uncertain' | 'missing' | 'not_owned' | 'extra' | 'duplicate';
  file_path?: string;
  file?: string;
  finding_id?: number;
  review_tier?: string;
  current_title?: string;
  current_track_number?: number | string | null;
  current_disc_number?: number | string | null;
  slot_title?: string;
  slot_track_number?: number | string | null;
  slot_disc_number?: number | string | null;
  title_difference?: string;
  confidence?: string;
  changes?: Record<string, { old?: unknown; new?: unknown }>;
  audio_title?: string;
  audio_artist?: string;
  audio_score?: number | null;
  duration_delta_ms?: number | null;
  playlists?: string[];
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
  };
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
  }>;
}

export interface ReviewCaseSummary {
  case_id: string;
  album: string | null;
  artist: string | null;
  pending: number;
  priority: number;
  finding_types: string[];
  error_count: number;
  plan?: ReviewPlan;
}

export interface ReviewQueuePage {
  items: ReviewCaseSummary[];
  total: number;
  ready_total?: number;
  page: number;
  limit: number;
}

export interface ReviewFinding extends RepairFinding {
  evidence_token: string;
  updated_at?: string | null;
  review_tier?: 'ready' | 'manual' | 'destructive' | 'blocked';
  review_reason?: string;
}

export interface ReviewCase {
  case_id: string;
  album: string | null;
  artist: string | null;
  folder: string | null;
  count: number;
  status: string;
  album_id?: string | number | null;
  plan?: ReviewPlan;
  decisions?: ReviewDecision[];
  album_plan?: AlbumReconciliationPlan | null;
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
  const body = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

export async function fetchReviewQueue(
  page = 0,
  q = '',
  category = '',
  risk = '',
): Promise<ReviewQueuePage> {
  const params = new URLSearchParams({
    page: String(page),
    q,
    job_id: 'library_reconciliation',
  });
  if (category) params.set('category', category);
  if (risk) params.set('risk', risk);
  return readJson<ReviewQueuePage>(await fetch(`/api/repair/review-queue?${params}`));
}

export async function applyReviewDecisionGroup(
  caseId: string,
  category: string,
  action: 'fix' | 'dismiss',
): Promise<{ success: boolean; applied?: number; total?: number; error?: string }> {
  return readJson(
    await fetch(
      `/api/repair/review-queue/${encodeURIComponent(caseId)}/decisions/${encodeURIComponent(category)}/apply`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      },
    ),
  );
}

export async function fetchReviewCase(caseId: string): Promise<ReviewCase> {
  return readJson<ReviewCase>(
    await fetch(
      `/api/repair/review-queue/${encodeURIComponent(caseId)}?job_id=library_reconciliation`,
    ),
  );
}

export async function runReconciliationScan(): Promise<void> {
  await readJson<{ success: boolean }>(
    await fetch('/api/repair/jobs/library_reconciliation/run', { method: 'POST' }),
  );
}

export async function fetchReconciliationScanStatus(): Promise<ReconciliationScanStatus> {
  return readJson<ReconciliationScanStatus>(await fetch('/api/repair/status'));
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
): Promise<{ success: boolean; message?: string; error?: string; stale?: boolean }> {
  return readJson(
    await fetch(
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
    ),
  );
}

export async function startReadyChanges(
  caseId?: string,
  organize = false,
): Promise<{ started: boolean; total?: number; error?: string; already_running?: boolean }> {
  return readJson(
    await fetch('/api/repair/review-queue/apply-ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_id: caseId || '', organize }),
    }),
  );
}

export async function fetchReviewApplyStatus(): Promise<BulkFixStatus> {
  return readJson<BulkFixStatus>(await fetch('/api/repair/bulk-fix/status'));
}
