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
