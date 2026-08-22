/**
 * Differential tests for the Auto-Sync hourly board against auto-sync.js
 * 741-859 / 1951-1976 / 436-457.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AutoSyncHourlyEntry, MirroredRow } from '../-sync.autosync';
import type { AutoSyncBoardActions } from './autosync-board';

import { AutoSyncBoard } from './autosync-board';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

function makeActions(): AutoSyncBoardActions {
  return {
    onDrop: vi.fn(),
    onRun: vi.fn(),
    onUnschedule: vi.fn(),
    onOrganizeChange: vi.fn(),
    onBulkMenu: vi.fn(),
    onRefresh: vi.fn(),
  };
}

const row = (over: Partial<MirroredRow> = {}): MirroredRow => ({
  id: 1,
  name: 'Late Night',
  source: 'spotify',
  track_count: 42,
  ...over,
});

const hourly = (over: Partial<AutoSyncHourlyEntry> = {}): AutoSyncHourlyEntry =>
  ({
    hours: 24,
    automation_id: 7,
    name: 'x',
    enabled: true,
    next_run: null,
    ...over,
  }) as AutoSyncHourlyEntry;

/** A drag payload the jsdom DataTransfer stand-in can carry. */
function dataTransfer(payload: string) {
  return { getData: () => payload, setData: vi.fn(), effectAllowed: '', dropEffect: '' };
}

/**
 * A stateful stand-in, so a dragstart → drop round trip carries whatever the
 * card actually wrote rather than a value the test supplied.
 */
function liveDataTransfer() {
  let held = '';
  return {
    setData: (_type: string, value: string) => {
      held = value;
    },
    getData: () => held,
    effectAllowed: '',
    dropEffect: '',
  };
}

/**
 * jsdom implements no DragEvent, so `fireEvent.dragLeave(el, {relatedTarget})`
 * silently drops the property — a test written that way asserts nothing about
 * the child-guard. MouseEvent DOES carry relatedTarget and React reads it off
 * the native event, so the guard is exercised for real.
 */
function dragLeaveTo(el: Element, relatedTarget: EventTarget | null) {
  // Handed to fireEvent rather than dispatched directly so the state update
  // it causes is flushed inside act() before the assertion reads the class.
  fireEvent(el, new MouseEvent('dragleave', { bubbles: true, relatedTarget }));
}

afterEach(() => {
  delete window.playlistQualityProfileSelectHtml;
});

describe('AutoSyncBoard (741-859)', () => {
  const renderBoard = (
    playlists: MirroredRow[],
    playlistSchedules: Record<string, AutoSyncHourlyEntry> = {},
    runHistory: { playlist_id?: number; status?: string }[] = [],
  ) => {
    const actions = makeActions();
    return {
      actions,
      ...render(
        <AutoSyncBoard
          playlists={playlists}
          playlistSchedules={playlistSchedules}
          runHistory={runHistory}
          now={NOW}
          actions={actions}
        />,
      ),
    };
  };

  it('renders one lane per standard bucket, all empty, with the drag hint', () => {
    const { container } = renderBoard([]);
    const lanes = container.querySelectorAll('.auto-sync-lane');
    expect(lanes).toHaveLength(10);
    expect(lanes[0].getAttribute('data-hours')).toBe('1');
    expect(lanes[0].className).toContain('empty');
    expect(lanes[0].className).not.toContain('filled');
    expect(lanes[0].querySelector('.auto-sync-lane-hint')?.textContent).toContain(
      'Drag a playlist here to sync every 1 hour',
    );
    // An empty lane carries no count badge.
    expect(lanes[0].querySelector('.auto-sync-lane-count')).toBeNull();
  });

  it('fills a lane with its scheduled cards and counts them', () => {
    const { container } = renderBoard([row({ id: 1, name: 'A' }), row({ id: 2, name: 'B' })], {
      '1': hourly({ hours: 24 }),
      '2': hourly({ hours: 24 }),
    });
    const lane = container.querySelector('[data-hours="24"]') as HTMLElement;
    expect(lane.className).toContain('filled');
    expect(lane.querySelector('.auto-sync-lane-count')?.textContent).toBe('2');
    expect(lane.querySelectorAll('.auto-sync-scheduled-card')).toHaveLength(2);
    expect(lane.querySelector('.auto-sync-lane-hint')).toBeNull();
  });

  it('spells out the interval and the next run on each card', () => {
    const soon = new Date(NOW + 3 * 3600_000).toISOString().replace('Z', '');
    const { container } = renderBoard([row({ id: 1 })], {
      '1': hourly({ hours: 12, next_run: soon }),
    });
    const timing = container.querySelector('.auto-sync-scheduled-timing') as HTMLElement;
    expect(timing.querySelector('span')?.textContent).toBe('Every 12 hours');
    expect(timing.querySelector('small')?.textContent).toBe('next in 3h');
  });

  it('omits the next-run line when the schedule has no next run', () => {
    const { container } = renderBoard([row({ id: 1 })], { '1': hourly({ next_run: null }) });
    expect(container.querySelector('.auto-sync-scheduled-timing small')).toBeNull();
  });

  it('treats a MISSING enabled flag as enabled (`!== false`, 1952)', () => {
    const { container } = renderBoard([row({ id: 1 })], {
      '1': hourly({ enabled: undefined }),
    });
    expect(container.querySelector('.auto-sync-scheduled-card')?.className).not.toContain(
      'disabled',
    );
  });

  it('marks a custom-interval lane and says so in the badge', () => {
    const { container } = renderBoard([row({ id: 1 })], { '1': hourly({ hours: 6 }) });
    const lane = container.querySelector('[data-hours="6"]') as HTMLElement;
    expect(lane.className).toContain('custom');
    expect(lane.querySelector('.auto-sync-lane-badge span')?.textContent).toContain('· custom');
    expect(container.querySelector('[data-hours="24"]')?.className).not.toContain('custom');
  });

  it('groups the sidebar by source and labels each card with its assignment', () => {
    const { container } = renderBoard(
      [row({ id: 1, name: 'A', source: 'spotify' }), row({ id: 2, name: 'B', source: 'tidal' })],
      { '1': hourly({ hours: 8 }) },
    );
    const groups = container.querySelectorAll('.auto-sync-source-group');
    expect(groups).toHaveLength(2);
    expect(groups[0].querySelector('.auto-sync-source-title-label')?.textContent).toBe('Spotify');
    const cards = container.querySelectorAll('.auto-sync-playlist');
    expect(cards[0].className).toContain('scheduled');
    expect(cards[0].querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Every 8 hours',
    );
    expect(cards[1].className).not.toContain('scheduled');
    expect(cards[1].querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Unscheduled',
    );
  });

  it('quarantines a non-schedulable source instead of offering it (238-244)', () => {
    const { container } = renderBoard([
      row({ id: 1, name: 'Mixtape', source: 'file' }),
      row({ id: 2, name: 'Radio', source: 'lastfm' }),
      row({ id: 3, name: 'Real', source: 'spotify' }),
    ]);
    const disabled = container.querySelector('.auto-sync-source-group-disabled') as HTMLElement;
    expect(disabled.querySelectorAll('.auto-sync-playlist.unavailable')).toHaveLength(2);
    expect(disabled.querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      'File Imports · refresh not supported',
    );
    // ...and it is NOT draggable into a lane.
    expect(disabled.querySelector('.auto-sync-playlist')?.getAttribute('draggable')).toBeNull();
    // The schedulable one still gets a normal group.
    expect(
      container.querySelectorAll('.auto-sync-source-group:not(.auto-sync-source-group-disabled)'),
    ).toHaveLength(1);
  });

  it('shows the empty-sidebar copy when nothing is refreshable', () => {
    const { container } = renderBoard([row({ id: 1, source: 'file' })]);
    expect(container.querySelector('.auto-sync-empty')?.textContent).toBe(
      'No refreshable mirrored playlists yet.',
    );
    // The unavailable group still renders beside it.
    expect(container.querySelector('.auto-sync-source-group-disabled')).not.toBeNull();
  });

  it('filters the sidebar by name and by source label, and clears', () => {
    const { container } = renderBoard([
      row({ id: 1, name: 'Late Night', source: 'spotify' }),
      row({ id: 2, name: 'Morning', source: 'tidal' }),
    ]);
    const input = container.querySelector('.auto-sync-sidebar-search') as HTMLInputElement;
    // No clear button until there is something to clear.
    expect(container.querySelector('.auto-sync-sidebar-filter-clear')).toBeNull();

    fireEvent.change(input, { target: { value: 'morning' } });
    expect(container.querySelectorAll('.auto-sync-playlist')).toHaveLength(1);
    expect(container.querySelector('.auto-sync-playlist-name')?.textContent).toBe('Morning');

    fireEvent.change(input, { target: { value: 'spotify' } });
    expect(container.querySelector('.auto-sync-playlist-name')?.textContent).toBe('Late Night');

    fireEvent.click(container.querySelector('.auto-sync-sidebar-filter-clear') as HTMLElement);
    expect(input.value).toBe('');
    expect(container.querySelectorAll('.auto-sync-playlist')).toHaveLength(2);
  });

  it('filters the LANES too, not just the sidebar', () => {
    const { container } = renderBoard(
      [row({ id: 1, name: 'Late Night' }), row({ id: 2, name: 'Morning' })],
      { '1': hourly({ hours: 24 }), '2': hourly({ hours: 24 }) },
    );
    expect(container.querySelectorAll('.auto-sync-scheduled-card')).toHaveLength(2);
    fireEvent.change(container.querySelector('.auto-sync-sidebar-search') as HTMLInputElement, {
      target: { value: 'morning' },
    });
    const lane = container.querySelector('[data-hours="24"]') as HTMLElement;
    expect(lane.querySelectorAll('.auto-sync-scheduled-card')).toHaveLength(1);
    expect(lane.querySelector('.auto-sync-scheduled-name')?.textContent).toBe('Morning');
  });

  it('collapses variant rows into a kind group that toggles open', () => {
    const rows = [
      row({ id: 1, name: 'Discovery Weekly', _personalized: true, kind: 'mix', variant: 'Focus' }),
      row({ id: 2, name: 'Discovery Chill', _personalized: true, kind: 'mix', variant: 'Chill' }),
    ];
    const { container } = renderBoard(rows, { '1': hourly() });
    const group = container.querySelector('.auto-sync-kind-group') as HTMLElement;
    expect(group).not.toBeNull();
    expect(group.className).not.toContain('expanded');
    // One of the two is scheduled, so the head advertises it.
    expect(group.className).toContain('has-active');
    expect(group.querySelector('.auto-sync-kind-group-active')?.textContent).toBe('1 on');
    expect(group.querySelector('.auto-sync-kind-group-count')?.textContent).toBe('2');
    // Cards inside a kind group are titled by VARIANT, not the full name.
    expect(
      within(group)
        .getAllByText((_, el) => el?.className === 'auto-sync-playlist-name')
        .map((el) => el.textContent),
    ).toEqual(['Focus', 'Chill']);

    fireEvent.click(group.querySelector('.auto-sync-kind-group-head') as HTMLElement);
    expect(container.querySelector('.auto-sync-kind-group')?.className).toContain('expanded');
    fireEvent.click(container.querySelector('.auto-sync-kind-group-head') as HTMLElement);
    expect(container.querySelector('.auto-sync-kind-group')?.className).not.toContain('expanded');
  });

  it('drops a dragged playlist into the lane it was released on', () => {
    const { container, actions } = renderBoard([row({ id: 77 })]);
    const lane = container.querySelector('[data-hours="48"]') as HTMLElement;
    fireEvent.drop(lane, { dataTransfer: dataTransfer('77') });
    expect(actions.onDrop).toHaveBeenCalledWith(77, 48);
  });

  it('MOVES a scheduled card to the lane it is dragged onto', () => {
    const { container, actions } = renderBoard([row({ id: 31 })], { '31': hourly({ hours: 24 }) });
    const card = container.querySelector('.auto-sync-scheduled-card') as HTMLElement;
    const dt = liveDataTransfer();
    fireEvent.dragStart(card, { dataTransfer: dt });
    fireEvent.drop(container.querySelector('[data-hours="4"]') as HTMLElement, {
      dataTransfer: dt,
    });
    expect(actions.onDrop).toHaveBeenCalledWith(31, 4);
  });

  it('drags a SIDEBAR card into a lane carrying its own id', () => {
    const { container, actions } = renderBoard([row({ id: 12 }), row({ id: 13, name: 'B' })]);
    const cards = container.querySelectorAll('.auto-sync-playlist');
    const dt = liveDataTransfer();
    fireEvent.dragStart(cards[1], { dataTransfer: dt });
    fireEvent.drop(container.querySelector('[data-hours="72"]') as HTMLElement, {
      dataTransfer: dt,
    });
    expect(actions.onDrop).toHaveBeenCalledWith(13, 72);
  });

  it('ignores a drop that carries no playlist id', () => {
    const { container, actions } = renderBoard([row({ id: 77 })]);
    fireEvent.drop(container.querySelector('[data-hours="48"]') as HTMLElement, {
      dataTransfer: dataTransfer('not-a-number'),
    });
    expect(actions.onDrop).not.toHaveBeenCalled();
  });

  it('highlights a lane on drag-over and drops the highlight on leave', () => {
    const { container } = renderBoard([row()]);
    const lane = container.querySelector('[data-hours="1"]') as HTMLElement;
    expect(lane.className).not.toContain('drag-over');
    fireEvent.dragOver(lane, { dataTransfer: dataTransfer('1') });
    expect(lane.className).toContain('drag-over');
    dragLeaveTo(lane, document.body);
    expect(lane.className).not.toContain('drag-over');
  });

  it('KEEPS the highlight when the cursor merely moves onto a child (2032)', () => {
    const { container } = renderBoard([row({ id: 1 })], { '1': hourly({ hours: 1 }) });
    const lane = container.querySelector('[data-hours="1"]') as HTMLElement;
    fireEvent.dragOver(lane, { dataTransfer: dataTransfer('1') });
    expect(lane.className).toContain('drag-over');
    dragLeaveTo(lane, lane.querySelector('.auto-sync-scheduled-card'));
    expect(lane.className).toContain('drag-over');
  });

  it('drops the highlight on the lane that was left, not on every lane', () => {
    const { container } = renderBoard([row()]);
    const [first, second] = Array.from(container.querySelectorAll('.auto-sync-lane'));
    fireEvent.dragOver(first, { dataTransfer: dataTransfer('1') });
    fireEvent.dragOver(second, { dataTransfer: dataTransfer('1') });
    dragLeaveTo(first, document.body);
    expect(first.className).not.toContain('drag-over');
    expect(second.className).toContain('drag-over');
  });

  it('opens the bulk menu for the group it was clicked in', () => {
    const { container, actions } = renderBoard([
      row({ id: 1, source: 'spotify' }),
      row({ id: 2, source: 'tidal' }),
    ]);
    const btns = container.querySelectorAll('.auto-sync-source-bulk-btn');
    expect(btns[1].getAttribute('title')).toBe('Schedule all Tidal playlists at the same interval');
    fireEvent.click(btns[1]);
    expect(actions.onBulkMenu).toHaveBeenCalledTimes(1);
    expect((actions.onBulkMenu as ReturnType<typeof vi.fn>).mock.calls[0][1]).toBe('tidal');
  });

  it('has no Refresh of its own — the modal header owns the only one', () => {
    // The board, the weekly board, the monitor and the history each grew a
    // Refresh calling the same handler. One survives, in the header.
    renderBoard([]);
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull();
  });
});
