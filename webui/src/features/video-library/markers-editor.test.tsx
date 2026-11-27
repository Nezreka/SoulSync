/**
 * the skip marker editor on a title's page: shows where each marker is and
 * who found it, sets one by hand (nudged), removes one, and only for an
 * admin.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MarkersPanel, parseStamp, stamp } from './markers-editor';

const FILE = {
  file_id: 31,
  name: 'Show - S01E02.mkv',
  duration: 1400,
  markers: [
    { kind: 'intro', start: 61.25, end: 151, source: 'detected', confidence: 0.93 },
    { kind: 'credits', start: 1300, end: 1400, source: 'plex' },
  ],
  sources: [
    { kind: 'intro', start: 61.25, end: 151, source: 'detected' },
    { kind: 'credits', start: 1300, end: 1400, source: 'plex' },
  ],
  removed: ['recap'],
  analysis: { status: 'done', attempts: 1 },
  sidecar: { writes: true, written: true, reachable: true },
};

const calls: { url: string; init?: RequestInit }[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(
        JSON.stringify({ success: true, kind: 'episode', id: 9, files: [FILE] }),
        {
          status: 200,
        },
      );
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('times', () => {
  it('stamp and parse round trip', () => {
    expect(stamp(61.25)).toBe('1:01.25');
    expect(stamp(3725.5)).toBe('1:02:05.50');
    expect(stamp(59.999)).toBe('1:00.00');
    expect(parseStamp('1:01.25')).toBe(61.25);
    expect(parseStamp('61.25')).toBe(61.25);
    expect(parseStamp('1:02:05')).toBe(3725);
    expect(parseStamp('soon')).toBeNull();
  });
});

describe('the marker editor', () => {
  it('shows each marker and where it came from', async () => {
    render(<MarkersPanel kind="episode" itemId={9} canManage />);
    expect(await screen.findByText('1:01.25 – 2:31.00')).toBeTruthy();
    expect(screen.getByText('Found by SoulSync')).toBeTruthy();
    expect(screen.getByText('From Plex')).toBeTruthy();
    expect(screen.getByText('Removed')).toBeTruthy();
    expect(calls[0].url).toBe('/api/video/markers/episode/9');
  });

  it('adjusts a marker, nudged, and saves it for the file', async () => {
    render(<MarkersPanel kind="episode" itemId={9} canManage />);
    await screen.findByText('1:01.25 – 2:31.00');
    fireEvent.click(screen.getAllByRole('button', { name: 'Adjust' })[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Starts a tenth later' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ends one second later' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.init?.method === 'PUT')!;
    expect(put.url).toBe('/api/video/markers/episode/9');
    expect(JSON.parse(put.init?.body as string)).toEqual({
      kind: 'intro',
      start: 61.35,
      end: 152,
      file_id: 31,
      scope: 'file',
    });
  });

  it('removes a marker', async () => {
    render(<MarkersPanel kind="episode" itemId={9} canManage />);
    await screen.findByText('1:01.25 – 2:31.00');
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'DELETE')).toBe(true));
    expect(calls.find((c) => c.init?.method === 'DELETE')!.url).toBe(
      '/api/video/markers/episode/9?kind=intro&file_id=31',
    );
  });

  it('a movie shows credits only, and a member sees nothing', async () => {
    const view = render(<MarkersPanel kind="movie" itemId={4} canManage />);
    await screen.findByText('Credits');
    expect(screen.queryByText('Intro')).toBeNull();
    view.unmount();
    calls.length = 0;
    const member = render(<MarkersPanel kind="movie" itemId={4} canManage={false} />);
    expect(member.container.textContent).toBe('');
    expect(calls).toHaveLength(0);
  });
});
