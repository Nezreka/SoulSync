/**
 * the Converting section: what the server converts with, the GPU choice,
 * how many at once, the test button and why anything fell back to the CPU.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ConvertingStatus } from '../../-lib/converting';

const api = vi.hoisted(() => ({
  status: null as unknown as ConvertingStatus,
  saved: [] as unknown[],
  tested: 0,
}));

vi.mock('../../-lib/converting', async (orig) => {
  const real = await orig<typeof import('../../-lib/converting')>();
  return {
    ...real,
    convertingQuery: () => ({
      queryKey: real.convertingKey,
      queryFn: async () => api.status,
    }),
    saveConverting: async (changes: Partial<ConvertingStatus>) => {
      api.saved.push(changes);
      api.status = { ...api.status, ...changes };
      if (changes.gpu === 'off') api.status.using = { key: 'cpu', label: 'CPU', device: null };
      return api.status;
    },
    testGpu: async () => {
      api.tested += 1;
      return {
        ...api.status,
        test: { ok: true, label: 'NVIDIA GPU', hw_decode: true, seconds: 0.6 },
      };
    },
  };
});

import { ConvertingSection, usingWords } from './converting';

function base(): ConvertingStatus {
  return {
    slots: 3,
    gpu: 'auto',
    device: 'auto',
    slots_range: [1, 16],
    modes: ['auto', 'off', 'nvenc', 'qsv', 'vaapi', 'videotoolbox', 'amf', 'v4l2m2m'],
    ffmpeg: true,
    in_use: 1,
    using: { key: 'nvenc', label: 'NVIDIA GPU', device: null },
    tried: [
      {
        key: 'nvenc',
        label: 'NVIDIA GPU',
        works: true,
        reason: '',
        device: null,
        low_power: false,
      },
    ],
    broken: [],
    decode_cpu: [],
    last_fallback: null,
    devices: [],
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConvertingSection />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  api.saved = [];
  api.tested = 0;
});

describe('Converting settings', () => {
  it('says what converts and offers only GPUs the server found', async () => {
    api.status = base();
    mount();
    expect(await screen.findByText('NVIDIA GPU.')).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Auto' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'NVIDIA' })).toBeTruthy();
    // backends never tried on this box aren't offered
    expect(screen.queryByRole('radio', { name: 'Apple' })).toBeNull();
    expect(screen.getByText(/1 running now/)).toBeTruthy();
  });

  it('turning the GPU off and changing how many at once save at once', async () => {
    api.status = base();
    mount();
    fireEvent.click(await screen.findByRole('radio', { name: 'Off (CPU)' }));
    await waitFor(() => expect(api.saved).toEqual([{ gpu: 'off' }]));
    expect(await screen.findByText('The CPU (GPU off).')).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: '6' }));
    await waitFor(() => expect(api.saved).toEqual([{ gpu: 'off' }, { slots: 6 }]));
  });

  it('the test button runs the real pipeline and says how it went', async () => {
    api.status = base();
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Test GPU' }));
    expect(
      await screen.findByText(/Test passed: a 1080p clip decoded, scaled and encoded on the GPU/),
    ).toBeTruthy();
    expect(api.tested).toBe(1);
  });

  it('a GPU that broke says why, and so does a decode moved to the CPU', async () => {
    api.status = {
      ...base(),
      using: { key: 'cpu', label: 'CPU', device: null },
      broken: [{ key: 'vaapi', label: 'VA-API GPU', reason: 'No usable encoding entrypoint' }],
      decode_cpu: [{ key: 'nvenc', codec: 'h264', bit_depth: 10, reason: 'Invalid argument' }],
      last_fallback: { at: 1, backend: 'vaapi', what: 'VA-API GPU stopped working', reason: 'x' },
    };
    mount();
    expect(await screen.findByText('The CPU. No GPU worked.')).toBeTruthy();
    expect(
      screen.getByText(/VA-API GPU is off until tested again: No usable encoding entrypoint/),
    ).toBeTruthy();
    expect(screen.getByText(/10-bit H264 decodes on the CPU: Invalid argument/)).toBeTruthy();
  });

  it('no ffmpeg says so', () => {
    expect(usingWords({ ...base(), ffmpeg: false })).toMatch(/can't find ffmpeg/);
    expect(
      usingWords({
        ...base(),
        using: { key: 'vaapi', label: 'VA-API GPU', device: '/dev/dri/renderD129' },
      }),
    ).toBe('VA-API GPU (/dev/dri/renderD129).');
  });
});
