/**
 * "Save snapshot" keeps today's beatport list as its own playlist that never
 * refreshes, next to the live mirror that follows the chart.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { isLiveBeatportRef } from '../-beatport.lists';
import { BeatportSnapshotButton } from './beatport-snapshot-button';

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
});

describe('BeatportSnapshotButton', () => {
  it('shows only for lists that change', () => {
    expect(isLiveBeatportRef('hype-10')).toBe(true);
    expect(isLiveBeatportRef('genre:trance-main-floor:7:top-100')).toBe(true);
    expect(isLiveBeatportRef('chart:weekend-picks:905427')).toBe(true);
    expect(isLiveBeatportRef('release:the-jump-ep:7473727')).toBe(false);
    expect(isLiveBeatportRef('a1b2c3d4')).toBe(false);
    const { container } = render(<BeatportSnapshotButton chartRef="release:x:1" />);
    expect(container.innerHTML).toBe('');
  });

  it('saves and says where it went', async () => {
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: init?.body as string });
        return new Response(
          JSON.stringify({ success: true, name: 'Hype Top 10 · 2026-10-16', track_count: 10 }),
          { status: 200 },
        );
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    render(<BeatportSnapshotButton chartRef="hype-10" />);
    fireEvent.click(screen.getByText('📸 Save snapshot'));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Saved "Hype Top 10 · 2026-10-16" (10 tracks) to your mirrored playlists',
        'success',
      ),
    );
    expect(calls).toEqual([
      { url: '/api/beatport/snapshot', body: JSON.stringify({ ref: 'hype-10' }) },
    ]);
  });

  it('shows the error when it fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: false, error: "Couldn't take a snapshot" }), {
            status: 400,
          }),
      ),
    );
    const toast = vi.fn();
    window.showToast = toast;
    render(<BeatportSnapshotButton chartRef="hype-10" />);
    fireEvent.click(screen.getByText('📸 Save snapshot'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Couldn't take a snapshot", 'error'));
  });
});
