/**
 * downloads to this computer: each item is prepared on the server (a
 * converted copy can take a while), polled until it's ready, then handed to
 * the browser. one at a time, kept outside react so a closed sheet keeps
 * going and every download button sees the same progress.
 */

import { useSyncExternalStore } from 'react';

import {
  itemKey,
  type ItemGroup,
  prepare,
  preparedStatus,
  rememberSaved,
  reportThisComputer,
  saveToComputer,
  type ItemType,
  type Prepared,
} from './offline';

export interface LocalJob {
  key: string;
  item_type: ItemType;
  item_id: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  quality: string;
  /** the group it was asked for with ("season:12:2"), so its button shows it */
  group?: string;
  /** that group's name and art, kept with the saved file so the page can file it */
  groupInfo?: ItemGroup | null;
  status: 'waiting' | 'queued' | 'converting' | 'saving' | 'done' | 'failed';
  progress: number;
  error?: string | null;
}

const POLL_MS = 1500;

let jobs: LocalJob[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
let running = false;
let sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

function patch(key: string, next: Partial<LocalJob>) {
  jobs = jobs.map((j) => (j.key === key ? { ...j, ...next } : j));
  emit();
}

async function runOne(job: LocalJob) {
  let p: Prepared = await prepare(job.item_type, job.item_id, job.quality);
  while (p.status === 'queued' || p.status === 'converting') {
    patch(job.key, { status: p.status, progress: Math.max(0, Math.min(1, p.progress || 0)) });
    await sleep(POLL_MS);
    p = await preparedStatus(p.id);
  }
  if (p.status !== 'ready' || !p.url) throw new Error(p.error || "That couldn't be made ready.");
  patch(job.key, { status: 'saving', progress: 1 });
  saveToComputer(p.url, p.filename);
  rememberSaved({
    item_type: job.item_type,
    item_id: job.item_id,
    quality: job.quality,
    size_bytes: p.size_bytes ?? 0,
    title: job.title,
    subtitle: job.subtitle,
    artwork: job.artwork,
    saved_at: new Date().toISOString(),
    group: job.groupInfo ?? null,
  });
}

async function pump() {
  if (running) return;
  running = true;
  let saved = 0;
  try {
    for (;;) {
      const next = jobs.find((j) => j.status === 'waiting');
      if (!next) break;
      patch(next.key, { status: 'queued' });
      try {
        await runOne(next);
        patch(next.key, { status: 'done', progress: 1 });
        saved += 1;
      } catch (e) {
        patch(next.key, {
          status: 'failed',
          error: e instanceof Error ? e.message : "That couldn't be saved.",
        });
      }
    }
  } finally {
    running = false;
    if (saved) void reportThisComputer().catch(() => {});
  }
}

/** start saving these to this computer (one already going is left alone) */
export function saveLocally(
  items: Omit<LocalJob, 'key' | 'status' | 'progress' | 'error'>[],
): void {
  const busy = new Set(
    jobs.filter((j) => j.status !== 'done' && j.status !== 'failed').map((j) => j.key),
  );
  const fresh = items
    .map((it) => ({ ...it, key: itemKey(it.item_type, it.item_id) }))
    .filter((it) => !busy.has(it.key))
    .map((it) => ({ ...it, status: 'waiting' as const, progress: 0, error: null }));
  if (!fresh.length) return;
  const keys = new Set(fresh.map((f) => f.key));
  jobs = [...jobs.filter((j) => !keys.has(j.key)), ...fresh];
  emit();
  void pump();
}

export function clearFinished() {
  jobs = jobs.filter((j) => j.status !== 'done' && j.status !== 'failed');
  emit();
}

export function useLocalJobs(): LocalJob[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => jobs,
    () => jobs,
  );
}

/** tests: no waiting between polls, a clean slate */
export function _resetForTests(fastSleep = true) {
  jobs = [];
  running = false;
  if (fastSleep) sleep = () => Promise.resolve();
  emit();
}
