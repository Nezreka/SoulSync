/**
 * Differential tests for the Auto-Sync weekly board against auto-sync.js
 * 861-1078 and 2145-2232.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AutoSyncHourlyEntry, AutoSyncWeeklyEntry, MirroredRow } from '../-sync.autosync';
import type { AutoSyncWeeklyBoardActions, AutoSyncWeeklyDraft } from './autosync-weekly';

import {
  AutoSyncWeeklyBoard,
  AutoSyncWeeklyEditor,
  autoSyncWeeklyCardsByDay,
  autoSyncWeeklyDropDraft,
  autoSyncWeeklyEditorDraft,
  AUTO_SYNC_DEFAULT_WEEKLY_TIME,
} from './autosync-weekly';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

const row = (over: Partial<MirroredRow> = {}): MirroredRow => ({
  id: 1,
  name: 'Late Night',
  source: 'spotify',
  track_count: 42,
  ...over,
});

const weekly = (over: Partial<AutoSyncWeeklyEntry> = {}): AutoSyncWeeklyEntry =>
  ({
    automation_id: 7,
    name: 'x',
    enabled: true,
    next_run: null,
    time: '09:00',
    days: ['mon'],
    tz: 'UTC',
    ...over,
  }) as AutoSyncWeeklyEntry;

const hourly = (over: Partial<AutoSyncHourlyEntry> = {}): AutoSyncHourlyEntry =>
  ({
    hours: 8,
    automation_id: 3,
    name: 'x',
    enabled: true,
    next_run: null,
    ...over,
  }) as AutoSyncHourlyEntry;

function dataTransfer(payload: string) {
  return { getData: () => payload, setData: vi.fn(), effectAllowed: '', dropEffect: '' };
}

beforeEach(() => {
  window.showToast = vi.fn();
});

afterEach(() => {
  delete window.showToast;
});

describe('autoSyncWeeklyCardsByDay (917-931)', () => {
  it('renders a multi-day schedule under EVERY matching day', () => {
    const byDay = autoSyncWeeklyCardsByDay([row({ id: 1 })], {
      '1': weekly({ days: ['mon', 'wed', 'fri'] }),
    });
    expect(byDay.mon).toHaveLength(1);
    expect(byDay.wed).toHaveLength(1);
    expect(byDay.fri).toHaveLength(1);
    expect(byDay.tue).toHaveLength(0);
  });

  it('seeds all seven days even when nothing is scheduled', () => {
    const byDay = autoSyncWeeklyCardsByDay([], {});
    expect(Object.keys(byDay)).toEqual(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
  });

  it('drops a schedule whose playlist is not in the list', () => {
    // This is how the sidebar filter narrows the lanes: a filtered-out
    // playlist takes its cards with it.
    const byDay = autoSyncWeeklyCardsByDay([row({ id: 1 })], { '999': weekly() });
    expect(byDay.mon).toHaveLength(0);
  });

  it('ignores a garbage day rather than inventing a lane', () => {
    const byDay = autoSyncWeeklyCardsByDay([row({ id: 1 })], {
      '1': weekly({ days: ['mon', 'funday'] }),
    });
    expect(byDay.mon).toHaveLength(1);
    expect(byDay.funday).toBeUndefined();
  });

  it('survives a schedule with no days at all', () => {
    const byDay = autoSyncWeeklyCardsByDay([row({ id: 1 })], {
      '1': weekly({ days: undefined as unknown as string[] }),
    });
    expect(byDay.mon).toHaveLength(0);
  });

  it('matches a string schedule key against a numeric playlist id', () => {
    const byDay = autoSyncWeeklyCardsByDay([row({ id: 42 })], { '42': weekly() });
    expect(byDay.mon[0].playlist.id).toBe(42);
  });
});

describe('autoSyncWeeklyDropDraft (2160-2168)', () => {
  it('creates a single-day schedule with the defaults when there is none', () => {
    const draft = autoSyncWeeklyDropDraft(5, 'thu', undefined);
    expect(draft.days).toEqual(['thu']);
    expect(draft.time).toBe(AUTO_SYNC_DEFAULT_WEEKLY_TIME);
    expect(draft.playlistId).toBe(5);
    // The browser zone, not a hardcoded UTC.
    expect(draft.tz).toBeTruthy();
  });

  it('APPENDS the day to an existing schedule and keeps its time and zone', () => {
    const draft = autoSyncWeeklyDropDraft(
      5,
      'wed',
      weekly({ days: ['mon'], time: '23:30', tz: 'Europe/London' }),
    );
    expect(draft.days).toEqual(['mon', 'wed']);
    expect(draft.time).toBe('23:30');
    expect(draft.tz).toBe('Europe/London');
  });

  it('is a no-op when the day is already scheduled', () => {
    const existing = weekly({ days: ['mon', 'fri'] });
    expect(autoSyncWeeklyDropDraft(5, 'fri', existing).days).toEqual(['mon', 'fri']);
  });
});

describe('autoSyncWeeklyEditorDraft (2173-2185)', () => {
  it('copies an existing schedule rather than aliasing it', () => {
    const existing = weekly({ days: ['mon'], time: '07:15', tz: 'Asia/Tokyo' });
    const draft = autoSyncWeeklyEditorDraft(5, existing);
    expect(draft).toEqual({ playlistId: 5, time: '07:15', days: ['mon'], tz: 'Asia/Tokyo' });
    draft.days.push('sun');
    // Mutating the draft must not reach back into the loaded state.
    expect(existing.days).toEqual(['mon']);
  });

  it('opens with NO days selected when there is no schedule (2181)', () => {
    expect(autoSyncWeeklyEditorDraft(5, undefined).days).toEqual([]);
  });
});

describe('AutoSyncWeeklyEditor (1026-1077)', () => {
  const renderEditor = (over: Partial<React.ComponentProps<typeof AutoSyncWeeklyEditor>> = {}) => {
    const props = {
      draft: { playlistId: 1, time: '09:00', days: ['mon'], tz: 'UTC' },
      playlistName: 'Late Night',
      hasExisting: true,
      onChange: vi.fn(),
      onSave: vi.fn(),
      onUnschedule: vi.fn(),
      onClose: vi.fn(),
      ...over,
    };
    return { props, ...render(<AutoSyncWeeklyEditor {...props} />) };
  };

  it('marks only the selected days active', () => {
    const { container } = renderEditor({
      draft: { playlistId: 1, time: '09:00', days: ['mon', 'fri'], tz: 'UTC' },
    });
    const toggles = Array.from(container.querySelectorAll('.auto-sync-weekly-day-toggle'));
    expect(toggles).toHaveLength(7);
    expect(toggles.filter((t) => t.className.includes('active')).map((t) => t.textContent)).toEqual(
      ['Mon', 'Fri'],
    );
  });

  it('adds and removes a day without disturbing the others', () => {
    const { props, container } = renderEditor();
    const toggles = container.querySelectorAll('.auto-sync-weekly-day-toggle');
    fireEvent.click(toggles[2]);
    expect(props.onChange).toHaveBeenCalledWith(expect.objectContaining({ days: ['mon', 'wed'] }));
    fireEvent.click(toggles[0]);
    expect(props.onChange).toHaveBeenLastCalledWith(expect.objectContaining({ days: [] }));
  });

  it('edits the time and the timezone', () => {
    const { props, container } = renderEditor();
    fireEvent.change(container.querySelector('#auto-sync-weekly-time') as HTMLInputElement, {
      target: { value: '23:45' },
    });
    expect(props.onChange).toHaveBeenCalledWith(expect.objectContaining({ time: '23:45' }));
    // The timezone is folded away behind a summary line — it already defaults
    // to the browser's own, so almost nobody needs to change it.
    fireEvent.click(container.querySelector('.auto-sync-tz-summary') as HTMLElement);
    fireEvent.change(container.querySelector('#auto-sync-weekly-tz') as HTMLInputElement, {
      target: { value: 'Asia/Tokyo' },
    });
    expect(props.onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tz: 'Asia/Tokyo' }));
  });

  it('falls back rather than storing an emptied time or zone (2205-2215)', () => {
    const { props, container } = renderEditor();
    fireEvent.change(container.querySelector('#auto-sync-weekly-time') as HTMLInputElement, {
      target: { value: '' },
    });
    expect(props.onChange).toHaveBeenCalledWith(expect.objectContaining({ time: '09:00' }));
    // The timezone is folded away behind a summary line — it already defaults
    // to the browser's own, so almost nobody needs to change it.
    fireEvent.click(container.querySelector('.auto-sync-tz-summary') as HTMLElement);
    fireEvent.change(container.querySelector('#auto-sync-weekly-tz') as HTMLInputElement, {
      target: { value: '' },
    });
    expect(props.onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tz: 'UTC' }));
  });

  it('offers Unschedule only when a schedule exists (1068)', () => {
    const { container } = renderEditor({ hasExisting: false });
    expect(container.querySelector('.auto-sync-weekly-editor-delete')).toBeNull();
    const { container: has } = renderEditor({ hasExisting: true });
    expect(has.querySelector('.auto-sync-weekly-editor-delete')).not.toBeNull();
  });

  it('closes from the backdrop, the × and Cancel — but NOT from the panel', () => {
    const { props, container } = renderEditor();
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor') as HTMLElement);
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-backdrop') as HTMLElement);
    fireEvent.click(container.querySelector('.auto-sync-close') as HTMLElement);
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-cancel') as HTMLElement);
    expect(props.onClose).toHaveBeenCalledTimes(3);
  });

  it('names the playlist being edited', () => {
    const { container } = renderEditor({ playlistName: 'Deep Focus' });
    expect(container.querySelector('.auto-sync-weekly-editor-playlist')?.textContent).toBe(
      'Deep Focus',
    );
  });

  /* ── the timezone fold ── */
  it('shows the zone as a sentence, not a text field', () => {
    // It defaults to the browser's own, so it is right without being touched.
    const { container } = renderEditor();
    expect(container.querySelector('#auto-sync-weekly-tz')).toBeNull();
    expect(container.querySelector('.auto-sync-tz-summary')?.textContent).toContain('UTC');
  });

  it('opens to a field when you ask to change it', () => {
    const { container } = renderEditor();
    fireEvent.click(container.querySelector('.auto-sync-tz-summary') as HTMLElement);
    expect(container.querySelector('#auto-sync-weekly-tz')).not.toBeNull();
  });

  it('says so when the zone is one the system does not know', () => {
    // Silently accepting it would schedule a run for an hour that never comes.
    const { container } = renderEditor({
      draft: { playlistId: 1, time: '09:00', days: ['mon'], tz: 'America/Los_Angles' },
    });
    fireEvent.click(container.querySelector('.auto-sync-tz-summary') as HTMLElement);
    const field = container.querySelector('#auto-sync-weekly-tz') as HTMLElement;
    expect(field.getAttribute('aria-invalid')).toBe('true');
    expect(container.querySelector('.auto-sync-tz-bad')?.textContent).toContain(
      'not a timezone this system knows',
    );
  });

  it('shows the ordinary hint while the zone is valid', () => {
    const { container } = renderEditor();
    fireEvent.click(container.querySelector('.auto-sync-tz-summary') as HTMLElement);
    expect(container.querySelector('.auto-sync-tz-bad')).toBeNull();
    expect(container.querySelector('#auto-sync-weekly-tz')?.getAttribute('aria-invalid')).toBe(
      'false',
    );
  });
});

describe('AutoSyncWeeklyBoard (861-977)', () => {
  const renderBoard = (
    playlists: MirroredRow[],
    weeklySchedules: Record<string, AutoSyncWeeklyEntry> = {},
    playlistSchedules: Record<string, AutoSyncHourlyEntry> = {},
  ) => {
    const actions: AutoSyncWeeklyBoardActions = {
      onSave: vi.fn(),
      onRun: vi.fn(),
      onUnschedule: vi.fn(),
      onOrganizeChange: vi.fn(),
      onRefresh: vi.fn(),
    };
    return {
      actions,
      ...render(
        <AutoSyncWeeklyBoard
          playlists={playlists}
          weeklySchedules={weeklySchedules}
          playlistSchedules={playlistSchedules}
          runHistory={[]}
          now={NOW}
          actions={actions}
        />,
      ),
    };
  };

  it('renders seven day lanes, Monday first', () => {
    const { container } = renderBoard([]);
    const lanes = container.querySelectorAll('.auto-sync-lane');
    expect(lanes).toHaveLength(7);
    expect(lanes[0].getAttribute('data-day')).toBe('mon');
    expect(lanes[6].getAttribute('data-day')).toBe('sun');
    expect(lanes[0].querySelector('.auto-sync-lane-badge b')?.textContent).toBe('Mon');
    expect(lanes[0].querySelector('.auto-sync-lane-badge span')?.textContent).toBe('Weekly');
    expect(lanes[0].querySelector('.auto-sync-lane-hint')?.textContent).toContain(
      'Drag a playlist here to sync every Mon',
    );
  });

  it('carries the weekly lane class the CSS keys off', () => {
    const { container } = renderBoard([]);
    expect(container.querySelector('main')?.className).toBe(
      'auto-sync-lanes auto-sync-weekly-lanes',
    );
  });

  it('puts one playlist under each of its scheduled days', () => {
    const { container } = renderBoard([row({ id: 1, name: 'Late Night' })], {
      '1': weekly({ days: ['mon', 'fri'] }),
    });
    const mon = container.querySelector('[data-day="mon"]') as HTMLElement;
    const fri = container.querySelector('[data-day="fri"]') as HTMLElement;
    const tue = container.querySelector('[data-day="tue"]') as HTMLElement;
    expect(mon.querySelector('.auto-sync-scheduled-name')?.textContent).toBe('Late Night');
    expect(fri.querySelector('.auto-sync-scheduled-name')?.textContent).toBe('Late Night');
    expect(tue.querySelector('.auto-sync-scheduled-card')).toBeNull();
    expect(mon.querySelector('.auto-sync-lane-count')?.textContent).toBe('1');
  });

  it('spells out the weekly label, the timezone and the next run', () => {
    const soon = new Date(NOW + 3 * 3600_000).toISOString().replace('Z', '');
    const { container } = renderBoard([row({ id: 1 })], {
      '1': weekly({ days: ['mon'], time: '09:00', tz: 'Europe/London', next_run: soon }),
    });
    const timing = container.querySelector('.auto-sync-scheduled-timing') as HTMLElement;
    expect(timing.querySelector('span')?.textContent).toBe('Mon @ 09:00');
    const smalls = Array.from(timing.querySelectorAll('small')).map((s) => s.textContent);
    expect(smalls).toEqual(['Europe/London', 'next in 3h']);
  });

  it('falls back to UTC when the schedule carries no zone', () => {
    const { container } = renderBoard([row({ id: 1 })], {
      '1': weekly({ tz: undefined as unknown as string }),
    });
    expect(container.querySelector('.auto-sync-scheduled-timing small')?.textContent).toBe('UTC');
  });

  it('carries the weekly card class', () => {
    const { container } = renderBoard([row({ id: 1 })], { '1': weekly() });
    expect(container.querySelector('.auto-sync-scheduled-card')?.className).toContain(
      'auto-sync-weekly-card',
    );
  });

  it('marks a playlist scheduled on the HOURLY board as spoken for elsewhere (879-887)', () => {
    const { container } = renderBoard(
      [row({ id: 1, name: 'A' }), row({ id: 2, name: 'B' }), row({ id: 3, name: 'C' })],
      { '1': weekly() },
      { '2': hourly({ hours: 8 }) },
    );
    const cards = container.querySelectorAll('.auto-sync-playlist');
    expect(cards[0].className).toContain('scheduled');
    expect(cards[0].className).not.toContain('scheduled-elsewhere');
    expect(cards[0].querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Mon @ 09:00',
    );

    expect(cards[1].className).toContain('scheduled-elsewhere');
    expect(cards[1].querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Hourly (every 8 hours)',
    );

    expect(cards[2].className).not.toContain('scheduled');
    expect(cards[2].querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Unscheduled',
    );
  });

  it('offers NO bulk button — that is the hourly board only (894-901)', () => {
    const { container } = renderBoard([row({ id: 1 })]);
    expect(container.querySelector('.auto-sync-source-bulk-btn')).toBeNull();
  });

  it('quarantines a non-schedulable source', () => {
    const { container } = renderBoard([row({ id: 1, source: 'file', name: 'Mixtape' })]);
    expect(container.querySelector('.auto-sync-source-group-disabled')).not.toBeNull();
    expect(container.querySelector('.auto-sync-empty')?.textContent).toBe(
      'No refreshable mirrored playlists yet.',
    );
  });

  it('filters the sidebar and the lanes together', () => {
    const { container } = renderBoard(
      [row({ id: 1, name: 'Late Night' }), row({ id: 2, name: 'Morning' })],
      { '1': weekly(), '2': weekly() },
    );
    expect(container.querySelectorAll('.auto-sync-scheduled-card')).toHaveLength(2);
    fireEvent.change(container.querySelector('.auto-sync-sidebar-search') as HTMLInputElement, {
      target: { value: 'morning' },
    });
    expect(container.querySelectorAll('.auto-sync-scheduled-card')).toHaveLength(1);
    expect(container.querySelectorAll('.auto-sync-playlist')).toHaveLength(1);
  });

  it('saves a single-day schedule when a fresh playlist is dropped', () => {
    const { container, actions } = renderBoard([row({ id: 7 })]);
    fireEvent.drop(container.querySelector('[data-day="thu"]') as HTMLElement, {
      dataTransfer: dataTransfer('7'),
    });
    expect(actions.onSave).toHaveBeenCalledWith(
      expect.objectContaining({ playlistId: 7, days: ['thu'], time: '09:00' }),
    );
  });

  it('AUGMENTS an existing schedule when dropped on a second day', () => {
    const { container, actions } = renderBoard([row({ id: 7 })], {
      '7': weekly({ days: ['mon'], time: '22:00', tz: 'Europe/London' }),
    });
    fireEvent.drop(container.querySelector('[data-day="wed"]') as HTMLElement, {
      dataTransfer: dataTransfer('7'),
    });
    expect(actions.onSave).toHaveBeenCalledWith({
      playlistId: 7,
      days: ['mon', 'wed'],
      time: '22:00',
      tz: 'Europe/London',
    });
  });

  it('refuses a drop from a source Auto-Sync cannot refresh (2153-2158)', () => {
    const { container, actions } = renderBoard([row({ id: 7, source: 'file' })]);
    fireEvent.drop(container.querySelector('[data-day="mon"]') as HTMLElement, {
      dataTransfer: dataTransfer('7'),
    });
    expect(actions.onSave).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'That playlist source cannot be refreshed by Auto-Sync.',
      'info',
    );
  });

  it('ignores a drop for a playlist that is not there at all', () => {
    const { container, actions } = renderBoard([row({ id: 7 })]);
    fireEvent.drop(container.querySelector('[data-day="mon"]') as HTMLElement, {
      dataTransfer: dataTransfer('999'),
    });
    expect(actions.onSave).not.toHaveBeenCalled();
  });

  it('opens the editor from a card and saves the edited draft', () => {
    const { container, actions } = renderBoard([row({ id: 7 })], {
      '7': weekly({ days: ['mon'] }),
    });
    expect(container.querySelector('.auto-sync-weekly-editor')).toBeNull();
    fireEvent.click(container.querySelector('.auto-sync-scheduled-card') as HTMLElement);
    expect(container.querySelector('.auto-sync-weekly-editor')).not.toBeNull();

    // Add Friday, then save.
    const toggles = container.querySelectorAll('.auto-sync-weekly-day-toggle');
    fireEvent.click(toggles[4]);
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-save') as HTMLElement);
    expect(actions.onSave).toHaveBeenCalledWith(
      expect.objectContaining({ playlistId: 7, days: ['mon', 'fri'] }),
    );
    // ...and the editor closes behind it.
    expect(container.querySelector('.auto-sync-weekly-editor')).toBeNull();
  });

  it('REFUSES to save an empty day set, and stays open (2221-2224)', () => {
    const { container, actions } = renderBoard([row({ id: 7 })], {
      '7': weekly({ days: ['mon'] }),
    });
    fireEvent.click(container.querySelector('.auto-sync-scheduled-card') as HTMLElement);
    fireEvent.click(container.querySelectorAll('.auto-sync-weekly-day-toggle')[0]);
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-save') as HTMLElement);
    expect(actions.onSave).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'Pick at least one day for the weekly schedule.',
      'error',
    );
    expect(container.querySelector('.auto-sync-weekly-editor')).not.toBeNull();
  });

  it('unschedules from the editor and closes it (2229-2234)', () => {
    const { container, actions } = renderBoard([row({ id: 7 })], { '7': weekly() });
    fireEvent.click(container.querySelector('.auto-sync-scheduled-card') as HTMLElement);
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-delete') as HTMLElement);
    expect(actions.onUnschedule).toHaveBeenCalledWith(7);
    expect(container.querySelector('.auto-sync-weekly-editor')).toBeNull();
  });

  it('discards the draft on Cancel without saving', () => {
    const { container, actions } = renderBoard([row({ id: 7 })], { '7': weekly() });
    fireEvent.click(container.querySelector('.auto-sync-scheduled-card') as HTMLElement);
    fireEvent.click(container.querySelectorAll('.auto-sync-weekly-day-toggle')[3]);
    fireEvent.click(container.querySelector('.auto-sync-weekly-editor-cancel') as HTMLElement);
    expect(actions.onSave).not.toHaveBeenCalled();
    expect(container.querySelector('.auto-sync-weekly-editor')).toBeNull();
  });

  it('opens the editor for the card that was clicked, not the first one', () => {
    const { container } = renderBoard([row({ id: 1, name: 'A' }), row({ id: 2, name: 'B' })], {
      '1': weekly({ days: ['mon'] }),
      '2': weekly({ days: ['mon'] }),
    });
    const cards = container.querySelectorAll('.auto-sync-scheduled-card');
    fireEvent.click(cards[1]);
    expect(container.querySelector('.auto-sync-weekly-editor-playlist')?.textContent).toBe('B');
  });

  it('has no Refresh of its own — the modal header owns the only one', () => {
    renderBoard([]);
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull();
  });
});

describe('Escape inside the weekly editor', () => {
  it('dismisses the EDITOR without closing the modal behind it', () => {
    // The Auto-Sync modal closes on Escape via a document listener. Without
    // the editor swallowing the key first, editing a weekly schedule and
    // pressing Escape closed the whole modal and lost the in-progress edit.
    const onClose = vi.fn();
    const modalClose = vi.fn();
    document.addEventListener('keydown', modalClose);
    try {
      render(
        <AutoSyncWeeklyEditor
          draft={{ playlistId: 1, days: ['mon'], time: '03:00', tz: 'UTC' }}
          playlistName="Mix"
          hasExisting={false}
          onChange={vi.fn()}
          onSave={vi.fn()}
          onUnschedule={vi.fn()}
          onClose={onClose}
        />,
      );
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(modalClose).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', modalClose);
    }
  });
});
