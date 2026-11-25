/**
 * Every route Settings > People reads and writes. Errors come back as
 * values, never throws, so each screen can say what happened in words:
 * P0's routes answer {success: false, error: <code>, message: <words>}.
 */

import { appURL } from '@/platform/url-base';

import type {
  AuditEntry,
  Catalogue,
  FeatureCatalogue,
  Impact,
  Invite,
  PermMap,
  PermValue,
  Person,
  PersonPerms,
  QualityOptions,
  Sidebar,
  Template,
} from './types';

export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; code: string; message: string };

const FALLBACK: Record<number, string> = {
  0: "Couldn't reach SoulSync. Check the connection and try again.",
  403: 'People and access are for admins.',
  503: "Permissions can't be read right now, so nothing was changed.",
};

async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(appURL(path), {
      credentials: 'same-origin',
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    return { ok: false, status: 0, code: 'network', message: FALLBACK[0] };
  }
  let body: Record<string, unknown> = {};
  try {
    body = ((await response.json()) ?? {}) as Record<string, unknown>;
  } catch {
    body = {};
  }
  if (!response.ok || body.success === false) {
    const code = typeof body.error === 'string' ? body.error : 'error';
    const message =
      (typeof body.message === 'string' && body.message) ||
      FALLBACK[response.status] ||
      (code !== 'error' && !/^[a-z_]+$/.test(code) ? code : "That didn't work.");
    return { ok: false, status: response.status, code, message };
  }
  return { ok: true, data: body as T };
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});

// ── reads ──

export const getCatalogue = (signal?: AbortSignal) =>
  call<Catalogue>('/api/permissions/catalogue', { signal });

export const getQualityOptions = (signal?: AbortSignal) =>
  call<QualityOptions>('/api/permissions/quality-options', { signal });

export const getPolicy = (signal?: AbortSignal) =>
  call<{ policy: PermMap; changed: string[] }>('/api/permissions/policy', { signal });

export const getTemplates = (signal?: AbortSignal) =>
  call<{ templates: Template[]; signup_template_id: number | null }>('/api/permissions/templates', {
    signal,
  });

export const getProfiles = (signal?: AbortSignal) =>
  call<{ profiles: Person[] }>('/api/profiles', { signal });

export const getPersonPerms = (id: number, signal?: AbortSignal) =>
  call<PersonPerms>(`/api/profiles/${id}/permissions`, { signal });

export const getInvites = (signal?: AbortSignal) =>
  call<{ invites: Invite[] }>('/api/profiles/invites', { signal });

export const getAudit = (limit: number, offset: number, signal?: AbortSignal) =>
  call<{ entries: AuditEntry[] }>(`/api/profiles/audit?limit=${limit}&offset=${offset}`, {
    signal,
  });

export const getFeatureCatalogue = (signal?: AbortSignal) =>
  call<FeatureCatalogue>('/api/profiles/feature-defaults', { signal });

/** asks spent per member, per limited kind (music, movies, tv each count on their own) */
export interface AsksUsed {
  kind?: 'music' | 'movies' | 'tv';
  limit: number;
  days: number;
  used: number;
  remaining: number;
}
export const getAsksUsed = (signal?: AbortSignal) =>
  call<{ used: Record<string, Partial<Record<'music' | 'movies' | 'tv', AsksUsed>>> }>(
    '/api/permissions/asks-used',
    { signal },
  );

/** requests waiting for an admin: music (and music videos) plus movies & tv.
 *  null when neither answered, so the hero leaves the clause out */
export async function getWaiting(signal?: AbortSignal): Promise<number | null> {
  const [music, video] = await Promise.all([
    call<{ pending?: number }>('/api/requests/music/counts', { signal }),
    call<{ pending?: number }>('/api/video/requests/counts', { signal }),
  ]);
  if (!music.ok && !video.ok) return null;
  return (music.ok ? music.data.pending || 0 : 0) + (video.ok ? video.data.pending || 0 : 0);
}

// ── writes ──

export interface PolicyBody {
  set?: PermMap;
  reset?: string[] | 'all';
}

export const putPolicy = (body: PolicyBody, dryRun: boolean) =>
  call<{ policy: PermMap; impact: Impact; dry_run: boolean }>(
    `/api/permissions/policy${dryRun ? '?dry_run=1' : ''}`,
    json('PUT', body),
  );

export interface TemplateBody {
  name?: string;
  description?: string;
  perms?: PermMap;
  reset?: string[] | 'all';
  sidebar?: Sidebar;
}

export const putTemplate = (id: number, body: TemplateBody, dryRun = false) =>
  call<{ template: Template; impact: Impact | null; dry_run: boolean }>(
    `/api/permissions/templates/${id}${dryRun ? '?dry_run=1' : ''}`,
    json('PUT', body),
  );

export const createTemplate = (body: {
  name: string;
  description?: string;
  from_template_id?: number;
  perms?: Record<string, PermValue>;
  sidebar?: Sidebar;
}) => call<{ template: Template }>('/api/permissions/templates', json('POST', body));

export const deleteTemplate = (id: number, reassignTo?: number) =>
  call<{ deleted: number; moved: Array<{ id: number; name: string }> }>(
    `/api/permissions/templates/${id}${reassignTo ? `?reassign_to=${reassignTo}` : ''}`,
    { method: 'DELETE' },
  );

export const putSignupTemplate = (templateId: number) =>
  call<{ signup_template_id: number }>(
    '/api/permissions/signup-template',
    json('PUT', { template_id: templateId }),
  );

export const revokeInvite = (id: number) =>
  call<{ success: boolean }>(`/api/profiles/invites/${id}`, { method: 'DELETE' });
