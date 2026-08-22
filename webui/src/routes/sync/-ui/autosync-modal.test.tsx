/**
 * Differential tests for the Auto-Sync Manager modal shell — auto-sync.js
 * 571-740 and the bulk popover at 1256-1313.
 */

import { fireEvent, render, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AutoSyncScheduleState } from '../-sync.autosync';

import { AutoSyncBulkMenu, AutoSyncModal } from './autosync-modal';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

const emptyState = (over: Partial<AutoSyncScheduleState> = {}): AutoSyncScheduleState => ({
  playlists: [],
  automations: [],
  playlistSchedules: {},
  weeklySchedules: {},
  automationPipelines: [],
  runHistory: [],
  runHistoryTotal: 0,
  ...over,
});

function renderModal(over: Partial<React.ComponentProps<typeof AutoSyncModal>> = {}) {
  const props: React.ComponentProps<typeof AutoSyncModal> = {
    state: emptyState(),
    loading: false,
    loadError: null,
    now: NOW,
    historyFilter: 'all',
    onHistoryFilterChange: vi.fn(),
    onLoadMoreHistory: vi.fn(),
    onRefresh: vi.fn(),
    onClose: vi.fn(),
    boardActions: {
      onDrop: vi.fn(),
      onRun: vi.fn(),
      onUnschedule: vi.fn(),
      onOrganizeChange: vi.fn(),
    },
    weeklyActions: {
      onSave: vi.fn(),
      onRun: vi.fn(),
      onUnschedule: vi.fn(),
      onOrganizeChange: vi.fn(),
    },
    onBulkSchedule: vi.fn(),
    onBulkUnschedule: vi.fn(),
    onOpenDetails: vi.fn(),
    onRunAgain: vi.fn(),
    ...over,
  };
  return { props, ...render(<AutoSyncModal {...props} />) };
}

describe('the three shell states (588, 640-649, 652-731)', () => {
  it('shows the loading body, with no tabs or summary', () => {
    const { container } = renderModal({ loading: true });
    expect(container.querySelector('.auto-sync-loading')?.textContent).toBe('Loading schedule...');
    expect(container.querySelector('.auto-sync-tabs')).toBeNull();
    expect(container.querySelector('.auto-sync-summary')).toBeNull();
  });

  it('replaces the whole body on a load error, and offers a way out of it', () => {
    const { container, props } = renderModal({ loadError: 'it broke' });
    expect(container.querySelector('.auto-sync-error p')?.textContent).toBe('it broke');
    expect(container.querySelector('.auto-sync-tabs')).toBeNull();
    expect(container.querySelector('.auto-sync-loading')).toBeNull();

    // Without this the only recovery was to close the modal and reopen it,
    // which nothing on screen told the user to do.
    const retry = container.querySelector('.auto-sync-error-retry') as HTMLElement;
    expect(retry).not.toBeNull();
    fireEvent.click(retry);
    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it('describes the feature the SAME way in every state', () => {
    // The three states used to carry three different blurbs, so what Auto-Sync
    // *is* changed depending on whether its data had arrived.
    const blurbOf = (over: Parameters<typeof renderModal>[0]) => {
      const { container, unmount } = renderModal(over);
      const text = container.querySelector('.auto-sync-header p')?.textContent;
      unmount();
      return text;
    };
    const ready = blurbOf({});
    expect(ready).toBeTruthy();
    expect(blurbOf({ loading: true })).toBe(ready);
    expect(blurbOf({ loadError: 'it broke' })).toBe(ready);
  });

  it('prefers the error over the loading state', () => {
    const { container } = renderModal({ loading: true, loadError: 'broke' });
    expect(container.querySelector('.auto-sync-error')).not.toBeNull();
    expect(container.querySelector('.auto-sync-loading')).toBeNull();
  });

  it('renders the full manager once loaded', () => {
    const { container } = renderModal();
    expect(container.querySelector('.auto-sync-eyebrow')?.textContent).toBe('Playlist automation');
    expect(container.querySelector('.auto-sync-header h3')?.textContent).toBe('Auto-Sync Manager');
    expect(container.querySelectorAll('.auto-sync-tab-panel')).toHaveLength(4);
  });
});

describe('closing (594, 585)', () => {
  it('closes from the × in every state', () => {
    for (const over of [{}, { loading: true }, { loadError: 'x' }]) {
      const { container, props } = renderModal(over);
      fireEvent.click(container.querySelector('.auto-sync-close') as HTMLElement);
      expect(props.onClose).toHaveBeenCalledTimes(1);
    }
  });

  it('closes on a click on the OVERLAY but not on the modal itself', () => {
    const { container, props } = renderModal();
    fireEvent.click(container.querySelector('.auto-sync-modal') as HTMLElement);
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('.auto-sync-overlay') as HTMLElement);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });
});

describe('the summary counters (658-664)', () => {
  it('shows one fact per slot, with paused riding along on the schedule count', () => {
    const { container } = renderModal({
      state: emptyState({
        playlists: [{ id: 1, track_count: 10 }, { id: 2, track_count: '5' }, { id: 3 }],
        playlistSchedules: {
          '1': { hours: 24, enabled: true } as never,
          '2': { hours: 8, enabled: false } as never,
        },
        weeklySchedules: { '3': { enabled: true } as never },
        automationPipelines: [{ id: 9 }, { id: 10 }],
      }),
    });
    const nums = Array.from(container.querySelectorAll('.auto-sync-summary span')).map(
      (s) => s.textContent,
    );
    // 3 scheduled (2 hourly + 1 weekly) and 2 pipelines. "active schedules"
    // was the same number as "scheduled" until something was paused, so the
    // paused one rides on the first tile instead of taking a second; and
    // "mirrored tracks" was never about scheduling at all.
    expect(nums).toEqual(['3', '2']);
    expect(container.querySelector('.auto-sync-summary small')?.textContent).toBe(
      'scheduled playlists · 1 paused',
    );
  });

  it('says nothing about paused when nothing is', () => {
    const { container } = renderModal({
      state: emptyState({
        playlists: [{ id: 1, track_count: 10 }],
        playlistSchedules: { '1': { hours: 24, enabled: true } as never },
      }),
    });
    expect(container.querySelector('.auto-sync-summary small')?.textContent).toBe(
      'scheduled playlist',
    );
  });

  it('surfaces failed runs, which is the one number here you would act on', () => {
    const { container } = renderModal({
      state: emptyState({ runHistory: [{ status: 'error' }, { status: 'error' }] as never }),
    });
    const bad = container.querySelector('.auto-sync-summary-bad');
    expect(bad?.querySelector('span')?.textContent).toBe('2');
    expect(bad?.querySelector('small')?.textContent).toBe('failed runs');
  });

  it('hides the failure tile when there are none', () => {
    const { container } = renderModal();
    expect(container.querySelector('.auto-sync-summary-bad')).toBeNull();
  });

  it('reads enabled as plain truthiness here, unlike everywhere else (660)', () => {
    // An omitted `enabled` counts as scheduled but NOT as active. Transcribed
    // from the vanilla rather than harmonised, so the numbers match.
    const { container } = renderModal({
      state: emptyState({ playlistSchedules: { '1': { hours: 24 } as never } }),
    });
    const nums = Array.from(container.querySelectorAll('.auto-sync-summary span')).map(
      (s) => s.textContent,
    );
    expect(nums[0]).toBe('1');
    expect(nums[1]).toBe('0');
  });
});

describe('the tabs (712-731)', () => {
  it('starts on the hourly board and moves the active class', () => {
    const { container } = renderModal();
    const tabs = container.querySelectorAll('.auto-sync-tabs button');
    expect(tabs[0].textContent).toBe('Hourly Board');
    expect(tabs[0].className).toContain('active');

    fireEvent.click(tabs[1]);
    expect(container.querySelectorAll('.auto-sync-tabs button')[1].className).toContain('active');
    expect(container.querySelectorAll('.auto-sync-tabs button')[0].className).not.toContain(
      'active',
    );
    expect(container.querySelector('#auto-sync-weekly-panel')?.className).toContain('active');
    expect(container.querySelector('#auto-sync-schedule-panel')?.className).not.toContain('active');
  });

  it('names all four tabs', () => {
    const { container } = renderModal();
    expect(
      Array.from(container.querySelectorAll('.auto-sync-tabs button')).map((b) =>
        b.textContent?.replace(/\d+$/, ''),
      ),
    ).toEqual(['Hourly Board', 'Weekly Board', 'Automation Pipelines', 'Run History']);
  });

  it('keeps every panel MOUNTED, so board state survives a tab switch', () => {
    const { container } = renderModal({
      state: emptyState({
        playlists: [{ id: 1, name: 'Late Night', source: 'spotify', track_count: 3 }],
      }),
    });
    // Type a sidebar filter on the hourly board...
    const search = container.querySelector(
      '#auto-sync-schedule-panel .auto-sync-sidebar-search',
    ) as HTMLInputElement;
    fireEvent.change(search, { target: { value: 'late' } });
    expect(search.value).toBe('late');

    // ...switch away and back. An unmount would have discarded it.
    fireEvent.click(container.querySelectorAll('.auto-sync-tabs button')[1]);
    fireEvent.click(container.querySelectorAll('.auto-sync-tabs button')[0]);
    expect(
      (
        container.querySelector(
          '#auto-sync-schedule-panel .auto-sync-sidebar-search',
        ) as HTMLInputElement
      ).value,
    ).toBe('late');
  });

  it('badges the Run History tab with the error count, and only then', () => {
    const { container: clean } = renderModal();
    expect(clean.querySelector('.auto-sync-tab-badge')).toBeNull();

    const { container } = renderModal({
      state: emptyState({
        runHistory: [{ status: 'error' }, { status: 'skipped' }, { status: 'completed' }],
        runHistoryTotal: 3,
      }),
    });
    const badges = container.querySelectorAll('.auto-sync-tab-badge');
    // Exactly ONE badge, on the history tab — not one per tab.
    expect(badges).toHaveLength(1);
    expect(badges[0].textContent).toBe('2');
    expect(badges[0].className).toContain('error');
    // On the history tab, not any other.
    expect(
      within(container.querySelectorAll('.auto-sync-tabs button')[3] as HTMLElement).getByText('2'),
    ).toBeTruthy();
  });
});

describe('the bulk popover (1256-1313)', () => {
  function renderMenu(over: Partial<React.ComponentProps<typeof AutoSyncBulkMenu>> = {}) {
    const props = {
      source: 'tidal',
      anchor: { top: 100, left: 40 },
      onSchedule: vi.fn(),
      onUnschedule: vi.fn(),
      onClose: vi.fn(),
      ...over,
    };
    return { props, ...render(<AutoSyncBulkMenu {...props} />) };
  }

  it('offers every standard bucket, labelled, plus custom and unschedule', () => {
    const { container } = renderMenu();
    expect(container.querySelector('.auto-sync-bulk-menu-title')?.textContent).toBe(
      'Schedule all Tidal',
    );
    const buckets = Array.from(
      container.querySelectorAll('.auto-sync-bulk-menu-buckets button'),
    ).map((b) => b.textContent);
    expect(buckets).toHaveLength(10);
    expect(buckets[0]).toBe('Every 1 hour');
    expect(buckets[9]).toBe('Every week');
    expect(container.querySelector('.auto-sync-bulk-menu-custom')?.textContent).toBe(
      'Custom interval…',
    );
    expect(container.querySelector('.auto-sync-bulk-menu-unschedule')?.textContent).toBe(
      'Unschedule all',
    );
  });

  it('positions itself under its anchor', () => {
    const { container } = renderMenu({ anchor: { top: 250, left: 12 } });
    const menu = container.querySelector('.auto-sync-bulk-menu') as HTMLElement;
    expect(menu.style.top).toBe('250px');
    expect(menu.style.left).toBe('12px');
  });

  it('schedules at the bucket that was clicked', () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelectorAll('.auto-sync-bulk-menu-buckets button')[6]);
    expect(props.onSchedule).toHaveBeenCalledWith(24);
  });

  it('asks for a custom interval INLINE, never through window.prompt', () => {
    const promptSpy = vi.spyOn(window, 'prompt');
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-custom') as HTMLElement);
    expect(promptSpy).not.toHaveBeenCalled();

    const input = container.querySelector(
      '.auto-sync-bulk-menu-custom-row input',
    ) as HTMLInputElement;
    // 1305: the vanilla's prompt is pre-filled with 6.
    expect(input.value).toBe('6');
    fireEvent.change(input, { target: { value: '36' } });
    fireEvent.click(
      container.querySelector('.auto-sync-bulk-menu-custom-row button') as HTMLElement,
    );
    expect(props.onSchedule).toHaveBeenCalledWith(36);
    promptSpy.mockRestore();
  });

  it('accepts the custom interval from Enter as well as the button', () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-custom') as HTMLElement);
    const input = container.querySelector(
      '.auto-sync-bulk-menu-custom-row input',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '12' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onSchedule).toHaveBeenCalledWith(12);
  });

  it('refuses a bad interval with the vanilla wording, and does not schedule', () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-custom') as HTMLElement);
    const input = container.querySelector(
      '.auto-sync-bulk-menu-custom-row input',
    ) as HTMLInputElement;
    for (const bad of ['0', '-3', 'abc', '']) {
      fireEvent.change(input, { target: { value: bad } });
      fireEvent.click(
        container.querySelector('.auto-sync-bulk-menu-custom-row button') as HTMLElement,
      );
      expect(props.onSchedule).not.toHaveBeenCalled();
    }
    expect(container.querySelector('.auto-sync-bulk-menu-error')?.textContent).toBe(
      'Interval must be a whole number of hours, 1 or greater',
    );
  });

  it('clears the error once the field is edited again', () => {
    const { container } = renderMenu();
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-custom') as HTMLElement);
    const input = container.querySelector(
      '.auto-sync-bulk-menu-custom-row input',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(
      container.querySelector('.auto-sync-bulk-menu-custom-row button') as HTMLElement,
    );
    expect(container.querySelector('.auto-sync-bulk-menu-error')).not.toBeNull();
    fireEvent.change(input, { target: { value: '6' } });
    expect(container.querySelector('.auto-sync-bulk-menu-error')).toBeNull();
  });

  it('unschedules the whole source', () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-unschedule') as HTMLElement);
    expect(props.onUnschedule).toHaveBeenCalledTimes(1);
  });

  it('closes on an outside click, but NOT on a click inside itself (1289-1295)', () => {
    vi.useFakeTimers();
    const { container, props } = renderMenu();
    // 1291: the listener is deferred a tick, so the opening click cannot
    // immediately close it.
    fireEvent.click(document.body);
    expect(props.onClose).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-title') as HTMLElement);
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(document.body);
    expect(props.onClose).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

describe('the bulk popover inside the modal', () => {
  const withPlaylist = emptyState({
    playlists: [{ id: 1, name: 'Late Night', source: 'tidal', track_count: 3 }],
  });

  it('opens from a source group, and reports that source', () => {
    const { container, props } = renderModal({ state: withPlaylist });
    expect(container.querySelector('.auto-sync-bulk-menu')).toBeNull();
    fireEvent.click(container.querySelector('.auto-sync-source-bulk-btn') as HTMLElement);
    expect(container.querySelector('.auto-sync-bulk-menu-title')?.textContent).toBe(
      'Schedule all Tidal',
    );

    fireEvent.click(container.querySelectorAll('.auto-sync-bulk-menu-buckets button')[0]);
    expect(props.onBulkSchedule).toHaveBeenCalledWith('tidal', 1);
    // ...and it closes behind the choice.
    expect(container.querySelector('.auto-sync-bulk-menu')).toBeNull();
  });

  it('routes Unschedule all to its own action and closes', () => {
    const { container, props } = renderModal({ state: withPlaylist });
    fireEvent.click(container.querySelector('.auto-sync-source-bulk-btn') as HTMLElement);
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-unschedule') as HTMLElement);
    expect(props.onBulkUnschedule).toHaveBeenCalledWith('tidal');
    expect(container.querySelector('.auto-sync-bulk-menu')).toBeNull();
  });
});

describe('the monitor and the panels are wired through', () => {
  it('has exactly ONE Refresh, in the header, and it is wired', () => {
    const { container, props } = renderModal();
    const refreshes = [...container.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === 'Refresh',
    );
    expect(refreshes).toHaveLength(1);
    expect(refreshes[0].closest('.auto-sync-header')).not.toBeNull();
    fireEvent.click(refreshes[0]);
    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it('opens the details modal from a running pipeline card', () => {
    const { container, props } = renderModal({
      state: emptyState({
        playlists: [{ id: 4, name: 'Busy', pipeline_state: { status: 'running' } }],
      }),
    });
    fireEvent.click(container.querySelector('.auto-sync-monitor-card button') as HTMLElement);
    expect(props.onOpenDetails).toHaveBeenCalledWith(4);
  });

  it('feeds the history panel its filter and paging callbacks', () => {
    const { container, props } = renderModal({
      state: emptyState({ runHistory: [{ id: 1, status: 'error' }], runHistoryTotal: 40 }),
      historyFilter: 'all',
    });
    fireEvent.click(container.querySelectorAll('.auto-sync-tabs button')[3]);
    fireEvent.click(container.querySelectorAll('.auto-sync-history-filter-btn')[1]);
    expect(props.onHistoryFilterChange).toHaveBeenCalledWith('error');
    fireEvent.click(container.querySelector('.auto-sync-history-load-more') as HTMLElement);
    expect(props.onLoadMoreHistory).toHaveBeenCalledTimes(1);
  });
});

describe('Escape', () => {
  it('closes the modal', () => {
    const { props } = renderModal();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('ignores other keys', () => {
    const { props } = renderModal();
    fireEvent.keyDown(document, { key: 'Enter' });
    fireEvent.keyDown(document, { key: 'a' });
    expect(props.onClose).not.toHaveBeenCalled();
  });
});
