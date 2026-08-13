import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Automation } from '../-automations.types';

import { AutomationCard } from './automation-card';

const updateAutomationTrigger = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('../-automations.api', () => ({ updateAutomationTrigger }));

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, ...over };
}

const card = () => document.querySelector('.automation-card')!;
/** The schedule line — one state word, the card's most-asked question. */
const next = () => document.querySelector('.auto-tile-next')!;

beforeEach(() => {
  updateAutomationTrigger.mockClear();
});

describe('AutomationCard markup', () => {
  it('carries the classes and data attributes the CSS and vanilla filter rely on', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 7,
          name: 'Nightly',
          enabled: 1,
          trigger_type: 'schedule',
          action_type: 'process_wishlist',
        })}
      />,
    );
    const el = card();
    // .automation-card is the seam the vanilla progress renderer and the
    // drag/drop code reach through; .automation-tile is the redesign modifier
    // that keeps those rules off the vanilla cards the video page still paints.
    expect(el.className).toBe('automation-card automation-tile sched-unscheduled');
    expect(el.getAttribute('data-id')).toBe('7');
    // The vanilla filter read these two to apply its dropdowns; the React
    // filter matches on them, and the CSS still selects on the card classes.
    expect(el.getAttribute('data-trigger-type')).toBe('schedule');
    expect(el.getAttribute('data-action-type')).toBe('process_wishlist');
    expect(document.querySelector('.automation-status')?.className).toContain('enabled');
  });

  it('marks disabled and system rows with their modifier classes', () => {
    render(<AutomationCard automation={auto({ id: 1, enabled: 0, is_system: 1 })} />);
    expect(card().className).toBe('automation-card automation-tile sched-off disabled system');
    expect(document.querySelector('.automation-status')?.className).toContain('disabled');
  });

  it('renders the trigger → action flow with icons', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          trigger_type: 'schedule',
          trigger_config: { interval: 6, unit: 'hours' },
          action_type: 'process_wishlist',
        })}
      />,
    );
    expect(document.querySelector('.flow-trigger')?.textContent).toContain('Every 6 hours');
    expect(document.querySelector('.flow-action')?.textContent).toContain('Process Wishlist');
  });

  it('shows the delay chip only when a delay is configured', () => {
    const { unmount } = render(
      <AutomationCard automation={auto({ id: 1, action_config: { delay: 5 } })} />,
    );
    expect(document.querySelector('.flow-delay')?.textContent).toContain('5m');
    unmount();
    render(<AutomationCard automation={auto({ id: 2, action_config: { delay: 0 } })} />);
    expect(document.querySelector('.flow-delay')).toBeNull();
  });

  it('renders one notify chip per then_action', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          then_actions: [{ type: 'discord_webhook' }, { type: 'telegram' }],
        })}
      />,
    );
    const chips = [...document.querySelectorAll('.flow-notify')].map((n) => n.textContent);
    expect(chips).toEqual(['Discord', 'Telegram']);
  });

  it('hides duplicate/group/delete on system automations', () => {
    const { unmount } = render(<AutomationCard automation={auto({ id: 1, is_system: 1 })} />);
    expect(document.querySelector('.automation-dupe-btn')).toBeNull();
    expect(document.querySelector('.automation-group-btn')).toBeNull();
    expect(document.querySelector('.automation-delete-btn')).toBeNull();
    // Run / toggle / edit stay: system automations are still runnable.
    expect(document.querySelector('.automation-run-btn')).not.toBeNull();
    expect(document.querySelector('.automation-edit-btn')).not.toBeNull();
    unmount();

    render(<AutomationCard automation={auto({ id: 2 })} />);
    expect(document.querySelector('.automation-dupe-btn')).not.toBeNull();
    expect(document.querySelector('.automation-delete-btn')).not.toBeNull();
  });

  it('keeps the countdown outside the selector the vanilla 1s interval rewrites', () => {
    // stats-automations.js rewrites `.auto-next-run[data-next]` document-wide
    // every second, and still does for the vanilla cards on the video page.
    // The tile's schedule line carries neither the class nor the attribute, so
    // React owns that text node alone.
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          next_run: new Date(Date.now() + 3_600_000).toISOString(),
        })}
      />,
    );
    expect(next().textContent).toMatch(/^Next in \d/);
    expect(document.querySelector('.auto-next-run')).toBeNull();
    expect(document.querySelector('[data-next]')).toBeNull();
  });
});

describe('AutomationCard actions', () => {
  it('fires each handler with the automation', () => {
    const onRun = vi.fn();
    const onToggle = vi.fn();
    const onDelete = vi.fn();
    const a = auto({ id: 3, name: 'Nightly', enabled: 1 });
    render(<AutomationCard automation={a} onRun={onRun} onToggle={onToggle} onDelete={onDelete} />);

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    expect(onRun).toHaveBeenCalledWith(a);

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    expect(onToggle).toHaveBeenCalledWith(a);

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    expect(onDelete).toHaveBeenCalledWith(a);
  });

  it('offers the run-history link only once the automation has run', () => {
    const { unmount } = render(<AutomationCard automation={auto({ id: 1, run_count: 0 })} />);
    expect(document.querySelector('.auto-runs-link')).toBeNull();
    unmount();
    const onShowHistory = vi.fn();
    const a = auto({ id: 2, run_count: 4 });
    render(<AutomationCard automation={a} onShowHistory={onShowHistory} />);
    // It sits in the head as a badge now rather than being the fifth fragment
    // of a grey meta line — and it leads somewhere.
    const badge = document.querySelector('.auto-tile-head .auto-runs-link')!;
    expect(badge.textContent).toBe('4 runs');
    fireEvent.click(badge);
    expect(onShowHistory).toHaveBeenCalledWith(a);
  });

  it('separates delete from the controls it used to sit flush against', () => {
    render(<AutomationCard automation={auto({ id: 1 })} />);
    const actions = [...document.querySelector('.automation-actions')!.children].map(
      (n) => n.className,
    );
    // The one irreversible control is preceded by a rule, not by another
    // look-alike emoji button.
    expect(actions[actions.indexOf('automation-delete-btn') - 1]).toBe('auto-tile-sep');
  });
});

describe('the schedule line answers the card‘s most-asked question', () => {
  it('counts down while the side is live', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          next_run: new Date(Date.now() + 3_600_000).toISOString(),
        })}
      />,
    );
    expect(next().className).toContain('waiting');
    expect(card().className).toContain('sched-waiting');
    expect(card().className).not.toContain('paused');
  });

  it('replaces the countdown with Paused when the side is paused', () => {
    // The engine skips the slot but keeps the schedule alive, so next_run is
    // still populated — the card must not read it as a promise.
    render(
      <AutomationCard
        paused
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          next_run: new Date(Date.now() + 3_600_000).toISOString(),
        })}
      />,
    );
    expect(next().textContent).toBe('Paused');
    expect(next().textContent).not.toContain('Next');
    expect(card().className).toContain('paused');
  });

  it('stops an event automation claiming to Listen while paused', () => {
    render(
      <AutomationCard paused automation={auto({ id: 1, enabled: 1, trigger_type: 'app_started' })} />,
    );
    expect(next().textContent).toBe('Paused');
  });

  it('says switched off rather than paused for an automation the user turned off', () => {
    render(
      <AutomationCard paused automation={auto({ id: 1, enabled: 0, trigger_type: 'app_started' })} />,
    );
    expect(next().textContent).toBe('Switched off');
    expect(card().className).toContain('disabled');
    expect(card().className).not.toContain('paused');
  });

  it('says Listening for an armed event automation', () => {
    render(<AutomationCard automation={auto({ id: 1, enabled: 1, trigger_type: 'app_started' })} />);
    expect(next().textContent).toBe('Listening');
  });

  it('says due rather than overdue once the stored time has passed', () => {
    // The scheduler arms next_run and picks the row up on its next pass, so a
    // timestamp a moment in the past is normal operation, not a fault.
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          next_run: new Date(Date.now() - 60_000).toISOString(),
        })}
      />,
    );
    expect(next().textContent).toBe('Due now');
  });

  it('a run in flight outranks every other schedule state', () => {
    render(
      <AutomationCard
        paused
        automation={auto({ id: 1, enabled: 0, trigger_type: 'schedule' })}
        progress={{ status: 'running', progress: 40, log: [] }}
      />,
    );
    expect(next().textContent).toBe('Running now');
  });

  it('keeps Run reachable while paused — a manual run still works', () => {
    const onRun = vi.fn();
    render(<AutomationCard paused onRun={onRun} automation={auto({ id: 1, enabled: 1 })} />);
    fireEvent.click(document.querySelector('.automation-run-btn')!);
    expect(onRun).toHaveBeenCalled();
  });
});

describe('the glow edge is the progress bar', () => {
  it('idles as a full-width hairline and fills while the automation runs', () => {
    const { unmount } = render(<AutomationCard automation={auto({ id: 1, enabled: 1 })} />);
    const idle = document.querySelector('.auto-tile-edge') as HTMLElement;
    expect(idle.className).not.toContain('running');
    expect(idle.style.width).toBe('');
    unmount();

    render(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1 })}
        progress={{ status: 'running', progress: 40, log: [] }}
      />,
    );
    const live = document.querySelector('.auto-tile-edge') as HTMLElement;
    expect(live.className).toContain('running');
    expect(live.style.width).toBe('40%');
  });

  it('shows a sliver at 0% so a run that just started is still visible', () => {
    render(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1 })}
        progress={{ status: 'running', progress: 0, log: [] }}
      />,
    );
    expect((document.querySelector('.auto-tile-edge') as HTMLElement).style.width).toBe('2%');
  });

  it('does not repeat itself inside the progress panel', () => {
    // The panel used to carry its own bar — the same number, twice, on one card.
    render(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1 })}
        progress={{ status: 'running', progress: 40, phase: 'Scanning', log: [] }}
      />,
    );
    expect(document.querySelector('.auto-progress-bar')).toBeNull();
    expect(document.querySelector('.auto-progress-phase')?.textContent).toBe('Scanning');
  });
});

describe('the cadence is editable on the card face', () => {
  const editable = () => document.querySelector('.auto-flow-editable') as HTMLElement | null;

  it('offers an interval editor for a schedule trigger', async () => {
    const onRefresh = vi.fn();
    render(
      <AutomationCard
        onRefresh={onRefresh}
        automation={auto({
          id: 5,
          name: 'Nightly',
          enabled: 1,
          trigger_type: 'schedule',
          trigger_config: { interval: 6, unit: 'hours' },
        })}
      />,
    );
    fireEvent.click(editable()!);
    // Seeded from the server copy, not from a default.
    expect((screen.getByLabelText('Interval') as HTMLInputElement).value).toBe('6');
    expect((screen.getByLabelText('Interval unit') as HTMLSelectElement).value).toBe('hours');

    fireEvent.change(screen.getByLabelText('Interval'), { target: { value: '12' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(updateAutomationTrigger).toHaveBeenCalledWith(5, { interval: 12, unit: 'hours' });
  });

  it('offers a time editor for a daily trigger, keeping the stored timezone', async () => {
    // daily_time also carries tz; sending only `time` would silently re-home
    // the schedule to the server default.
    render(
      <AutomationCard
        automation={auto({
          id: 6,
          enabled: 1,
          trigger_type: 'daily_time',
          trigger_config: { time: '03:00', tz: 'Europe/Berlin' },
        })}
      />,
    );
    fireEvent.click(editable()!);
    const input = screen.getByLabelText('Time of day') as HTMLInputElement;
    expect(input.value).toBe('03:00');
    fireEvent.change(input, { target: { value: '04:30' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(updateAutomationTrigger).toHaveBeenCalledWith(6, {
        time: '04:30',
        tz: 'Europe/Berlin',
      }),
    );
  });

  it('leaves weekly and event triggers to the builder', () => {
    // weekly_time needs a day multi-select and an event trigger has nothing to
    // set — a second builder growing inside a card is not the fix.
    const { unmount } = render(
      <AutomationCard automation={auto({ id: 1, enabled: 1, trigger_type: 'weekly_time' })} />,
    );
    expect(editable()).toBeNull();
    unmount();
    render(<AutomationCard automation={auto({ id: 2, enabled: 1, trigger_type: 'app_started' })} />);
    expect(editable()).toBeNull();
  });

  it('discards an abandoned edit rather than reopening on it', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          trigger_type: 'schedule',
          trigger_config: { interval: 6, unit: 'hours' },
        })}
      />,
    );
    fireEvent.click(editable()!);
    fireEvent.change(screen.getByLabelText('Interval'), { target: { value: '99' } });
    fireEvent.click(screen.getByText('Cancel'));
    expect(updateAutomationTrigger).not.toHaveBeenCalled();
    fireEvent.click(editable()!);
    expect((screen.getByLabelText('Interval') as HTMLInputElement).value).toBe('6');
  });
});

describe('a failed run is visible from across the room', () => {
  const meta = () => document.querySelector('.automation-meta')!.textContent ?? '';

  it('marks the card and leads the meta line with the failure', () => {
    render(
      <AutomationCard
        automation={auto({
          id: 1,
          enabled: 1,
          last_run: '2026-08-12 09:00:00',
          last_error: 'boom',
          run_count: 41,
        })}
      />,
    );
    expect(card().className).toContain('errored');
    // Worst news first: the failure used to sit LAST, after "Runs: 41".
    expect(meta().indexOf('Failed')).toBeLessThan(meta().indexOf('Last:'));
    expect(document.querySelector('.auto-meta-fail')?.getAttribute('title')).toBe('boom');
  });

  it('does not mark a card that has never failed', () => {
    render(<AutomationCard automation={auto({ id: 1, enabled: 1, run_count: 3 })} />);
    expect(card().className).not.toContain('errored');
  });

  it('reads as busy rather than broken while a new run is in flight', () => {
    // The error describes the LAST run; a card mid-run should not still be red.
    render(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1, last_error: 'boom' })}
        progress={{ status: 'running', progress: 20, log: [] }}
      />,
    );
    expect(card().className).toContain('running');
    expect(card().className).not.toContain('errored');
  });
});
