/**
 * Filled Tags card: what SoulSync filled on its own this week, each run with
 * its files and their tags before and after, and undo per file or per run.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fillWords, readSummary, runWords } from '../-tag-fills.api';
import { TagFillsCard } from './tag-fills-card';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();

const RUN = {
  batch_id: 'queue:2026-11-20T10:00:00.000000Z',
  source: 'queue',
  at: '2026-11-20 10:00:00',
  files: 63,
  fields: 214,
  covers: 0,
  undone: 0,
};

const FILE = {
  id: 5,
  file_id: 1,
  path: '/music/Artist/Album/07 - Track 7.flac',
  mode: 'fill',
  status: 'filled',
  fields: [{ key: 'genre', label: 'Genre', before: '', after: 'Rock' }],
  cover: null,
  filled_at: '2026-11-20 10:00:00',
  undone_at: null,
  undo: null,
};

function summary(over: Record<string, unknown> = {}) {
  return {
    mode: 'fill',
    days: 7,
    files: 63,
    fields: 214,
    covers: 0,
    runs: [RUN],
    undo: {},
    ...over,
  };
}

function serve(state: unknown, files: unknown[] = [FILE]) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () =>
        init?.method === 'POST'
          ? { success: true }
          : url.includes('/files')
            ? { success: true, files }
            : state,
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

const posts = () =>
  fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === 'POST');

describe('the words', () => {
  it('counts tags, files and covers', () => {
    expect(fillWords(214, 63)).toBe('214 tags in 63 files');
    expect(fillWords(1, 1, 2)).toBe('1 tag in 1 file, 2 covers');
  });

  it('says who filled a run and what is left of it', () => {
    expect(runWords(RUN)).toBe('2026-11-20 10:00 · on its own · 214 tags in 63 files');
    expect(runWords({ ...RUN, source: 'job', undone: 3 })).toBe(
      '2026-11-20 10:00 · Library Re-tag · 214 tags in 63 files · 3 undone',
    );
    expect(runWords({ ...RUN, undone: 63 })).toBe('2026-11-20 10:00 · on its own · undone');
  });
});

describe("a reply that is not the card's shape", () => {
  it('reads as an empty summary, never a crash', () => {
    expect(readSummary({ success: true })).toEqual({
      mode: 'fill',
      days: 7,
      files: 0,
      fields: 0,
      covers: 0,
      runs: [],
      undo: {},
    });
    expect(readSummary([])).toBeNull();
    expect(readSummary({ runs: [{ files: 2 }, RUN] })!.runs).toEqual([RUN]);
  });

  it('renders on a server that does not know the route', async () => {
    serve({ success: true });
    render(<TagFillsCard />);
    await waitFor(() =>
      expect(document.getElementById('tag-fills-week')!.textContent).toBe('0 tags in 0 files'),
    );
  });
});

describe('TagFillsCard', () => {
  it('says what was filled this week and the setting', async () => {
    serve(summary());
    render(<TagFillsCard />);
    await waitFor(() =>
      expect(document.getElementById('tag-fills-week')!.textContent).toBe('214 tags in 63 files'),
    );
    expect(document.getElementById('tag-fills-mode')!.textContent).toBe('Fill empty tags');
  });

  it('opens a run to show each file before and after, and undoes one file', async () => {
    serve(summary());
    render(<TagFillsCard />);
    await waitFor(() => expect(screen.getByText('Show')).toBeTruthy());
    fireEvent.click(screen.getByText('Show'));
    await waitFor(() => expect(screen.getByText('07 - Track 7.flac')).toBeTruthy());
    expect(screen.getByText('Rock')).toBeTruthy();
    // the run's undo and the file's
    const undo = screen.getAllByText('Undo');
    expect(undo).toHaveLength(2);
    fireEvent.click(undo[1]);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/repair/tag-fills/5/undo');
  });

  it('undoes a run after asking', async () => {
    serve(summary());
    render(<TagFillsCard />);
    await waitFor(() => expect(screen.getByText('Undo')).toBeTruthy());
    fireEvent.click(screen.getByText('Undo'));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/repair/tag-fills/undo-run');
    expect(JSON.parse((posts()[0][1] as RequestInit).body as string)).toEqual({
      run: RUN.batch_id,
    });
  });

  it('does nothing when the run undo is cancelled', async () => {
    confirmSpy.mockResolvedValue(false);
    serve(summary());
    render(<TagFillsCard />);
    await waitFor(() => expect(screen.getByText('Undo')).toBeTruthy());
    fireEvent.click(screen.getByText('Undo'));
    await act(async () => {});
    expect(posts()).toHaveLength(0);
  });

  it('has no undo for a run already undone, or a file', async () => {
    serve(summary({ runs: [{ ...RUN, undone: 63 }] }), [{ ...FILE, status: 'undone' }]);
    render(<TagFillsCard />);
    await waitFor(() => expect(screen.getByText('Show')).toBeTruthy());
    expect(screen.queryByText('Undo')).toBeNull();
    fireEvent.click(screen.getByText('Show'));
    await waitFor(() => expect(screen.getByText('Undone')).toBeTruthy());
    expect(screen.queryByText('Undo')).toBeNull();
  });

  it('fills now, and not while the setting is off', async () => {
    serve(summary());
    render(<TagFillsCard />);
    const button = () => document.getElementById('tag-fills-now') as HTMLButtonElement;
    await waitFor(() => expect(button().disabled).toBe(false));
    fireEvent.click(button());
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/repair/tag-fills/run');
    cleanup();
    serve(summary({ mode: 'off', runs: [], files: 0, fields: 0 }));
    render(<TagFillsCard />);
    await waitFor(() => expect(screen.getByText(/filed as Library Re-tag findings/)).toBeTruthy());
    expect(button().disabled).toBe(true);
  });
});
