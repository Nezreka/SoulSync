/**
 * The timeline's "drop to schedule" hint is a fixed affordance at the right
 * edge — it must get out of the way when a scheduled node sits there, and it
 * is pointless when there is nothing unscheduled to drag.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { OverviewTimelineEntry } from './use-overview-model';

import { OverviewTimeline } from './overview-timeline';

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
