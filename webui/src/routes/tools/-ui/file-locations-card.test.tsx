/**
 * Update File Locations card: a check groups the moved records by folder,
 * Update sends only the ticked folder pairs, and a run can be undone.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { leftAloneSummary } from '../-file-locations.api';
import { FileLocationsCard } from './file-locations-card';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();

const PREVIEW = {
  checked: 12345,
  total: 12345,
  repointed: 0,
  counts: { repoint: 12340, present: 0, not_found: 3, ambiguous: 2 },
  groups: [
    {
      old_root: '/app/Transfer',
      new_root: '/music',
      count: 12340,
      examples: [{ old: '/app/Transfer/A/B/x.flac', new: '/music/A/B/x.flac' }],
    },
    { old_root: '/old/disk', new_root: '/music/disk', count: 5, examples: [] },
  ],
  other_groups: 0,
};

const RUN = {
  id: 7,
  kind: 'manual',
  status: 'done',
  repointed: 12340,
  groups: [],
  started_at: '2026-11-19 10:00:00',
  finished_at: '2026-11-19 10:05:00',
  undone_at: null,
};

function status(over: Record<string, unknown> = {}) {
  return {
    status: 'done',
    action: 'preview',
    checked: 12345,
    total: 12345,
    result: PREVIEW,
    preview: PREVIEW,
    error: null,
    runs: [],
    ...over,
  };
}

function serve(state: unknown) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (init?.method === 'POST' ? { success: true } : state),
    } as never),
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  confirmSpy.mockReset().mockResolvedValue(true);
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, { showToast: toastSpy, showConfirmDialog: confirmSpy });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function posts() {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === 'POST');
}

describe('FileLocationsCard', () => {
  it('lists each folder pair a check found, with its count', async () => {
    serve(status());
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getByText('/app/Transfer → /music')).toBeTruthy());
    expect(screen.getByText('12,340 files')).toBeTruthy();
    expect(
      screen.getByText(/Left alone: 3 not found anywhere · 2 found in more than one place/),
    ).toBeTruthy();
    expect(screen.getByText('Update 12,345 files')).toBeTruthy();
  });

  it('sends only the ticked folder pairs', async () => {
    serve(status());
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(2));
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByText('Update 12,340 files'));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const [url, init] = posts()[0];
    expect(url).toBe('/api/library/file-locations/apply');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      groups: [{ old_root: '/app/Transfer', new_root: '/music' }],
    });
  });

  it('does nothing when the confirm is cancelled', async () => {
    confirmSpy.mockResolvedValue(false);
    serve(status());
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getByText('Update 12,345 files')).toBeTruthy());
    fireEvent.click(screen.getByText('Update 12,345 files'));
    await act(async () => {});
    expect(posts()).toHaveLength(0);
  });

  it('offers undo on a finished run and posts it', async () => {
    serve(status({ preview: null, result: null, status: 'idle', runs: [RUN] }));
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getByText('Undo')).toBeTruthy());
    fireEvent.click(screen.getByText('Undo'));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/library/file-locations/runs/7/undo');
  });

  it('has no undo for a run already undone', async () => {
    serve(
      status({ preview: null, result: null, status: 'idle', runs: [{ ...RUN, status: 'undone' }] }),
    );
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getByText(/undone/)).toBeTruthy());
    expect(screen.queryByText('Undo')).toBeNull();
  });

  it('starts a check', async () => {
    serve(status({ preview: null, result: null, status: 'idle' }));
    render(<FileLocationsCard />);
    await waitFor(() => expect(screen.getByText('Check Locations')).toBeTruthy());
    fireEvent.click(screen.getByText('Check Locations'));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/library/file-locations/preview');
  });
});

describe('leftAloneSummary', () => {
  it('words the counts, biggest first, and skips zeros', () => {
    expect(leftAloneSummary({ not_found: 1, different_file: 4, ambiguous: 0 })).toEqual([
      '4 a different size than recorded',
      '1 not found anywhere',
    ]);
  });
});
