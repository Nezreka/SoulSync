/**
 * The timeline's "drop to schedule" hint is a fixed affordance at the right
 * edge — it must get out of the way when a scheduled node sits there, and it
 * is pointless when there is nothing unscheduled to drag.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { OverviewTimelineEntry } from './use-overview-model';

import { clusterTimeline, OverviewTimeline } from './overview-timeline';

function entry(id: number, hoursFromNow: number): OverviewTimelineEntry {
  return {
    row: { id, name: `Playlist ${id}`, source: 'spotify' } as OverviewTimelineEntry['row'],
    nextRunMs: Date.now() + hoursFromNow * 3600 * 1000,
    cadenceLabel: 'Every 1 day',
  };
}

function renderTimeline(scheduled: OverviewTimelineEntry[], unscheduledCount: number) {
  const unscheduled = Array.from({ length: unscheduledCount }, (_, i) => ({
    id: 100 + i,
    name: `Unscheduled ${i}`,
  })) as OverviewTimelineEntry['row'][];
  render(
    <OverviewTimeline
      scheduled={scheduled}
      scheduledLater={0}
      unscheduled={unscheduled}
      now={Date.now()}
      onOpenPlaylist={vi.fn()}
      onScheduleDrop={vi.fn()}
    />,
  );
}

describe('OverviewTimeline drop hint', () => {
  it('shows the hint when there is room and something to drag', () => {
    renderTimeline([entry(1, 2)], 2);
    expect(screen.getByText('drop to schedule')).toBeDefined();
  });

  it('hides the hint when a node sits at the right edge', () => {
    // 47h of a 48h window -> pct ~97, under the fixed hint.
    renderTimeline([entry(1, 47)], 2);
    expect(screen.queryByText('drop to schedule')).toBeNull();
  });

  it('hides the hint when there is nothing unscheduled', () => {
    renderTimeline([entry(1, 2)], 0);
    expect(screen.queryByText('drop to schedule')).toBeNull();
  });

  it('renders unscheduled chips as keyboard-focusable buttons', () => {
    renderTimeline([], 2);
    const chips = screen.getAllByRole('button', { name: /Unscheduled/ });
    expect(chips).toHaveLength(2);
    // The button is the keyboard path: Enter opens the schedule menu.
    chips[0].click();
  });
});

const NOW = 1_700_000_000_000;

function fixedEntry(id: number, hoursFromNow: number): OverviewTimelineEntry {
  return {
    row: { id, name: `Playlist ${id}`, source: 'spotify' } as OverviewTimelineEntry['row'],
    nextRunMs: NOW + hoursFromNow * 3600 * 1000,
    cadenceLabel: 'Every 1 day',
  };
}

describe('clusterTimeline', () => {
  it('merges entries whose 128px nodes would overlap', () => {
    // 960px track -> node is 13.3% wide; 12h/13h/14h sit at 25/27.1/29.2%.
    const clusters = clusterTimeline(
      [fixedEntry(1, 12), fixedEntry(2, 13), fixedEntry(3, 14)],
      NOW,
      960,
    );
    expect(clusters).toHaveLength(1);
    expect(clusters[0].entries.map((e) => e.row.id)).toEqual([1, 2, 3]);
  });

  it('keeps well-separated entries apart', () => {
    const clusters = clusterTimeline([fixedEntry(1, 2), fixedEntry(2, 30)], NOW, 960);
    expect(clusters).toHaveLength(2);
    expect(clusters[0].entries).toHaveLength(1);
    expect(clusters[1].entries).toHaveLength(1);
  });

  it('sorts by time before merging', () => {
    const clusters = clusterTimeline(
      [fixedEntry(3, 14), fixedEntry(1, 12), fixedEntry(2, 13)],
      NOW,
      960,
    );
    expect(clusters).toHaveLength(1);
    expect(clusters[0].entries.map((e) => e.row.id)).toEqual([1, 2, 3]);
  });

  it('adapts to the measured track width', () => {
    // 12h (25%) vs 16h (33.3%): overlapping nodes on a narrow track,
    // comfortably separate on a wide one.
    const narrow = clusterTimeline([fixedEntry(1, 12), fixedEntry(2, 16)], NOW, 500);
    expect(narrow).toHaveLength(1);
    const wide = clusterTimeline([fixedEntry(1, 12), fixedEntry(2, 16)], NOW, 2400);
    expect(wide).toHaveLength(2);
  });
});

describe('OverviewTimeline clusters', () => {
  function renderClustered() {
    const onOpenPlaylist = vi.fn();
    render(
      <OverviewTimeline
        scheduled={[entry(1, 12), entry(2, 13), entry(3, 14)]}
        scheduledLater={0}
        unscheduled={[]}
        now={Date.now()}
        onOpenPlaylist={onOpenPlaylist}
        onScheduleDrop={vi.fn()}
      />,
    );
    return { onOpenPlaylist };
  }

  it('renders one cluster node for overlapping entries, not three', () => {
    renderClustered();
    const btn = screen.getByRole('button', { name: /Playlist 1 \+2/ });
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('dialog')).toBeNull();
    // the three playlists are not individually on the axis
    expect(screen.queryByRole('button', { name: /Playlist 2/ })).toBeNull();
  });

  it('expanding lists every member; picking one opens it and closes', () => {
    const { onOpenPlaylist } = renderClustered();
    fireEvent.click(screen.getByRole('button', { name: /Playlist 1 \+2/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getAllByRole('button')).toHaveLength(3);
    fireEvent.click(within(dialog).getByRole('button', { name: /Playlist 2/ }));
    expect(onOpenPlaylist).toHaveBeenCalledWith(2);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Escape closes the member list', () => {
    renderClustered();
    fireEvent.click(screen.getByRole('button', { name: /Playlist 1 \+2/ }));
    expect(screen.getByRole('dialog')).toBeDefined();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a lone entry still opens its playlist directly', () => {
    const onOpenPlaylist = vi.fn();
    render(
      <OverviewTimeline
        scheduled={[entry(1, 2), entry(2, 30)]}
        scheduledLater={0}
        unscheduled={[]}
        now={Date.now()}
        onOpenPlaylist={onOpenPlaylist}
        onScheduleDrop={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Playlist 1/ }));
    expect(onOpenPlaylist).toHaveBeenCalledWith(1);
    // no cluster UI anywhere
    expect(screen.queryByText('+2')).toBeNull();
  });
});
