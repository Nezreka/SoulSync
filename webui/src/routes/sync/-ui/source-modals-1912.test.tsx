import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SYNC_SOURCES } from '../-sync.sources';
import { useSourceVertical } from '../-sync.use-vertical';
import { SourceModals } from './source-modals';

/**
 * #1912: Download Missing used to skip every track identification hadn't
 * found, without saying so, so a lookup that failed once stayed failed until
 * someone re-identified. Now it asks, and a retry of the misses carries on
 * into the download by itself.
 */

const FOUND = {
  index: 0,
  yt_track: 'Hangin On',
  yt_artist: 'Chris Young',
  status_class: 'found',
  spotify_data: { id: 'dz1', name: "Hangin' On" },
};
const MISSED = {
  index: 1,
  yt_track: 'Just in Case',
  yt_artist: 'Morgan Johnston',
  status_class: 'not_found',
};
const REFOUND = {
  ...MISSED,
  status_class: 'found',
  spotify_data: { id: 'dz2', name: 'Just in Case' },
};

let calls: string[] = [];

function stubFetch() {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      const body = url.includes('retry-failed-discovery')
        ? { success: true, retry_count: 1 }
        : url.includes('/discovery/status/')
          ? {
              phase: 'discovered',
              status: 'discovered',
              complete: true,
              progress: 100,
              spotify_total: 2,
              spotify_matches: 2,
              results: [FOUND, REFOUND],
            }
          : {};
      return new Response(JSON.stringify(body));
    }),
  );
}

function Harness({ results = [FOUND, MISSED] }: { results?: unknown[] }) {
  const vertical = useSourceVertical(SYNC_SOURCES.mirrored);
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          vertical.hydrate('mirrored_7', {
            phase: 'discovered',
            playlist: { name: 'Country', tracks: [{}, {}] },
            spotify_matches: results.filter((r) => (r as { spotify_data?: unknown }).spotify_data)
              .length,
            discovery_results: results,
          });
          setOpenId('mirrored_7');
        }}
      >
        open-it
      </button>
      <SourceModals
        config={SYNC_SOURCES.mirrored}
        vertical={vertical}
        openId={openId}
        onClose={() => setOpenId(null)}
        standalone={false}
        mirroredSource="deezer"
      />
    </div>
  );
}

const engine = vi.fn(async (..._args: unknown[]) => {});

beforeEach(() => {
  stubFetch();
  window.openDownloadMissingModalForYouTube = engine as never;
  window.showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  engine.mockClear();
  delete window.showConfirmDialog;
  delete window.showToast;
  Reflect.deleteProperty(window, 'openDownloadMissingModalForYouTube');
});

function downloadedNames(): string[] {
  const tracks = engine.mock.calls[0][2] as { name: string }[];
  return tracks.map((t) => t.name);
}

describe('Download Missing with tracks identification missed', () => {
  it('asks, retries only the misses, then downloads what is found now', async () => {
    const confirm = vi.fn(async (_options?: Record<string, unknown>) => true);
    window.showConfirmDialog = confirm;
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));

    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(confirm.mock.calls[0][0]).toMatchObject({
      message: expect.stringContaining("1 of 2 tracks aren't identified yet"),
      confirmText: 'Retry, then download',
      cancelText: 'Download the rest',
    });
    // the retry's status poll runs on the mirrored 1s interval
    await waitFor(() => expect(engine).toHaveBeenCalledTimes(1), { timeout: 4000 });
    expect(calls.some((u) => u.includes('/api/mirrored-playlists/7/retry-failed-discovery'))).toBe(
      true,
    );
    // the retried track made it into the download
    expect(downloadedNames()).toEqual(["Hangin' On", 'Just in Case']);
    // and it asked once, not again after the retry
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it('"Download the rest" downloads what is identified, as before', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));
    await waitFor(() => expect(engine).toHaveBeenCalledTimes(1));
    expect(downloadedNames()).toEqual(["Hangin' On"]);
    expect(calls.some((u) => u.includes('retry-failed-discovery'))).toBe(false);
  });

  it('closing the window while it retries drops the download, even once the retry lands', async () => {
    window.showConfirmDialog = vi.fn(async () => true);
    // the retry starts; its status answers only after the window is closed
    let land: () => void = () => {};
    const landed = new Promise<void>((resolve) => {
      land = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('retry-failed-discovery')) {
          return new Response(JSON.stringify({ success: true, retry_count: 1 }));
        }
        if (url.includes('/discovery/status/')) {
          await landed;
          return new Response(
            JSON.stringify({
              phase: 'discovered',
              status: 'discovered',
              complete: true,
              spotify_total: 2,
              spotify_matches: 2,
              results: [FOUND, REFOUND],
            }),
          );
        }
        return new Response('{}');
      }),
    );
    render(<Harness />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));
    await screen.findByText(/Identifying .* matches/);
    fireEvent.click(screen.getByText('🏠 Close'));
    // open it again before the retry lands: closing meant never mind, so the
    // old request must not fire just because the same window is back
    fireEvent.click(screen.getByText('open-it'));
    land();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
    });
    expect(engine).not.toHaveBeenCalled();
  });

  it('nothing missed, nothing asked', async () => {
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm;
    render(<Harness results={[FOUND]} />);
    fireEvent.click(screen.getByText('open-it'));
    fireEvent.click(screen.getByText('🔍 Download Missing Tracks'));
    await waitFor(() => expect(engine).toHaveBeenCalledTimes(1));
    expect(confirm).not.toHaveBeenCalled();
  });
});
