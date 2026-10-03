import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { Automation } from '../-automations.types';

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

function stubFetch(rows: Automation[], master: { music: boolean } = { music: true }) {
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
      if (url.match(/\/api\/automations\/\d+/) || url.includes('bulk-toggle'))
        return json({ success: true, updated: 2 });
      if (url.includes('/api/automations')) return json(rows);
      return json({});
    }),
  );
}

function renderPage(rows: Automation[], master?: { music: boolean }, entry = '/automations') {
  requests = [];
  stubFetch(rows, master);
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

/** The library URL for one collection; the new home of everything the old
 *  stacked sections rendered. */
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
  window.showAutomationBuilder = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
});

describe('AutomationsPage navigation', () => {
  it('lands on the overview by default, not a scroll of every collection', async () => {
    renderPage([
      auto({ id: 1, name: 'Alpha', group_name: 'Chores' }),
      auto({ id: 2, name: 'Beta', group_name: 'Weekend' }),
    ]);
    await screen.findByText('Alpha'); // overview names rows, it does not grid them

    expect(document.querySelector('.automx-overview')).not.toBeNull();
    expect(document.querySelector('.automx-sidebar')).not.toBeNull();
    // The whole point: one collection at a time. The overview shows panels,
    // not the card grid, so no automation grid is on the page.
    expect(document.querySelector('.automx-library')).toBeNull();
  });

  it('shows the first-automation invitation on a fresh install', async () => {
    window._buildAutomationHub = vi.fn(() => document.createElement('div'));
    renderPage([]);
    await screen.findByText('No automations yet');

    expect(document.querySelector('.automx-empty .automx-new-btn-lg')).not.toBeNull();
    // The guides are reference material for existing automations; a fresh
    // install must not see the invitation AND a wall of reference docs.
    expect(window._buildAutomationHub).not.toHaveBeenCalled();
    delete window._buildAutomationHub;
    // With nothing to show, the page is the All collection — the sidebar
    // must agree with the content instead of highlighting Overview.
    const activeItems = [...document.querySelectorAll('.automx-nav-item.active')];
    expect(activeItems).toHaveLength(1);
    expect(activeItems[0].textContent).toMatch(/All automations/);
  });

  it('opens one group from the sidebar without showing the rest', async () => {
    renderLibrary(
      [
        auto({ id: 1, name: 'Alpha', group_name: 'Chores' }),
        auto({ id: 2, name: 'Beta', group_name: 'Weekend' }),
        auto({ id: 3, name: 'Loose' }),
      ],
      undefined,
      'group:Chores',
    );
    await screen.findByText('Alpha');

    expect(screen.queryByText('Beta')).toBeNull();
    expect(screen.queryByText('Loose')).toBeNull();
    expect(document.querySelector('.automx-library-title')?.textContent).toBe('Chores');
  });

  it('puts the failing and the never-run in Needs attention, not the healthy', async () => {
    renderLibrary(
      [
        auto({ id: 1, name: 'Broken', last_error: 'boom' }),
        auto({ id: 2, name: 'Fresh', last_run: undefined }),
        auto({ id: 3, name: 'Healthy', last_run: '2026-09-28 00:00:00' }),
      ],
      undefined,
      'attention',
    );
    await screen.findByText('Broken');

    expect(screen.getByText('Fresh')).toBeInTheDocument();
    expect(screen.queryByText('Healthy')).toBeNull();
  });

  it('splits timer triggers from event triggers', async () => {
    const rows = [
      auto({ id: 1, name: 'Timer', trigger_type: 'schedule' }),
      auto({ id: 2, name: 'Listener', trigger_type: 'track_downloaded' }),
    ];
    const { unmount } = renderLibrary(rows, undefined, 'scheduled');
    await screen.findByText('Timer');
    expect(screen.queryByText('Listener')).toBeNull();
    unmount();

    renderLibrary(rows, undefined, 'events');
    await screen.findByText('Listener');
    expect(screen.queryByText('Timer')).toBeNull();
  });

  it('lands a bare search URL in the All library', async () => {
    // Old bookmarks and shared links carry ?q= with no view — the overview
    // would swallow that query silently, so the page routes it to All.
    renderPage(
      [auto({ id: 1, name: 'Alpha' }), auto({ id: 2, name: 'Beta' })],
      undefined,
      '/automations?q=Alpha',
    );
    await screen.findByText('Alpha');

    expect(document.querySelector('.automx-library-title')?.textContent).toBe('All automations');
    expect(screen.queryByText('Beta')).toBeNull();
  });

  it('routes the sidebar search to the All collection', async () => {
    renderPage([auto({ id: 1, name: 'Alpha' }), auto({ id: 2, name: 'Beta' })]);
    await screen.findByText('Up next');

    fireEvent.change(screen.getByLabelText('Search automations'), {
      target: { value: 'Alpha' },
    });

    await waitFor(() =>
      expect(document.querySelector('.automx-library-title')?.textContent).toBe('All automations'),
    );
    expect(screen.queryByText('Beta')).toBeNull();
  });

  it('renders the Guides from the shared vanilla hub builder', async () => {
    const built = document.createElement('div');
    built.className = 'automations-section';
    built.id = 'auto-section-hub';
    built.textContent = 'Automation Hub';
    window._buildAutomationHub = vi.fn(() => built);

    renderLibrary([auto({ id: 1, name: 'Alpha' })], undefined, 'guides');
    await screen.findByText('Automation Hub');

    expect(window._buildAutomationHub).toHaveBeenCalled();
    expect(document.querySelector('#auto-section-hub')).not.toBeNull();
    delete window._buildAutomationHub;
  });

  it('survives the hub builder being unavailable', async () => {
    delete window._buildAutomationHub;
    renderLibrary([auto({ id: 1, name: 'Alpha' })], undefined, 'guides');
    // The guides view must still render; a missing hub is not a crash.
    await waitFor(() => expect(document.querySelector('.automx-guides')).not.toBeNull());
  });
});

describe('AutomationsPage interactions', () => {
  it('toggles an automation without a toast', async () => {
    renderLibrary([auto({ id: 5, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(screen.getByLabelText('Disable Nightly'));
    await waitFor(() => expect(sent('/automations/5/toggle')).toHaveLength(1));
    // The switch is its own feedback; vanilla showed nothing on success.
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('runs an automation and reports it', async () => {
    renderLibrary([auto({ id: 6, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    await waitFor(() => expect(sent('/automations/6/run')).toHaveLength(1));
    expect(window.showToast).toHaveBeenCalledWith('Automation triggered', 'success');
  });

  it('confirms before deleting, and does nothing if declined', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    renderLibrary([auto({ id: 7, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    // The important half: a declined confirm must not issue the DELETE.
    expect(sent('/automations/7', 'DELETE')).toHaveLength(0);
  });

  it('deletes once confirmed', async () => {
    renderLibrary([auto({ id: 8, name: 'Nightly' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.automation-delete-btn')!);
    await waitFor(() => expect(sent('/automations/8', 'DELETE')).toHaveLength(1));
    expect(window.showToast).toHaveBeenCalledWith('Automation deleted', 'success');
  });

  it('flips the master switch to the opposite of its current state', async () => {
    // The master switch lives in the sidebar, above the collections.
    renderLibrary([auto({ id: 1 })], { music: true });
    await waitFor(() => expect(document.querySelector('.automx-master.on')).not.toBeNull());

    fireEvent.click(document.querySelector('.automx-master')!);
    await waitFor(() => expect(sent('/automations/master')).toHaveLength(1));
    expect(sent('/automations/master')[0].body).toEqual({ side: 'music', enabled: false });
  });

  it('bulk-toggles the whole group, including cards the filter hides', async () => {
    // The library removes filtered-out cards from the DOM entirely, so
    // sourcing ids from the rendered cards would toggle only the visible
    // subset. The request must carry the group's ids, not the visible ones.
    renderLibrary(
      [
        auto({ id: 11, name: 'Alpha', group_name: 'Chores' }),
        auto({ id: 12, name: 'Beta', group_name: 'Chores' }),
      ],
      undefined,
      'group:Chores',
      '&q=Alpha',
    );
    await screen.findByText('Alpha');
    expect(screen.queryByText('Beta')).toBeNull(); // genuinely not rendered

    fireEvent.click(screen.getByTitle('Disable all'));
    await waitFor(() => expect(sent('bulk-toggle')).toHaveLength(1));
    expect(sent('bulk-toggle')[0].body).toEqual({ automation_ids: [11, 12], enabled: false });
  });

  it('judges Enable/Disable all over the whole group, not the visible subset', async () => {
    // The discriminating case: the group is PARTLY enabled, and the filter
    // leaves only the enabled one visible. Judged over the visible subset the
    // button reads "Disable all" (1 of 1 enabled); judged over the group it
    // reads "Enable all" (1 of 2). A group where every member is enabled
    // cannot tell the two apart — which is what an earlier version of this
    // test did, and it passed against the wrong implementation.
    renderLibrary(
      [
        auto({ id: 43, name: 'Alpha', group_name: 'Chores', enabled: 1 }),
        auto({ id: 44, name: 'Beta', group_name: 'Chores', enabled: 0 }),
      ],
      undefined,
      'group:Chores',
      '&q=Alpha',
    );
    await screen.findByText('Alpha');
    expect(screen.getByTitle('Enable all')).toBeInTheDocument();
    expect(screen.queryByTitle('Disable all')).toBeNull();
  });

  it('surfaces a server error as a toast', async () => {
    renderLibrary([auto({ id: 9, name: 'Nightly' })]);
    await screen.findByText('Nightly');
    // The endpoints answer {error} with HTTP 200, so the payload check is what
    // catches this — an ok-status check alone would report success.
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ error: 'engine unavailable' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    fireEvent.click(document.querySelector('.automation-run-btn')!);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Error: engine unavailable', 'error'),
    );
  });

  it('opens the run-history modal from the Runs link', async () => {
    window.showAutomationHistory = vi.fn();
    renderLibrary([auto({ id: 31, name: 'Nightly', run_count: 4, action_type: 'scan_library' })]);
    await screen.findByText('Nightly');

    fireEvent.click(document.querySelector('.auto-runs-link')!);
    expect(window.showAutomationHistory).toHaveBeenCalledWith(31, 'Nightly', 'scan_library');
    delete window.showAutomationHistory;
  });
});

describe('AutomationsPage group management', () => {
  const chores = () => [
    auto({ id: 21, name: 'Alpha', group_name: 'Chores' }),
    auto({ id: 22, name: 'Beta', group_name: 'Chores' }),
  ];
  const choresLib = (rows = chores()) => renderLibrary(rows, undefined, 'group:Chores');

  it('assigns an automation to an existing group', async () => {
    renderLibrary([...chores(), auto({ id: 23, name: 'Loose' })]);
    await screen.findByText('Loose');

    // The group button on the ungrouped card opens the dropdown.
    const looseCard = [...document.querySelectorAll('.automation-card')].find(
      (c) => c.getAttribute('data-id') === '23',
    )!;
    fireEvent.click(looseCard.querySelector('.automation-group-btn')!);

    const option = await screen.findByText('Chores', { selector: '.auto-group-option' });
    fireEvent.click(option);

    await waitFor(() => expect(sent('/automations/23', 'PUT')).toHaveLength(1));
    expect(sent('/automations/23', 'PUT')[0].body).toEqual({ group_name: 'Chores' });
  });

  it('creates a new group from the input, ignoring an empty submit', async () => {
    renderLibrary([auto({ id: 24, name: 'Loose' })]);
    await screen.findByText('Loose');
    fireEvent.click(document.querySelector('.automation-group-btn')!);

    const input = await screen.findByLabelText('New group name');
    // Enter on blank must NOT fire. The vanilla handler passed '' straight to
    // _assignGroup, where `|| null` silently UNGROUPED instead.
    fireEvent.keyDown(input, { key: 'Enter' });
    // Same reasoning: give an errant request a chance to be issued.
    await waitFor(() => expect(screen.getByLabelText('New group name')).toBeInTheDocument());
    expect(sent('/automations/24', 'PUT')).toHaveLength(0);

    fireEvent.change(input, { target: { value: '  Errands  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(sent('/automations/24', 'PUT')).toHaveLength(1));
    expect(sent('/automations/24', 'PUT')[0].body).toEqual({ group_name: 'Errands' });
  });

  it('renames a group over every automation in it', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Rename group'));
    const input = await screen.findByLabelText('Rename group Chores');
    fireEvent.change(input, { target: { value: 'Errands' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(sent('automations/group', 'PUT')).toHaveLength(1));
    expect(sent('automations/group', 'PUT')[0].body).toEqual({
      automation_ids: [21, 22],
      group_name: 'Errands',
    });
  });

  it('does not PUT when a rename is unchanged or blank', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Rename group'));
    const input = await screen.findByLabelText('Rename group Chores');
    fireEvent.keyDown(input, { key: 'Enter' }); // unchanged
    fireEvent.click(screen.getByTitle('Rename group'));
    const again = await screen.findByLabelText('Rename group Chores');
    fireEvent.change(again, { target: { value: '   ' } });
    fireEvent.keyDown(again, { key: 'Enter' }); // blank

    await waitFor(() => expect(screen.queryByLabelText('Rename group Chores')).toBeNull());
    expect(sent('automations/group', 'PUT')).toHaveLength(0);
  });

  it('escape abandons a rename without saving', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Rename group'));
    const input = await screen.findByLabelText('Rename group Chores');
    fireEvent.change(input, { target: { value: 'Errands' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    // Wait for the editor to actually close before asserting. Checking
    // synchronously here passes whatever Escape does, because the mutation
    // would not have issued its request yet — the assertion has to outlive it.
    await waitFor(() => expect(screen.queryByLabelText('Rename group Chores')).toBeNull());
    expect(sent('automations/group', 'PUT')).toHaveLength(0);
  });

  it('offers keep-or-delete when removing a group, and cancel does nothing', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Delete group'));
    await screen.findByText(/Delete Group/);
    fireEvent.click(screen.getByText('Cancel'));

    expect(sent('automations/group', 'PUT')).toHaveLength(0);
    expect(sent('/automations/21', 'DELETE')).toHaveLength(0);
  });

  it('dissolving a group ungroups its automations rather than deleting them', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Delete group'));
    fireEvent.click(await screen.findByText(/Keep Automations/));

    await waitFor(() => expect(sent('automations/group', 'PUT')).toHaveLength(1));
    expect(sent('automations/group', 'PUT')[0].body).toEqual({
      automation_ids: [21, 22],
      group_name: null,
    });
    // The destructive path must NOT have run.
    expect(sent('/automations/21', 'DELETE')).toHaveLength(0);
  });

  it('deleting everything removes each automation in the group', async () => {
    choresLib();
    await screen.findByText('Alpha');

    fireEvent.click(screen.getByTitle('Delete group'));
    fireEvent.click(await screen.findByText(/Delete Everything/));

    await waitFor(() => expect(sent('/automations/21', 'DELETE')).toHaveLength(1));
    await waitFor(() => expect(sent('/automations/22', 'DELETE')).toHaveLength(1));
    expect(sent('automations/group', 'PUT')).toHaveLength(0);
  });
});

describe('AutomationsPage layout parity', () => {
  it('puts cards inside .automations-grid, not loose in the library', () => {
    // .automations-grid is `repeat(2, 1fr)`. Rendering cards as direct
    // children of the library body silently collapses the page to one card
    // per row — which is exactly what shipped before this test existed.
    renderLibrary([auto({ id: 1, name: 'Alpha' }), auto({ id: 2, name: 'Beta' })]);
    return screen.findByText('Alpha').then(() => {
      const grid = document.querySelector('.automx-view .automations-grid');
      expect(grid).not.toBeNull();
      expect(grid!.querySelectorAll('.automation-card')).toHaveLength(2);
      // and no card escaped the grid
      expect(document.querySelectorAll('.automx-view > .automation-card')).toHaveLength(0);
    });
  });

  it('keeps the library count over the collection, not the filtered subset', async () => {
    // Filtering narrows the cards; the collection's own size must not tick
    // down as you type — that was the vanilla contract for section counts.
    renderLibrary(
      [auto({ id: 41, name: 'Alpha' }), auto({ id: 42, name: 'Beta' })],
      undefined,
      'all',
      '&q=Alpha',
    );
    await screen.findByText('Alpha');

    expect(screen.queryByText('Beta')).toBeNull(); // card filtered out
    expect(document.querySelector('.automx-library-sub')?.textContent).toMatch(/2 automations/);
  });

  it('supplies a handler for every affordance the card renders', async () => {
    // onShowHistory was declared as a prop and simply never passed, so the
    // Runs link rendered and did nothing. This asserts the card is never handed
    // an incomplete handler set again — the failure mode is a dead control,
    // which no type error and no rendering test would catch.
    const { AutomationCard } = await import('./automation-card');
    const declared = [
      'onRun',
      'onToggle',
      'onEdit',
      'onDuplicate',
      'onAssignGroup',
      'onDelete',
      'onShowHistory',
    ];
    const source = AutomationCard.toString();
    // Every handler the component invokes must be one the page provides.
    const invoked = declared.filter((name) => source.includes(name));
    const pageSource = (await import('node:fs')).readFileSync(
      'src/routes/automations/-ui/automations-page.tsx',
      'utf8',
    );
    const missing = invoked.filter((name) => !pageSource.includes(`${name}:`));
    expect(missing).toEqual([]);
  });
});
