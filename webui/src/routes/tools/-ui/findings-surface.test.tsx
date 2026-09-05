/**
 * The findings surface — health hero, filters, the grouped inbox, the list
 * inside an open group, pagination, bulk paths, and the per-type detail
 * renderer.
 *
 * The safety-critical assertions here are the mass-orphan gate (a fix-all that
 * would delete files must go through the type-the-phrase dialog) and the
 * ask-once-per-kind ordering in the bulk path — both are places where a plausible
 * refactor deletes a confirmation without any test noticing.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RepairFinding, RepairJob } from '../-tools.types';

import { FindingsSurface } from './findings-surface';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();
const navigateSpy = vi.fn();
const playSpy = vi.fn();

/**
 * Route by URL, preferring the LONGEST matching key.
 *
 * Per-finding routes MUST be keyed by their full path: `/api/repair/findings` is
 * a prefix of `/api/repair/findings/1/fix`, so a short `/1/fix` key loses the
 * longest-match race and the fix call gets handed the findings list instead —
 * the same shadowing that made a hero test pass for the wrong reason.
 */
function routes(map: Record<string, unknown>, fallback: unknown = {}) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map)
      .filter((key) => url.includes(key))
      .sort((a, b) => b.length - a.length)[0];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (hit ? map[hit] : fallback),
    } as never);
  });
}

async function flush() {
  await act(async () => {});
}

const FINDINGS = '/api/repair/findings';
const COUNTS = '/api/repair/findings/counts';

const finding = (over: Partial<RepairFinding> = {}): RepairFinding => ({
  id: 1,
  job_id: 'orphan_file_detector',
  finding_type: 'orphan_file',
  severity: 'warning',
  status: 'pending',
  title: 'Orphan file',
  description: 'Not in the database',
  file_path: '/music/a/b.flac',
  entity_type: 'file',
  created_at: new Date().toISOString(),
  details: {},
  ...over,
});

const page = (items: RepairFinding[], total = items.length) => ({ items, total, page: 0 });

const JOBS: RepairJob[] = [
  {
    job_id: 'orphan_file_detector',
    display_name: 'Orphan File Detector',
    description: '',
    enabled: true,
    is_running: false,
  } as RepairJob,
  {
    job_id: 'dead_file_cleaner',
    display_name: 'Dead File Cleaner',
    description: '',
    enabled: true,
    is_running: false,
  } as RepairJob,
];

const GROUPS = '/api/repair/findings/groups';
const TYPES = '/api/repair/finding-types';

const group = (over: Record<string, unknown> = {}) => ({
  finding_type: 'orphan_file',
  pending: 3,
  resolved: 0,
  dismissed: 0,
  total: 3,
  severity_max: 'warning',
  last_seen: null,
  job_ids: ['orphan_file_detector'],
  ...over,
});

const typeInfo = (over: Record<string, unknown> = {}) => ({
  type: 'orphan_file',
  label: 'Orphan Files',
  verb: 'Review & Move',
  fixable: true,
  destructive: true,
  job_ids: ['orphan_file_detector'],
  ...over,
});

function renderSurface(jobs: RepairJob[] = JOBS) {
  const onStatusChanged = vi.fn();
  const result = render(
    <FindingsSurface jobs={jobs} runs={[]} trackCount={10000} onStatusChanged={onStatusChanged} />,
  );
  return { ...result, onStatusChanged };
}

/**
 * Most of what follows is about the finding LIST, which only renders inside an
 * open group or a search. Searching is the cheaper of the two: it needs no
 * groups payload and scopes to no type, so the list behaves exactly as the
 * flat list always did.
 */
async function renderList(jobs: RepairJob[] = JOBS) {
  const result = renderSurface(jobs);
  await flush();
  fireEvent.change(document.getElementById('repair-findings-search') as HTMLElement, {
    target: { value: 'a' },
  });
  await flush();
  return result;
}

/** The JSON body of the request whose URL matches `match`. */
function bodyOf(match: string): unknown {
  const call = fetchMock.mock.calls.find((entry) => String(entry[0]).includes(match));
  expect(call, `expected a request matching ${match}`).toBeDefined();
  return JSON.parse(((call?.[1] as RequestInit | undefined)?.body as string) ?? '{}');
}

/** Answer a prompt overlay by clicking one of its buttons. */
function clickPrompt(id: string) {
  const button = document.getElementById(id);
  expect(button, `prompt button #${id} should be showing`).not.toBeNull();
  fireEvent.click(button as HTMLElement);
}

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  confirmSpy.mockReset().mockResolvedValue(true);
  navigateSpy.mockReset();
  playSpy.mockReset();
  routes({});
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, {
    showToast: toastSpy,
    showConfirmDialog: confirmSpy,
    SoulSyncWebShellBridge: {
      navigateToArtistDetail: navigateSpy,
      playLibraryTrack: playSpy,
    },
  });
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ── Health hero + status control ─────────────────────────────────────────────

describe('the health hero', () => {
  it('scores a clean library 100 and says so', async () => {
    routes({ [COUNTS]: { pending: 0 }, [GROUPS]: { groups: [] } });
    renderSurface();
    await flush();
    expect(document.querySelector('.repair-health-score')?.textContent).toBe('100');
    expect(document.querySelector('.repair-health-band')?.textContent).toBe('healthy');
    expect(document.querySelector('.repair-health-bar-empty')).not.toBeNull();
  });

  it('weights errors far above info, and the bar segments follow the weighting', async () => {
    routes({
      [COUNTS]: { pending: 403 },
      [GROUPS]: {
        groups: [
          group({ finding_type: 'corrupt_audio', pending: 3, severity_max: 'error' }),
          group({ finding_type: 'missing_cover_art', pending: 400, severity_max: 'info' }),
        ],
      },
      [TYPES]: {
        types: [
          typeInfo({ type: 'corrupt_audio', label: 'Corrupt Audio', destructive: true }),
          typeInfo({ type: 'missing_cover_art', label: 'Missing Cover Art', destructive: false }),
        ],
      },
    });
    renderSurface();
    await flush();

    // 3 errors weigh 3.0; 400 info rows weigh 8.0. Three broken files are a
    // QUARTER of the bar next to four hundred missing covers — which is the
    // whole point of weighting it.
    const segments = [...document.querySelectorAll('.repair-health-seg')];
    expect(segments).toHaveLength(2);
    expect(segments[0].className).toContain('info');
    expect(segments[0].getAttribute('title')).toContain('Missing Cover Art');
    expect(segments[1].className).toContain('error');
  });

  it('a bar segment opens that group in the inbox', async () => {
    routes({
      [COUNTS]: { pending: 3 },
      [GROUPS]: { groups: [group()] },
      [TYPES]: { types: [typeInfo()] },
      [FINDINGS]: page([finding()]),
    });
    renderSurface();
    await flush();

    fireEvent.click(document.querySelector('.repair-health-seg') as HTMLElement);
    await flush();
    expect(document.querySelector('.repair-inbox-group.open')).not.toBeNull();
  });

  it('offers Fix all safe only for fixable, non-destructive pending rows', async () => {
    routes({
      [COUNTS]: { pending: 30 },
      [GROUPS]: {
        groups: [
          group({ finding_type: 'orphan_file', pending: 20 }),
          group({ finding_type: 'missing_cover_art', pending: 10, severity_max: 'info' }),
        ],
      },
      [TYPES]: {
        types: [
          typeInfo({ type: 'orphan_file', destructive: true }),
          typeInfo({ type: 'missing_cover_art', label: 'Missing Cover Art', destructive: false }),
        ],
      },
    });
    renderSurface();
    await flush();
    // The 20 orphans move files; only the 10 art rows are safe.
    expect(document.querySelector('.repair-health-fix-safe')?.textContent).toBe(
      'Fix all safe (10)',
    );
  });

  it('sends safe_only — never a fix_action — when Fix all safe runs', async () => {
    routes({
      [COUNTS]: { pending: 10 },
      [GROUPS]: { groups: [group({ finding_type: 'missing_cover_art', pending: 10 })] },
      [TYPES]: { types: [typeInfo({ type: 'missing_cover_art', destructive: false })] },
      '/bulk-fix-start': { started: true, total: 10 },
    });
    renderSurface();
    await flush();

    fireEvent.click(document.querySelector('.repair-health-fix-safe') as HTMLElement);
    await flush();
    expect(bodyOf('/bulk-fix-start')).toEqual({ safe_only: true });
  });

  it('disables Fix all safe when nothing is safe to fix', async () => {
    routes({
      [COUNTS]: { pending: 3 },
      [GROUPS]: { groups: [group()] },
      [TYPES]: { types: [typeInfo({ destructive: true })] },
    });
    renderSurface();
    await flush();
    expect((document.querySelector('.repair-health-fix-safe') as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe('the status segmented control', () => {
  it('shows a count per status so an empty list explains itself', async () => {
    routes({ [COUNTS]: { pending: 0, resolved: 5, dismissed: 2, total: 7 } });
    renderSurface();
    await flush();
    const segments = [...document.querySelectorAll('.repair-status-seg')].map(
      (node) => node.textContent,
    );
    expect(segments).toEqual(['Open0', 'Fixed5', 'Dismissed2', 'All7']);
  });

  it('grows an Auto-fixed segment only once the worker has fixed something', async () => {
    // auto_fixed is its OWN status, so it is neither in the resolved count nor
    // reachable from the other three segments — an install where it is always
    // zero should not carry a control that always reads zero either.
    routes({ [COUNTS]: { pending: 1, resolved: 0, dismissed: 0, auto_fixed: 9, total: 10 } });
    renderSurface();
    await flush();
    const segments = [...document.querySelectorAll('.repair-status-seg')].map(
      (node) => node.textContent,
    );
    expect(segments).toEqual(['Open1', 'Fixed0', 'Dismissed0', 'Auto-fixed9', 'All10']);
  });

  it('never changes the filter by itself', async () => {
    // The old surface flipped to All Status on your behalf when pending was
    // empty, then explained itself in a notice it never removed. The counts
    // above make the flip unnecessary; the filter is the user's alone.
    routes({ [COUNTS]: { pending: 0, dismissed: 9 }, [FINDINGS]: page([]) });
    renderSurface();
    await flush();
    expect(document.querySelector('.repair-status-seg.active')?.textContent).toContain('Open');
    expect(document.querySelector('.repair-auto-switch-notice')).toBeNull();
  });

  it('scopes the list to the chosen status', async () => {
    routes({ [COUNTS]: { dismissed: 9 }, [FINDINGS]: page([]) });
    await renderList();

    fireEvent.click(screen.getByText('Dismissed').closest('button') as HTMLElement);
    await flush();
    const url = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .findLast((u) => u.includes('?'));
    expect(url).toContain('status=dismissed');
  });
});

describe('cache health', () => {
  it('shows the cache health bar with its scored dot', async () => {
    routes({
      [COUNTS]: { pending: 1 },
      '/api/repair/cache-health': { total_entities: 1200, junk_entities: 80, stale_mb_nulls: 0 },
    });
    renderSurface();
    await flush();
    expect(document.querySelector('.repair-cache-health-dot')?.className).toContain('poor');
    expect(document.querySelector('.repair-cache-health-summary')?.textContent).toContain(
      'Needs Attention',
    );
  });

  it('the cache health bar opens the shared vanilla modal', async () => {
    const openHealth = vi.fn();
    Object.assign(window, { openCacheHealthModal: openHealth });
    routes({
      [COUNTS]: { pending: 1 },
      '/api/repair/cache-health': { total_entities: 10, junk_entities: 0, stale_mb_nulls: 0 },
    });
    renderSurface();
    await flush();

    fireEvent.click(document.querySelector('.repair-cache-health-bar') as HTMLElement);
    expect(openHealth).toHaveBeenCalled();
  });

  it('never asks for cache health when the counts call failed', async () => {
    // The vanilla only reaches `_loadCacheHealthStats` on the success path — a
    // dashboard that blanked itself does not then go fetch a bar to put in it.
    fetchMock.mockImplementation((url: string) => {
      if (url.includes(COUNTS)) return Promise.reject(new Error('nope'));
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) } as never);
    });
    renderSurface();
    await flush();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/cache-health'))).toBe(false);
  });

  it('hides the cache health bar when the cache is empty', async () => {
    routes({
      [COUNTS]: { pending: 1 },
      '/api/repair/cache-health': { total_entities: 0, total_searches: 0 },
    });
    renderSurface();
    await flush();
    expect(document.querySelector('.repair-cache-health')).toBeNull();
  });
});

// ── The inbox ────────────────────────────────────────────────────────────────

describe('the findings inbox', () => {
  it('orders worst-first, with destructive types last inside a severity band', async () => {
    routes({
      [GROUPS]: {
        groups: [
          group({ finding_type: 'missing_cover_art', pending: 400, severity_max: 'info' }),
          group({ finding_type: 'orphan_file', pending: 9, severity_max: 'warning' }),
          group({ finding_type: 'metadata_gap', pending: 2, severity_max: 'warning' }),
          group({ finding_type: 'corrupt_audio', pending: 1, severity_max: 'error' }),
        ],
      },
      [TYPES]: {
        types: [
          typeInfo({ type: 'missing_cover_art', destructive: false }),
          typeInfo({ type: 'orphan_file', destructive: true }),
          typeInfo({ type: 'metadata_gap', destructive: false }),
          typeInfo({ type: 'corrupt_audio', destructive: true }),
        ],
      },
    });
    renderSurface();
    await flush();

    const order = [...document.querySelectorAll('.repair-inbox-group')].map((node) =>
      node.getAttribute('data-finding-type'),
    );
    // metadata_gap (2 rows) beats orphan_file (9) inside the warning band
    // because orphan_file moves files — the safe decision comes first.
    expect(order).toEqual(['corrupt_audio', 'metadata_gap', 'orphan_file', 'missing_cover_art']);
  });

  it('shows the blurb and the served verb, and no fix button for an unfixable type', async () => {
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'fake_lossless', pending: 4 })] },
      [TYPES]: {
        types: [
          typeInfo({
            type: 'fake_lossless',
            label: 'Fake Lossless',
            verb: null,
            fixable: false,
            destructive: false,
          }),
        ],
      },
    });
    renderSurface();
    await flush();

    expect(document.querySelector('.repair-inbox-blurb')?.textContent).toContain('upscaled');
    expect(document.querySelector('.repair-inbox-btn')?.textContent).toBe('Dismiss all');
  });

  it('opens exactly one group at a time and scopes the list to its type', async () => {
    routes({
      [GROUPS]: {
        groups: [
          group({ finding_type: 'orphan_file' }),
          group({ finding_type: 'metadata_gap', severity_max: 'warning' }),
        ],
      },
      [TYPES]: {
        types: [
          typeInfo({ type: 'orphan_file' }),
          typeInfo({ type: 'metadata_gap', destructive: false }),
        ],
      },
      [FINDINGS]: page([finding()]),
    });
    renderSurface();
    await flush();

    const heads = [...document.querySelectorAll('.repair-inbox-head')];
    fireEvent.click(heads[0]);
    await flush();
    expect(
      fetchMock.mock.calls.map((call) => String(call[0])).findLast((u) => u.includes('?')),
    ).toContain('finding_type=metadata_gap');

    fireEvent.click(heads[1]);
    await flush();
    expect(document.querySelectorAll('.repair-inbox-group.open')).toHaveLength(1);
  });

  it('does not fetch any findings while the inbox is collapsed', async () => {
    routes({ [GROUPS]: { groups: [group()] }, [TYPES]: { types: [typeInfo()] } });
    renderSurface();
    await flush();
    // The list used to load 30 rows nobody had asked to see, on every open.
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/findings?'))).toBe(false);
  });

  it('a group fix is scoped to its finding type, never to a job', async () => {
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'missing_cover_art', pending: 12 })] },
      [TYPES]: {
        types: [
          typeInfo({
            type: 'missing_cover_art',
            label: 'Missing Cover Art',
            verb: 'Apply Art',
            destructive: false,
          }),
        ],
      },
      '/bulk-fix-start': { started: true, total: 12 },
    });
    renderSurface();
    await flush();

    fireEvent.click(screen.getByText('Apply Art all (12)'));
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ destructive: false, confirmText: 'Apply Art' }),
    );
    expect(bodyOf('/bulk-fix-start')).toEqual({ finding_type: 'missing_cover_art' });
  });

  it('marks a destructive group fix destructive and says files are touched', async () => {
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'empty_folder', pending: 4 })] },
      [TYPES]: {
        types: [
          typeInfo({
            type: 'empty_folder',
            label: 'Empty Folders',
            verb: 'Delete Folder',
            destructive: true,
          }),
        ],
      },
      '/bulk-fix-start': { started: true, total: 4 },
    });
    renderSurface();
    await flush();

    fireEvent.click(screen.getByText('Delete Folder…'));
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        destructive: true,
        message: expect.stringContaining('deletes files on disk'),
      }),
    );
  });

  it('still gates a mass orphan delete behind the witness-me phrase', async () => {
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'orphan_file', pending: 120 })] },
      [TYPES]: { types: [typeInfo()] },
      '/bulk-fix-start': { started: true, total: 120 },
    });
    renderSurface();
    await flush();

    fireEvent.click(screen.getByText('Review & Move…'));
    await flush();
    clickPrompt('_orphan-delete');
    await flush();

    const input = document.getElementById('witness-me-input') as HTMLInputElement;
    expect(input, 'the witness-me dialog must appear for a mass delete').not.toBeNull();
    fireEvent.change(input, { target: { value: 'Witness Me' } });
    fireEvent.click(document.getElementById('witness-confirm') as HTMLElement);
    await flush();
    expect(bodyOf('/bulk-fix-start')).toEqual({
      finding_type: 'orphan_file',
      fix_action: 'delete',
    });
  });

  it('dismisses a whole group by type rather than by shipping ids', async () => {
    routes({
      [GROUPS]: { groups: [group({ pending: 900 })] },
      [TYPES]: { types: [typeInfo()] },
      '/api/repair/findings/bulk': { success: true, updated: 900 },
    });
    renderSurface();
    await flush();

    fireEvent.click(screen.getByText('Dismiss all'));
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('never raised again') }),
    );
    expect(bodyOf('/api/repair/findings/bulk')).toEqual({
      finding_type: 'orphan_file',
      action: 'dismiss',
    });
  });

  it('says All Clear when no group survives the filters', async () => {
    routes({ [GROUPS]: { groups: [] } });
    renderSurface();
    await flush();
    expect(screen.getByText('All Clear')).toBeTruthy();
  });
});

// ── Filters ──────────────────────────────────────────────────────────────────

describe('filters', () => {
  it('sends only the non-empty filters, plus page and limit', async () => {
    routes({ [FINDINGS]: page([]) });
    await renderList();

    fireEvent.change(document.getElementById('repair-findings-severity-filter') as HTMLElement, {
      target: { value: 'warning' },
    });
    await flush();

    const url = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .findLast((u) => u.includes('?'));
    expect(url).toContain('severity=warning');
    expect(url).toContain('status=pending');
    expect(url).toContain('sort=newest');
    expect(url).toContain('q=a');
    expect(url).toContain('page=0');
    expect(url).toContain('limit=30');
    expect(url).not.toContain('job_id=');
    // A search deliberately escapes the grouping — it looks everywhere.
    expect(url).not.toContain('finding_type=');
  });

  it('a search replaces the inbox rather than filtering it', async () => {
    routes({
      [GROUPS]: { groups: [group()] },
      [TYPES]: { types: [typeInfo()] },
      [FINDINGS]: page([]),
    });
    await renderList();
    expect(document.querySelector('.repair-inbox')).toBeNull();
    expect(document.querySelector('.repair-search-note')).not.toBeNull();
  });

  it('passes the chosen sort through to the server', async () => {
    routes({ [FINDINGS]: page([]) });
    await renderList();

    fireEvent.change(document.getElementById('repair-findings-sort') as HTMLElement, {
      target: { value: 'path' },
    });
    await flush();
    const url = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .findLast((u) => u.includes('?'));
    expect(url).toContain('sort=path');
  });

  it('persists the page size and resets to page one', async () => {
    routes({ [FINDINGS]: page([]) });
    await renderList();

    fireEvent.change(document.getElementById('repair-page-size-select') as HTMLElement, {
      target: { value: '100' },
    });
    await flush();

    expect(localStorage.getItem('repairFindingsPageSize')).toBe('100');
    const url = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .findLast((u) => u.includes('?'));
    expect(url).toContain('limit=100');
  });

  it('reads the stored page size on mount, ignoring a value not on the menu', async () => {
    localStorage.setItem('repairFindingsPageSize', '60');
    routes({ [FINDINGS]: page([]) });
    await renderList();
    expect((document.getElementById('repair-page-size-select') as HTMLSelectElement).value).toBe(
      '60',
    );

    cleanup();
    localStorage.setItem('repairFindingsPageSize', '999');
    await renderList();
    expect((document.getElementById('repair-page-size-select') as HTMLSelectElement).value).toBe(
      '30',
    );
  });

  it('builds the job dropdown from the job list', async () => {
    routes({ [FINDINGS]: page([]) });
    renderSurface();
    await flush();
    const options = [
      ...(document.getElementById('repair-findings-job-filter') as HTMLSelectElement).options,
    ].map((option) => option.textContent);
    expect(options).toEqual(['All Jobs', 'Orphan File Detector', 'Dead File Cleaner']);
  });

  it('filters the inbox by job without a round trip', async () => {
    routes({
      [GROUPS]: {
        groups: [
          group({ finding_type: 'orphan_file', job_ids: ['orphan_file_detector'] }),
          group({ finding_type: 'dead_file', job_ids: ['dead_file_cleaner'] }),
        ],
      },
      [TYPES]: {
        types: [typeInfo({ type: 'orphan_file' }), typeInfo({ type: 'dead_file' })],
      },
    });
    renderSurface();
    await flush();
    const before = fetchMock.mock.calls.length;

    fireEvent.change(document.getElementById('repair-findings-job-filter') as HTMLElement, {
      target: { value: 'dead_file_cleaner' },
    });
    await flush();

    const shown = [...document.querySelectorAll('.repair-inbox-group')].map((node) =>
      node.getAttribute('data-finding-type'),
    );
    expect(shown).toEqual(['dead_file']);
    // Tens of group rows, not thousands — re-querying the server for a
    // dropdown change would buy nothing.
    expect(fetchMock.mock.calls.length).toBe(before);
  });
});

// ── The list ─────────────────────────────────────────────────────────────────

describe('the finding list', () => {
  it('carries the id, job and mass-orphan flag the safety gate reads', async () => {
    routes({ [FINDINGS]: page([finding({ id: 42, details: { mass_orphan: true } })]) });
    await renderList();
    await flush();

    const card = document.querySelector('.repair-finding-card') as HTMLElement;
    expect(card.dataset.id).toBe('42');
    expect(card.dataset.jobId).toBe('orphan_file_detector');
    expect(card.dataset.massOrphan).toBe('true');
    expect(card.className).toContain('warning');
  });

  it('writes data-mass-orphan="false" rather than omitting it', async () => {
    routes({ [FINDINGS]: page([finding()]) });
    await renderList();
    await flush();
    expect((document.querySelector('.repair-finding-card') as HTMLElement).dataset.massOrphan).toBe(
      'false',
    );
  });

  it('renders the type badge, the path and the meta row', async () => {
    routes({
      [FINDINGS]: page([finding({ entity_id: 'abc123', entity_type: 'track' })]),
    });
    await renderList();
    await flush();

    expect(document.querySelector('.repair-finding-type-badge')?.textContent).toBe('Orphan');
    expect(document.querySelector('.repair-finding-path')?.textContent).toBe('/music/a/b.flac');
    const meta = document.querySelector('.repair-finding-meta')?.textContent || '';
    // The job's DISPLAY name, matching the filter dropdown and the
    // dashboard chips — the raw snake_case id meant one job went by two
    // names on the same screen.
    expect(meta).toContain('Orphan File Detector');
    expect(meta).toContain('track');
    expect(meta).toContain('ID: abc123');
  });

  it('shows the action badge on a non-pending finding and hides the fix buttons', async () => {
    routes({
      [FINDINGS]: page([finding({ status: 'resolved', user_action: 'deleted_file' })]),
    });
    await renderList();
    await flush();

    expect(document.querySelector('.repair-finding-status-badge')?.textContent).toBe(
      'File Deleted',
    );
    expect(document.querySelector('.repair-finding-btn.fix')).toBeNull();
    expect(document.querySelector('.repair-finding-btn.dismiss')).toBeNull();
  });

  it('falls back to the raw status when the action has no label', async () => {
    routes({
      [FINDINGS]: page([finding({ status: 'dismissed', user_action: 'something_new' })]),
    });
    await renderList();
    await flush();
    expect(document.querySelector('.repair-finding-status-badge')?.textContent).toBe('dismissed');
  });

  it('omits the fix button for a type with no automated fix', async () => {
    routes({ [FINDINGS]: page([finding({ finding_type: 'path_mismatch' })]) });
    await renderList();
    await flush();
    expect(document.querySelector('.repair-finding-btn.fix')).toBeNull();
    expect(document.querySelector('.repair-finding-btn.dismiss')).not.toBeNull();
  });

  it('shows the empty state when nothing matches', async () => {
    routes({ [FINDINGS]: page([]) });
    await renderList();
    await flush();
    expect(screen.getByText('Nothing here matches your filters.')).toBeTruthy();
  });

  it('shows an error row when the list call fails', async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes(`${FINDINGS}?`)
        ? Promise.reject(new Error('boom'))
        : Promise.resolve({ ok: true, status: 200, json: async () => ({}) } as never),
    );
    await renderList();
    await flush();
    expect(screen.getByText('Error loading findings')).toBeTruthy();
  });

  it('toggles the detail panel from the row body', async () => {
    routes({ [FINDINGS]: page([finding({ id: 5 })]) });
    await renderList();
    await flush();

    const panel = document.getElementById('repair-detail-5') as HTMLElement;
    expect(panel.className).not.toContain('open');
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);
    expect(panel.className).toContain('open');
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);
    expect(panel.className).not.toContain('open');
  });

  it('mounts a row detail ONLY while it is open', async () => {
    // Every collapsed row used to build its full 20-branch detail tree and
    // fetch its album/artist art, hidden behind max-height:0 — so a 100-row
    // page rendered 100 invisible panels and hammered the thumbnail
    // endpoints for content nobody had asked to see.
    routes({ [FINDINGS]: page([finding({ id: 5 })]) });
    await renderList();
    await flush();

    const panel = () => document.getElementById('repair-detail-5') as HTMLElement;
    expect(panel().querySelector('.repair-finding-detail-inner')?.children.length).toBe(0);

    fireEvent.click(document.querySelector('.repair-finding-expand-btn') as HTMLElement);
    await flush();
    expect(
      panel().querySelector('.repair-finding-detail-inner')?.children.length || 0,
    ).toBeGreaterThan(0);
  });

  it('offers the critical severity the jobs actually emit', async () => {
    // `error` is the corruption detector's severity — the most urgent
    // findings in the system, and they had no filter option at all.
    await renderList();
    await flush();
    const options = Array.from(
      document.querySelectorAll('#repair-findings-severity-filter option'),
    ).map((o) => (o as HTMLOptionElement).value);
    expect(options).toContain('error');
  });
  it('expands when the chevron is clicked', async () => {
    // It used to be decorative: `.repair-finding-actions` stops propagation
    // and the chevron carried no handler, so the ONE control that looks like
    // an expander did nothing and only the row body worked. It has its own
    // handler now (and still stops propagation, or the row toggle would
    // immediately undo it).
    routes({ [FINDINGS]: page([finding({ id: 5 })]) });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-expand-btn') as HTMLElement);
    expect((document.getElementById('repair-detail-5') as HTMLElement).className).toContain('open');
    fireEvent.click(document.querySelector('.repair-finding-expand-btn') as HTMLElement);
    expect((document.getElementById('repair-detail-5') as HTMLElement).className).not.toContain(
      'open',
    );
  });

  it('does not toggle the detail when the checkbox is clicked', async () => {
    routes({ [FINDINGS]: page([finding({ id: 5 })]) });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-select input') as HTMLElement);
    expect((document.getElementById('repair-detail-5') as HTMLElement).className).not.toContain(
      'open',
    );
  });
});

// ── Per-finding actions ──────────────────────────────────────────────────────

describe('per-finding actions', () => {
  it('prompts for the orphan action and sends the chosen one', async () => {
    routes({
      [FINDINGS]: page([finding({ id: 1 })]),
      '/api/repair/findings/1/fix': { success: true, message: 'Moved' },
    });
    const { onStatusChanged } = await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_orphan-staging');
    await flush();

    expect(bodyOf('/1/fix')).toEqual({
      fix_action: 'staging',
    });
    expect(toastSpy).toHaveBeenCalledWith('Moved', 'success');
    expect(onStatusChanged).toHaveBeenCalled();
  });

  it('offers a corrupt file with no track behind it a delete, not a re-download', async () => {
    // The corruption detector walks the library folders as well as the
    // catalogue. Those rows carry `entity_type: 'file'` and no id, had no
    // per-row button at all, and the group button said "Re-download" over a
    // fix that could only answer "No track ID associated with this finding".
    routes({
      [FINDINGS]: page([
        finding({
          id: 4,
          job_id: 'audio_corruption_detector',
          finding_type: 'corrupt_audio',
          severity: 'error',
          title: 'Corrupt file: Unknown - 01 - Miss YOU!',
          entity_type: 'file',
          entity_id: null,
        }),
      ]),
      '/api/repair/findings/4/fix': { success: true, message: 'Deleted the corrupt file.' },
    });
    await renderList();
    await flush();

    const button = document.querySelector('.repair-finding-btn.fix') as HTMLElement;
    expect(button?.textContent).toBe('Delete File');

    fireEvent.click(button);
    await flush();

    expect(confirmSpy).toHaveBeenCalled();
    expect(String(confirmSpy.mock.calls[0]?.[0]?.message)).not.toContain('re-download');
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/4/fix'))).toBe(true);
  });

  it('does not delete the file when that confirm is declined', async () => {
    confirmSpy.mockResolvedValue(false);
    routes({
      [FINDINGS]: page([
        finding({ id: 4, finding_type: 'corrupt_audio', entity_type: 'file', entity_id: null }),
      ]),
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();

    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/fix'))).toBe(false);
  });

  it('lets a re-tag with a hand-set field be settled per row', async () => {
    // lib2 keeps a per-field override layer, and a re-tag respects it. That is
    // the right default and the wrong thing to make silent: someone who fixed
    // a title months ago and has since fixed the catalogue needs a way to say
    // "the catalogue wins now".
    routes({
      [FINDINGS]: page([
        finding({
          id: 6,
          job_id: 'library_retag',
          finding_type: 'library_retag',
          title: 'Tags out of date: Drake - One Dance',
          entity_type: 'track',
          entity_id: 'lib2:6',
          details: { has_manual_conflict: true, manual_fields: ['Title'] },
        }),
      ]),
      '/api/repair/findings/6/fix': { success: true, message: 'Wrote tags' },
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_retag-overwrite');
    await flush();

    expect(bodyOf('/6/fix')).toEqual({ fix_action: 'overwrite_manual' });
  });

  it('does not stop to ask when nothing on the row was hand-set', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 7,
          job_id: 'library_retag',
          finding_type: 'library_retag',
          entity_type: 'track',
          entity_id: 'lib2:7',
          details: { has_manual_conflict: false },
        }),
      ]),
      '/api/repair/findings/7/fix': { success: true, message: 'Wrote tags' },
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();

    // No fix_action at all: keeping hand-set fields is what the handler does
    // with none, so sending 'safe' would add a string nothing reads.
    expect(bodyOf('/7/fix')).toEqual({});
  });

  it('sends nothing when the prompt is cancelled', async () => {
    routes({ [FINDINGS]: page([finding({ id: 1 })]) });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_orphan-cancel');
    await flush();

    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/fix'))).toBe(false);
  });

  it('routes the quality-upgrade "Ignore" choice to dismiss, not fix', async () => {
    routes({
      [FINDINGS]: page([finding({ id: 3, finding_type: 'quality_upgrade' })]),
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_qual-ignore');
    await flush();

    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/3/dismiss'))).toBe(true);
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/3/fix'))).toBe(false);
  });

  it('routes the discography "Just Clear" choice to dismiss', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 4,
          finding_type: 'missing_discography_track',
          job_id: 'discography_backfill',
        }),
      ]),
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_dbf-dismiss');
    await flush();

    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/4/dismiss'))).toBe(true);
  });

  it('sends an empty body for discography "Add to Wishlist" — the handler defaults', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 4,
          finding_type: 'missing_discography_track',
          job_id: 'discography_backfill',
        }),
      ]),
      '/api/repair/findings/4/fix': { success: true },
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    clickPrompt('_dbf-add');
    await flush();

    expect(bodyOf('/4/fix')).toEqual({});
  });

  it('reports the server error when a fix fails', async () => {
    routes({
      [FINDINGS]: page([finding({ id: 1, finding_type: 'empty_folder', job_id: 'x' })]),
      '/api/repair/findings/1/fix': { success: false, error: 'folder not empty' },
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    expect(toastSpy).toHaveBeenCalledWith('folder not empty', 'error');
  });

  it('toasts on a network failure rather than leaving the button stuck', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith('/1/fix')) return Promise.reject(new Error('offline'));
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          String(url).includes(`${FINDINGS}?`)
            ? page([finding({ id: 1, finding_type: 'empty_folder', job_id: 'x' })])
            : {},
      } as never);
    });
    await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();
    expect(toastSpy).toHaveBeenCalledWith('Error applying fix', 'error');
    expect((document.querySelector('.repair-finding-btn.fix') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('keeps the fix button disabled through a successful fix until the list reloads', async () => {
    // The vanilla leaves the button on "..." until the reload replaces the list's
    // innerHTML — which is what stops a second click firing a second fix.
    const reload: { release: (() => void) | null } = { release: null };
    let listCalls = 0;
    fetchMock.mockImplementation((url: string) => {
      const target = String(url);
      if (target.endsWith('/1/fix')) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true }) });
      }
      if (target.includes(`${FINDINGS}?`)) {
        listCalls += 1;
        if (listCalls > 1) {
          // Hold the reload open so the in-flight state stays observable.
          return new Promise((resolve) => {
            reload.release = () =>
              resolve({
                ok: true,
                status: 200,
                json: async () => page([finding({ id: 1, finding_type: 'empty_folder' })]),
              });
          });
        }
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          target.includes(`${FINDINGS}?`)
            ? page([finding({ id: 1, finding_type: 'empty_folder' })])
            : {},
      });
    });

    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-btn.fix') as HTMLElement);
    await flush();

    const button = document.querySelector('.repair-finding-btn.fix') as HTMLButtonElement;
    expect(button.textContent).toBe('...');
    expect(button.disabled).toBe(true);

    reload.release?.();
    await flush();
    await flush();
    expect((document.querySelector('.repair-finding-btn.fix') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('dismisses a finding and refreshes', async () => {
    routes({ [FINDINGS]: page([finding({ id: 8 })]) });
    const { onStatusChanged } = await renderList();
    await flush();

    fireEvent.click(document.querySelector('.repair-finding-btn.dismiss') as HTMLElement);
    await flush();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/8/dismiss'))).toBe(true);
    expect(onStatusChanged).toHaveBeenCalled();
  });
});

// ── Bulk paths ───────────────────────────────────────────────────────────────

describe('bulk selection', () => {
  it('reveals the bulk bar and counts the selection', async () => {
    routes({ [FINDINGS]: page([finding({ id: 1 }), finding({ id: 2 })]) });
    await renderList();
    await flush();

    expect(document.getElementById('repair-findings-selection')).toBeNull();
    fireEvent.click(document.querySelectorAll('.repair-finding-select input')[0]);
    await flush();
    expect(document.getElementById('repair-findings-selection')).not.toBeNull();
    expect(document.querySelector('.repair-bulk-count')?.textContent).toBe('1 selected');
  });

  it('select-all is indeterminate on a partial page and checked on a full one', async () => {
    routes({ [FINDINGS]: page([finding({ id: 1 }), finding({ id: 2 })]) });
    await renderList();
    await flush();

    const selectAll = document.getElementById('repair-select-all-cb') as HTMLInputElement;
    fireEvent.click(document.querySelectorAll('.repair-finding-select input')[0]);
    await flush();
    expect(selectAll.indeterminate).toBe(true);
    expect(selectAll.checked).toBe(false);

    fireEvent.click(document.querySelectorAll('.repair-finding-select input')[1]);
    await flush();
    expect(selectAll.indeterminate).toBe(false);
    expect(selectAll.checked).toBe(true);
  });

  it('no longer offers a filter-wide Fix All from the selection bar', async () => {
    // A cross-type Fix All could not carry a fix_action safely — 'delete'
    // removes an orphan's file and names the track to KEEP for a duplicate.
    // The whole-group button replaced it, and the backend refuses an action
    // that spans more than one type.
    routes({ [FINDINGS]: page([finding({ id: 1 })], 90) });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    expect(document.getElementById('repair-fix-all-btn')).toBeNull();
  });

  it('bulk-dismisses the selection', async () => {
    routes({ [FINDINGS]: page([finding({ id: 1 }), finding({ id: 2 })]) });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Dismiss Selected'));
    await flush();

    expect(bodyOf('/findings/bulk')).toEqual({
      ids: [1, 2],
      action: 'dismiss',
    });
    expect(toastSpy).toHaveBeenCalledWith('2 findings dismissed', 'success');
  });

  it('asks once per finding KIND, then fixes each id with its own action', async () => {
    routes({
      [FINDINGS]: page([
        finding({ id: 1, job_id: 'orphan_file_detector' }),
        finding({ id: 2, job_id: 'orphan_file_detector' }),
        finding({ id: 3, job_id: 'dead_file_cleaner', finding_type: 'dead_file' }),
      ]),
      '/api/repair/findings/1/fix': { success: true },
      '/api/repair/findings/2/fix': { success: true },
      '/api/repair/findings/3/fix': { success: true },
    });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Fix Selected'));
    await flush();

    clickPrompt('_orphan-staging');
    await flush();
    clickPrompt('_dead-remove');
    await flush();

    const bodyFor = (id: number) => bodyOf(`/${id}/fix`);
    expect(bodyFor(1)).toEqual({ fix_action: 'staging' });
    expect(bodyFor(2)).toEqual({ fix_action: 'staging' });
    expect(bodyFor(3)).toEqual({ fix_action: 'remove' });
    expect(toastSpy).toHaveBeenCalledWith('Fixed 3', 'success');
  });

  it('aborts the whole bulk run if any prompt is cancelled', async () => {
    routes({
      [FINDINGS]: page([
        finding({ id: 1, finding_type: 'orphan_file' }),
        finding({ id: 3, finding_type: 'dead_file', job_id: 'dead_file_cleaner' }),
      ]),
    });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Fix Selected'));
    await flush();
    clickPrompt('_orphan-staging');
    await flush();
    clickPrompt('_dead-cancel');
    await flush();

    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/fix'))).toBe(false);
  });

  it('gates a mass orphan DELETE behind the witness-me phrase', async () => {
    const items = Array.from({ length: 25 }, (_, index) =>
      finding({ id: index + 1, details: { mass_orphan: true } }),
    );
    routes({ [FINDINGS]: page(items, 25) });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Fix Selected'));
    await flush();
    clickPrompt('_orphan-delete');
    await flush();

    const input = document.getElementById('witness-me-input') as HTMLInputElement;
    expect(input).not.toBeNull();
    const confirm = document.getElementById('witness-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    fireEvent.change(input, { target: { value: 'nope' } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(input, { target: { value: '  Witness Me  ' } });
    expect(confirm.disabled).toBe(false);

    fireEvent.click(document.getElementById('witness-cancel') as HTMLElement);
    await flush();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/fix'))).toBe(false);
  });

  it('does NOT gate a staging move, however many files', async () => {
    const items = Array.from({ length: 25 }, (_, index) =>
      finding({ id: index + 1, details: { mass_orphan: true } }),
    );
    routes({ [FINDINGS]: page(items, 25) }, { success: true });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Fix Selected'));
    await flush();
    clickPrompt('_orphan-staging');
    await flush();

    expect(document.getElementById('witness-me-input')).toBeNull();
  });

  it('does not gate a delete when no finding carries the mass-orphan flag', async () => {
    const items = Array.from({ length: 25 }, (_, index) => finding({ id: index + 1 }));
    routes({ [FINDINGS]: page(items, 25) }, { success: true });
    await renderList();
    await flush();

    fireEvent.click(document.getElementById('repair-select-all-cb') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Fix Selected'));
    await flush();
    clickPrompt('_orphan-delete');
    await flush();

    expect(document.getElementById('witness-me-input')).toBeNull();
  });
});

// ── The background bulk run ──────────────────────────────────────────────────

describe('the background bulk run', () => {
  /** Start a run the way the surface now offers one: from a group. */
  async function fixGroup(
    findingType: string,
    pending: number,
    info: Record<string, unknown>,
    extra: Record<string, unknown> = {},
  ) {
    routes({
      [GROUPS]: { groups: [group({ finding_type: findingType, pending })] },
      [TYPES]: { types: [typeInfo({ type: findingType, ...info })] },
      ...extra,
    });
    const result = renderSurface();
    await flush();
    fireEvent.click(document.querySelector('.repair-inbox-btn') as HTMLElement);
    // The confirm, the start call and its toast are each a microtask hop
    // apart; one flush only gets as far as the dialog.
    await flush();
    await flush();
    await flush();
    return result;
  }

  it('starts the run and polls its progress', async () => {
    // The status route starts idle: a run already in flight disables every
    // group button (below), so a fixture that reported one would let this
    // test pass without the click ever landing.
    const status: Record<string, unknown> = { running: false };
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'metadata_gap', pending: 200 })] },
      [TYPES]: {
        types: [
          typeInfo({
            type: 'metadata_gap',
            label: 'Metadata Gaps',
            verb: 'Auto-Fill',
            destructive: false,
          }),
        ],
      },
      '/api/repair/findings/bulk-fix-start': { started: true, total: 200 },
      '/api/repair/bulk-fix/status': status,
    });
    renderSurface();
    await flush();

    fireEvent.click(document.querySelector('.repair-inbox-btn') as HTMLElement);
    status.running = true;
    status.done = 12;
    status.total = 200;
    await flush();
    await flush();
    await flush();

    expect(toastSpy).toHaveBeenCalledWith('Fixing 200 metadata gaps in the background…', 'info');
    await waitFor(() =>
      expect(document.getElementById('repair-bulk-count')?.textContent).toContain(
        'Fixing 12 / 200',
      ),
    );
  });

  it('shows the run bar with no group open — a run is surface-level', async () => {
    routes({
      [GROUPS]: { groups: [group()] },
      [TYPES]: { types: [typeInfo()] },
      '/api/repair/bulk-fix/status': { running: true, done: 4, total: 9 },
    });
    renderSurface();
    await waitFor(() =>
      expect(document.getElementById('repair-bulk-count')?.textContent).toContain('Fixing 4 / 9'),
    );
    // …and every group button is held while it runs, so a second run cannot
    // be started on top of the first.
    expect((document.querySelector('.repair-inbox-btn') as HTMLButtonElement).disabled).toBe(true);
  });

  it('picks up a run that is already going', async () => {
    const status: Record<string, unknown> = { running: false };
    routes({
      [GROUPS]: { groups: [group({ finding_type: 'metadata_gap', pending: 200 })] },
      [TYPES]: {
        types: [
          typeInfo({
            type: 'metadata_gap',
            label: 'Metadata Gaps',
            verb: 'Auto-Fill',
            destructive: false,
          }),
        ],
      },
      '/api/repair/findings/bulk-fix-start': { already_running: true },
      '/api/repair/bulk-fix/status': status,
    });
    renderSurface();
    await flush();

    fireEvent.click(document.querySelector('.repair-inbox-btn') as HTMLElement);
    status.running = true;
    status.done = 1;
    status.total = 5;
    await flush();
    await flush();
    await flush();

    expect(toastSpy).toHaveBeenCalledWith(
      'A bulk fix is already running — showing its progress',
      'info',
    );
  });

  it('reports a start failure', async () => {
    await fixGroup(
      'metadata_gap',
      200,
      { label: 'Metadata Gaps', verb: 'Auto-Fill', destructive: false },
      { '/api/repair/findings/bulk-fix-start': { started: false, error: 'worker busy' } },
    );
    expect(toastSpy).toHaveBeenCalledWith('worker busy', 'error');
  });

  it('stops a run through the stop endpoint', async () => {
    await fixGroup(
      'metadata_gap',
      200,
      { label: 'Metadata Gaps', verb: 'Auto-Fill', destructive: false },
      {
        '/api/repair/findings/bulk-fix-start': { started: true, total: 200 },
        '/api/repair/bulk-fix/status': { running: true, done: 3, total: 200 },
      },
    );
    await waitFor(() => expect(screen.getByText('Stop')).toBeTruthy());

    fireEvent.click(screen.getByText('Stop'));
    await flush();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/bulk-fix/stop'))).toBe(true);
    expect(toastSpy).toHaveBeenCalledWith('Stopping after the current fix...', 'info');
  });

  it('re-checks for an outside bulk run on every refresh', async () => {
    // `_checkBulkFixResume` sits at the top of the counts load, so a run
    // started in another tab is picked up by the next refresh — not only by a
    // re-entry into the page.
    routes({
      [FINDINGS]: page([finding({ id: 8 })]),
      '/api/repair/bulk-fix/status': { running: false },
    });
    await renderList();
    await flush();
    const before = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes('/bulk-fix/status'),
    ).length;

    fireEvent.click(document.querySelector('.repair-finding-btn.dismiss') as HTMLElement);
    await flush();

    const after = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes('/bulk-fix/status'),
    ).length;
    expect(after).toBeGreaterThan(before);
  });

  it('uses the ordinary confirm for a smaller orphan delete', async () => {
    await fixGroup('orphan_file', 30, {});
    clickPrompt('_orphan-delete');
    await flush();
    expect(document.getElementById('witness-me-input')).toBeNull();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Orphan Files', destructive: true }),
    );
  });

  it('confirms a staging move without marking it destructive', async () => {
    await fixGroup('orphan_file', 30, {});
    clickPrompt('_orphan-staging');
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Move to Staging', destructive: false }),
    );
  });

  it('sends discography "Just Clear" through the clear endpoint, never bulk-fix', async () => {
    await fixGroup(
      'missing_discography_track',
      40,
      { label: 'Missing Discography', verb: 'Add to Wishlist', destructive: false },
      { '/api/repair/findings/clear': { success: true, deleted: 40 } },
    );
    clickPrompt('_dbf-dismiss');
    await flush();

    // finding_type scopes it to THIS group. A job that emits several finding
    // types would otherwise lose all of its pending rows when you cleared one
    // group — the same missing-filter bug as #1142, one level down.
    expect(bodyOf('/findings/clear')).toEqual({
      job_id: 'orphan_file_detector',
      status: 'pending',
      finding_type: 'missing_discography_track',
    });
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('bulk-fix-start'))).toBe(false);
    expect(toastSpy).toHaveBeenCalledWith('Cleared 40 findings', 'success');
  });

  it('routes the quality-upgrade "Ignore" choice to a group dismiss', async () => {
    await fixGroup(
      'quality_upgrade',
      12,
      { label: 'Quality Upgrades', verb: 'Upgrade' },
      { '/api/repair/findings/bulk': { success: true, updated: 12 } },
    );
    clickPrompt('_qual-ignore');
    await flush();

    expect(bodyOf('/api/repair/findings/bulk')).toEqual({
      finding_type: 'quality_upgrade',
      action: 'dismiss',
    });
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('bulk-fix-start'))).toBe(false);
  });
});

// ── Clear findings ───────────────────────────────────────────────────────────

describe('clear findings', () => {
  it('names the current filter scope in the confirm and sends it', async () => {
    routes({
      [FINDINGS]: page([finding()]),
      '/api/repair/findings/clear': { success: true, deleted: 6 },
    });
    await renderList();
    await flush();

    fireEvent.change(document.getElementById('repair-findings-job-filter') as HTMLElement, {
      target: { value: 'dead_file_cleaner' },
    });
    await flush();
    fireEvent.click(screen.getByText('Clear Findings'));
    await flush();

    // `renderList` types 'a' into the search box, so this test has always run
    // with a search active — and it used to assert that Clear sent only the
    // job and status. That WAS #1142, pinned as a passing test: the prompt
    // described a wider delete than the user's filters, and the request
    // performed one. Both now carry the search term.
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        message:
          'Delete all findings for Dead File Cleaner (pending), matching "a"? This cannot be undone.',
      }),
    );
    expect(bodyOf('/findings/clear')).toEqual({
      job_id: 'dead_file_cleaner',
      status: 'pending',
      q: 'a',
    });
  });

  it('says "all jobs" when no job filter is set', async () => {
    routes({ [FINDINGS]: page([finding()]) });
    await renderList();
    await flush();
    fireEvent.click(screen.getByText('Clear Findings'));
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('for all jobs (pending)') }),
    );
  });

  it('sends nothing when the confirm is declined', async () => {
    confirmSpy.mockResolvedValue(false);
    routes({ [FINDINGS]: page([finding()]) });
    await renderList();
    await flush();
    fireEvent.click(screen.getByText('Clear Findings'));
    await flush();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/findings/clear'))).toBe(false);
  });
});

// ── Pagination ───────────────────────────────────────────────────────────────

describe('pagination', () => {
  it('renders no pagination for a single page', async () => {
    routes({ [FINDINGS]: page([finding()], 10) });
    await renderList();
    await flush();
    expect(document.querySelectorAll('.repair-page-btn')).toHaveLength(0);
  });

  it('highlights the page the SERVER echoed, not the one we asked for', async () => {
    // The vanilla paginates on `data.page`. A backend that clamps an
    // out-of-range request must move the highlight with it.
    routes({ [FINDINGS]: { items: [finding()], total: 300, page: 4 } });
    await renderList();
    await flush();
    const pagination = document.getElementById('repair-findings-pagination') as HTMLElement;
    expect(pagination.querySelector('.repair-page-btn.active')?.textContent).toBe('5');
  });

  it('renders the window, the total, and moves pages', async () => {
    routes({ [FINDINGS]: page([finding()], 300) });
    await renderList();
    await flush();

    const pagination = document.getElementById('repair-findings-pagination') as HTMLElement;
    expect(within(pagination).getByText('300 total')).toBeTruthy();
    expect(pagination.querySelector('.repair-page-btn.active')?.textContent).toBe('1');

    fireEvent.click(within(pagination).getByText('3'));
    await flush();
    const url = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .findLast((u) => u.includes('?'));
    expect(url).toContain('page=2');
  });
});

// ── The detail renderer ──────────────────────────────────────────────────────

/** Render one finding and open its detail panel. */
async function openDetail(over: Partial<RepairFinding>) {
  routes({ [FINDINGS]: page([finding({ id: 1, ...over })]) });
  await renderList();
  await flush();
  fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);
  return document.querySelector('.repair-finding-detail-inner') as HTMLElement;
}

function gridPairs(root: HTMLElement): Array<[string, string]> {
  const keys = [...root.querySelectorAll('.repair-detail-key')];
  const values = [...root.querySelectorAll('.repair-detail-val')];
  return keys.map((key, index) => [key.textContent || '', values[index]?.textContent || '']);
}

describe('the detail renderer', () => {
  it('genre_cleanup spells out what stays and what goes', async () => {
    const detail = await openDetail({
      finding_type: 'genre_cleanup',
      details: {
        kept_genres: ['rock'],
        removed_genres: ['seen live', 'favourites'],
        entity: 'artist',
      },
    });
    expect(gridPairs(detail)).toEqual([
      ['Kept', 'rock'],
      ['Removed', 'seen live, favourites'],
      ['Applies to', 'Artist genres'],
    ]);
  });

  it('genre_cleanup says so when the whitelist would strip everything', async () => {
    const detail = await openDetail({
      finding_type: 'genre_cleanup',
      details: { kept_genres: [], removed_genres: ['x'] },
    });
    expect(gridPairs(detail)[0][1]).toBe('— none (all genres are off your whitelist)');
  });

  it('genre_enrichment shows the proposal, review items, and provenance', async () => {
    const detail = await openDetail({
      finding_type: 'genre_enrichment',
      details: {
        original_genres: ['Rock'],
        proposed_genres: ['Rock', 'Alternative Rock'],
        added_genres: ['Alternative Rock'],
        ambiguous_genres: [
          { raw: 'alt rock', candidates: ['Alternative Rock', 'Indie Rock'], score: 0.82 },
        ],
        rejected_genres: ['unrelated tag'],
        omitted_due_to_cap: ['Post-Rock'],
        sources: { 'Alternative Rock': ['spotify', 'discogs'] },
        cache_stats: { metadata_cache_hits: 2, live_calls: 0 },
      },
    });

    expect(gridPairs(detail)).toEqual([
      ['Current genres', 'Rock'],
      ['Proposed genres', 'Rock, Alternative Rock'],
      ['Added', 'Alternative Rock'],
      ['Omitted at cap', 'Post-Rock'],
      ['Rejected', 'unrelated tag'],
      ['Ambiguous', 'alt rock: Alternative Rock, Indie Rock (82%)'],
      ['Sources', 'Alternative Rock: spotify, discogs'],
      ['Cache / external calls', 'metadata 2, live 0'],
    ]);
  });

  it('comma_artist_split shows the resulting tag and clickable library chips', async () => {
    const detail = await openDetail({
      finding_type: 'comma_artist_split',
      details: {
        combined_name: 'A, B',
        new_display_artist: 'A; B',
        track_count: 3,
        tracks: [{ title: 'One', album: 'LP' }],
        parts_resolution: [
          { name: 'A', in_library: true, library_artist_id: 'nav-9' },
          { name: 'B' },
        ],
      },
    });

    expect(gridPairs(detail)).toContainEqual(['New artist tag', 'A; B']);
    const chip = within(detail).getByTitle("Open A's page");
    fireEvent.click(chip);
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith('nav-9', 'A');
    expect(detail.textContent).toContain('…and 2 more track(s)');
  });

  it('orphan_file uppercases the format and formats the size', async () => {
    const detail = await openDetail({
      finding_type: 'orphan_file',
      details: { folder: '/music/a', format: 'flac', file_size: 2_097_152 },
    });
    expect(gridPairs(detail)).toEqual([
      ['Folder', '/music/a'],
      ['Format', 'FLAC'],
      ['File Size', '2.0 MB'],
      ['Full Path', '/music/a/b.flac'],
    ]);
  });

  it('the play button hands the track to the shell bridge', async () => {
    const detail = await openDetail({
      finding_type: 'dead_file',
      details: { title: 'Song', artist: 'Band', album: 'LP' },
    });
    fireEvent.click(within(detail).getByText(/Play/));
    expect(playSpy).toHaveBeenCalledWith(
      expect.objectContaining({ file_path: '/music/a/b.flac', title: 'Song' }),
      'LP',
      'Band',
    );
  });

  it('acoustid_mismatch draws three score bars with their bands', async () => {
    const detail = await openDetail({
      finding_type: 'acoustid_mismatch',
      details: { fingerprint_score: 0.95, title_similarity: 0.6, artist_similarity: 0.1 },
    });
    const bands = [...detail.querySelectorAll('.repair-score-bar-fill')].map(
      (node) => node.className,
    );
    expect(bands[0]).toContain('good');
    expect(bands[1]).toContain('warn');
    expect(bands[2]).toContain('bad');
    expect(gridPairs(detail)).toContainEqual(['Expected Title', '-']);
  });

  it('fake_lossless draws the spectrum bar, and omits it without both numbers', async () => {
    const withBar = await openDetail({
      finding_type: 'fake_lossless',
      details: { detected_cutoff_khz: 16, expected_min_khz: 20, format: 'flac' },
    });
    expect(withBar.querySelector('.repair-spectrum-bar')).not.toBeNull();
    expect(withBar.textContent).toContain('16 kHz detected');

    cleanup();
    const without = await openDetail({
      finding_type: 'fake_lossless',
      details: { format: 'flac' },
    });
    expect(without.querySelector('.repair-spectrum-bar')).toBeNull();
  });

  it('duplicate_tracks marks the lossless copy KEEP even with no bitrate', async () => {
    const detail = await openDetail({
      finding_type: 'duplicate_tracks',
      details: {
        tracks: [
          { id: 1, title: 'Song', artist: 'Band', file_path: '/a/x.mp3', bitrate: 320 },
          { id: 2, title: 'Song', artist: 'Band', file_path: '/a/x.flac' },
        ],
      },
    });
    const items = [...detail.querySelectorAll('.repair-detail-subitem')];
    expect(items[0].textContent).toContain('REMOVE');
    expect(items[1].textContent).toContain('KEEP');
    expect(items[1].className).toContain('best');
  });

  it('clicking a duplicate confirms, then posts that track id as the fix action', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'duplicate_tracks',
          details: {
            tracks: [
              { id: 1, track_id: 'trk-1', title: 'A', artist: 'B', file_path: '/a/x.mp3' },
              { id: 2, track_id: 'trk-2', title: 'A', artist: 'B', file_path: '/a/x.flac' },
            ],
          },
        }),
      ]),
      '/api/repair/findings/1/fix': { success: true, message: 'kept' },
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(document.querySelectorAll('.repair-detail-subitem')[0]);
    await flush();

    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Keep This Version', destructive: true }),
    );
    expect(bodyOf('/1/fix')).toEqual({ fix_action: 'trk-1' });
  });

  it('each duplicate copy plays ITS OWN file, verbatim', async () => {
    // #1214. The whole point is A/B-ing two copies, so the two buttons must not
    // hand over the same file. exact_path is what guarantees it: without it
    // playLibraryTrack re-resolves title+artist through resolve-track, which is
    // LIMIT 1, and two copies of one song would both play whichever row it hit.
    const detail = await openDetail({
      finding_type: 'duplicate_tracks',
      details: {
        tracks: [
          {
            id: 1,
            title: 'Song',
            artist: 'Band',
            album: 'LP',
            file_path: '/a/x.mp3',
            bitrate: 320,
          },
          { id: 2, title: 'Song', artist: 'Band', album: 'LP', file_path: '/a/x.flac' },
        ],
      },
    });
    const buttons = [...detail.querySelectorAll('.repair-subitem-play')];
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(playSpy).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ file_path: '/a/x.mp3', id: 1, exact_path: true }),
      'LP',
      'Band',
    );
    expect(playSpy).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ file_path: '/a/x.flac', id: 2, exact_path: true }),
      'LP',
      'Band',
    );
  });

  it('auditioning a copy does not choose it', async () => {
    // The row means "keep this version" and the fix is destructive, so the play
    // button must not bubble into it.
    const detail = await openDetail({
      finding_type: 'duplicate_tracks',
      details: {
        tracks: [
          { id: 1, track_id: 'trk-1', title: 'Song', artist: 'Band', file_path: '/a/x.mp3' },
          { id: 2, track_id: 'trk-2', title: 'Song', artist: 'Band', file_path: '/a/x.flac' },
        ],
      },
    });
    fireEvent.click(detail.querySelectorAll('.repair-subitem-play')[0]);
    await flush();
    expect(playSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('a copy with no file path gets no play button', async () => {
    const detail = await openDetail({
      finding_type: 'duplicate_tracks',
      details: {
        tracks: [
          { id: 1, title: 'Song', artist: 'Band', file_path: '/a/x.mp3' },
          { id: 2, title: 'Song', artist: 'Band' },
        ],
      },
    });
    expect(detail.querySelectorAll('.repair-subitem-play')).toHaveLength(1);
    const rows = [...detail.querySelectorAll('.repair-detail-subitem')];
    expect(rows[1].className).not.toContain('playable');
  });

  it('duplicate_tracks with no track list falls back to the count', async () => {
    const detail = await openDetail({
      finding_type: 'duplicate_tracks',
      details: { count: 4 },
    });
    expect(gridPairs(detail)).toEqual([['Count', '4']]);
  });

  it('incomplete_album draws the completion bar and lists the missing tracks', async () => {
    const detail = await openDetail({
      finding_type: 'incomplete_album',
      details: {
        artist: 'Band',
        album_title: 'LP',
        actual_tracks: 9,
        expected_tracks: 12,
        primary_source: 'tidal',
        primary_album_id: 'tid-1',
        spotify_album_id: 'spot-1',
        missing_tracks: [{ track_number: 4, name: 'Gone', duration_ms: 210_000 }],
      },
    });
    expect(gridPairs(detail)).toContainEqual(['Tidal ID', 'tid-1']);
    expect(gridPairs(detail)).toContainEqual(['Spotify ID', 'spot-1']);
    expect(detail.querySelector('.repair-completion-label')?.textContent).toBe(
      '9 of 12 tracks (75%)',
    );
    expect(detail.textContent).toContain('#4 Gone');
    expect(detail.textContent).toContain('Duration: 210s');
  });

  it('missing_cover_art offers a separate apply per image', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'missing_cover_art',
          details: {
            artist: 'Band',
            found_artwork_url: 'http://x/album.jpg',
            found_artist_url: 'http://x/artist.jpg',
            artist_thumb_url: 'http://x/current.jpg',
          },
        }),
      ]),
      '/api/repair/findings/1/fix': { success: true },
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(screen.getByText('Use for artist'));
    await flush();
    expect(bodyOf('/1/fix')).toEqual({ fix_action: 'artist' });
    // The current artist image is context only — it gets no apply button.
    expect(screen.getAllByRole('button', { name: /Use for/ })).toHaveLength(2);
  });

  it('library_retag shows an old → new diff with ∅ for a blank tag', async () => {
    const detail = await openDetail({
      finding_type: 'library_retag',
      details: {
        source: 'spotify',
        tracks: [
          {
            title: 'Song',
            file_path: '/a/01 Song.flac',
            changes: { album_artist: { old: '', new: 'Band' } },
          },
        ],
      },
    });
    expect(detail.textContent).toContain('Source: spotify');
    expect(gridPairs(detail)).toEqual([['album artist', '∅   →   Band', 'highlight'].slice(0, 2)]);
    expect(detail.textContent).toContain('01 Song.flac');
  });

  it('library_retag says cover-only when nothing else would change', async () => {
    const detail = await openDetail({
      finding_type: 'library_retag',
      details: { cover_action: 'refresh', tracks: [] },
    });
    expect(detail.textContent).toContain('Tags already correct');
  });

  it('track_number_mismatch keeps track 0 visible', async () => {
    const detail = await openDetail({
      finding_type: 'track_number_mismatch',
      details: { current_track_num: 0, correct_track_num: 1, changes: ['track 0 → 1'] },
    });
    expect(gridPairs(detail)).toContainEqual(['Current Track #', '0']);
    expect(detail.textContent).toContain('track 0 → 1');
  });

  it('short_preview_track keeps a 0s file length visible', async () => {
    const detail = await openDetail({
      finding_type: 'short_preview_track',
      details: { file_duration_s: 0, expected_duration_s: 214 },
    });
    expect(gridPairs(detail)).toContainEqual(['File Length', '0s']);
    expect(gridPairs(detail)).toContainEqual(['Real Length', '214s']);
  });

  it('expired_download shows only the filename, not the whole path', async () => {
    const detail = await openDetail({
      finding_type: 'expired_download',
      details: {
        title: 'Song',
        origin: 'playlist',
        origin_context: 'Chill',
        file_path: '/a/b/c.mp3',
      },
    });
    expect(gridPairs(detail)).toContainEqual(['Source', 'playlist — Chill']);
    expect(gridPairs(detail)).toContainEqual(['File', 'c.mp3']);
  });

  it('metadata_gap lists each found field as its own success row', async () => {
    const detail = await openDetail({
      finding_type: 'metadata_gap',
      details: { artist: 'Band', found_fields: { genre: 'rock', year: 1994 } },
    });
    expect(gridPairs(detail)).toContainEqual(['Found: genre', 'rock']);
    expect(gridPairs(detail)).toContainEqual(['Found: year', '1994']);
  });

  it('an unknown type dumps its scalar keys and skips objects and thumb urls', async () => {
    const detail = await openDetail({
      finding_type: 'something_new',
      details: { odd_key: 'value', nested: { a: 1 }, album_thumb_url: 'http://x' },
    });
    expect(gridPairs(detail)).toEqual([
      ['Odd Key', 'value'],
      ['File', '/music/a/b.flac'],
    ]);
  });

  it('an unknown type with nothing to show says so', async () => {
    const detail = await openDetail({
      finding_type: 'something_new',
      file_path: null,
      details: {},
    });
    expect(detail.textContent).toContain('No additional details available');
  });

  it('the artist media card resolves an id-less finding by exact name', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'metadata_gap',
          details: { artist: 'Low', artist_thumb_url: 'http://x/a.jpg' },
        }),
      ]),
      '/api/library/artists': {
        artists: [
          { id: 3, name: 'Below' },
          { id: 4, name: 'low' },
        ],
      },
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(screen.getByTitle("Open Low's page"));
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith(4, 'low');
  });

  it('says so rather than guessing when there is no exact name match', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'metadata_gap',
          details: { artist: 'Low', artist_thumb_url: 'http://x/a.jpg' },
        }),
      ]),
      '/api/library/artists': { artists: [{ id: 3, name: 'Below' }] },
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(screen.getByTitle("Open Low's page"));
    await flush();
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(toastSpy).toHaveBeenCalledWith(`"Low" isn't in your library`, 'info');
  });

  it('navigates straight to a stored numeric artist id without a lookup', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'metadata_gap',
          details: { artist: 'Band', artist_thumb_url: 'http://x/a.jpg', artist_id: 77 },
        }),
      ]),
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(screen.getByTitle("Open Band's page"));
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith(77, 'Band');
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/library/artists'))).toBe(
      false,
    );
  });

  it('keeps an alphanumeric artist id as a string rather than coercing it to NaN', async () => {
    routes({
      [FINDINGS]: page([
        finding({
          id: 1,
          finding_type: 'metadata_gap',
          details: { artist: 'Band', artist_thumb_url: 'http://x/a.jpg', artist_id: 'nav-abc' },
        }),
      ]),
    });
    await renderList();
    await flush();
    fireEvent.click(document.querySelector('.repair-finding-main') as HTMLElement);

    fireEvent.click(screen.getByTitle("Open Band's page"));
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith('nav-abc', 'Band');
  });

  it('an artist image with no name is not a link', async () => {
    const detail = await openDetail({
      finding_type: 'metadata_gap',
      details: { artist_thumb_url: 'http://x/a.jpg' },
    });
    expect(detail.querySelector('.repair-finding-media-card--link')).toBeNull();
  });
});
