import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { StemName } from '../-sample-studio.types';

import StemsPanel from './stems-panel';

type StemsPayload = { success: boolean; data: unknown; error: string | null };

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function renderPanel(props: {
  trackId: number | null;
  activeStem?: StemName | null;
  onSelectStem?: (stem: StemName | null) => void;
}) {
  const client = makeClient();
  return render(
    <QueryClientProvider client={client}>
      <StemsPanel
        trackId={props.trackId}
        activeStem={props.activeStem ?? null}
        onSelectStem={props.onSelectStem ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

const doneInfo = {
  track_id: 7,
  status: 'done',
  stems: ['drums', 'vocals', 'bass', 'other'],
  backend: 'demucs',
  stems_available: true,
};

describe('StemsPanel', () => {
  let statusBody: unknown;
  let posted: unknown[];

  beforeEach(() => {
    posted = [];
    statusBody = { track_id: 7, status: 'idle', stems: [], stems_available: true };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = input instanceof Request ? input.method : (init?.method ?? 'GET');
        if (
          url.includes('/api/sample/stems') &&
          !url.includes('/status') &&
          !url.includes('/audio')
        ) {
          if (method === 'POST') {
            posted.push(input instanceof Request ? await input.json() : {});
            statusBody = doneInfo;
            return new Response(
              JSON.stringify({ success: true, data: doneInfo, error: null } satisfies StemsPayload),
            );
          }
        }
        if (url.includes('/api/sample/stems/status')) {
          return new Response(
            JSON.stringify({ success: true, data: statusBody, error: null } satisfies StemsPayload),
          );
        }
        return new Response(JSON.stringify({ success: true, data: {}, error: null }), {
          status: 404,
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('offers separation when the track has no stems', async () => {
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Separate stems')).toBeInTheDocument();
    expect(screen.getByText(/Split this track into drums/i)).toBeInTheDocument();
  });

  it('posts separation with method=demucs and shows the four stems with solo/mute', async () => {
    renderPanel({ trackId: 7 });

    fireEvent.click(await screen.findByText('Separate stems'));

    // POST went out with the track id and the method…
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ track_id: 7, method: 'demucs' });

    // …and the panel lands on the four-stem mixer.
    for (const name of ['Drums', 'Vocals', 'Bass', 'Other']) {
      expect(await screen.findByRole('button', { name: `○ ${name}` })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Play all' })).toBeInTheDocument();
    expect(screen.getAllByTitle(/Solo /)).toHaveLength(4);
    expect(screen.getAllByTitle(/Mute /)).toHaveLength(4);
  });

  it('always offers the rough splits — even when Demucs is not installed', async () => {
    statusBody = { track_id: 7, status: 'idle', stems: [], stems_available: false };
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Rough splits (built-in)')).toBeInTheDocument();
    expect(screen.getByText('Drums / Music (rough)')).toBeInTheDocument();
    expect(screen.getByText('Center (rough)')).toBeInTheDocument();
    // The setup note is shown once the status lands, but the rough actions stay available.
    expect(await screen.findByText('Stem separation needs a one-time setup')).toBeInTheDocument();
  });

  it('posts rough-drums with its method', async () => {
    renderPanel({ trackId: 7 });

    fireEvent.click(await screen.findByText('Drums / Music (rough)'));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ track_id: 7, method: 'rough-drums' });
  });

  it('labels rough outputs honestly — never as stems', async () => {
    statusBody = {
      track_id: 7,
      status: 'done',
      method: 'rough-drums',
      stems: ['drums-rough', 'music-rough'],
      labels: { 'drums-rough': 'Drums (rough)', 'music-rough': 'Music (rough)' },
      stems_available: true,
      rough_available: true,
    };
    renderPanel({ trackId: 7 });

    expect(await screen.findByRole('button', { name: '○ Drums (rough)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '○ Music (rough)' })).toBeInTheDocument();
    expect(screen.queryByText(/Rough stems|rough stems/i)).not.toBeInTheDocument();
  });

  it('shows the failed state with a retry button', async () => {
    statusBody = { track_id: 7, status: 'error: disk full', stems: [], stems_available: true };
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Separation failed')).toBeInTheDocument();
    expect(screen.getByText('disk full')).toBeInTheDocument();
    expect(screen.getByText('Try again')).toBeInTheDocument();
  });

  it('shows the setup note instead of the button when stems are unavailable', async () => {
    statusBody = { track_id: 7, status: 'idle', stems: [], stems_available: false };
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Stem separation needs a one-time setup')).toBeInTheDocument();
    expect(screen.queryByText('Separate stems')).not.toBeInTheDocument();
    expect(screen.getAllByText(/pip install demucs/)).toHaveLength(2); // native + Docker steps
  });

  it('shows the setup note instead of retry when a failed track has no backend', async () => {
    statusBody = { track_id: 7, status: 'error: boom', stems: [], stems_available: false };
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Stem separation needs a one-time setup')).toBeInTheDocument();
    expect(screen.queryByText('Try again')).not.toBeInTheDocument();
  });

  it('selecting a stem notifies the parent', async () => {
    statusBody = doneInfo;
    const onSelectStem = vi.fn();
    renderPanel({ trackId: 7, onSelectStem });

    const drumsButton = await screen.findByRole('button', { name: '○ Drums' });
    fireEvent.click(drumsButton);
    await waitFor(() => expect(onSelectStem).toHaveBeenCalledWith('drums'));
  });
});
