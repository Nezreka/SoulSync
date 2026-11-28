/**
 * Live TV API client (/api/live/*): HDHomeRun discovery, M3U/XMLTV sources,
 * channels, EPG program windows, and shared HLS tuning.
 */

import { apiClient, readJson } from '@/app/api-client';

export type LiveSourceKind = 'hdhomerun' | 'm3u' | 'xmltv';

export interface LiveSource {
  id: string;
  kind: LiveSourceKind;
  name: string;
  url: string;
  tuner_count: number;
  device_id?: string | null;
  enabled: boolean;
  channel_count?: number;
  last_refresh_at?: number | null;
  last_error?: string | null;
}

export interface LiveChannel {
  id: string;
  source_id: string;
  source_name?: string;
  source_kind?: LiveSourceKind;
  guide_number: string;
  name: string;
  custom_name?: string | null;
  display_name: string;
  stream_url: string;
  logo_url?: string | null;
  group_title?: string | null;
  tvg_id?: string | null;
  epg_id?: string | null;
  effective_epg_id: string;
  enabled: boolean;
}

export interface LiveProgram {
  epg_channel_id: string;
  start_ts: number;
  end_ts: number;
  title: string;
  subtitle?: string | null;
  description?: string | null;
  category?: string | null;
  icon_url?: string | null;
  episode_num?: string | null;
  is_new?: number | boolean;
  is_live?: number | boolean;
  placeholder?: boolean;
}

export interface DiscoveredHdhr {
  friendly_name: string;
  model_number: string;
  firmware_version: string;
  device_id: string;
  device_auth: string;
  base_url: string;
  lineup_url: string;
  tuner_count: number;
}

export interface LiveTuneResult {
  success: boolean;
  error?: string;
  message?: string;
  session?: {
    channel_id: string;
    source_id: string;
    channel_name: string;
    viewers: number;
    playlist_url: string;
  };
  channel?: LiveChannel;
}

export async function fetchLiveSources(): Promise<LiveSource[]> {
  const res = await readJson<{ success?: boolean; sources?: LiveSource[] }>(
    apiClient.get('live/sources'),
  );
  return res.sources ?? [];
}

export async function addLiveSource(body: {
  kind: LiveSourceKind;
  name: string;
  url: string;
  tuner_count?: number;
  device_id?: string;
  device_auth?: string;
}): Promise<{ success: boolean; source?: LiveSource; error?: string }> {
  return readJson(apiClient.post('live/sources', { json: body }));
}

export async function deleteLiveSource(id: string): Promise<{ success: boolean }> {
  return readJson(apiClient.delete(`live/sources/${encodeURIComponent(id)}`));
}

export async function refreshLiveSource(
  id: string,
): Promise<{ success: boolean; added?: number; updated?: number; programs?: number; error?: string }> {
  return readJson(apiClient.post(`live/sources/${encodeURIComponent(id)}/refresh`, { json: {} }));
}

export async function discoverHdHomeRun(
  host?: string,
): Promise<{ success: boolean; devices?: DiscoveredHdhr[]; error?: string }> {
  return readJson(apiClient.post('live/hdhomerun/discover', { json: host ? { host } : {} }));
}

export async function fetchLiveChannels(includeDisabled = false): Promise<LiveChannel[]> {
  const res = await readJson<{ success?: boolean; channels?: LiveChannel[] }>(
    apiClient.get('live/channels', { searchParams: includeDisabled ? { all: '1' } : {} }),
  );
  return res.channels ?? [];
}

export async function updateLiveChannel(
  id: string,
  patch: { enabled?: boolean; custom_name?: string; epg_id?: string; guide_number?: string },
): Promise<{ success: boolean; channel?: LiveChannel }> {
  return readJson(apiClient.patch(`live/channels/${encodeURIComponent(id)}`, { json: patch }));
}

export async function fetchLivePrograms(
  startTs?: number,
  endTs?: number,
): Promise<Record<string, LiveProgram[]>> {
  const params: Record<string, number> = {};
  if (startTs != null) params.start = startTs;
  if (endTs != null) params.end = endTs;
  const res = await readJson<{ success?: boolean; programs?: Record<string, LiveProgram[]> }>(
    apiClient.get('live/programs', { searchParams: params }),
  );
  return res.programs ?? {};
}

export async function tuneLiveChannel(id: string): Promise<LiveTuneResult> {
  return readJson(apiClient.post(`live/tune/${encodeURIComponent(id)}`, { json: {} }));
}

export async function leaveLiveChannel(id: string): Promise<void> {
  await apiClient.post(`live/leave/${encodeURIComponent(id)}`, { json: {} });
}
