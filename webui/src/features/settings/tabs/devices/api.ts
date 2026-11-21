/**
 * Every route Settings > Devices reads and writes (api/devices.py).
 * Errors come back as values, never throws, so each part can say what
 * happened in words.
 */

import { appURL } from '@/platform/url-base';

export type Result<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

const FALLBACK: Record<number, string> = {
  0: "Couldn't reach SoulSync. Check the connection and try again.",
  403: 'Devices are for admins.',
};

export async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
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
    return { ok: false, status: 0, message: FALLBACK[0] };
  }
  let body: Record<string, unknown> = {};
  try {
    body = ((await response.json()) ?? {}) as Record<string, unknown>;
  } catch {
    body = {};
  }
  if (!response.ok || body.success === false) {
    const message =
      (typeof body.error === 'string' && body.error) ||
      FALLBACK[response.status] ||
      "That didn't work.";
    return { ok: false, status: response.status, message };
  }
  return { ok: true, data: body as T };
}

export interface ServerIdentity {
  lookup_id: string;
  fingerprint: string;
  public_key: string;
}

export const getIdentity = (signal?: AbortSignal) =>
  call<ServerIdentity>('/api/devices/identity', { signal });

const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});

// ── paired devices ──

export type DeviceState = 'live' | 'unprofiled' | 'expired' | 'revoked';

export interface Device {
  id: number;
  name: string;
  platform: string;
  app: string;
  kind: string;
  profile_id: number | null;
  profile_name: string | null;
  created_at: string;
  last_seen_at: string | null;
  last_ip: string | null;
  state: DeviceState;
  pick_seconds_left: number | null;
}

export const getDevices = (signal?: AbortSignal) =>
  call<{ devices: Device[] }>('/api/devices', { signal });

export const revokeDevice = (id: number) =>
  call<unknown>(`/api/devices/${id}`, { method: 'DELETE' });

export const renameDevice = (id: number, name: string) =>
  call<unknown>(`/api/devices/${id}`, json('PATCH', { name }));

// ── invite codes ──

export interface LiveCode {
  id: number;
  note: string | null;
  created_at: string;
  expires_at: number;
  seconds_left: number;
  wrong_tries: number;
}

export interface Address {
  kind: 'local' | 'remote' | 'custom';
  url: string;
}

export interface NewCode {
  id: number;
  code: string;
  code_hash: string;
  expires_at: number;
  seconds_left: number;
  payload: Record<string, unknown>;
  payload_text: string;
  warnings: string[];
}

export const getCodes = (signal?: AbortSignal) =>
  call<{ codes: LiveCode[] }>('/api/devices/codes', { signal });

export const createCode = (note: string) =>
  call<NewCode>('/api/devices/codes', json('POST', { note }));

export const cancelCode = (id: number) =>
  call<unknown>(`/api/devices/codes/${id}`, { method: 'DELETE' });

// ── this server: addresses and reachability ──

export interface Hit {
  ip: string;
  at: number;
  via: 'self' | 'outside';
  from_home: boolean;
}

export interface Probe {
  nonce: string;
  path: string;
  url: string | null;
  seconds_left: number;
  hits: Hit[];
}

export interface Guidance {
  id: string;
  title: string;
  body: string;
}

export interface ServerInfo extends ServerIdentity {
  addresses: Address[];
  remote_url: string | null;
  custom_urls: string[];
  warnings: string[];
  probe: Probe | null;
  guidance: Guidance[];
}

export interface CheckResult {
  ok: boolean;
  code: string;
  words: string;
}

export const getServer = (signal?: AbortSignal) =>
  call<ServerInfo>('/api/devices/server', { signal });

export const saveAddresses = (remote_url: string, custom_urls: string[]) =>
  call<ServerInfo>('/api/devices/addresses', json('PUT', { remote_url, custom_urls }));

export const checkReachability = () =>
  call<{ result: CheckResult | null; probe: Probe; guidance: Guidance[] }>(
    '/api/devices/reachability',
    json('POST', {}),
  );

export const getProbe = (signal?: AbortSignal) =>
  call<{ probe: Probe | null }>('/api/devices/reachability', { signal });

// ── music apps (the Subsonic api at /rest) ──

export interface BridgeServer {
  id: 'plex' | 'jellyfin' | 'navidrome';
  name: string;
  on: boolean;
  active: boolean;
}

export interface MusicApps {
  profiles: { id: number; name: string }[];
  addresses: Address[];
  bridge: BridgeServer[];
  transcode_format: string;
  formats: string[];
  ffmpeg: boolean;
}

export interface NewMusicApp {
  device_id: number;
  name: string;
  server: string;
  addresses: Address[];
  username: string;
  password: string;
  api_key: string;
  qr_text: string;
}

export const getMusicApps = (signal?: AbortSignal) =>
  call<MusicApps>('/api/devices/music-apps', { signal });

export const setUpMusicApp = (profile_id: number, name: string) =>
  call<NewMusicApp>('/api/devices/music-apps', json('POST', { profile_id, name }));

export const saveMusicAppSettings = (body: {
  bridge?: Partial<Record<BridgeServer['id'], boolean>>;
  transcode_format?: string;
}) => call<MusicApps>('/api/devices/music-apps/settings', json('PUT', body));
