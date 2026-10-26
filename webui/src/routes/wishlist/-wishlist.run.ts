import { useEffect, useState } from 'react';

/**
 * "Download now" (#1822). One click starts the wishlist; progress shows on the
 * page instead of in a modal you have to drive. The old path was Download
 * Wishlist, pick a category, wait for an empty analysis table, Begin Analysis.
 * The endpoint already takes the whole list when no category is sent.
 */

export type WishlistRunScope = 'all' | 'albums' | 'singles';

export interface StartResult {
  batchId: string | null;
  /** the auto run already has the wishlist; the page just watches that one. */
  alreadyRunning: boolean;
}

export async function startWishlistDownload(scope: WishlistRunScope): Promise<StartResult> {
  const response = await fetch('/api/wishlist/download_missing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // no category and no track ids = every wanted track. the manual run always
    // downloads even if the library check thinks a track is owned, so there is
    // no force option to send.
    body: JSON.stringify(scope === 'all' ? {} : { category: scope }),
  });
  if (response.status === 409) return { batchId: null, alreadyRunning: true };
  const data = (await response.json().catch(() => ({}))) as {
    success?: boolean;
    batch_id?: string;
    error?: string;
  };
  if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
  return { batchId: data.batch_id ?? null, alreadyRunning: false };
}

/** stops every batch in the run (the server cancels the album siblings too). */
export async function stopWishlistRun(batchId: string): Promise<void> {
  const response = await fetch(`/api/playlists/${encodeURIComponent(batchId)}/cancel_batch`, {
    method: 'POST',
  });
  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `HTTP ${response.status}`);
  }
}

interface RunStatusPayload {
  phase?: string;
  analysis_progress?: { total?: number; processed?: number };
  tasks?: { status?: string }[];
}

export interface RunSummary {
  stage: 'preparing' | 'downloading' | 'finished' | 'stopped';
  total: number;
  done: number;
  failed: number;
  /** 0..1 for the progress bar. */
  fraction: number;
  label: string;
}

const FAILED = new Set(['failed', 'not_found']);
const SETTLED = new Set(['completed', 'failed', 'not_found', 'cancelled', 'skipped']);

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** what the strip says about a run, from the merged batch status. */
export function summarizeRun(status: RunStatusPayload | null | undefined): RunSummary | null {
  if (!status || !status.phase) return null;
  const phase = status.phase;
  if (phase === 'analysis' || phase === 'queued') {
    const total = status.analysis_progress?.total ?? 0;
    const processed = status.analysis_progress?.processed ?? 0;
    return {
      stage: 'preparing',
      total,
      done: 0,
      failed: 0,
      fraction: total > 0 ? Math.min(1, processed / total) : 0,
      label:
        total > 0 ? `Getting ready · ${processed} of ${plural(total, 'track')}` : 'Getting ready',
    };
  }
  const tasks = status.tasks ?? [];
  const total = tasks.length;
  const done = tasks.filter((t) => t.status === 'completed').length;
  const failed = tasks.filter((t) => FAILED.has(t.status ?? '')).length;
  const settled = tasks.filter((t) => SETTLED.has(t.status ?? '')).length;
  const stage =
    phase === 'cancelled'
      ? 'stopped'
      : phase === 'complete' || phase === 'error'
        ? 'finished'
        : 'downloading';
  const verb =
    stage === 'downloading' ? 'Downloading' : stage === 'stopped' ? 'Stopped' : 'Finished';
  const parts = [`${done} done`];
  if (failed) parts.push(`${failed} failed`);
  return {
    stage,
    total,
    done,
    failed,
    fraction: total > 0 ? settled / total : stage === 'downloading' ? 0 : 1,
    label: total > 0 ? `${verb} ${plural(total, 'track')} · ${parts.join(' · ')}` : `${verb}`,
  };
}

const POLL_MS = 2500;

/** polls the run while there is one. stops on its own when the id goes away. */
export function useWishlistRun(batchId: string | null): RunSummary | null {
  const [summary, setSummary] = useState<RunSummary | null>(null);

  useEffect(() => {
    setSummary(null);
    if (!batchId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const response = await fetch(
          `/api/download_status/batch?batch_ids=${encodeURIComponent(batchId)}`,
        );
        if (!response.ok) return;
        const data = (await response.json()) as { batches?: Record<string, RunStatusPayload> };
        if (!cancelled) setSummary(summarizeRun(data.batches?.[batchId]));
      } catch {
        /* a blip keeps the last summary on screen */
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [batchId]);

  return summary;
}
