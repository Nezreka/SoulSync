/**
 * Differential tests for the Auto-Sync monitor and read-only Automations
 * panels — auto-sync.js 1104-1198 and 1883-1918.
 */

import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AutomationRow, MirroredRow, PipelineState } from '../-sync.autosync';

import {
  AutoSyncAutomationPanel,
  AutoSyncMonitorCard,
  AutoSyncMonitorPanel,
} from './autosync-panels';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

const row = (id: number, name: string, state: PipelineState | null = null): MirroredRow => ({
  id,
  name,
  source: 'spotify',
  track_count: 10,
  pipeline_state: state,
});

afterEach(() => {
  delete window._autoFormatTrigger;
});

describe('AutoSyncMonitorPanel (1131-1160)', () => {
  const renderPanel = (playlists: MirroredRow[]) => {
    const onDetails = vi.fn();
    const onRefresh = vi.fn();
    return {
      onDetails,
      onRefresh,
      ...render(
        <AutoSyncMonitorPanel playlists={playlists} onDetails={onDetails} />,
      ),
    };
  };

  it('says nothing is running, with the nudge copy, when nothing is', () => {
    const { container } = renderPanel([row(1, 'A')]);
    expect(container.querySelector('.auto-sync-monitor-head strong')?.textContent).toBe(
      'No pipelines running',
    );
    expect(container.querySelector('.auto-sync-monitor-head small')?.textContent).toBe(
      'Use Run now on a scheduled playlist when you want the pipeline immediately.',
    );
    expect(container.querySelector('.auto-sync-monitor-empty small')?.textContent).toBe(
      'Scheduled playlists appear here while the all-in-one pipeline runs.',
    );
    expect(container.querySelector('.auto-sync-monitor-list')).toBeNull();
  });

  it('hides IDLE playlists from the monitor entirely (1107)', () => {
    const { container } = renderPanel([row(1, 'A', { status: 'idle' }), row(2, 'B', {})]);
    expect(container.querySelector('.auto-sync-monitor-empty')).not.toBeNull();
  });

  it('counts running pipelines and pluralises', () => {
    const one = renderPanel([row(1, 'A', { status: 'running' })]);
    expect(one.container.querySelector('.auto-sync-monitor-head strong')?.textContent).toBe(
      '1 pipeline running',
    );
    const two = renderPanel([
      row(1, 'A', { status: 'running' }),
      row(2, 'B', { status: 'running' }),
    ]);
    expect(two.container.querySelector('.auto-sync-monitor-head strong')?.textContent).toBe(
      '2 pipelines running',
    );
    expect(two.container.querySelector('.auto-sync-monitor-head small')?.textContent).toBe(
      'Live status refreshes while this modal is open.',
    );
  });

  it('puts running pipelines ahead of finished ones', () => {
    const { container } = renderPanel([
      row(1, 'Done', { status: 'finished', finished_at: 900 }),
      row(2, 'Live', { status: 'running', started_at: 100 }),
    ]);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['Live', 'Done']);
  });

  it('orders the non-running rows most-recent first', () => {
    const { container } = renderPanel([
      row(1, 'Older', { status: 'finished', finished_at: 100 }),
      row(2, 'Newer', { status: 'finished', finished_at: 900 }),
    ]);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['Newer', 'Older']);
  });

  it('falls back to started_at when a row never finished (1112)', () => {
    const { container } = renderPanel([
      row(1, 'Started early', { status: 'error', started_at: 100 }),
      row(2, 'Started late', { status: 'error', started_at: 900 }),
    ]);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['Started late', 'Started early']);
  });

  it('shows at most TWO finished rows (1134)', () => {
    const { container } = renderPanel([
      row(1, 'A', { status: 'finished', finished_at: 300 }),
      row(2, 'B', { status: 'finished', finished_at: 200 }),
      row(3, 'C', { status: 'finished', finished_at: 100 }),
    ]);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['A', 'B']);
  });

  it('caps the whole list at FOUR, so running work crowds out history (1135)', () => {
    const playlists = [
      row(1, 'R1', { status: 'running' }),
      row(2, 'R2', { status: 'running' }),
      row(3, 'R3', { status: 'running' }),
      row(4, 'R4', { status: 'running' }),
      row(5, 'F1', { status: 'finished', finished_at: 900 }),
    ];
    const { container } = renderPanel(playlists);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['R1', 'R2', 'R3', 'R4']);
  });

  it('shows every running pipeline even past four, before any recent one', () => {
    const playlists = [
      row(1, 'F1', { status: 'finished', finished_at: 900 }),
      ...[2, 3, 4, 5, 6].map((i) => row(i, `R${i}`, { status: 'running' })),
    ];
    const { container } = renderPanel(playlists);
    const names = Array.from(container.querySelectorAll('.auto-sync-monitor-title-row strong')).map(
      (e) => e.textContent,
    );
    expect(names).toEqual(['R2', 'R3', 'R4', 'R5']);
  });

  it('the monitor head has no Refresh — the modal header owns the only one', () => {
    const { container } = renderPanel([]);
    expect(container.querySelector('.auto-sync-monitor-head button')).toBeNull();
  });
});

describe('AutoSyncMonitorCard (1162-1182)', () => {
  const renderCard = (state: PipelineState, playlist = row(7, 'Late Night')) => {
    const onDetails = vi.fn();
    return {
      onDetails,
      ...render(<AutoSyncMonitorCard playlist={playlist} state={state} onDetails={onDetails} />),
    };
  };

  it('maps each status to its label and its class', () => {
    const cases: [string, string, string][] = [
      ['running', 'Running', 'running'],
      ['finished', 'Completed', 'finished'],
      ['skipped', 'Skipped', 'error'],
      ['error', 'Needs attention', 'error'],
      ['whatever', 'Idle', 'idle'],
    ];
    for (const [status, label, cls] of cases) {
      const { container } = renderCard({ status, phase: '' });
      expect(container.querySelector('.auto-sync-monitor-title-row span')?.textContent).toBe(label);
      expect(container.querySelector('.auto-sync-monitor-card')?.className).toContain(cls);
    }
  });

  it("prefers the backend's phase over the status label (1166)", () => {
    const { container } = renderCard({ status: 'running', phase: 'Discovering tracks' });
    expect(container.querySelector('.auto-sync-monitor-phase')?.textContent).toBe(
      'Discovering tracks',
    );
    const { container: none } = renderCard({ status: 'running' });
    expect(none.querySelector('.auto-sync-monitor-phase')?.textContent).toBe('Running');
  });

  it('clamps the progress bar and labels it for screen readers', () => {
    const { container } = renderCard({ status: 'running', progress: 42 });
    const bar = container.querySelector('.auto-sync-monitor-progress') as HTMLElement;
    expect(bar.getAttribute('aria-label')).toBe('42% complete');
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('42%');
  });

  it('clamps a nonsense progress value rather than overflowing the bar', () => {
    for (const [given, want] of [
      [200, '100%'],
      [-5, '0%'],
      ['nope', '0%'],
      [undefined, '0%'],
    ] as [unknown, string][]) {
      const { container } = renderCard({ status: 'running', progress: given as number });
      const inner = container.querySelector('.auto-sync-monitor-progress div') as HTMLElement;
      expect(inner.style.width).toBe(want);
    }
  });

  it('shows the LAST log line, not the first (1165)', () => {
    const { container } = renderCard({
      status: 'running',
      log: [{ message: 'first' }, { message: 'newest' }],
    });
    expect(container.querySelector('.auto-sync-monitor-card-main small')?.textContent).toBe(
      'newest',
    );
  });

  it('omits the log line when there is none', () => {
    const { container } = renderCard({ status: 'running', log: [] });
    expect(container.querySelector('.auto-sync-monitor-card-main small')).toBeNull();
  });

  it('falls back to the id when a playlist has no name', () => {
    const { container } = renderCard({ status: 'running' }, { id: 12, source: 'spotify' });
    expect(container.querySelector('.auto-sync-monitor-title-row strong')?.textContent).toBe(
      'Playlist #12',
    );
  });

  it('opens the details modal for that playlist', () => {
    const { container, onDetails } = renderCard({ status: 'running' });
    fireEvent.click(container.querySelector('.auto-sync-monitor-card button') as HTMLElement);
    expect(onDetails).toHaveBeenCalledWith(7);
  });
});

describe('AutoSyncAutomationPanel (1185-1198, 1883-1918)', () => {
  const auto = (over: Partial<AutomationRow> = {}): AutomationRow => ({
    id: 1,
    name: 'Nightly pipeline',
    trigger_type: 'schedule',
    trigger_config: { interval: 6, unit: 'hours' },
    // The panel is only ever fed playlist_pipeline rows, and
    // autoSyncPlaylistIdFromAutomation refuses to resolve an id without it.
    action_type: 'playlist_pipeline',
    action_config: { playlist_id: 5 },
    enabled: true,
    next_run: null,
    ...over,
  });

  const renderPanel = (automations: AutomationRow[], playlists: MirroredRow[] = []) =>
    render(
      <AutoSyncAutomationPanel automationPipelines={automations} playlists={playlists} now={NOW} />,
    );

  it('says so when there are none', () => {
    const { container } = renderPanel([]);
    expect(container.querySelector('.auto-sync-automation-empty')?.textContent).toBe(
      'No Automations-page playlist pipelines found.',
    );
    expect(container.querySelector('.auto-sync-automation-intro')).toBeNull();
  });

  it('marks every card read-only', () => {
    const { container } = renderPanel([auto()]);
    expect(container.querySelector('.auto-sync-automation-lock')?.textContent).toBe('Read only');
    expect(container.querySelector('.auto-sync-automation-intro strong')?.textContent).toBe(
      'Read-only Automations-page pipelines',
    );
  });

  it('formats an interval trigger', () => {
    const { container } = renderPanel([auto()]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('Every 6 hours');
  });

  it('formats daily, weekly and signal triggers', () => {
    const daily = renderPanel([
      auto({ trigger_type: 'daily_time', trigger_config: { time: '07:30' } }),
    ]);
    expect(daily.container.querySelector('.flow-trigger')?.textContent).toBe('Daily at 07:30');

    const weekly = renderPanel([
      auto({
        trigger_type: 'weekly_time',
        trigger_config: { days: ['mon', 'fri'], time: '09:00' },
      }),
    ]);
    expect(weekly.container.querySelector('.flow-trigger')?.textContent).toBe('Mon, Fri at 09:00');

    const sig = renderPanel([
      auto({ trigger_type: 'signal_received', trigger_config: { signal_name: 'go' } }),
    ]);
    expect(sig.container.querySelector('.flow-trigger')?.textContent).toBe('Signal: go');
  });

  it('defaults an empty weekly day set to "Every day" (4159)', () => {
    const { container } = renderPanel([
      auto({ trigger_type: 'weekly_time', trigger_config: { days: [], time: '09:00' } }),
    ]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('Every day at 09:00');
  });

  it('uses the mapped label for an event trigger', () => {
    const { container } = renderPanel([
      auto({ trigger_type: 'playlist_synced', trigger_config: {} }),
    ]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('Playlist Synced');
  });

  it('appends a condition summary, with a +N for the rest (4181-4185)', () => {
    const one = renderPanel([
      auto({
        trigger_type: 'playlist_synced',
        trigger_config: { conditions: [{ field: 'name', operator: 'is', value: 'x' }] },
      }),
    ]);
    expect(one.container.querySelector('.flow-trigger')?.textContent).toBe(
      'Playlist Synced (name is "x")',
    );

    const many = renderPanel([
      auto({
        trigger_type: 'playlist_synced',
        trigger_config: {
          conditions: [
            { field: 'name', operator: 'is', value: 'x' },
            { field: 'a', operator: 'is', value: 'b' },
            { field: 'c', operator: 'is', value: 'd' },
          ],
        },
      }),
    ]);
    expect(many.container.querySelector('.flow-trigger')?.textContent).toBe(
      'Playlist Synced (name is "x" +2 more)',
    );
  });

  it('humanizes an unmapped trigger rather than showing snake_case', () => {
    const { container } = renderPanel([
      auto({ trigger_type: 'video_deep_scan_library', trigger_config: {} }),
    ]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('Deep Scan Library');
  });

  it('consults the vanilla global only for an UNMAPPED type', () => {
    window._autoFormatTrigger = vi.fn(() => 'From block defs');
    const unmapped = renderPanel([auto({ trigger_type: 'monthly_time', trigger_config: {} })]);
    expect(unmapped.container.querySelector('.flow-trigger')?.textContent).toBe('From block defs');

    // A mapped label always wins, so the global is never asked.
    (window._autoFormatTrigger as ReturnType<typeof vi.fn>).mockClear();
    const mapped = renderPanel([auto({ trigger_type: 'playlist_synced', trigger_config: {} })]);
    expect(mapped.container.querySelector('.flow-trigger')?.textContent).toBe('Playlist Synced');
    expect(window._autoFormatTrigger).not.toHaveBeenCalled();
  });

  it('degrades to the humanized form when the global is absent', () => {
    const { container } = renderPanel([auto({ trigger_type: 'monthly_time', trigger_config: {} })]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('Monthly Time');
  });

  it('names the target playlist, or the id when it is not loaded', () => {
    const named = renderPanel([auto()], [row(5, 'Late Night')]);
    const meta = Array.from(
      named.container.querySelectorAll('.auto-sync-automation-meta span'),
    ).map((e) => e.textContent);
    expect(meta).toEqual(['Enabled', 'Spotify', 'Late Night', 'not scheduled']);

    const missing = renderPanel([auto()], []);
    expect(
      Array.from(missing.container.querySelectorAll('.auto-sync-automation-meta span')).map(
        (e) => e.textContent,
      ),
    ).toEqual(['Enabled', 'Pipeline', 'Playlist #5', 'not scheduled']);
  });

  it('recognises an all-playlists pipeline written either way (1887)', () => {
    for (const all of [true, 'true']) {
      const { container } = renderPanel([auto({ action_config: { all } })]);
      const meta = Array.from(container.querySelectorAll('.auto-sync-automation-meta span')).map(
        (e) => e.textContent,
      );
      expect(meta[1]).toBe('All sources');
      expect(meta[2]).toBe('All refreshable mirrored playlists');
    }
  });

  it('refuses to resolve a target for a NON-pipeline automation (256)', () => {
    const { container } = renderPanel(
      [auto({ action_type: 'sync_playlist' })],
      [row(5, 'Late Night')],
    );
    expect(
      Array.from(container.querySelectorAll('.auto-sync-automation-meta span'))[2]?.textContent,
    ).toBe('Custom pipeline target');
  });

  it('falls back to a generic target when there is no playlist at all', () => {
    const { container } = renderPanel([auto({ action_config: {} })]);
    expect(
      Array.from(container.querySelectorAll('.auto-sync-automation-meta span'))[2]?.textContent,
    ).toBe('Custom pipeline target');
  });

  it("says 'not scheduled' rather than nothing when there is no next run (1892)", () => {
    const { container } = renderPanel([auto({ next_run: null })]);
    expect(
      Array.from(container.querySelectorAll('.auto-sync-automation-meta span'))[3]?.textContent,
    ).toBe('not scheduled');
  });

  it('renders a real next-run label when there is one', () => {
    const soon = new Date(NOW + 2 * 3600_000).toISOString().replace('Z', '');
    const { container } = renderPanel([auto({ next_run: soon })]);
    expect(
      Array.from(container.querySelectorAll('.auto-sync-automation-meta span'))[3]?.textContent,
    ).toBe('next in 2h');
  });

  it('treats enabled as tri-state, matching the schedule builder', () => {
    for (const [enabled, want] of [
      [true, 'Enabled'],
      [undefined, 'Enabled'],
      [1, 'Enabled'],
      [false, 'Disabled'],
      [0, 'Disabled'],
    ] as [unknown, string][]) {
      const { container } = renderPanel([auto({ enabled })]);
      expect(container.querySelector('.auto-sync-status')?.textContent).toBe(want);
      expect(container.querySelector('.auto-sync-card-status-dot')?.className).toContain(
        want.toLowerCase(),
      );
    }
  });

  it('falls back to a generic name for an unnamed automation', () => {
    const { container } = renderPanel([auto({ name: undefined })]);
    expect(container.querySelector('.auto-sync-automation-title-row strong')?.textContent).toBe(
      'Playlist Pipeline',
    );
  });
});
