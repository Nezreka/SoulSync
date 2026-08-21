/**
 * The schedule picker on the card.
 *
 * This is what replaces the Auto-Sync board, so the thing worth pinning is that
 * it can express everything the board could: no schedule, any preset interval,
 * the common weekly shapes, and an arbitrary day-set through the editor the
 * board leaves behind.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AUTO_SYNC_BUCKETS, autoSyncIntervalLabel } from '../-sync.autosync';
import { SCHEDULE_WEEKLY_PRESETS, ScheduleMenu } from './schedule-menu';

function renderMenu(over: Partial<React.ComponentProps<typeof ScheduleMenu>> = {}) {
  const props: React.ComponentProps<typeof ScheduleMenu> = {
    row: { id: 1, name: 'Road Trip', source: 'spotify' },
    hours: null,
    weekly: false,
    anchor: { top: 100, left: 200 },
    onClose: vi.fn(),
    onPickHours: vi.fn(),
    onPickWeekly: vi.fn(),
    onCustomWeekly: vi.fn(),
    ...over,
  };
  const { container, unmount } = render(<ScheduleMenu {...props} />);
  const click = (label: string) =>
    fireEvent.click(
      [...container.querySelectorAll('.pl-menu-item')].find((b) =>
        b.textContent?.startsWith(label),
      ) as HTMLElement,
    );
  return { props, container, click, unmount };
}

describe('what it can express', () => {
  it('offers every interval the board had lanes for', () => {
    const { container } = renderMenu();
    const labels = [...container.querySelectorAll('.pl-menu-item')].map((b) => b.textContent);
    // Ten preset lanes, a filterable sidebar and a bulk popover existed to set
    // ONE integer. Every one of those lanes is an entry here.
    for (const hours of AUTO_SYNC_BUCKETS) {
      expect(labels.some((l) => l?.startsWith(autoSyncIntervalLabel(hours)))).toBe(true);
    }
  });

  it('sets an interval and closes', () => {
    const { props, click } = renderMenu();
    click('Every 8 hours');
    expect(props.onPickHours).toHaveBeenCalledWith(8);
    expect(props.onClose).toHaveBeenCalled();
  });

  it('unschedules through the first entry, so turning it off is one click', () => {
    const { props, click } = renderMenu({ hours: 8 });
    click('Not scheduled');
    expect(props.onPickHours).toHaveBeenCalledWith(null);
  });

  it('offers the common weekly shapes as one-click presets', () => {
    const { props, click } = renderMenu();
    click('Every weekday');
    expect(props.onPickWeekly).toHaveBeenCalledWith(['mon', 'tue', 'wed', 'thu', 'fri']);
  });

  it('every preset uses real weekday ids', () => {
    // A typo here would save a trigger whose days never match.
    const valid = new Set(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
    for (const preset of SCHEDULE_WEEKLY_PRESETS) {
      expect(preset.days.length).toBeGreaterThan(0);
      for (const day of preset.days) expect(valid).toContain(day);
    }
  });

  it('sends an arbitrary day-set to the editor rather than guessing', () => {
    // A weekly trigger is {days[], time, tz}: a flat list cannot express
    // "Mon + Wed + Fri at 03:00", which is why the board had a second tab.
    const { props, click } = renderMenu();
    click('Custom weekly…');
    expect(props.onCustomWeekly).toHaveBeenCalled();
  });

  it('offers to EDIT rather than create when a weekly schedule already exists', () => {
    const { click, props } = renderMenu({ weekly: true });
    click('Edit weekly schedule…');
    expect(props.onCustomWeekly).toHaveBeenCalled();
  });
});

describe('what it shows as current', () => {
  it('ticks the interval that is set', () => {
    const { container } = renderMenu({ hours: 8 });
    expect(container.querySelector('.pl-menu-item--on')?.textContent).toContain('Every 8 hours');
  });

  it('ticks "Not scheduled" when nothing is set', () => {
    const { container } = renderMenu();
    expect(container.querySelector('.pl-menu-item--on')?.textContent).toContain('Not scheduled');
  });

  it('does NOT tick "Not scheduled" for a weekly playlist', () => {
    // Weekly IS scheduled; saying otherwise would invite someone to overwrite
    // a weekly plan without realising there was one.
    const { container } = renderMenu({ weekly: true });
    const on = [...container.querySelectorAll('.pl-menu-item--on')].map((b) => b.textContent);
    expect(on.some((l) => l?.includes('Not scheduled'))).toBe(false);
  });
});

describe('dismissal', () => {
  it('names the playlist for assistive tech', () => {
    renderMenu();
    expect(screen.getByRole('menu').getAttribute('aria-label')).toBe(
      'Sync schedule for Road Trip',
    );
  });

  it('closes on Escape', () => {
    const { props } = renderMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('a click inside does not dismiss it', () => {
    const { props, container } = renderMenu();
    fireEvent.click(container.querySelector('.pl-menu') as HTMLElement);
    expect(props.onClose).not.toHaveBeenCalled();
  });
});
