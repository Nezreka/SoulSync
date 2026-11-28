import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoveredHdhr, LiveChannel, LiveSource } from '../../-lib/live';

const api = vi.hoisted(() => ({
  sources: [] as LiveSource[],
  channels: [] as LiveChannel[],
  discovered: [] as DiscoveredHdhr[],
  patches: [] as Array<{ id: string; patch: Record<string, unknown> }>,
}));

vi.mock('../../-lib/live', async (orig) => {
  const real = await orig<typeof import('../../-lib/live')>();
  return {
    ...real,
    fetchLiveSources: async () => api.sources,
    fetchLiveChannels: async () => api.channels,
    discoverHdHomeRun: async () => ({ success: true, devices: api.discovered }),
    updateLiveChannel: async (id: string, patch: Record<string, unknown>) => {
      api.patches.push({ id, patch });
      const ch = api.channels.find((c) => c.id === id);
      const updated = { ...(ch as LiveChannel), ...patch };
      api.channels = api.channels.map((c) => (c.id === id ? updated : c));
      return { success: true, channel: updated };
    },
  };
});

import { LiveTvSection } from './live-tv';

afterEach(() => {
  cleanup();
  api.patches = [];
});

describe('LiveTvSection', () => {
  it('lists sources and channels and discovers HDHomeRun tuners', async () => {
    api.sources = [
      {
        id: 'src_1',
        kind: 'hdhomerun',
        name: 'Attic Antenna',
        url: 'http://192.168.1.50',
        tuner_count: 4,
        enabled: true,
        channel_count: 1,
      },
    ];
    api.channels = [
      {
        id: 'ch_1',
        source_id: 'src_1',
        guide_number: '5.1',
        name: 'NBC 5',
        display_name: 'NBC 5',
        stream_url: 'http://192.168.1.50:5004/auto/v5.1',
        tvg_id: '5.1',
        effective_epg_id: '5.1',
        enabled: true,
      },
    ];
    api.discovered = [
      {
        friendly_name: 'HDHomeRun FLEX 4K',
        model_number: 'HDFX-4K',
        firmware_version: '20260101',
        device_id: '10A0B0C0',
        device_auth: 'auth',
        base_url: 'http://192.168.1.51',
        lineup_url: 'http://192.168.1.51/lineup.json',
        tuner_count: 4,
      },
    ];

    render(<LiveTvSection />);
    expect(await screen.findByText('Attic Antenna')).toBeInTheDocument();
    expect(screen.getByText('NBC 5')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Discover HDHomeRun on network' }));
    expect(await screen.findByText('HDHomeRun FLEX 4K')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Enable NBC 5' }));
    await waitFor(() => expect(api.patches).toEqual([{ id: 'ch_1', patch: { enabled: false } }]));
  });
});
