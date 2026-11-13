import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import type { Automation } from '../-automations.types';

import { AutomationCard } from './automation-card';

const updateAutomationTrigger = vi.hoisted(() => vi.fn(async () => {}));
const history = vi.hoisted(() => ({ rows: [] as unknown[] }));
vi.mock('../-automations.api', () => ({
  updateAutomationTrigger,
  automationRunsQueryOptions: (id: number) => ({
    queryKey: ['automations', 'runs', id],
    queryFn: async () => ({ history: history.rows }),
  }),
}));

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, ...over };
}

function show(ui: React.ReactElement) {
  return render(<QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>);
}

const item = () => document.querySelector('.automx-item')!;
/** The schedule cell: one state, the row's most-asked question. */
const next = () => document.querySelector('.automx-next')!;
const openRow = () => fireEvent.click(document.querySelector('.automx-row')!);

beforeEach(() => {
  updateAutomationTrigger.mockClear();
  history.rows = [];
});

describe('AutomationCard row', () => {
  it('carries the id and types the filters and the drag code read', () => {
    show(
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
    const el = item();
    expect(el.className).toBe('automx-item sched-unscheduled');
    expect(el.getAttribute('data-id')).toBe('7');
    expect(el.getAttribute('data-trigger-type')).toBe('schedule');
    expect(el.getAttribute('data-action-type')).toBe('process_wishlist');
  });

  it('marks switched-off and system rows', () => {
    show(<AutomationCard automation={auto({ id: 1, enabled: 0, is_system: 1 })} />);
    expect(item().className).toBe('automx-item sched-off off system');
  });

  it('drops the prefixes the section already says', () => {
    show(<AutomationCard automation={auto({ id: 1, name: '[System] Dead File Cleaner' })} />);
    expect(screen.getByText('Dead File Cleaner')).toBeInTheDocument();
    // the full name is still what a screen reader and the tooltip get
    expect(screen.getByLabelText('Run [System] Dead File Cleaner now')).toBeInTheDocument();
  });

  it('says what it does as when, then do, in words', () => {
    show(
      <AutomationCard
        automation={auto({
          id: 1,
          trigger_type: 'schedule',
          trigger_config: { interval: 168, unit: 'hours' },
          action_type: 'run_repair_job',
          action_config: { delay: 5 },
          then_actions: [{ type: 'discord_webhook' }],
        })}
      />,
    );
    const flow = document.querySelector('.automx-row-flow')!.textContent ?? '';
    // a weekly job read "Every 168 hours"
    expect(flow).toMatch(/^Every week → wait 5m → Run Maintenance Job → /);
    expect(flow).toMatch(/Discord/);
  });

  it('names the playlist a sync is for', () => {
    show(
      <AutomationCard
        automation={auto({
          id: 1,
          name: 'Auto-Sync: Fresh House',
          action_type: 'playlist_pipeline',
          playlist: { id: 9, name: 'Fresh House', image_url: 'https://img/cover.jpg' },
        })}
      />,
    );
    expect(document.querySelector('.automx-row-flow')!.textContent).toMatch(/Sync Fresh House$/);
    expect(document.querySelector('.automx-tile img')?.getAttribute('src')).toBe(
      'https://img/cover.jpg',
    );
  });

  it('shows the last result in words, and a failure in its place', () => {
    const { unmount } = show(
      <AutomationCard
        automation={auto({
          id: 1,
          action_type: 'scan_watchlist',
          last_run: '2026-01-01 00:00:00',
          last_result: { status: 'completed', artists_scanned: 68, tracks_added_to_wishlist: 10 },
        })}
      />,
    );
    expect(document.querySelector('.automx-row-last')!.textContent).toBe(
      'Checked 68 artists, wishlisted 10 tracks',
    );
    unmount();
    show(
      <AutomationCard
        automation={auto({ id: 2, last_error: 'boom', last_result: { status: 'completed' } })}
      />,
    );
    const last = document.querySelector('.automx-row-last')!;
    expect(last.textContent).toBe('Failed: boom');
    expect(last.className).toContain('bad');
    expect(item().className).toContain('errored');
  });

  it('reads as running, not broken, while a run is in flight', () => {
    show(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1, last_error: 'boom' })}
        progress={{ status: 'running', progress: 40, phase: 'Scanning artists' }}
      />,
    );
    expect(item().className).toContain('running');
    expect(item().className).not.toContain('errored');
    expect(document.querySelector('.automx-row-last')!.textContent).toBe('Scanning artists');
    expect((document.querySelector('.automx-edge') as HTMLElement).style.width).toBe('40%');
    expect(next().textContent).toBe('Running now');
  });
});

describe('AutomationCard schedule cell', () => {
  const timer = (over: Partial<Automation> = {}) =>
    auto({
      id: 1,
      enabled: 1,
      trigger_type: 'schedule',
      next_run: new Date(Date.now() + 3 * 3600_000).toISOString(),
      ...over,
    });

  it('counts down while the side is live', () => {
    show(<AutomationCard automation={timer()} />);
    expect(next().textContent).toBe('in 3h');
    expect(next().className).toContain('waiting');
  });

  it('replaces the countdown with Paused when the side is paused', () => {
    show(<AutomationCard automation={timer()} paused />);
    expect(next().textContent).toBe('Paused');
  });

  it('stops an event automation claiming to listen while paused', () => {
    const { unmount } = show(
      <AutomationCard automation={auto({ id: 1, enabled: 1, trigger_type: 'batch_complete' })} />,
    );
    expect(next().textContent).toBe('Listening');
    unmount();
    show(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1, trigger_type: 'batch_complete' })}
        paused
      />,
    );
    expect(next().textContent).toBe('Paused');
  });

  it('says switched off, not paused, for one the user turned off', () => {
    show(<AutomationCard automation={timer({ enabled: 0 })} paused />);
    expect(next().textContent).toBe('Switched off');
  });
});

describe('AutomationCard actions', () => {
  it('toggles and runs from the row without opening it', () => {
    const onToggle = vi.fn();
    const onRun = vi.fn();
    const a = auto({ id: 3, name: 'Nightly', enabled: 1 });
    show(<AutomationCard automation={a} onToggle={onToggle} onRun={onRun} />);

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    fireEvent.click(screen.getByLabelText('Run Nightly now'));
    expect(onToggle).toHaveBeenCalledWith(a);
    expect(onRun).toHaveBeenCalledWith(a);
    expect(document.querySelector('.automx-detail')).toBeNull();
  });

  it('opens to every other action, each calling its handler', () => {
    const on = {
      onRun: vi.fn(),
      onEdit: vi.fn(),
      onShowHistory: vi.fn(),
      onDuplicate: vi.fn(),
      onAssignGroup: vi.fn(),
      onDelete: vi.fn(),
    };
    const a = auto({ id: 3, name: 'Nightly', enabled: 1 });
    show(<AutomationCard automation={a} {...on} />);
    openRow();

    for (const [label, handler] of [
      ['Run now', on.onRun],
      ['Edit', on.onEdit],
      ['Run history', on.onShowHistory],
      ['Duplicate', on.onDuplicate],
      ['Move to group', on.onAssignGroup],
      ['Delete', on.onDelete],
    ] as const) {
      fireEvent.click(screen.getByRole('button', { name: label }));
      expect(handler, label).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0], label).toBe(a);
    }
  });

  it('gives a system automation no duplicate, group or delete', () => {
    show(<AutomationCard automation={auto({ id: 1, is_system: 1 })} />);
    openRow();
    expect(screen.getByRole('button', { name: 'Run history' })).toBeInTheDocument();
    for (const label of ['Duplicate', 'Move to group', 'Delete']) {
      expect(screen.queryByRole('button', { name: label })).toBeNull();
    }
  });

  it('opens from the chevron too, and tells its owner', () => {
    const onOpenChange = vi.fn();
    show(
      <AutomationCard automation={auto({ id: 1, name: 'Nightly' })} onOpenChange={onOpenChange} />,
    );
    fireEvent.click(screen.getByLabelText('Show details for Nightly'));
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(document.querySelector('.automx-detail')).not.toBeNull();
    expect(screen.getByLabelText('Hide details for Nightly')).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('stays shut when its owner says so', () => {
    show(<AutomationCard automation={auto({ id: 1 })} open={false} />);
    openRow();
    expect(document.querySelector('.automx-detail')).toBeNull();
  });
});

describe('AutomationCard detail', () => {
  it('shows what the last run counted, and how many runs there have been', () => {
    show(
      <AutomationCard
        automation={auto({
          id: 1,
          run_count: 146,
          last_result: {
            status: 'completed',
            artists_scanned: 68,
            new_tracks_found: '10',
            _manages_own_progress: true,
            duration_seconds: 21,
            backup_path: 'x.db',
          },
        })}
        open
      />,
    );
    const figures = [...document.querySelectorAll('.automx-figures div')].map((d) => d.textContent);
    expect(figures).toEqual(['68artists scanned', '10new tracks found', '146runs so far']);
  });

  it('lists the last stored runs with their own words', async () => {
    history.rows = [
      { id: 2, status: 'error', summary: 'Reconnect MetaSync.', duration_seconds: 0.2 },
      { id: 1, status: 'completed', summary: 'completed', duration_seconds: 2100 },
    ];
    show(<AutomationCard automation={auto({ id: 1 })} open />);
    await screen.findByText('Reconnect MetaSync.');
    const runs = [...document.querySelectorAll('.automx-run')];
    expect(runs.map((r) => r.querySelector('.automx-run-what')!.textContent)).toEqual([
      'Reconnect MetaSync.',
      'Completed',
    ]);
    expect(runs.map((r) => r.querySelector('.automx-run-took')!.textContent)).toEqual([
      '<1s',
      '35m',
    ]);
    expect(runs[0].querySelector('.automx-run-dot')!.className).toContain('error');
  });

  it('says so when nothing is kept', async () => {
    show(<AutomationCard automation={auto({ id: 1 })} open />);
    await screen.findByText('No runs kept yet.');
  });

  it('streams the run log inside the opened row', () => {
    show(
      <AutomationCard
        automation={auto({ id: 1, enabled: 1 })}
        open
        progress={{
          status: 'running',
          progress: 10,
          phase: 'Working',
          log: [{ type: 'info', text: 'step one' }],
        }}
      />,
    );
    const panel = document.querySelector('.automation-output')!;
    expect(panel.className).toContain('visible');
    expect(panel.querySelector('.auto-progress-phase')!.textContent).toBe('Working');
    expect(panel.querySelector('.auto-log-line')!.textContent).toBe('step one');
  });
});

describe('AutomationCard cadence', () => {
  const scheduled = (config: Record<string, unknown>, type = 'schedule') =>
    auto({ id: 9, name: 'Nightly', enabled: 1, trigger_type: type, trigger_config: config });

  it('changes an interval in place, keeping the rest of the config', async () => {
    const onRefresh = vi.fn();
    show(
      <AutomationCard
        automation={scheduled({ interval: 6, unit: 'hours', jitter: 3 })}
        open
        onRefresh={onRefresh}
      />,
    );
    fireEvent.click(screen.getByTitle('Change when this runs'));
    fireEvent.change(screen.getByLabelText('Interval'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Interval unit'), { target: { value: 'days' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateAutomationTrigger).toHaveBeenCalledTimes(1));
    expect(updateAutomationTrigger).toHaveBeenCalledWith(9, {
      interval: 2,
      unit: 'days',
      jitter: 3,
    });
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it('changes a daily time without dropping its timezone', async () => {
    show(
      <AutomationCard
        automation={scheduled({ time: '03:00', tz: 'Europe/Berlin' }, 'daily_time')}
        open
      />,
    );
    fireEvent.click(screen.getByTitle('Change when this runs'));
    fireEvent.change(screen.getByLabelText('Time of day'), { target: { value: '04:30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updateAutomationTrigger).toHaveBeenCalledWith(9, {
        time: '04:30',
        tz: 'Europe/Berlin',
      }),
    );
  });

  it('cancel leaves the schedule alone', () => {
    show(<AutomationCard automation={scheduled({ interval: 6, unit: 'hours' })} open />);
    fireEvent.click(screen.getByTitle('Change when this runs'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(updateAutomationTrigger).not.toHaveBeenCalled();
    expect(screen.getByTitle('Change when this runs').textContent).toBe('WhenEvery 6 hours');
  });

  it('sends a weekly schedule to the builder instead of editing it here', () => {
    show(
      <AutomationCard
        automation={scheduled({ time: '02:00', days: ['mon'] }, 'weekly_time')}
        open
      />,
    );
    expect(screen.queryByTitle('Change when this runs')).toBeNull();
    expect(document.querySelector('.automx-block')!.textContent).toBe('WhenMon at 02:00');
  });
});
