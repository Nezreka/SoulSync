/**
 * Tools > Update File Locations (api/file_locations.py).
 *
 * The music library folder moved (/app/Transfer to /music, say) and the
 * records still name the old one. A preview groups what would move by old
 * and new folder; apply moves the records (never the files); every run can be
 * undone. One job at a time, polled through /status.
 */

export interface FileLocationGroup {
  old_root: string;
  new_root: string;
  count: number;
  examples?: Array<{ old: string; new: string }>;
}

export type FileLocationOutcome =
  | 'repoint'
  | 'present'
  | 'not_found'
  | 'ambiguous'
  | 'different_file'
  | 'unverified'
  | 'outside_library'
  | 'partial_match'
  | 'claimed'
  | 'old_root_live';

export interface FileLocationWalk {
  checked: number;
  total: number;
  repointed: number;
  counts: Partial<Record<FileLocationOutcome, number>>;
  groups: FileLocationGroup[];
  other_groups: number;
  stopped?: boolean;
  run_id?: number;
}

export interface FileLocationRun {
  id: number;
  kind: 'manual' | 'scan' | 'watch';
  status: 'running' | 'done' | 'stopped' | 'undone';
  repointed: number;
  groups: FileLocationGroup[];
  started_at: string | null;
  finished_at: string | null;
  undone_at: string | null;
}

export interface FileLocationState {
  status: 'idle' | 'running' | 'done' | 'error';
  action: 'preview' | 'apply' | 'undo' | null;
  checked: number;
  total: number;
  result: (FileLocationWalk & { restored?: number; skipped?: number }) | null;
  preview: FileLocationWalk | null;
  error: string | null;
  runs?: FileLocationRun[];
}

export interface StartResult {
  ok: boolean;
  error?: string;
}

const BASE = '/api/library/file-locations';

async function post(path: string, body?: unknown): Promise<StartResult> {
  try {
    const response = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (response.ok) return { ok: true };
    const data = (await response.json().catch(() => ({}))) as { error?: string };
    return { ok: false, error: data.error || `HTTP ${response.status}` };
  } catch {
    return { ok: false, error: 'network error' };
  }
}

/** null is "no news": a failed poll keeps the card as it was. */
export async function fetchFileLocationStatus(
  timeoutMs = 10000,
): Promise<FileLocationState | null> {
  try {
    const response = await fetch(`${BASE}/status`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) return null;
    return (await response.json()) as FileLocationState;
  } catch {
    return null;
  }
}

export const startFileLocationPreview = () => post('/preview');

/** `groups` limits the move to the folder pairs ticked in the preview. */
export const startFileLocationApply = (
  groups: Array<Pick<FileLocationGroup, 'old_root' | 'new_root'>>,
) => post('/apply', { groups });

export const undoFileLocationRun = (runId: number) => post(`/runs/${runId}/undo`);

export const stopFileLocationJob = () => post('/stop');

/** what the preview left alone, in words, biggest first. */
export function leftAloneSummary(counts: FileLocationWalk['counts']): string[] {
  const words: Array<[FileLocationOutcome, string]> = [
    ['not_found', 'not found anywhere'],
    ['ambiguous', 'found in more than one place'],
    ['different_file', 'a different size than recorded'],
    ['unverified', 'no recorded size to check'],
    ['outside_library', 'only outside the library folders'],
    ['partial_match', 'only a filename match'],
    ['claimed', 'already held by another record'],
    ['old_root_live', 'old folder still has files'],
  ];
  return words
    .map(([key, label]) => [counts[key] ?? 0, label] as const)
    .filter(([n]) => n > 0)
    .sort((a, b) => b[0] - a[0])
    .map(([n, label]) => `${n.toLocaleString()} ${label}`);
}
