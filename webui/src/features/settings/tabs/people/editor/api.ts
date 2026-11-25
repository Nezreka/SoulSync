/** the calls the editor makes. errors carry the server's plain words. */

import type {
  Catalogue,
  FeaturesAdminPayload,
  ProfileCard,
  ProfilePermissions,
  QualityOptions,
  SaveBody,
  Template,
} from './types';

export class EditorApiError extends Error {
  status: number;
  code: string;
  constructor(message: string, status: number, code = '') {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch {
    throw new EditorApiError('Connection error. Check the server and try again.', 0);
  }
  const body = (await res.json().catch(() => null)) as
    | (T & { success?: boolean; error?: string; message?: string })
    | null;
  if (!res.ok || !body || body.success === false) {
    const message =
      body?.message ||
      (res.status === 403
        ? 'Only admins can change what people can do.'
        : body?.error || "That didn't work. Try again in a moment.");
    throw new EditorApiError(message, res.status, body?.error || '');
  }
  return body;
}

export const fetchCatalogue = () => call<Catalogue>('/api/permissions/catalogue');
export const fetchTemplates = () =>
  call<{ templates: Template[]; signup_template_id: number | null }>('/api/permissions/templates');
export const fetchQuality = () => call<QualityOptions>('/api/permissions/quality-options');
export const fetchPermissions = (id: number) =>
  call<ProfilePermissions>(`/api/profiles/${id}/permissions`);
export const fetchProfiles = () => call<{ profiles: ProfileCard[] }>('/api/profiles');
export const fetchFeatures = (id: number) =>
  call<FeaturesAdminPayload>(`/api/profiles/${id}/features`);

export const savePermissions = (id: number, body: SaveBody) =>
  call<ProfilePermissions>(`/api/profiles/${id}/permissions`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });

export const saveProfile = (id: number, body: Record<string, unknown>) =>
  call<{ success: boolean }>(`/api/profiles/${id}`, { method: 'PUT', body: JSON.stringify(body) });

export const saveFeatures = (id: number, body: Record<string, unknown>) =>
  call<FeaturesAdminPayload>(`/api/profiles/${id}/features`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
