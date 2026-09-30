import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

const track = {
  id: 7,
  title: 'Test Track',
  artist_name: 'Test Artist',
  album_title: 'Test Album',
  duration: 180,
  file_path: '/music/test.flac',
  bitrate: 900,
  bpm: 120,
};

const peaks = {
  buckets: 1500,
  duration_s: 180,
  min: Array.from({ length: 1500 }, () => -0.5),
  max: Array.from({ length: 1500 }, () => 0.5),
};

const analysis = {
  track_id: 7,
  status: 'done',
  bpm: 120,
  onsets: [0.5, 1.0, 1.5],
  duration_s: 180,
};

function stubFetch(analysisBody: unknown, analysisStatus = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      let body: unknown = {};
      let status = 200;
      if (url.includes('/api/library/recently-added')) {
        body = { success: true, data: { items: [track], type: 'tracks' } };
      } else if (url.includes('/api/library/tracks')) {
        body = { success: true, data: { tracks: [track] } };
      } else if (url.includes('/api/sample/peaks')) {
        body = { success: true, data: peaks };
      } else if (url.includes('/api/sample/analysis')) {
        body = { success: true, data: analysisBody };
        status = analysisStatus;
      }
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderRoute(entries = ['/sample-studio']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: entries });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

describe('sample-studio route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    stubFetch(analysis);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SoulSyncWebShellBridge;
  });

  it('is registered as a React shell route', async () => {
    renderRoute();
    await waitFor(() => {
      expect(screen.getByText('Library')).toBeInTheDocument();
    });
  });

  it('renders the three-column shell with the empty editor state', async () => {
    renderRoute();
    await waitFor(() => {
      expect(screen.getByText('Your library is the sample pack.')).toBeInTheDocument();
      expect(screen.getByText('Sample Stash')).toBeInTheDocument();
    });
    expect(await screen.findByText('Test Track')).toBeInTheDocument();
  });

  it('shows the staged analysis-pending state when the backend returns 202', async () => {
    stubFetch({ track_id: 7, status: 'queued' }, 202);
    renderRoute();

    await waitFor(() => expect(screen.getByText('Test Track')).toBeInTheDocument());
    screen.getByText('Test Track').click();

    // Peaks resolve from the stub, so the staged copy acknowledges the
    // waveform is up while tempo/chops are still being found.
    await waitFor(() => {
      expect(screen.getByText(/Waveform’s up — finding the tempo/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Waveform’s ready — finding the tempo/)).toBeInTheDocument();
  });

  it('shows the analysis error banner with a working Try again', async () => {
    const analysisCalls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes('/api/sample/analysis')) analysisCalls.push(url);
      return stubbedResponse(url, { track_id: 7, status: 'error: disk full' }, 200);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderRoute();

    await waitFor(() => expect(screen.getByText('Test Track')).toBeInTheDocument());
    screen.getByText('Test Track').click();

    await waitFor(() => {
      expect(screen.getByText(/Couldn't analyze this track/)).toBeInTheDocument();
    });
    expect(screen.getByText(/disk full/)).toBeInTheDocument();

    const before = analysisCalls.length;
    screen.getByRole('button', { name: 'Try again' }).click();
    await waitFor(() => {
      expect(analysisCalls.length).toBeGreaterThan(before);
    });
  });

  it('opens the keyboard shortcut overlay with ?', async () => {
    renderRoute();
    await waitFor(() => expect(screen.getByText('Test Track')).toBeInTheDocument());
    screen.getByText('Test Track').click();

    await waitFor(() => expect(screen.getByText('Sample Stash')).toBeInTheDocument());
    const editorBody = document.querySelector('[tabindex="0"]');
    expect(editorBody).not.toBeNull();
    fireEvent.keyDown(editorBody!, { key: '?' });

    expect(await screen.findByText('Keyboard shortcuts')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Close shortcuts|Close/ }));
    await waitFor(() => {
      expect(screen.queryByText('Keyboard shortcuts')).not.toBeInTheDocument();
    });
  });

  it('debounces the library search before hitting the API', async () => {
    const searched: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes('/api/library/tracks')) searched.push(url);
        return stubbedResponse(url, analysis, 200);
      }),
    );
    renderRoute();
    await waitFor(() => expect(screen.getByText('Test Track')).toBeInTheDocument());

    const input = screen.getByLabelText('Search library');
    fireEvent.change(input, { target: { value: 'r' } });
    fireEvent.change(input, { target: { value: 'ro' } });
    fireEvent.change(input, { target: { value: 'roc' } });
    // The 300ms debounce timer cannot have fired synchronously.
    expect(searched).toHaveLength(0);

    // After the debounce elapses, exactly one search fires — for the final query.
    await waitFor(() => expect(searched).toHaveLength(1), { timeout: 5000 });
    expect(searched[0]).toContain('q=roc');
  });
});

function stubbedResponse(url: string, analysisBody: unknown, analysisStatus = 200) {
  let body: unknown = {};
  let status = 200;
  if (url.includes('/api/library/recently-added')) {
    body = { success: true, data: { items: [track], type: 'tracks' } };
  } else if (url.includes('/api/library/tracks')) {
    body = { success: true, data: { tracks: [track] } };
  } else if (url.includes('/api/sample/peaks')) {
    body = { success: true, data: peaks };
  } else if (url.includes('/api/sample/analysis')) {
    body = { success: true, data: analysisBody };
    status = analysisStatus;
  }
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
