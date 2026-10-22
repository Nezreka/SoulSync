/**
 * Every server call the setup flow makes. The read-only ones (environment,
 * discovery, probes, libraries) run in the preview too; anything that saves
 * lives in apply.ts behind the preview switch.
 */

import { apiClient, readJson } from '@/app/api-client';

import type { Environment, PathCheck, ProbeResult, ServerLibraries } from './types';

export function fetchEnvironment(pretend?: 'docker' | 'bare'): Promise<Environment> {
  return readJson(
    apiClient.get('onboarding/environment', pretend ? { searchParams: { pretend } } : {}),
  );
}

export async function discoverServices(services?: string[]): Promise<Record<string, string>> {
  const body = await readJson<{ found: Record<string, string> }>(
    apiClient.post('onboarding/discover', { json: services ? { services } : {}, timeout: 30000 }),
  );
  return body.found ?? {};
}

export async function checkPaths(
  paths: Record<string, string>,
): Promise<Record<string, PathCheck>> {
  const body = await readJson<{ results: Record<string, PathCheck> }>(
    apiClient.post('onboarding/check-paths', { json: { paths } }),
  );
  return body.results ?? {};
}

export async function probeService(
  service: string,
  config: Record<string, unknown>,
): Promise<ProbeResult> {
  try {
    return await readJson<ProbeResult>(
      apiClient.post('onboarding/probe', { json: { service, config }, timeout: 20000 }),
    );
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : 'Check failed' };
  }
}

export async function listLibraries(
  server: string,
  config: Record<string, unknown>,
): Promise<ServerLibraries> {
  try {
    return await readJson<ServerLibraries>(
      apiClient.post('onboarding/libraries', { json: { server, config }, timeout: 30000 }),
    );
  } catch {
    return { music: [], movies: [], tv: [] };
  }
}

export async function topArtists(
  server: string,
  config: Record<string, unknown>,
  libraryId: string,
): Promise<string[]> {
  try {
    const body = await readJson<{ artists: string[] }>(
      apiClient.post('onboarding/top-artists', {
        json: { server, config, library_id: libraryId },
        timeout: 30000,
      }),
    );
    return body.artists ?? [];
  } catch {
    return [];
  }
}

export interface ArtistHit {
  id: string;
  name: string;
  image_url?: string;
}

export async function searchArtists(query: string): Promise<ArtistHit[]> {
  try {
    const body = await readJson<{ artists?: ArtistHit[] }>(
      apiClient.get('discover/build-playlist/search-artists', { searchParams: { query } }),
    );
    return (body.artists ?? []).map((a) => ({ ...a, id: String(a.id) }));
  } catch {
    return [];
  }
}

// ---- sign-ins that save their own session (skipped in the preview) ------

export interface PlexPin {
  request_id: string;
  code: string;
  auth_url: string;
}

export function startPlexPin(): Promise<PlexPin & { success: boolean; error?: string }> {
  return readJson(apiClient.post('plex/pin/start'));
}

export function checkPlexPin(requestId: string): Promise<{
  success: boolean;
  token?: string;
  found_url?: string;
  expired?: boolean;
  error?: string;
}> {
  return readJson(apiClient.get('plex/pin/status', { searchParams: { request_id: requestId } }));
}

export function startTidalLogin(): Promise<{
  verification_uri?: string;
  user_code?: string;
  error?: string;
}> {
  return readJson(apiClient.post('tidal/download/auth/start'));
}

export function checkTidalLogin(): Promise<{ status: string; message?: string }> {
  return readJson(apiClient.get('tidal/download/auth/check'));
}

export async function qobuzLogin(
  email: string,
  password: string,
): Promise<{ success: boolean; error?: string }> {
  try {
    return await readJson(apiClient.post('qobuz/auth/login', { json: { email, password } }));
  } catch (error) {
    return { success: false, error: error instanceof Error ? error.message : 'Login failed' };
  }
}

export function restoreBackup(bundle: unknown): Promise<{ success: boolean; error?: string }> {
  return readJson(apiClient.post('config/import', { json: bundle }));
}
