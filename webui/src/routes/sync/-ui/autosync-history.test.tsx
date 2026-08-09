/**
 * Differential tests for the Auto-Sync run-history panel — auto-sync.js
 * 1200-1253 and 1394-1882.
 */

import { fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AutoSyncHistoryEntry, AutoSyncHistoryFilter } from '../-sync.autosync';

import { AutoSyncHistoryPanel } from './autosync-history';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

const entry = (over: Partial<AutoSyncHistoryEntry> = {}): AutoSyncHistoryEntry => ({
  id: 1,
  playlist_id: 5,
  playlist_name: 'Late Night',
  status: 'completed',
  trigger_source: 'auto_sync',
  before_json: { track_count: 10 },
  after_json: { track_count: 12 },
  result_json: {},
  ...over,
});

function renderPanel(
  history: AutoSyncHistoryEntry[],
  over: Partial<React.ComponentProps<typeof AutoSyncHistoryPanel>> = {},
) {
  const props = {
    history,
    total: history.length,
    filter: 'all' as AutoSyncHistoryFilter,
    onFilterChange: vi.fn(),
    onLoadMore: vi.fn(),
    onRefresh: vi.fn(),
    onRunAgain: vi.fn(),
    now: NOW,
    ...over,
  };
  return { props, ...render(<AutoSyncHistoryPanel {...props} />) };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the empty and filtered-empty states (1201-1208, 1407-1418)', () => {
  it('short-circuits to the no-runs copy before any chrome', () => {
    const { container } = renderPanel([]);
    expect(container.querySelector('.auto-sync-history-empty strong')?.textContent).toBe(
      'No playlist pipeline runs yet',
    );
    // No filter tabs, no refresh button — the whole panel is the empty state.
    expect(container.querySelector('.auto-sync-history-filters')).toBeNull();
    expect(container.querySelector('.auto-sync-history-intro')).toBeNull();
  });

  it('KEEPS the chrome when a filter matches nothing, and says which', () => {
    const { container } = renderPanel([entry({ status: 'completed' })], { filter: 'error' });
    expect(container.querySelector('.auto-sync-history-filters')).not.toBeNull();
    expect(container.querySelector('.auto-sync-history-list strong')?.textContent).toBe(
      'No failed runs in the loaded window',
    );
    expect(container.querySelector('.auto-sync-history-list span')?.textContent).toBe(
      'Switch filters or load more history.',
    );
  });

  it('uses the completed wording for the other empty filter', () => {
    const { container } = renderPanel([entry({ status: 'error' })], { filter: 'completed' });
    expect(container.querySelector('.auto-sync-history-list strong')?.textContent).toBe(
      'No completed runs in the loaded window',
    );
  });
});

describe('the filter tabs (1210-1219)', () => {
  const mixed = [
    entry({ id: 1, status: 'completed' }),
    entry({ id: 2, status: 'finished' }),
    entry({ id: 3, status: 'error' }),
    entry({ id: 4, status: 'skipped' }),
  ];

  it('counts against the WHOLE window, not the filtered view', () => {
    // Rendered while Errors is active: All and Completed must still be right.
    const { container } = renderPanel(mixed, { filter: 'error' });
    const counts = Array.from(container.querySelectorAll('.auto-sync-history-filter-btn')).map(
      (b) => b.textContent?.trim(),
    );
    expect(counts).toEqual(['All 4', 'Errors 2', 'Completed 2']);
  });

  it("counts 'skipped' as an error and 'finished' as completed", () => {
    const { container } = renderPanel([entry({ status: 'skipped' })]);
    const counts = Array.from(container.querySelectorAll('.auto-sync-history-filter-btn em')).map(
      (e) => e.textContent,
    );
    expect(counts).toEqual(['1', '1', '0']);
  });

  it('marks the active tab, and only that one', () => {
    const { container } = renderPanel(mixed, { filter: 'completed' });
    const active = Array.from(container.querySelectorAll('.auto-sync-history-filter-btn'))
      .filter((b) => b.className.includes('active'))
      .map((b) => b.textContent?.trim());
    expect(active).toEqual(['Completed 2']);
  });

  it('flags the Errors tab only when there ARE errors', () => {
    const { container: withErrors } = renderPanel(mixed);
    const errTab = withErrors.querySelectorAll('.auto-sync-history-filter-btn')[1];
    expect(errTab.className).toContain('has-errors');

    const { container: clean } = renderPanel([entry({ status: 'completed' })]);
    expect(clean.querySelectorAll('.auto-sync-history-filter-btn')[1].className).not.toContain(
      'has-errors',
    );
    // ...and never on the other two, even though All is non-zero.
    expect(clean.querySelectorAll('.auto-sync-history-filter-btn')[0].className).not.toContain(
      'has-errors',
    );
  });

  it('reports the chosen filter', () => {
    const { container, props } = renderPanel(mixed);
    fireEvent.click(container.querySelectorAll('.auto-sync-history-filter-btn')[1]);
    expect(props.onFilterChange).toHaveBeenCalledWith('error');
  });

  it('narrows the rendered rows', () => {
    const { container } = renderPanel(mixed, { filter: 'error' });
    expect(container.querySelectorAll('.auto-sync-history-entry')).toHaveLength(2);
  });
});

describe('load more and the running total (1235-1241, 1433-1438)', () => {
  it('offers load-more only when the window is short of the total', () => {
    const { container } = renderPanel([entry()], { total: 10 });
    const btn = container.querySelector('.auto-sync-history-load-more');
    expect(btn?.textContent?.trim()).toBe('Load more (1 of 10)');

    const { container: whole } = renderPanel([entry()], { total: 1 });
    expect(whole.querySelector('.auto-sync-history-load-more')).toBeNull();
  });

  it('compares load-more against the UNFILTERED window (1234)', () => {
    // 2 loaded of 5 total, but only 1 matches the filter. Load-more still
    // offers, because there are more RUNS to fetch — not more matches.
    const { container } = renderPanel(
      [entry({ id: 1, status: 'error' }), entry({ id: 2, status: 'completed' })],
      { total: 5, filter: 'error' },
    );
    expect(container.querySelector('.auto-sync-history-load-more')?.textContent?.trim()).toBe(
      'Load more (2 of 5)',
    );
  });

  it('separates the two comparisons: total vs WINDOW, total vs VISIBLE', () => {
    // 2 loaded of 2 total, 1 matching. Load-more must NOT offer (nothing left
    // to fetch) while the running total MUST show (rows are hidden by filter).
    const { container } = renderPanel(
      [entry({ id: 1, status: 'error' }), entry({ id: 2, status: 'completed' })],
      { total: 2, filter: 'error' },
    );
    expect(container.querySelector('.auto-sync-history-load-more')).toBeNull();
    expect(container.querySelector('.auto-sync-history-total')?.textContent).toBe(
      'Showing 1 of 2 runs',
    );
  });

  it('puts the running total inside the list', () => {
    const { container } = renderPanel([entry()], { total: 9 });
    const list = container.querySelector('.auto-sync-history-list') as HTMLElement;
    expect(list.querySelector('.auto-sync-history-total')?.textContent).toBe('Showing 1 of 9 runs');
  });

  it('omits the running total when everything is shown', () => {
    const { container } = renderPanel([entry()], { total: 1 });
    expect(container.querySelector('.auto-sync-history-total')).toBeNull();
  });

  it('wires load-more and refresh', () => {
    const { container, props } = renderPanel([entry()], { total: 10 });
    fireEvent.click(container.querySelector('.auto-sync-history-load-more') as HTMLElement);
    expect(props.onLoadMore).toHaveBeenCalledTimes(1);
    fireEvent.click(
      container.querySelector('.auto-sync-history-intro-controls > button') as HTMLElement,
    );
    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });
});

describe('the entry card (1478-1572)', () => {
  it('shows the playlist, the flow and the track delta', () => {
    const { container } = renderPanel([entry()]);
    expect(container.querySelector('.auto-sync-history-name')?.textContent).toBe('Late Night');
    expect(container.querySelector('.auto-sync-history-flow')?.textContent).toBe(
      'auto_sync->Refresh->Discover->Sync + wishlist',
    );
    const delta = container.querySelector('.auto-sync-history-delta') as HTMLElement;
    expect(delta.textContent).toBe('12 tracks (+2)');
    expect(delta.className).toContain('pos');
  });

  it('colours a negative and a flat delta differently', () => {
    const { container: neg } = renderPanel([
      entry({ before_json: { track_count: 12 }, after_json: { track_count: 10 } }),
    ]);
    expect(neg.querySelector('.auto-sync-history-delta')?.className).toContain('neg');
    expect(neg.querySelector('.auto-sync-history-delta')?.textContent).toBe('10 tracks (-2)');

    const { container: flat } = renderPanel([
      entry({ before_json: { track_count: 10 }, after_json: { track_count: 10 } }),
    ]);
    expect(flat.querySelector('.auto-sync-history-delta')?.className).toContain('zero');
    // No parenthetical at all when nothing moved.
    expect(flat.querySelector('.auto-sync-history-delta')?.textContent).toBe('10 tracks');
  });

  it('gives a completed run NO status modifier class (1520)', () => {
    for (const status of ['completed', 'finished']) {
      const { container } = renderPanel([entry({ status })]);
      expect(container.querySelector('.auto-sync-history-entry')?.className).not.toContain(
        'auto-sync-history-entry-',
      );
    }
    const { container: err } = renderPanel([entry({ status: 'error' })]);
    expect(err.querySelector('.auto-sync-history-entry')?.className).toContain(
      'auto-sync-history-entry-error',
    );
  });

  it('maps status to a dot and a label', () => {
    const cases: [string, string, string][] = [
      ['completed', 'Completed', 'enabled'],
      ['finished', 'Completed', 'enabled'],
      ['error', 'Error', 'disabled'],
      ['skipped', 'Skipped', 'disabled'],
      ['weird', 'weird', 'enabled'],
    ];
    for (const [status, label, dot] of cases) {
      const { container } = renderPanel([entry({ status })]);
      expect(container.querySelector('.auto-sync-history-status')?.textContent).toBe(label);
      expect(container.querySelector('.auto-sync-card-status-dot')?.className).toContain(dot);
    }
  });

  it('counts minutes below the hour, hours below the day (4267-4269)', () => {
    const at = (ms: number) => new Date(NOW - ms).toISOString().replace('Z', '');
    const cases: [number, string][] = [
      [30_000, 'just now'],
      [30 * 60_000, '30m ago'],
      [5 * 3600_000, '5h ago'],
      [3 * 86400_000, '3d ago'],
    ];
    for (const [ago, want] of cases) {
      const { container } = renderPanel([entry({ started_at: at(ago) })]);
      expect(container.querySelector('.auto-sync-history-time')?.textContent).toBe(want);
    }
  });

  it('shows a relative start time and a formatted duration', () => {
    const started = new Date(NOW - 90 * 60_000).toISOString().replace('Z', '');
    const { container } = renderPanel([entry({ started_at: started, duration_seconds: 75 })]);
    expect(container.querySelector('.auto-sync-history-time')?.textContent).toBe('1h ago');
    expect(container.querySelector('.auto-sync-history-duration')?.textContent).toBe('1m 15s');
  });

  it('omits time and duration when the run recorded neither', () => {
    const { container } = renderPanel([entry()]);
    expect(container.querySelector('.auto-sync-history-time')).toBeNull();
    expect(container.querySelector('.auto-sync-history-duration')).toBeNull();
  });

  it('falls back through after/before name to the playlist id', () => {
    const { container } = renderPanel([
      entry({ playlist_name: undefined, after_json: { name: 'From after' } }),
    ]);
    expect(container.querySelector('.auto-sync-history-name')?.textContent).toBe('From after');

    const { container: none } = renderPanel([
      entry({ playlist_name: undefined, after_json: {}, before_json: {}, playlist_id: 77 }),
    ]);
    expect(none.querySelector('.auto-sync-history-name')?.textContent).toBe('Playlist #77');
  });

  it("defaults the trigger chip to 'pipeline'", () => {
    const { container } = renderPanel([entry({ trigger_source: undefined })]);
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('pipeline');
  });

  it('survives a NULL row, where the vanilla blanks the whole panel', () => {
    // The vanilla's tab counts deref h.status on every render, before the
    // normalizer or the per-row try/catch can help. Declared hardening.
    const { container } = renderPanel([null as never, entry({ id: 2, playlist_name: 'Real' })]);
    expect(container.querySelectorAll('.auto-sync-history-entry')).toHaveLength(2);
    expect(container.querySelector('.auto-sync-history-name')?.textContent).toBe(
      'Playlist pipeline run',
    );
  });

  it('keeps a null row out of the error and completed counts', () => {
    const { container } = renderPanel([null as never]);
    const counts = Array.from(container.querySelectorAll('.auto-sync-history-filter-btn em')).map(
      (e) => e.textContent,
    );
    expect(counts).toEqual(['1', '0', '0']);
  });
});

describe('expanding a row (1650-1664)', () => {
  it('starts collapsed and toggles from the row', () => {
    const { container } = renderPanel([entry()]);
    const row = container.querySelector('.auto-sync-history-row') as HTMLElement;
    expect(row.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('.auto-sync-history-detail')?.className).not.toContain(
      'expanded',
    );

    fireEvent.click(row);
    expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('.auto-sync-history-detail')?.className).toContain('expanded');
    expect(container.querySelector('.auto-sync-history-entry')?.className).toContain('expanded');

    fireEvent.click(row);
    expect(row.getAttribute('aria-expanded')).toBe('false');
  });

  it('toggles from Enter and Space, and nothing else', () => {
    const { container } = renderPanel([entry()]);
    const row = container.querySelector('.auto-sync-history-row') as HTMLElement;
    fireEvent.keyDown(row, { key: 'a' });
    expect(row.getAttribute('aria-expanded')).toBe('false');
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(row.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(row, { key: ' ' });
    expect(row.getAttribute('aria-expanded')).toBe('false');
  });

  it('toggles ONCE from the expand button, not twice through the row (1624)', () => {
    const { container } = renderPanel([entry()]);
    const row = container.querySelector('.auto-sync-history-row') as HTMLElement;
    fireEvent.click(container.querySelector('.auto-sync-history-expand-btn') as HTMLElement);
    // A double-fire would land back on collapsed.
    expect(row.getAttribute('aria-expanded')).toBe('true');
  });

  it('expands only the row that was clicked', () => {
    const { container } = renderPanel([entry({ id: 1 }), entry({ id: 2 })]);
    const rows = container.querySelectorAll('.auto-sync-history-row');
    fireEvent.click(rows[1]);
    expect(rows[0].getAttribute('aria-expanded')).toBe('false');
    expect(rows[1].getAttribute('aria-expanded')).toBe('true');
  });

  it('wires the row to its detail panel for screen readers', () => {
    const { container } = renderPanel([entry({ id: 42 })]);
    const row = container.querySelector('.auto-sync-history-row') as HTMLElement;
    expect(row.getAttribute('aria-controls')).toBe('auto-sync-history-42');
    expect(container.querySelector('.auto-sync-history-detail')?.id).toBe('auto-sync-history-42');
  });
});

describe('the detail panel (1721-1784)', () => {
  const detailed = entry({
    before_json: {
      track_count: 10,
      discovered_count: 2,
      wishlisted_count: 1,
      in_library_count: 5,
    },
    after_json: {
      track_count: 12,
      discovered_count: 2,
      wishlisted_count: 0,
      in_library_count: 9,
    },
    started_at: '2026-01-01T09:00:00',
    finished_at: '2026-01-01T09:01:00',
    duration_seconds: 60,
    source: 'spotify',
    result_json: { playlists_refreshed: 1, tracks_synced: 4, tracks_discovered: 'completed' },
    log_lines: ['line one', { message: 'line two', type: 'error' }],
  });

  it('renders the four stat cards in order, with signed deltas', () => {
    const { container } = renderPanel([detailed]);
    const stats = Array.from(container.querySelectorAll('.auto-sync-history-stat'));
    expect(stats.map((s) => s.querySelector('.auto-sync-history-stat-label')?.textContent)).toEqual(
      ['Tracks', 'Discovered', 'Wishlisted', 'In library'],
    );
    const tracks = stats[0];
    expect(tracks.querySelector('.stat-before')?.textContent).toBe('10');
    expect(tracks.querySelector('.stat-after')?.textContent).toBe('12');
    expect(tracks.querySelector('.stat-delta')?.textContent).toBe('+2');
    expect(tracks.querySelector('.stat-delta')?.className).toContain('pos');
    // A flat stat shows no delta chip at all.
    expect(stats[1].querySelector('.stat-delta')).toBeNull();
    expect(stats[2].querySelector('.stat-delta')?.textContent).toBe('-1');
  });

  it('renders the four facts, with a dash where nothing was recorded', () => {
    const { container } = renderPanel([entry()]);
    const facts = Array.from(container.querySelectorAll('.auto-sync-history-fact'));
    expect(facts.map((f) => f.querySelector('span')?.textContent)).toEqual([
      'Started',
      'Finished',
      'Duration',
      'Source',
    ]);
    // An empty datetime becomes 'Not recorded' via autoSyncValueLabel.
    expect(facts[0].querySelector('strong')?.textContent).toBe('Not recorded');
    expect(facts[2].querySelector('strong')?.textContent).toBe('—');
  });

  it('renders only the result pills that carry a value, and NEVER tracks_discovered', () => {
    const { container } = renderPanel([detailed]);
    const pills = Array.from(container.querySelectorAll('.auto-sync-history-result-pill')).map(
      (p) => p.textContent,
    );
    // 1737-1740: tracks_discovered is a status string, not a count.
    expect(pills).toEqual(['Refreshed1', 'Synced4']);
    expect(container.textContent).not.toContain('Discovered: completed');
  });

  it('omits the pill row entirely when the result carries none', () => {
    const { container } = renderPanel([entry({ result_json: {} })]);
    expect(container.querySelector('.auto-sync-history-result-row')).toBeNull();
  });

  it('keeps a zero-valued pill, which is not the same as a missing one', () => {
    const { container } = renderPanel([entry({ result_json: { tracks_synced: 0 } })]);
    expect(container.querySelector('.auto-sync-history-result-pill')?.textContent).toBe('Synced0');
  });

  it('renders the last 20 log lines, typed', () => {
    const { container } = renderPanel([detailed]);
    const lines = Array.from(container.querySelectorAll('.auto-sync-history-log-line'));
    expect(lines.map((l) => l.textContent)).toEqual(['line one', 'line two']);
    expect(lines[0].className).toContain('auto-sync-history-log-info');
    expect(lines[1].className).toContain('auto-sync-history-log-error');
  });

  it('keeps only the NEWEST 20 log lines (1788)', () => {
    const many = Array.from({ length: 25 }, (_, i) => `line ${i}`);
    const { container } = renderPanel([entry({ log_lines: many })]);
    const lines = Array.from(container.querySelectorAll('.auto-sync-history-log-line'));
    expect(lines).toHaveLength(20);
    expect(lines[0].textContent).toBe('line 5');
    expect(lines[19].textContent).toBe('line 24');
  });

  it('shows a run error when the result carries one', () => {
    const { container } = renderPanel([entry({ result_json: { error: 'it broke' } })]);
    expect(container.querySelector('.auto-sync-history-error')?.textContent).toBe('it broke');
  });

  it('offers Run again with the resolved playlist, and stops the row toggling', () => {
    const { container, props } = renderPanel([entry({ playlist_id: 5 })]);
    const row = container.querySelector('.auto-sync-history-row') as HTMLElement;
    fireEvent.click(container.querySelector('.auto-sync-history-run-again') as HTMLElement);
    expect(props.onRunAgain).toHaveBeenCalledWith(5, 'Late Night');
    expect(row.getAttribute('aria-expanded')).toBe('false');
  });

  it('omits Run again when no playlist id can be resolved', () => {
    const { container } = renderPanel([
      entry({ playlist_id: undefined, before_json: {}, after_json: {} }),
    ]);
    expect(container.querySelector('.auto-sync-history-run-again')).toBeNull();
  });

  it('parses a snapshot that arrives as a JSON STRING (1632-1642)', () => {
    const { container } = renderPanel([
      entry({
        before_json: JSON.stringify({ track_count: 3 }),
        after_json: JSON.stringify({ track_count: 8 }),
      }),
    ]);
    expect(container.querySelector('.auto-sync-history-delta')?.textContent).toBe('8 tracks (+5)');
  });

  it('survives a snapshot that is unparseable junk', () => {
    const { container } = renderPanel([
      entry({ before_json: '{not json', after_json: '{also not' }),
    ]);
    expect(container.querySelector('.auto-sync-history-delta')?.textContent).toBe('0 tracks');
  });
});

describe('a row that is not an object at all (1596-1607)', () => {
  it('renders the placeholder run for a non-object row', () => {
    const { container } = renderPanel([
      'garbage' as never,
      entry({ id: 2, playlist_name: 'Real' }),
    ]);
    const names = Array.from(container.querySelectorAll('.auto-sync-history-name')).map(
      (n) => n.textContent,
    );
    expect(names).toEqual(['Playlist pipeline run', 'Real']);
    // It reads as a completed run with the default trigger.
    expect(container.querySelector('.auto-sync-history-status')?.textContent).toBe('Completed');
    expect(container.querySelector('.flow-trigger')?.textContent).toBe('pipeline');
  });

  it('survives a NULL row, where the vanilla blanks the whole panel', () => {
    // The vanilla's tab counts deref h.status on every render, before the
    // normalizer or the per-row try/catch can help. Declared hardening.
    const { container } = renderPanel([null as never, entry({ id: 2, playlist_name: 'Real' })]);
    expect(container.querySelectorAll('.auto-sync-history-entry')).toHaveLength(2);
    expect(container.querySelector('.auto-sync-history-name')?.textContent).toBe(
      'Playlist pipeline run',
    );
  });

  it('keeps a null row out of the error and completed counts', () => {
    const { container } = renderPanel([null as never]);
    const counts = Array.from(container.querySelectorAll('.auto-sync-history-filter-btn em')).map(
      (e) => e.textContent,
    );
    expect(counts).toEqual(['1', '0', '0']);
  });
});

describe('per-row error isolation (1428-1432)', () => {
  it('replaces only the broken row, leaving the rest of the list intact', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    // A log_lines value that is an array of one throwing object: JSON.stringify
    // on a circular structure throws inside the row's own render.
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    const { container } = renderPanel([
      entry({ id: 1, playlist_name: 'Good one' }),
      entry({ id: 2, playlist_name: 'Bad one', log_lines: [circular as never] }),
      entry({ id: 3, playlist_name: 'Also good' }),
    ]);

    const cards = Array.from(container.querySelectorAll('.auto-sync-history-entry'));
    expect(cards).toHaveLength(3);
    expect(cards[1].className).toContain('auto-sync-history-entry-error');
    expect(within(cards[1] as HTMLElement).getByText('Render error')).toBeTruthy();
    // The row keeps its identity, and its neighbours are untouched.
    expect(cards[1].querySelector('strong')?.textContent).toBe('Bad one');
    expect(cards[0].querySelector('.auto-sync-history-name')?.textContent).toBe('Good one');
    expect(cards[2].querySelector('.auto-sync-history-name')?.textContent).toBe('Also good');
  });
});
