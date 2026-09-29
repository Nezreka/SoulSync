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
};

describe('StemsPanel', () => {
  let statusBody: unknown;
  let posted: unknown[];

  beforeEach(() => {
    posted = [];
    statusBody = { track_id: 7, status: 'idle', stems: [] };
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

  it('posts separation and shows the four stems with solo/mute', async () => {
    renderPanel({ trackId: 7 });

    fireEvent.click(await screen.findByText('Separate stems'));

    // POST went out with the track id…
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ track_id: 7 });

    // …and the panel lands on the four-stem mixer.
    for (const name of ['Drums', 'Vocals', 'Bass', 'Other']) {
      expect(await screen.findByText(name, { exact: false })).toBeInTheDocument();
    }
    expect(screen.getByText('▶ Play all')).toBeInTheDocument();
    expect(screen.getAllByTitle(/Solo /)).toHaveLength(4);
    expect(screen.getAllByTitle(/Mute /)).toHaveLength(4);
  });

  it('shows the failed state with a retry button', async () => {
    statusBody = { track_id: 7, status: 'error: disk full', stems: [] };
    renderPanel({ trackId: 7 });
    expect(await screen.findByText('Separation failed')).toBeInTheDocument();
    expect(screen.getByText('disk full')).toBeInTheDocument();
    expect(screen.getByText('Try again')).toBeInTheDocument();
  });

  it('selecting a stem notifies the parent', async () => {
    statusBody = doneInfo;
    const onSelectStem = vi.fn();
    renderPanel({ trackId: 7, onSelectStem });

    const drumsButton = await screen.findByRole('button', { name: /drums/i });
    fireEvent.click(drumsButton);
    await waitFor(() => expect(onSelectStem).toHaveBeenCalledWith('drums'));
  });
});
