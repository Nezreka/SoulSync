/**
 * a hand-off from another page (the issues "find duplicates" fix) to library
 * maintenance: land on one job's findings, searched for the reported item.
 * used up by the first maintenance surface that mounts; stale after a minute.
 */

export interface FindingsFocus {
  jobId: string;
  query?: string;
}

const STALE_MS = 60_000;
let pending: (FindingsFocus & { at: number }) | null = null;

export function requestFindingsFocus(focus: FindingsFocus): void {
  pending = { jobId: focus.jobId, query: focus.query?.trim() || undefined, at: Date.now() };
}

export function takeFindingsFocus(): FindingsFocus | null {
  const next = pending;
  pending = null;
  if (!next || Date.now() - next.at > STALE_MS) return null;
  return { jobId: next.jobId, query: next.query };
}
