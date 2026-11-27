/**
 * the server's video converting, for admins: how many at once, the GPU on,
 * off or auto, what it found, and the test button.
 * /api/player/converting/settings and /test (api/player_stream.py).
 */

import { queryOptions } from '@tanstack/react-query';
import { HTTPError } from 'ky';

import { apiClient } from '@/app/api-client';

export type GpuMode =
  | 'auto'
  | 'off'
  | 'nvenc'
  | 'qsv'
  | 'vaapi'
  | 'videotoolbox'
  | 'amf'
  | 'v4l2m2m';

export interface ConvertingTried {
  key: string;
  label: string;
  works: boolean;
  reason: string;
  device: string | null;
  low_power: boolean;
}

export interface ConvertingTest {
  ok: boolean;
  label?: string;
  hw_decode?: boolean;
  seconds?: number;
  reason?: string;
  note?: string;
}

export interface ConvertingStatus {
  slots: number;
  gpu: GpuMode;
  device: string;
  slots_range: [number, number];
  modes: GpuMode[];
  ffmpeg: boolean;
  in_use: number | null;
  using: { key: string; label: string; device: string | null };
  tried: ConvertingTried[];
  broken: { key: string; label: string; reason: string }[];
  decode_cpu: { key: string; codec: string; bit_depth: number; reason: string }[];
  last_fallback: { at: number; backend: string; what: string; reason: string } | null;
  devices: string[];
  test?: ConvertingTest;
}

export const GPU_NAMES: Record<GpuMode, string> = {
  auto: 'Auto',
  off: 'Off (CPU)',
  nvenc: 'NVIDIA',
  qsv: 'Intel Quick Sync',
  vaapi: 'VA-API',
  videotoolbox: 'Apple',
  amf: 'AMD AMF',
  v4l2m2m: 'Raspberry Pi',
};

export const convertingKey = ['player', 'converting'] as const;

async function call<T>(run: () => Promise<T>, fallback: string): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (error instanceof HTTPError) {
      let words = '';
      try {
        const body = (await error.response.clone().json()) as { message?: unknown };
        words = typeof body.message === 'string' ? body.message : '';
      } catch {
        words = '';
      }
      throw new Error(words || fallback, { cause: error });
    }
    throw error;
  }
}

export const convertingQuery = () =>
  queryOptions({
    queryKey: convertingKey,
    queryFn: () =>
      call(
        () => apiClient.get('player/converting/settings').json<ConvertingStatus>(),
        "Couldn't load the converting settings.",
      ),
    staleTime: 30_000,
  });

export function saveConverting(
  changes: Partial<Pick<ConvertingStatus, 'slots' | 'gpu' | 'device'>>,
) {
  return call(
    () => apiClient.put('player/converting/settings', { json: changes }).json<ConvertingStatus>(),
    "Couldn't save that setting.",
  );
}

export function testGpu() {
  return call(
    () => apiClient.post('player/converting/test', { timeout: 120_000 }).json<ConvertingStatus>(),
    "Couldn't run the test.",
  );
}
