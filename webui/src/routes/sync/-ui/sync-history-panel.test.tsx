/**
 * The sync history list.
 *
 * The port's one behavioural change is that live progress is state, not writes
 * into six ids per row — so the thing worth pinning is that a running row keeps
 * its drawer across a re-render, which is exactly what the vanilla lost.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { SyncHistoryEntry } from '../-sync.history';

import { SyncHistoryPanel, type SyncHistoryPanelProps } from './sync-history-panel';

const NOW = Date.parse('2026-08-21T12:00:00Z');

function entry(over: Partial<SyncHistoryEntry> = {}): SyncHistoryEntry {
  return {
    id: 7,
    playlist_name: 'Road Trip',
    source: 'spotify',
    started_at: '2026-08-21T10:00:00Z',
    completed_at: '2026-08-21T10:05:00Z',
    tracks_found: 12,
    ...over,
  };
}

function renderPanel(over: Partial<SyncHistoryPanelProps> = {}) {
  const props: SyncHistoryPanelProps = {
    entries: [entry()],
    stats: { spotify: 3, tidal: 1 },
    total: 1,
    page: 1,
    pageSize: 20,
    source: null,
    loading: false,
    error: '',
    resyncs: {},
    onSelectSource: vi.fn(),
    onPage: vi.fn(),
    onResync: vi.fn(),
    onCancel: vi.fn(),
    onDelete: vi.fn(),
    now: NOW,
    ...over,
  };
  return { props, ...render(<SyncHistoryPanel {...props} />) };
}

describe('the list', () => {
  it('renders a row with its name, stats and source', () => {
    const { container } = renderPanel();
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
    expect(screen.getByText('12 found')).toBeInTheDocument();
    expect(container.querySelector('.sync-history-source-badge')?.textContent).toBe('spotify');
  });

  it('falls back to a source mark when a row has no artwork', () => {
    const { container } = renderPanel();
    expect(container.querySelector('.sync-history-thumb-placeholder')).not.toBeNull();
    expect(container.querySelector('.sync-history-thumb')).toBeNull();
  });

  it('uses the stored artwork when there is some', () => {
    const { container } = renderPanel({ entries: [entry({ thumb_url: '/art.jpg' })] });
    expect(container.querySelector('.sync-history-thumb')?.getAttribute('src')).toBe('/art.jpg');
  });

  it('says the history is empty rather than showing a blank pane', () => {
    renderPanel({ entries: [], stats: {} });
    expect(screen.getByText(/No sync history yet/)).toBeInTheDocument();
  });

  it('shows a load failure instead of pretending the history is empty', () => {
    renderPanel({ entries: [], error: 'Error loading sync history' });
    expect(screen.getByText('Error loading sync history')).toBeInTheDocument();
    expect(screen.queryByText(/No sync history yet/)).toBeNull();
  });

  it('shows loading over the previous rows', () => {
    renderPanel({ loading: true });
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('Road Trip')).toBeNull();
  });
});

describe('the source tabs', () => {
  it('offers All plus each source, busiest first', () => {
    const { container } = renderPanel();
    const tabs = [...container.querySelectorAll('.sync-history-tab')].map((t) => t.textContent);
    expect(tabs[0]).toContain('All');
    expect(tabs[1]).toContain('Spotify');
  });

  it('marks the active tab for assistive tech, not only with a class', () => {
    const { container } = renderPanel({ source: 'spotify' });
    const active = container.querySelector('[aria-selected="true"]');
    expect(active?.textContent).toContain('Spotify');
  });

  it('picking one asks the caller, passing null for All', () => {
    const { props, container } = renderPanel({ source: 'spotify' });
    fireEvent.click(container.querySelector('.sync-history-tab') as HTMLElement);
    expect(props.onSelectSource).toHaveBeenCalledWith(null);
  });
});

describe('pagination', () => {
  it('is absent when everything fits on one page', () => {
    const { container } = renderPanel({ total: 5 });
    expect(container.querySelector('.sync-history-pagination')).toBeNull();
  });

  it('appears with more than a page, and disables the ends', () => {
    const { container } = renderPanel({ total: 41, page: 1 });
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument();
    const [prev] = container.querySelectorAll('.sync-history-page-btn');
    expect((prev as HTMLButtonElement).disabled).toBe(true);
  });

  it('Next asks for the following page', () => {
    const { props } = renderPanel({ total: 41, page: 2 });
    fireEvent.click(screen.getByText('Next'));
    expect(props.onPage).toHaveBeenCalledWith(3);
  });
});

describe('the row actions', () => {
  it('re-syncs and deletes through the caller', () => {
    const { props } = renderPanel();
    fireEvent.click(screen.getByText('Re-sync'));
    expect(props.onResync).toHaveBeenCalledWith(7);
    fireEvent.click(screen.getByLabelText('Delete Road Trip from history'));
    expect(props.onDelete).toHaveBeenCalledWith(7);
  });

  it('a running row shows its progress and disables re-sync', () => {
    const { container } = renderPanel({
      resyncs: {
        7: {
          syncPlaylistId: 'resync_7_1',
          progress: {
            percent: 40,
            step: 'Matching — Sexy Boy',
            matched: 2,
            failed: 0,
            total: 5,
            phase: 'running',
          },
        },
      },
    });
    expect(
      (container.querySelector('.sync-history-progress-bar-fill') as HTMLElement).style.width,
    ).toBe('40%');
    expect(screen.getByText('Matching — Sexy Boy')).toBeInTheDocument();
    expect((screen.getByText('Syncing…') as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers Cancel only while the run is still going', () => {
    const running = {
      7: {
        syncPlaylistId: 'x',
        progress: {
          percent: 10,
          step: 'Working',
          matched: 0,
          failed: 0,
          total: 5,
          phase: 'running' as const,
        },
      },
    };
    const { props } = renderPanel({ resyncs: running });
    fireEvent.click(screen.getByText('Cancel'));
    expect(props.onCancel).toHaveBeenCalledWith(7);
  });

  it('drops Cancel once the run has ended — there is nothing left to cancel', () => {
    renderPanel({
      resyncs: {
        7: {
          syncPlaylistId: 'x',
          progress: {
            percent: 100,
            step: 'Sync complete — 5/5 matched, 5 synced',
            matched: 5,
            failed: 0,
            total: 5,
            phase: 'finished',
          },
        },
      },
    });
    expect(screen.queryByText('Cancel')).toBeNull();
  });
});
