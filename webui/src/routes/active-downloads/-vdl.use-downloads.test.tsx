/**
 * Regression tests for the video downloads controller:
 *
 *  - A failed first fetch keeps `loaded` false (the skeleton shimmer) instead
 *    of flashing "No downloads yet" — the old page never rendered on a
 *    failed fetch.
 *  - Video view state (filter / expanded drawers / collapsed groups) survives
 *    unmount/remount via the module-level store — parity with the old page's
 *    module-scope _filter / _expanded / _gcollapse.
 */

import { act, render, screen, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { VdlCardActions } from './-ui/vdl-list';

import { VdlList } from './-ui/vdl-list';
import { useVideoDownloads } from './-vdl.use-downloads';

vi.mock('@/platform/shell/route-controllers', () => ({
  useProfile: () => ({ profileId: 2, isAdmin: true }),
}));

function stubActiveDownloads(handler: () => Response) {
  server.use(http.get('/api/video/downloads/active', handler));
}

const listProps = {
  filter: 'all' as const,
  expanded: new Set<number>(),
  collapsedGroups: new Set<string>(),
  metaFor: () => undefined,
  ensureMeta: () => {},
  onToggleGroup: () => {},
  cardActions: {
    onCancel: () => {},
    onRetry: () => {},
    onBlock: () => {},
    onRemove: () => {},
    onToggle: () => {},
  } as VdlCardActions,
  onCancelGroup: () => {},
};

describe('first-fetch failure keeps the skeleton', () => {
  it('loaded stays false when the first fetch fails, so the list shows the skeleton, not the empty state', async () => {
    stubActiveDownloads(() => HttpResponse.error());
    const { result } = renderHook(() => useVideoDownloads());
    // let the mount tick settle — the failed fetch must never flip `loaded`
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(result.current.loaded).toBe(false);
    expect(result.current.downloads).toHaveLength(0);
  });

  it('loaded flips true once a fetch returns downloads', async () => {
    stubActiveDownloads(() => HttpResponse.json({ downloads: [{ id: 1, status: 'downloading' }] }));
    const { result } = renderHook(() => useVideoDownloads());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.downloads).toHaveLength(1);
  });

  it('the list renders the skeleton, not the empty state, while the first fetch has not settled', () => {
    render(<VdlList {...listProps} downloads={[]} loaded={false} />);
    expect(document.querySelector('[data-vdl-skel]')).not.toBeNull();
    expect(screen.queryByText('No downloads yet')).toBeNull();
  });

  it('the list renders the empty state once loaded with no downloads', () => {
    render(<VdlList {...listProps} downloads={[]} loaded={true} />);
    expect(document.querySelector('[data-vdl-skel]')).toBeNull();
    expect(screen.getByText('No downloads yet')).not.toBeNull();
  });
});

describe('view state survives unmount/remount', () => {
  it('restores filter, expanded drawers and collapsed groups after a remount', async () => {
    stubActiveDownloads(() => HttpResponse.json({ downloads: [] }));
    const first = renderHook(() => useVideoDownloads());
    await waitFor(() => expect(first.result.current.loaded).toBe(true));
    act(() => {
      first.result.current.setFilter('failed');
      first.result.current.toggleExpanded(101);
      first.result.current.toggleGroup('show:456:S3');
    });
    expect(first.result.current.filter).toBe('failed');
    first.unmount();

    const second = renderHook(() => useVideoDownloads());
    expect(second.result.current.filter).toBe('failed');
    expect(second.result.current.expanded.has(101)).toBe(true);
    expect(second.result.current.collapsedGroups.has('show:456:S3')).toBe(true);

    // leave the store clean for any later test in this file
    act(() => {
      second.result.current.setFilter('all');
      second.result.current.toggleExpanded(101);
      second.result.current.toggleGroup('show:456:S3');
    });
    second.unmount();
  });
});
