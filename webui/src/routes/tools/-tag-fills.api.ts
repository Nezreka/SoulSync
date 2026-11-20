/**
 * Tools > Filled Tags (api/repair.py, core/library2/tag_fill.py).
 *
 * With "Fill empty tags automatically" on (Settings > Library >
 * Post-Processing), SoulSync writes what the library knows into the tags a
 * file left blank. Every write is kept with the value before and after, and
 * can be undone one file or one run at a time.
 */

import { readTagChanges, type TagChange } from './-tools.finding-copy';

export type TagFillMode = 'off' | 'fill' | 'update';

export interface TagFillRun {
  batch_id: string;
  /** queue: the worker's own pass. job: a Library Re-tag run */
  source: 'queue' | 'job' | string;
  at: string | null;
  files: number;
  fields: number;
  covers: number;
  undone: number;
}

export interface TagFillUndoState {
  running?: boolean;
  batch_id?: string;
  done?: number;
  total?: number;
  files?: number;
  failed?: number;
  result?: { success: boolean; files?: number; failed?: number; error?: string };
}

export interface TagFillSummary {
  mode: TagFillMode;
  days: number;
  files: number;
  fields: number;
  covers: number;
  runs: TagFillRun[];
  undo: TagFillUndoState;
}

export interface TagFillFile {
  id: number;
  file_id: number | null;
  path: string | null;
  mode: string;
  status: 'filled' | 'undone';
  fields: TagChange[];
  cover: 'added' | 'replaced' | null;
  filled_at: string | null;
  undone_at: string | null;
  undo: { restored?: string[]; kept?: string[] } | null;
}

export interface TagFillResult {
  ok: boolean;
  error?: string;
  kept?: string[];
}

const BASE = '/api/repair/tag-fills';

async function post(path: string, body?: unknown): Promise<TagFillResult> {
  try {
    const response = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    const data = (await response.json().catch(() => ({}))) as {
      error?: string;
      kept?: string[];
    };
    if (response.ok) return { ok: true, kept: data.kept };
    return { ok: false, error: data.error || `HTTP ${response.status}` };
  } catch {
    return { ok: false, error: 'network error' };
  }
}

/** null is "no news": a failed read keeps the card as it was. */
export async function fetchTagFills(timeoutMs = 10000): Promise<TagFillSummary | null> {
  try {
    const response = await fetch(BASE, { signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) return null;
    return readSummary(await response.json());
  } catch {
    return null;
  }
}

const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

/** the reply in the card's shape, whatever came back: an older server or a
 *  proxy page must not break the Tools page */
export function readSummary(raw: unknown): TagFillSummary | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const data = raw as Record<string, unknown>;
  const mode = data.mode === 'off' || data.mode === 'update' ? data.mode : 'fill';
  const runs = Array.isArray(data.runs)
    ? (data.runs as Array<Record<string, unknown>>)
        .filter((run) => run && typeof run.batch_id === 'string')
        .map((run) => ({
          batch_id: run.batch_id as string,
          source: typeof run.source === 'string' ? run.source : 'queue',
          at: typeof run.at === 'string' ? run.at : null,
          files: num(run.files),
          fields: num(run.fields),
          covers: num(run.covers),
          undone: num(run.undone),
        }))
    : [];
  const undo = data.undo && typeof data.undo === 'object' ? (data.undo as TagFillUndoState) : {};
  return {
    mode,
    days: num(data.days) || 7,
    files: num(data.files),
    fields: num(data.fields),
    covers: num(data.covers),
    runs,
    undo,
  };
}

export async function fetchTagFillFiles(run: string): Promise<TagFillFile[] | null> {
  try {
    const response = await fetch(`${BASE}/files?run=${encodeURIComponent(run)}`);
    if (!response.ok) return null;
    const data = (await response.json()) as { files?: unknown };
    if (!Array.isArray(data?.files)) return [];
    return (data.files as Array<Record<string, unknown>>)
      .filter((file) => file && typeof file.id === 'number')
      .map((file) => ({
        ...(file as unknown as TagFillFile),
        fields: readTagChanges(file.fields),
      }));
  } catch {
    return null;
  }
}

export const undoTagFill = (id: number) => post(`/${id}/undo`);
export const undoTagFillRun = (run: string) => post('/undo-run', { run });
export const startTagFill = () => post('/run');

export const MODE_WORDS: Record<TagFillMode, string> = {
  off: 'Off',
  fill: 'Fill empty tags',
  update: 'Fill and update',
};

const count = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** "214 tags in 63 files", or with covers "… and 4 covers" */
export function fillWords(fields: number, files: number, covers = 0): string {
  const tags = `${count(fields, 'tag')} in ${count(files, 'file')}`;
  return covers ? `${tags}, ${count(covers, 'cover')}` : tags;
}

/** one run in a line: when, who, what */
export function runWords(run: TagFillRun): string {
  const when = (run.at || '').slice(0, 16).replace('T', ' ');
  const who = run.source === 'job' ? 'Library Re-tag' : 'on its own';
  if (run.undone >= run.files) return `${when} · ${who} · undone`;
  const what = fillWords(run.fields, run.files, run.covers);
  return `${when} · ${who} · ${what}${run.undone ? ` · ${run.undone.toLocaleString()} undone` : ''}`;
}

/** the file's name, from its library path */
export function fileName(path: string | null): string {
  return (path || '').split(/[\\/]/).pop() || '(unknown file)';
}
