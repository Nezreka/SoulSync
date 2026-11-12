import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { Automation, AutomationActivity } from '../-automations.types';

/**
 * Driven through the REAL router, with the manifest reporting /automations as
 * React-owned.
 *
 * The page cannot be rendered standalone — useProfile() reads the root route
 * context — and going through the router also exercises the loader, the
 * dormancy guard and URL-driven view/nav state rather than a stub of them.
 * Only getShellRouteByPageId is faked; the rest of the manifest is real,
 * because the router builds its route table from it.
 */
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'automations'
        ? { ...actual.getShellRouteByPageId('automations'), kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

function auto(over: Partial<Automation> & { id: number }): Automation {
  return { name: `auto-${over.id}`, enabled: 1, ...over };
}

let requests: { url: string; method: string; body?: unknown }[] = [];

/** the vanilla builders, held here because the page wraps the globals */
let showBuilder = vi.fn();
let showVideoBuilder = vi.fn();

/** what /api/automations/activity answers; a test sets it before rendering */
let activity: AutomationActivity = {};

function stubFetch(
  rows: Automation[],
  master: { music: boolean; video?: boolean } = { music: true },
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (
        init?.method ?? (input instanceof Request ? input.method : 'GET')
      ).toUpperCase();
      // ky sends a Request object, so a JSON payload is NOT on init.body —
      // reading only init.body silently recorded `undefined` for every call
      // that carries one.
      let raw: string | undefined;
      if (init?.body) raw = String(init.body);
      else if (input instanceof Request) raw = (await input.clone().text()) || undefined;
      let body: unknown;
      if (raw) {
        try {
          body = JSON.parse(raw);
        } catch {
          body = raw;
        }
      }
      requests.push({ url, method, body });

      const json = (data: unknown) =>
        new Response(JSON.stringify(data), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });

      if (url.includes('/api/automations/master')) return json(master);
      if (url.includes('/api/automations/activity')) return json(activity);
      if (url.match(/\/api\/automations\/\d+\/history/)) return json({ history: [], total: 0 });
      if (url.match(/\/api\/automations\/\d+/) || url.includes('bulk-toggle'))
        return json({ success: true, updated: 2 });
      if (url.includes('/api/automations')) return json(rows);
      return json({});
    }),
  );
}

function renderPage(
  rows: Automation[],
  master?: { music: boolean; video?: boolean },
  entry = '/automations',
) {
  requests = [];
  stubFetch(rows, master);
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

/** The list, through one lens (or, from an older link, one section). */
const libUrl = (nav: string, extra = '') =>
  `/automations?view=library&nav=${encodeURIComponent(nav)}${extra}`;

function renderLibrary(rows: Automation[], master?: { music: boolean }, nav = 'all', extra = '') {
  return renderPage(rows, master, libUrl(nav, extra));
}

const sent = (fragment: string, method = 'POST') =>
  requests.filter((r) => r.url.includes(fragment) && r.method === method);

// Shared by every block below — a missing shell bridge makes the route
// redirect instead of rendering, which surfaces as "cannot find text".
beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.showConfirmDialog = vi.fn(async () => true);
  showBuilder = vi.fn();
  showVideoBuilder = vi.fn();
  window.showAutomationBuilder = showBuilder;
  window.showVideoAutomationBuilder = showVideoBuilder;
  activity = {};
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
});

const pill = (name: RegExp | string) => screen.getByRole('tab', { name });
const sectionNames = () =>
  [...document.querySelectorAll('.automx-sec')].map((s) => s.getAttribute('data-section'));
const rowNames = (root: ParentNode = document) =>
  [...root.querySelectorAll('.automx-row-name')].map((n) => n.textContent);
const item = (id: number) => document.querySelector(`.automx-item[data-id="${id}"]`)!;
const openRow = (id: number) => fireEvent.click(item(id).querySelector('.automx-row')!);

describe('AutomationsPage navigation', () => {
  it('lands on the overview, with the four places as pills', async () => {
    renderPage([
      auto({ id: 1, name: 'Alpha', group_name: 'Chores', last_run: '2026-01-01 00:00:00' }),
      auto({ id: 2, name: 'Beta', group_name: 'Weekend' }),
    ]);
    await screen.findAllByText('Alpha');

    expect(document.querySelector('.automx-overview')).not.toBeNull();
    expect(document.querySelector('.automx-library')).toBeNull();
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Overview',
      'All automations2',
      'Activity',
      'Guides',
    ]);
    expect(pill('Overview')).toHaveAttribute('aria-selected', 'true');
  });

  it('shows the first-automation invitation on a fresh install', async () => {
    window._buildAutomationHub = vi.fn(() => document.createElement('div'));
    renderPage([]);
    await screen.findByText('No automations yet');

    expect(document.querySelector('.automx-empty .automx-cap-primary')).not.toBeNull();
    // The guides are reference material for existing automations; a fresh
    // install must not see the invitation AND a wall of reference docs.
    expect(window._buildAutomationHub).not.toHaveBeenCalled();
    delete window._buildAutomationHub;
    expect(screen.queryByRole('tab', { name: 'Guides' })).toBeNull();
    // With nothing to show, the page is the list: the pill agrees.
    expect(pill(/All automations/)).toHaveAttribute('aria-selected', 'true');
  });

  it('lists every automation in sections: pipeline, groups, own, housekeeping', async () => {
    renderLibrary([
      auto({ id: 1, name: 'Scan', is_system: 1, action_type: 'scan_watchlist' }),
      auto({ id: 2, name: 'Backup', is_system: 1, action_type: 'backup_database' }),
      auto({ id: 3, name: 'Alpha', group_name: 'Chores' }),
      auto({ id: 4, name: 'Loose' }),
      // a system row filed under a name is not a user group
      auto({
        id: 5,
        name: '[System] Job',
        is_system: 1,
        group_name: 'Maintenance',
        owned_by: 'system:repair_job',
        action_type: 'run_repair_job',
      }),
    ]);
    await screen.findByText('Alpha');

    expect(sectionNames()).toEqual(['operations', 'group:Chores', 'ungrouped', 'maintenance']);
    // housekeeping starts folded under All: it would bury the rest
    expect(rowNames()).toEqual(['Scan', 'Alpha', 'Loose']);
    fireEvent.click(screen.getByLabelText('Expand Maintenance'));
    expect(rowNames()).toEqual(['Scan', 'Alpha', 'Loose', 'Backup', 'Job']);
  });

  it('puts the failing, the failing-lately and the never-run under Needs attention', async () => {
    activity = {
      recent: [
        {
          automation_id: 4,
          name: 'Flaky',
          runs: 24,
          failed: 23,
          skipped: 0,
          avg_seconds: 0,
          last_error: 'Reconnect MetaSync.',
        },
      ],
    };
    renderLibrary(
      [
        auto({ id: 1, name: 'Broken', last_error: 'boom', last_run: '2026-01-01 00:00:00' }),
        auto({ id: 2, name: 'Fresh', enabled: 1 }),
        auto({ id: 3, name: 'Healthy', last_run: '2026-01-01 00:00:00' }),
        // its last run worked, so last_error is clear: only the history knows
        auto({ id: 4, name: 'Flaky', last_run: '2026-01-01 00:00:00' }),
      ],
      undefined,
      'attention',
    );
    await screen.findByText('Flaky');
    await waitFor(() => expect(rowNames()).toEqual(['Broken', 'Flaky', 'Fresh']));
    expect(screen.getByRole('button', { name: /Needs attention/ }).textContent).toBe(
      'Needs attention 3',
    );
  });

  it('splits timer triggers from event triggers', async () => {
    const rows = [
      auto({ id: 1, name: 'Timer', trigger_type: 'schedule' }),
      auto({ id: 2, name: 'Event', trigger_type: 'batch_complete' }),
    ];
    const { unmount } = renderLibrary(rows, undefined, 'scheduled');
    await screen.findByText('Timer');
    expect(rowNames()).toEqual(['Timer']);
    unmount();
    renderLibrary(rows, undefined, 'events');
    await screen.findByText('Event');
    expect(rowNames()).toEqual(['Event']);
  });

  it('a lens unfolds housekeeping, since that is where the switched-off ones live', async () => {
    renderLibrary(
      [
        auto({ id: 1, name: 'Backup', is_system: 1, action_type: 'backup_database', enabled: 0 }),
        auto({ id: 2, name: 'On' }),
      ],
      undefined,
      'off',
    );
    await screen.findByText('Backup');
    expect(rowNames()).toEqual(['Backup']);
  });

  it('changes lens from the chips', async () => {
    renderLibrary([auto({ id: 1, name: 'On' }), auto({ id: 2, name: 'Off', enabled: 0 })]);
    await screen.findByText('On');
    fireEvent.click(screen.getByRole('button', { name: /Switched off/ }));
    await waitFor(() => expect(rowNames()).toEqual(['Off']));
    expect(screen.getByRole('button', { name: /Switched off/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('an older link to one group shows that group alone', async () => {
    renderLibrary(
      [auto({ id: 1, name: 'Alpha', group_name: 'Chores' }), auto({ id: 2, name: 'Loose' })],
      undefined,
      'group:Chores',
    );
    await screen.findByText('Alpha');
    expect(sectionNames()).toEqual(['group:Chores']);
  });

  it('lands a bare search URL in the list', async () => {
    renderPage(
      [auto({ id: 1, name: 'Nightly scan' }), auto({ id: 2, name: 'Other' })],
      undefined,
      '/automations?q=nightly',
    );
    await screen.findByText('Nightly scan');
    expect(document.querySelector('.automx-library')).not.toBeNull();
    expect(rowNames()).toEqual(['Nightly scan']);
    expect(document.querySelector('.automx-filter-count')!.textContent).toBe('1 of 2');
  });

  it('a search that matches nothing offers to clear it', async () => {
    renderLibrary([auto({ id: 1, name: 'Alpha' })], undefined, 'all', '&q=zzz');
    await screen.findByText('No matches');
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    await screen.findByText('Alpha');
  });

  it('leaving the list leaves its search behind', async () => {
    renderLibrary(
      [auto({ id: 1, name: 'Alpha', last_run: '2026-01-01 00:00:00' })],
      undefined,
      'all',
      '&q=alp',
    );
    await screen.findByText('Alpha');
    fireEvent.click(pill('Overview'));
    await waitFor(() => expect(document.querySelector('.automx-overview')).not.toBeNull());
  });

  it('renders the Guides from the shared vanilla hub builder', async () => {
    const hub = document.createElement('div');
    hub.id = 'the-hub';
    window._buildAutomationHub = vi.fn(() => hub);
    renderLibrary([auto({ id: 1 })], undefined, 'guides');
    await waitFor(() => expect(document.getElementById('the-hub')).not.toBeNull());
    expect(pill('Guides')).toHaveAttribute('aria-selected', 'true');
    delete window._buildAutomationHub;
  });

  it('survives the hub builder being unavailable', async () => {
    delete window._buildAutomationHub;
    renderLibrary([auto({ id: 1 })], undefined, 'guides');
    await waitFor(() => expect(document.querySelector('.automx-guides')).not.toBeNull());
  });
});

describe('AutomationsPage overview', () => {
  const soon = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

  it('draws the next day as lanes, slow runs as dots and fast ones as a band', async () => {
    renderPage([
      auto({
        id: 1,
        name: 'Daily scan',
        is_system: 1,
        action_type: 'scan_watchlist',
        trigger_type: 'schedule',
        trigger_config: { interval: 8, unit: 'hours' },
        next_run: soon(60),
      }),
      auto({
        id: 2,
        name: 'Wishlist',
        is_system: 1,
        action_type: 'process_wishlist',
        trigger_type: 'schedule',
        trigger_config: { interval: 30, unit: 'minutes' },
        next_run: soon(5),
      }),
      auto({
        id: 3,
        name: 'Off one',
        enabled: 0,
        trigger_type: 'schedule',
        trigger_config: { interval: 8, unit: 'hours' },
        next_run: soon(60),
      }),
    ]);
    await screen.findAllByText('Daily scan');
    const lanes = [...document.querySelectorAll('.automx-lane')];
    expect(lanes).toHaveLength(1);
    expect(lanes[0].querySelector('.automx-lane-name')!.textContent).toBe('Operations2 on a timer');
    // every 8 hours from an hour out: 1h, 9h, 17h
    expect(lanes[0].querySelectorAll('.automx-dot')).toHaveLength(3);
    expect(lanes[0].querySelector('.automx-lane-band')).not.toBeNull();
  });

  it('counts recent failures, not only the last run', async () => {
    activity = {
      recent: [
        {
          automation_id: 1,
          name: 'Charts',
          runs: 24,
          failed: 23,
          skipped: 0,
          avg_seconds: 0,
          last_error: 'Reconnect MetaSync.',
        },
      ],
    };
    renderPage([auto({ id: 1, name: 'Charts', last_run: '2026-01-01 00:00:00' })]);
    await screen.findByText(/Failed 23 of its last 24 runs\. Reconnect MetaSync\./);
    expect(document.querySelector('.automx-head-sub')!.textContent).toMatch(/1 needs attention/);
  });

  it('says so when nothing is stuck', async () => {
    renderPage([auto({ id: 1, name: 'Fine', last_run: '2026-01-01 00:00:00' })]);
    await screen.findByText('Nothing is stuck');
  });

  it('runs and edits from Needs attention', async () => {
    renderPage([
      auto({ id: 9, name: 'Broken', last_error: 'boom', last_run: '2026-01-01 00:00:00' }),
    ]);
    await screen.findAllByText('Failed: boom');
    const row = document.querySelector('.automx-attn')!;
    fireEvent.click(row.querySelector('.automx-cap')!);
    await waitFor(() => expect(sent('/automations/9/run')).toHaveLength(1));
  });

  it('shelves the playlists that sync on a timer, with their covers', async () => {
    renderPage([
      auto({
        id: 1,
        name: 'Auto-Sync: Fresh House',
        owned_by: 'auto_sync',
        group_name: 'Playlist Auto-Sync',
        action_type: 'playlist_pipeline',
        trigger_type: 'schedule',
        trigger_config: { interval: 2, unit: 'days' },
        next_run: soon(300),
        playlist: { id: 4, name: 'Fresh House', image_url: 'https://img/fresh.jpg' },
      }),
      auto({
        id: 2,
        name: 'Auto-Sync: Daily Mix 1',
        owned_by: 'auto_sync',
        group_name: 'Playlist Auto-Sync',
        action_type: 'personalized_pipeline',
        trigger_type: 'schedule',
        trigger_config: { interval: 8, unit: 'hours' },
        next_run: soon(60),
      }),
    ]);
    await screen.findByText('Playlists on a schedule');
    const tiles = [...document.querySelectorAll('.automx-pl')];
    // soonest first; one has a cover, the other its name on a tile
    expect(tiles.map((t) => t.querySelector('.automx-line-name')!.textContent)).toEqual([
      'Daily Mix 1',
      'Fresh House',
    ]);
    expect(tiles[1].querySelector('img')?.getAttribute('src')).toBe('https://img/fresh.jpg');
    expect(tiles[0].querySelector('.automx-art-name')!.textContent).toBe('Daily Mix 1');
    expect(tiles[1].querySelector('em')!.textContent).toBe('in 5h');
  });

  it('offers to resume when the side is paused, and draws no schedule', async () => {
    renderPage([auto({ id: 1, name: 'Timer', trigger_type: 'schedule', next_run: soon(60) })], {
      music: false,
    });
    await screen.findByText('Automations are paused');
    expect(document.querySelector('.automx-lane')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Resume automations' }));
    await waitFor(() => expect(sent('/automations/master')).toHaveLength(1));
    expect(sent('/automations/master')[0].body).toEqual({ side: 'music', enabled: true });
  });

  it('a name on the overview finds that automation in the list', async () => {
    renderPage([
      auto({ id: 1, name: 'Nightly scan', last_run: '2026-01-01 00:00:00' }),
      auto({ id: 2, name: 'Other', last_run: '2026-01-02 00:00:00' }),
    ]);
    const line = (await screen.findAllByTitle('Nightly scan: find in the list'))[0];
    fireEvent.click(line);
    await waitFor(() => expect(rowNames()).toEqual(['Nightly scan']));
  });
});

describe('AutomationsPage activity', () => {
  it('shows the last day by automation, trouble first, and opens its history', async () => {
    window.showAutomationHistory = vi.fn();
    activity = {
      totals: { runs: 30, failed: 23, skipped: 2 },
      days: [
        { day: '2026-11-10', completed: 10, failed: 2, skipped: 0 },
        { day: '2026-11-11', completed: 5, failed: 23, skipped: 2 },
      ],
      recent: [
        {
          automation_id: 1,
          name: '[System] Charts',
          runs: 24,
          failed: 23,
          skipped: 0,
          avg_seconds: 0.2,
          last_error: 'Reconnect MetaSync.',
        },
        { automation_id: 2, name: 'Sync', runs: 6, failed: 0, skipped: 2, avg_seconds: 130 },
        { automation_id: 3, name: 'Clean', runs: 4, failed: 0, skipped: 0, avg_seconds: 3 },
      ],
    };
    renderPage(
      [
        auto({ id: 1, name: '[System] Charts', action_type: 'metasync_refresh_charts' }),
        auto({ id: 2, name: 'Sync' }),
        auto({ id: 3, name: 'Clean' }),
      ],
      undefined,
      '/automations?view=activity',
    );
    await screen.findByText('Reconnect MetaSync.');
    expect(pill('Activity')).toHaveAttribute('aria-selected', 'true');
    expect([...document.querySelectorAll('.automx-totals b')].map((b) => b.textContent)).toEqual([
      '30',
      '23',
      '2',
      '3',
    ]);
    const rows = [...document.querySelectorAll('button.automx-tr')];
    expect(rows.map((r) => r.querySelector('.automx-td-name')!.textContent)).toEqual([
      'Charts',
      'Sync',
      'Clean',
    ]);
    expect(rows.map((r) => r.querySelector('.automx-td-chips')!.textContent)).toEqual([
      '23 failed',
      '2 skipped',
      'All completed',
    ]);
    expect(rows.map((r) => r.querySelector('.automx-td-what')!.textContent)).toEqual([
      'Reconnect MetaSync.',
      '4 completed',
      '',
    ]);
    expect(document.querySelectorAll('.automx-bar-day')).toHaveLength(2);

    fireEvent.click(rows[0]);
    expect(window.showAutomationHistory).toHaveBeenCalledWith(
      1,
      '[System] Charts',
      'metasync_refresh_charts',
    );
    delete window.showAutomationHistory;
  });

  it('says so when nothing ran', async () => {
    activity = { recent: [], days: [], totals: { runs: 0, failed: 0, skipped: 0 } };
    renderPage([auto({ id: 1 })], undefined, '/automations?view=activity');
    await screen.findByText('Nothing ran in the last 24 hours.');
  });
});

describe('AutomationsPage interactions', () => {
  it('toggles an automation without a toast', async () => {
    renderLibrary([auto({ id: 5, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    await waitFor(() => expect(sent('/automations/5/toggle')).toHaveLength(1));
    // The switch is its own feedback.
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('runs an automation and reports it', async () => {
    renderLibrary([auto({ id: 6, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(screen.getByLabelText('Run Nightly now'));
    await waitFor(() => expect(sent('/automations/6/run')).toHaveLength(1));
    expect(window.showToast).toHaveBeenCalledWith('Automation triggered', 'success');
  });

  it('confirms before deleting, and does nothing if declined', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    renderLibrary([auto({ id: 7, name: 'Nightly' })]);
    await screen.findByText('Nightly');
    openRow(7);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(sent('/automations/7', 'DELETE')).toHaveLength(0);
  });

  it('deletes once confirmed', async () => {
    renderLibrary([auto({ id: 8, name: 'Nightly' })]);
    await screen.findByText('Nightly');
    openRow(8);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent('/automations/8', 'DELETE')).toHaveLength(1));
  });

  it('opens one row at a time', async () => {
    renderLibrary([auto({ id: 1, name: 'Alpha' }), auto({ id: 2, name: 'Beta' })]);
    await screen.findByText('Alpha');
    openRow(1);
    openRow(2);
    expect(document.querySelectorAll('.automx-detail')).toHaveLength(1);
    expect(item(2).querySelector('.automx-detail')).not.toBeNull();
  });

  it('flips the master switch to the opposite of its current state', async () => {
    renderLibrary([auto({ id: 1 })], { music: true });
    await screen.findByText('Automations on');
    fireEvent.click(screen.getByText('Automations on'));
    await waitFor(() => expect(sent('/automations/master')).toHaveLength(1));
    expect(sent('/automations/master')[0].body).toEqual({ side: 'music', enabled: false });
  });

  it('surfaces a server error as a toast', async () => {
    renderLibrary([auto({ id: 9, name: 'Nightly' })]);
    await screen.findByText('Nightly');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: 'engine offline' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    fireEvent.click(screen.getByLabelText('Run Nightly now'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Error: engine offline', 'error'),
    );
  });

  it('opens the run-history modal from an opened row', async () => {
    window.showAutomationHistory = vi.fn();
    renderLibrary([auto({ id: 4, name: 'Nightly', run_count: 3, action_type: 'scan_library' })]);
    await screen.findByText('Nightly');
    openRow(4);
    fireEvent.click(screen.getByRole('button', { name: 'Run history' }));
    expect(window.showAutomationHistory).toHaveBeenCalledWith(4, 'Nightly', 'scan_library');
    delete window.showAutomationHistory;
  });
});

describe('AutomationsPage group management', () => {
  const chores = () => [
    auto({ id: 21, name: 'Alpha', group_name: 'Chores' }),
    auto({ id: 22, name: 'Beta', group_name: 'Chores' }),
  ];

  it('assigns an automation to an existing group', async () => {
    renderLibrary([...chores(), auto({ id: 23, name: 'Loose' })]);
    await screen.findByText('Loose');
    openRow(23);
    fireEvent.click(screen.getByRole('button', { name: 'Move to group' }));

    const option = await screen.findByText('Chores', { selector: '.auto-group-option' });
    fireEvent.click(option);

    await waitFor(() => expect(sent('/automations/23', 'PUT')).toHaveLength(1));
    expect(sent('/automations/23', 'PUT')[0].body).toEqual({ group_name: 'Chores' });
  });

  it('creates a new group from the input, ignoring an empty submit', async () => {
    renderLibrary([auto({ id: 24, name: 'Loose' })]);
    await screen.findByText('Loose');
    openRow(24);
    fireEvent.click(screen.getByRole('button', { name: 'Move to group' }));

    const input = await screen.findByLabelText('New group name');
    // Enter on blank must NOT fire: '' would ungroup instead.
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByLabelText('New group name')).toBeInTheDocument());
    expect(sent('/automations/24', 'PUT')).toHaveLength(0);

    fireEvent.change(input, { target: { value: '  Errands  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(sent('/automations/24', 'PUT')).toHaveLength(1));
    expect(sent('/automations/24', 'PUT')[0].body).toEqual({ group_name: 'Errands' });
  });

  it('bulk-toggles the whole group, including rows a search hides', async () => {
    renderLibrary(chores(), undefined, 'all', '&q=alpha');
    await screen.findByText('Alpha');
    expect(rowNames()).toEqual(['Alpha']);
    fireEvent.click(screen.getByTitle('Disable all'));
    await waitFor(() => expect(sent('bulk-toggle')).toHaveLength(1));
    expect(sent('bulk-toggle')[0].body).toEqual({ automation_ids: [21, 22], enabled: false });
  });

  it('judges Enable/Disable all over the whole group, not the visible rows', async () => {
    renderLibrary(
      [
        auto({ id: 21, name: 'Alpha', group_name: 'Chores' }),
        auto({ id: 22, name: 'Beta', group_name: 'Chores', enabled: 0 }),
      ],
      undefined,
      'all',
      '&q=alpha',
    );
    await screen.findByText('Alpha');
    // the one row on screen is on, the group is not all on
    expect(screen.getByTitle('Enable all')).toBeInTheDocument();
  });

  it('renames a group over every automation in it', async () => {
    renderLibrary(chores());
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Rename group'));
    const input = screen.getByLabelText('Rename group Chores');
    fireEvent.change(input, { target: { value: 'Errands' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(sent('/automations/group', 'PUT')).toHaveLength(1));
    expect(sent('/automations/group', 'PUT')[0].body).toEqual({
      automation_ids: [21, 22],
      group_name: 'Errands',
    });
  });

  it('does not PUT when a rename is unchanged or blank', async () => {
    renderLibrary(chores());
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Rename group'));
    fireEvent.keyDown(screen.getByLabelText('Rename group Chores'), { key: 'Enter' });
    fireEvent.click(screen.getByTitle('Rename group'));
    const input = screen.getByLabelText('Rename group Chores');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.queryByLabelText('Rename group Chores')).toBeNull());
    expect(sent('/automations/group', 'PUT')).toHaveLength(0);
  });

  it('escape abandons a rename without saving', async () => {
    renderLibrary(chores());
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByTitle('Rename group'));
    const input = screen.getByLabelText('Rename group Chores');
    fireEvent.change(input, { target: { value: 'Errands' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByLabelText('Rename group Chores')).toBeNull());
    expect(sent('/automations/group', 'PUT')).toHaveLength(0);
  });

  it('offers keep-or-delete when removing a group, and cancel does nothing', async () => {
    renderLibrary(chores());
    await screen.findByText('Alpha');
    fireEvent.click(screen.getByTitle('Delete group'));
    await screen
      .findByText(/Chores/, { selector: '.modal-overlay *, [role="dialog"] *' })
      .catch(() => undefined);
    fireEvent.click(await screen.findByRole('button', { name: /Cancel/ }));
    expect(sent('/automations/group', 'PUT')).toHaveLength(0);
    expect(requests.filter((r) => r.method === 'DELETE')).toHaveLength(0);
  });

  it('gives a system section no group actions', async () => {
    renderLibrary([auto({ id: 1, name: 'Scan', is_system: 1, action_type: 'scan_watchlist' })]);
    await screen.findByText('Scan');
    expect(screen.queryByTitle('Rename group')).toBeNull();
    expect(screen.queryByTitle('Delete group')).toBeNull();
  });
});

describe('AutomationsPage handlers', () => {
  it('supplies a handler for every action an opened row offers', async () => {
    window.showAutomationHistory = vi.fn();
    renderLibrary([auto({ id: 3, name: 'Nightly', run_count: 2 })]);
    await screen.findByText('Nightly');
    openRow(3);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    // the page wraps the global while it is mounted; the vanilla one still ran
    expect(showBuilder).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(sent('/automations/3/duplicate')).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(sent('/automations/3/run')).toHaveLength(1));
    delete window.showAutomationHistory;
  });
});

/**
 * The video side is this same page on /automations?media=video: the rows the
 * engine tags `owned_by: 'video'`, the video master switch, the video builder.
 */
describe('AutomationsPage on the video side', () => {
  const VIDEO = '/automations?media=video&view=library&nav=all';
  const rows = () => [
    auto({ id: 1, name: 'Sync Spotify', action_type: 'sync_playlist' }),
    auto({
      id: 2,
      name: 'Process Movie Wishlist',
      is_system: 1,
      owned_by: 'video',
      action_type: 'video_process_movie_wishlist',
    }),
    auto({ id: 3, name: 'Weekend movies', owned_by: 'video', action_type: 'video_scan_library' }),
    auto({
      id: 4,
      name: 'Apply Overlays',
      is_system: 1,
      owned_by: 'video',
      action_type: 'video_apply_overlays',
    }),
  ];

  it('shows the video rows only, system and user-built', async () => {
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Weekend movies');
    expect(rowNames().sort()).toEqual([
      'Apply Overlays',
      'Process Movie Wishlist',
      'Weekend movies',
    ]);
    expect(screen.queryByText('Sync Spotify')).toBeNull();
  });

  it('the music page still hides them', async () => {
    renderLibrary(rows());
    await screen.findByText('Sync Spotify');
    expect(rowNames()).toEqual(['Sync Spotify']);
  });

  it('asks the video permission, the video activity and the video block labels', async () => {
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Weekend movies');
    expect(window.SoulSyncWebShellBridge!.isPageAllowed).toHaveBeenCalledWith(
      'automations',
      '?media=video',
    );
    expect(sent('/api/automations/activity?side=video', 'GET').length).toBeGreaterThan(0);
    expect(sent('/api/video/automations/blocks', 'GET').length).toBeGreaterThan(0);
    expect(sent('/api/automations/blocks', 'GET')).toHaveLength(0);
  });

  it('reads and flips the video master switch, which starts paused', async () => {
    renderPage(rows(), { music: true }, VIDEO);
    await screen.findByText('Weekend movies');
    const master = screen.getByRole('button', { name: 'Paused' });
    expect(master.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(master);
    await waitFor(() => expect(sent('/automations/master')).toHaveLength(1));
    expect(sent('/automations/master')[0].body).toEqual({ side: 'video', enabled: true });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Video automations resumed', 'success'),
    );
  });

  it('opens the video builder, never the music one', async () => {
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Weekend movies');

    fireEvent.click(screen.getByRole('button', { name: 'New automation' }));
    expect(showVideoBuilder).toHaveBeenCalledWith(undefined);
    openRow(3);
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(showVideoBuilder).toHaveBeenCalledWith(3);
    expect(showBuilder).not.toHaveBeenCalled();
    document.body.classList.remove('vauto-building');
  });

  it('keeps the side as you move between the pills', async () => {
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Weekend movies');
    fireEvent.click(pill(/Activity/));
    await waitFor(() => expect(pill(/Activity/).getAttribute('aria-selected')).toBe('true'));
    fireEvent.click(pill(/All automations/));
    await screen.findByText('Weekend movies');
    expect(screen.queryByText('Sync Spotify')).toBeNull();
  });

  it('offers the overlay settings on the overlay automation, and only there', async () => {
    const openSettings = vi.fn();
    window.VideoOverlayEditor = { openSettings };
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Apply Overlays');

    openRow(2);
    expect(screen.queryByRole('button', { name: 'Configure overlays' })).toBeNull();
    openRow(2);
    openRow(4);
    fireEvent.click(screen.getByRole('button', { name: 'Configure overlays' }));
    expect(openSettings).toHaveBeenCalledTimes(1);
    delete window.VideoOverlayEditor;
  });

  it('repaints when the vanilla builder or a pipeline deploy says the list changed', async () => {
    renderPage(rows(), { music: true, video: true }, VIDEO);
    await screen.findByText('Weekend movies');
    const before = sent('/api/automations', 'GET').length;
    document.dispatchEvent(new CustomEvent('soulsync:automations-changed'));
    await waitFor(() => expect(sent('/api/automations', 'GET').length).toBeGreaterThan(before));
  });
});
