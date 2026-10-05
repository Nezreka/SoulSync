import type { RepairFinding } from '../tools/-tools.types';

export interface ReviewCaseSummary {
  case_id: string;
  album: string | null;
  artist: string | null;
  pending: number;
  priority: number;
  finding_types: string[];
  error_count: number;
}

export interface ReviewQueuePage {
  items: ReviewCaseSummary[];
  total: number;
  page: number;
  limit: number;
}

export interface ReviewFinding extends RepairFinding {
  evidence_token: string;
  updated_at?: string | null;
}

export interface ReviewCase {
  case_id: string;
  album: string | null;
  artist: string | null;
  folder: string | null;
  count: number;
  status: string;
  items: ReviewFinding[];
}

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

export async function fetchReviewQueue(page = 0, q = ''): Promise<ReviewQueuePage> {
  const params = new URLSearchParams({ page: String(page), q });
  return readJson<ReviewQueuePage>(await fetch(`/api/repair/review-queue?${params}`));
}

export async function fetchReviewCase(caseId: string): Promise<ReviewCase> {
  return readJson<ReviewCase>(
    await fetch(`/api/repair/review-queue/${encodeURIComponent(caseId)}`),
  );
}

export async function runReconciliationScan(): Promise<void> {
  await readJson<{ success: boolean }>(
    await fetch('/api/repair/jobs/library_reconciliation/run', { method: 'POST' }),
  );
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
