/**
 * The help routes (P1: core/help, api/help.py; contract in
 * scrap/help/HELP_API.md). Health, bundle and logs are admin only (403 for a
 * member); /status answers a member with {} but the sheet never asks.
 * Errors come back as values, not throws, so a view can say what went
 * wrong in words.
 */

import { appURL } from '@/platform/url-base';

export type Result<T> = { ok: true; data: T } | { ok: false; error: string; status: number };

export async function request<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(appURL(path), {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    return { ok: false, error: "Couldn't reach SoulSync.", status: 0 };
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const said = (body as { error?: unknown } | null)?.error;
    const fallback = response.status === 403 ? 'This is for admins.' : 'Something went wrong.';
    return {
      ok: false,
      error: typeof said === 'string' ? said : fallback,
      status: response.status,
    };
  }
  return { ok: true, data: body as T };
}

// ── health (admin; scrap/help/HELP_API.md) ──────────────────────────────

export type Severity = 'error' | 'warning' | 'info' | 'ok';

/** one button on a finding; the first is the primary one */
export type HealthFix =
  | { label: string; kind: 'post'; url: string; body?: Record<string, unknown>; recheck?: boolean }
  | { label: string; kind: 'settings'; tab: string; media?: 'video' }
  | { label: string; kind: 'page'; path: string; media?: 'video' }
  | { label: string; kind: 'external'; href: string }
  | { label: string; kind: 'checklist'; item: string; href?: string }
  | { label: string; kind: 'logs' }
  | { label: string; kind: 'recheck' };

export type HealthArea =
  | 'services'
  | 'paths'
  | 'disk'
  | 'video'
  | 'workers'
  | 'downloads'
  | 'automations'
  | 'setup'
  | 'updates'
  | 'checks';

export interface HealthFinding {
  /** stable and dotted: 'paths.download', 'workers.lastfm' */
  id: string;
  severity: Severity;
  area: HealthArea;
  title: string;
  detail?: string;
  fix?: HealthFix[];
  /** a docs page id, the finding's Learn more */
  docs?: string | null;
}

export interface HealthReport {
  status: Severity;
  checked_at: string | null;
  age_seconds?: number;
  took_ms?: number;
  counts?: Partial<Record<Severity, number>>;
  summary?: string;
  findings: HealthFinding[];
}

/** what the button's dot and Home's status card read */
export interface HelpStatus {
  /** error findings only (the dot); never warnings */
  errors: number;
  /** known once Health has been read in full, 0 from the poll alone */
  warnings: number;
  checked_at: string | null;
  /** the first run hasn't finished yet */
  refreshing: boolean;
  /** the findings that need the admin, errors then warnings; [] from the poll alone */
  top: { id: string; severity: Severity; title: string }[];
}

const SEVERITIES: Severity[] = ['error', 'warning', 'info', 'ok'];

function asSeverity(value: unknown): Severity {
  return SEVERITIES.includes(value as Severity) ? (value as Severity) : 'ok';
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
}

/** the status from a full report: counts, and the titles for Home's card */
export function statusFromHealth(report: HealthReport): HelpStatus {
  const findings = Array.isArray(report.findings) ? report.findings : [];
  const errors = findings.filter((f) => asSeverity(f.severity) === 'error');
  const warnings = findings.filter((f) => asSeverity(f.severity) === 'warning');
  return {
    errors: report.counts ? num(report.counts.error) : errors.length,
    warnings: report.counts ? num(report.counts.warning) : warnings.length,
    checked_at: report.checked_at ?? null,
    refreshing: false,
    top: [...errors, ...warnings].map((f) => ({
      id: f.id,
      severity: asSeverity(f.severity),
      title: f.title,
    })),
  };
}

/** GET /api/help/status: {errors, checked_at, refreshing} for an admin, {} for a member */
export function normalizeStatus(raw: unknown): HelpStatus | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (!('errors' in r) && !('checked_at' in r)) return null;
  return {
    errors: num(r.errors),
    warnings: 0,
    checked_at: typeof r.checked_at === 'string' ? r.checked_at : null,
    refreshing: r.refreshing === true,
    top: [],
  };
}

/** a poll answer over what Health told us: keep the titles while the count agrees */
export function mergeStatus(prev: HelpStatus | null, next: HelpStatus): HelpStatus {
  if (!prev || prev.errors !== next.errors) return next;
  return { ...prev, checked_at: next.checked_at ?? prev.checked_at, refreshing: next.refreshing };
}

export async function getStatus(signal?: AbortSignal): Promise<Result<HelpStatus | null>> {
  const res = await request<unknown>('/api/help/status', { signal });
  return res.ok ? { ok: true, data: normalizeStatus(res.data) } : res;
}

export function getHealth(fresh = false, signal?: AbortSignal): Promise<Result<HealthReport>> {
  return request<HealthReport>(`/api/help/health${fresh ? '?fresh=1' : ''}`, { signal });
}

// ── version (everyone) ──────────────────────────────────────────────────

export interface UpdateInfo {
  update_available: boolean;
  current_version: string | null;
  latest_version: string | null;
  severity: string | null;
  release_url: string | null;
  is_docker: boolean;
}

export function getUpdateInfo(signal?: AbortSignal): Promise<Result<UpdateInfo>> {
  return request<UpdateInfo>('/api/update-check', { signal });
}

// ── fixes (admin; a Health finding's post button) ───────────────────────

/** only the app's own api, never a url from somewhere else */
export function isApiPath(url: unknown): url is string {
  return typeof url === 'string' && /^\/api\/[\w\-./?=&%]*$/.test(url) && !url.includes('..');
}

export async function postFix(
  url: string,
  body?: Record<string, unknown>,
): Promise<Result<unknown>> {
  if (!isApiPath(url)) return { ok: false, error: "That fix isn't one SoulSync knows.", status: 0 };
  const res = await request<unknown>(url, { method: 'POST', body: JSON.stringify(body ?? {}) });
  if (!res.ok) return res;
  // some routes answer 200 with {success: false, error}
  const data = res.data as { success?: unknown; error?: unknown; message?: unknown } | null;
  if (data && data.success === false) {
    const said = typeof data.error === 'string' ? data.error : data.message;
    return {
      ok: false,
      error: typeof said === 'string' && said ? said : "That didn't work.",
      status: 200,
    };
  }
  return res;
}

// ── report a problem (admin) ────────────────────────────────────────────

export interface BundlePart {
  id: string;
  label: string;
  hint?: string;
  default: boolean;
  included: boolean;
  /** starts with its heading line, '== Version and platform ==' */
  text: string;
  bytes: number;
}

export interface Bundle {
  generated_at: string;
  version: string;
  filename: string;
  parts: BundlePart[];
  text: string;
  bytes: number;
  discord: string;
  github: { url: string; length: number; trimmed?: boolean };
}

export interface BundleRequest {
  /** the part ids to include; left out, the server's defaults */
  parts?: string[];
  note: string;
  where: string;
}

export function getBundle(req: BundleRequest, signal?: AbortSignal): Promise<Result<Bundle>> {
  return request<Bundle>('/api/help/bundle', {
    method: 'POST',
    body: JSON.stringify(req),
    signal,
  });
}

/** the first line of the server's text: 'SoulSync diagnostics, 2026-11-17 21:42 UTC' */
export function bundleHeader(text: string): string {
  return (text || '').split('\n')[0] ?? '';
}

/**
 * The server's join rule (core/help/bundle.py join), so a switch flips the
 * preview at once: header, the note block when there is a note or a where,
 * then each included part, a blank line between, and a newline at the end.
 */
export function joinBundle(
  header: string,
  note: string,
  where: string,
  parts: { included: boolean; text: string }[],
): string {
  let text = header;
  if (note || where) text += '\n\nWhat happened\n' + note + (where ? `\nWhere: ${where}` : '');
  for (const part of parts) if (part.included) text += '\n\n' + part.text;
  return text + '\n';
}

// ── logs (admin) ────────────────────────────────────────────────────────

export interface LogFile {
  name: string;
  bytes: number;
  size?: string;
  last_written: string | null;
  backup: boolean;
}

export interface LogGroup {
  name: string;
  label: string;
  description?: string;
  bytes: number;
  last_written: string | null;
  files: LogFile[];
}

export interface LogsList {
  log_dir: string;
  zip_name: string;
  total_bytes: number;
  logs: LogGroup[];
}

export function getLogs(signal?: AbortSignal): Promise<Result<LogsList>> {
  return request<LogsList>('/api/help/logs', { signal });
}

/** one file ('app.log.2'), or one log ('app') with its backups as a zip */
export function logDownloadURL(name: string, opts: { withBackups?: boolean; raw?: boolean } = {}) {
  const q = new URLSearchParams({ name });
  if (opts.withBackups) q.set('with_backups', '1');
  if (opts.raw) q.set('raw', '1');
  return appURL(`/api/help/logs/download?${q}`);
}

export function allLogsURL(raw = false): string {
  return appURL(`/api/help/logs/download-all${raw ? '?raw=1' : ''}`);
}

// ── saving files ────────────────────────────────────────────────────────

/** a download the server streams: the browser saves it, the page stays */
export function startDownload(url: string, filename = ''): void {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** text the sheet already holds, saved as a file (the bundle preview, as shown) */
export function saveText(text: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  startDownload(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
