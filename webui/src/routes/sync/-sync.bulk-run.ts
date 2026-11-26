/**
 * Syncing several playlists by hand, one after another (#2309).
 *
 * Two plain choices for the selection: Sync (put the playlists on your
 * server with the songs you already have; nothing is wishlisted or
 * downloaded) and Sync & download (the same, and the missing songs go on
 * the wishlist under your usual rules). Each playlist runs the same pipeline
 * its own button runs; the next starts when the last one has finished, so a
 * long selection never piles every sync on the server at once. Schedules are
 * not touched. Stop lets the one in progress finish and starts no more.
 */

import type { MirroredPipelineState } from './-sync.pipeline';

import { fetchMirroredPipelineStatus, runMirroredPipeline } from './-sync.api';
import { PIPELINE_POLL_MS } from './-sync.pipeline';

export type BulkMode = 'sync' | 'download';
export type BulkItemState = 'waiting' | 'running' | 'done' | 'failed' | 'stopped';

export interface BulkItem {
  id: number;
  name: string;
  state: BulkItemState;
  note?: string;
}

export interface BulkRunState {
  mode: BulkMode;
  items: BulkItem[];
  running: boolean;
  stopping: boolean;
}

export interface BulkDeps {
  start: typeof runMirroredPipeline;
  status: typeof fetchMirroredPipelineStatus;
  wait: (ms: number) => Promise<void>;
  /** at most this many status reads per playlist before giving up on it */
  maxPolls: number;
}

const defaults: BulkDeps = {
  start: runMirroredPipeline,
  status: fetchMirroredPipelineStatus,
  wait: (ms) => new Promise((r) => setTimeout(r, ms)),
  maxPolls: Math.ceil((2 * 60 * 60 * 1000) / PIPELINE_POLL_MS),
};

/** how one playlist's run ended, from its status */
function finished(state: MirroredPipelineState, seenRunning: boolean): BulkItemState | null {
  if (state.status === 'finished') return 'done';
  if (state.status === 'error' || state.status === 'skipped') return 'failed';
  // idle straight after the start is the server not having begun yet
  if (state.status === 'idle' && seenRunning) return 'done';
  return null;
}

/**
 * Run the selection in turn. ``publish`` hears every change; ``stopRequested``
 * is asked between playlists.
 */
export async function runInTurn(
  mode: BulkMode,
  rows: { id: number; name: string }[],
  publish: (state: BulkRunState) => void,
  stopRequested: () => boolean,
  deps: Partial<BulkDeps> = {},
): Promise<BulkRunState> {
  const d = { ...defaults, ...deps };
  let state: BulkRunState = {
    mode,
    running: true,
    stopping: false,
    items: rows.map((r) => ({ id: r.id, name: r.name, state: 'waiting' })),
  };
  const set = (i: number, patch: Partial<BulkItem>) => {
    state = { ...state, items: state.items.map((it, n) => (n === i ? { ...it, ...patch } : it)) };
    publish(state);
  };
  publish(state);
  for (let i = 0; i < state.items.length; i++) {
    if (stopRequested()) {
      for (let j = i; j < state.items.length; j++) set(j, { state: 'stopped' });
      break;
    }
    const item = state.items[i];
    set(i, { state: 'running' });
    try {
      await d.start(item.id, { skipWishlist: mode === 'sync' });
      let seenRunning = false;
      let end: BulkItemState | null = null;
      let last: MirroredPipelineState | null = null;
      for (let poll = 0; poll < d.maxPolls && end === null; poll++) {
        await d.wait(PIPELINE_POLL_MS);
        last = await d.status(item.id);
        if (last.status && last.status !== 'idle') seenRunning = true;
        end = finished(last, seenRunning || poll >= 3);
      }
      set(i, {
        state: end ?? 'failed',
        note:
          end === 'failed'
            ? last?.error || 'It stopped with an error.'
            : end
              ? undefined
              : 'Took too long.',
      });
    } catch (err) {
      set(i, { state: 'failed', note: err instanceof Error ? err.message : 'Could not start.' });
    }
  }
  state = { ...state, running: false };
  publish(state);
  return state;
}

export function bulkSummary(state: BulkRunState): string {
  const count = (s: BulkItemState) => state.items.filter((i) => i.state === s).length;
  const total = state.items.length;
  const done = count('done');
  const failed = count('failed');
  const stopped = count('stopped');
  if (state.running) {
    const at = state.items.findIndex((i) => i.state === 'running');
    return `Syncing ${at + 1} of ${total}`;
  }
  const bits = [`${done} of ${total} synced`];
  if (failed) bits.push(`${failed} failed`);
  if (stopped) bits.push(`${stopped} not started`);
  return bits.join(', ');
}
