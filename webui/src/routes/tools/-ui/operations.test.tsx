/**
 * Operations — the job containers and the tiles inside them.
 *
 * The contracts that outlive the redesign and must not be "tidied": the
 * `data-job-id` the socket dispatch keys on, and the card-class / dot-class
 * asymmetry (an idle enabled job gets dot 'enabled' and NO state class on the
 * card — they are separate ternaries and collapsing them is a silent
 * restyle).
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RepairJob, RepairJobRun } from '../-tools.types';

import { Operations } from './operations';

const fetchMock = vi.fn();
const toastSpy = vi.fn();

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

const job = (over: Partial<RepairJob> = {}): RepairJob =>
  ({
    job_id: 'orphan_file_detector',
    display_name: 'Orphan File Detector',
    description: 'Finds files with no database row',
    category: 'Files & storage',
    enabled: true,
    is_running: false,
    interval_hours: 24,
    ...over,
  }) as RepairJob;

function renderOps(jobs: RepairJob[] | null, runs: RepairJobRun[] = []) {
  const onChanged = vi.fn();
  const onHelp = vi.fn();
  const onShowFindings = vi.fn();
  const result = render(
    <Operations
      jobs={jobs}
      error={false}
      progress={{}}
      runs={runs}
      onChanged={onChanged}
      onHelp={onHelp}
      onShowFindings={onShowFindings}
    />,
  );
  return { ...result, onChanged, onHelp, onShowFindings };
}

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  routes({});
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, { showToast: toastSpy });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the containers', () => {
  it('groups jobs into their served families, in the house order', () => {
    const { container } = renderOps([
      job({ job_id: 'genre_cleanup', display_name: 'Genre Cleanup', category: 'Tags & metadata' }),
      job({
        job_id: 'genre_enrichment',
        display_name: 'Genre Enrichment',
        category: 'Tags & metadata',
      }),
      job({ job_id: 'orphan_file_detector', category: 'Files & storage' }),
      job({ job_id: 'cache_evictor', display_name: 'Cache Maintenance', category: 'System' }),
    ]);
    const families = [...container.querySelectorAll('.repair-family')].map((node) =>
      node.getAttribute('data-category'),
    );
    expect(families).toEqual(['Files & storage', 'Tags & metadata', 'System']);
    expect(
      container
        .querySelector('[data-category="Tags & metadata"]')
        ?.querySelector('[data-job-id="genre_enrichment"]'),
    ).not.toBeNull();
  });

  it('files a job with no category under Other, at the end', () => {
    // A new job must be visibly ungrouped, not silently missing.
    const { container } = renderOps([
      job({ job_id: 'brand_new', display_name: 'Brand New', category: null }),
      job({ job_id: 'orphan_file_detector' }),
    ]);
    expect(
      [...container.querySelectorAll('.repair-family')].map((n) => n.getAttribute('data-category')),
    ).toEqual(['Files & storage', 'Other']);
  });

  it('sorts jobs alphabetically inside a family', () => {
    const { container } = renderOps([
      job({ job_id: 'z', display_name: 'Zeta' }),
      job({ job_id: 'a', display_name: 'Alpha' }),
    ]);
    expect([...container.querySelectorAll('.repair-tile-name')].map((n) => n.textContent)).toEqual([
      'Alpha',
      'Zeta',
    ]);
  });

  it('summarises itself so a collapsed container still says something', () => {
    const { container } = renderOps([
      job({ job_id: 'a', display_name: 'A', is_running: true }),
      job({ job_id: 'b', display_name: 'B', enabled: false }),
      job({ job_id: 'c', display_name: 'C', enabled: true, next_run: null, last_run: null }),
    ]);
    expect(container.querySelector('.repair-family-summary')?.textContent).toBe(
      '3 jobs · 1 running · 1 due · 1 off',
    );
  });

  it('collapses and expands, and starts open', () => {
    const { container } = renderOps([job()]);
    expect(container.querySelectorAll('.repair-tile')).toHaveLength(1);
    fireEvent.click(container.querySelector('.repair-family-head') as HTMLElement);
    expect(container.querySelectorAll('.repair-tile')).toHaveLength(0);
    fireEvent.click(container.querySelector('.repair-family-head') as HTMLElement);
    expect(container.querySelectorAll('.repair-tile')).toHaveLength(1);
  });

  it('rolls the family&rsquo;s open findings up to the header', () => {
    const { container } = renderOps([
      job({ job_id: 'a', display_name: 'A', pending_findings_count: 12 }),
      job({ job_id: 'b', display_name: 'B', pending_findings_count: 30 }),
    ]);
    expect(container.querySelector('.repair-family-pending')?.textContent).toBe('42 open');
  });

  it('loads, errors and empties distinctly', () => {
    const { container } = renderOps(null);
    expect(container.querySelector('.repair-loading')).not.toBeNull();
    cleanup();

    render(
      <Operations
        jobs={null}
        error
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onHelp={vi.fn()}
        onShowFindings={vi.fn()}
      />,
    );
    expect(screen.getByText('Error loading jobs')).toBeTruthy();
    cleanup();

    renderOps([]);
    expect(screen.getByText('No Maintenance Jobs')).toBeTruthy();
  });
});

describe('the tile', () => {
  it('keeps the data-job-id the socket handler needs', () => {
    const { container } = renderOps([job()]);
    expect(
      container.querySelector('.repair-job-card[data-job-id="orphan_file_detector"]'),
    ).not.toBeNull();
  });

  it('keeps the card-class / dot-class asymmetry', () => {
    // Idle + enabled: dot 'enabled', and NO state class on the card.
    const { container } = renderOps([job({ enabled: true, is_running: false })]);
    const card = container.querySelector('.repair-job-card') as HTMLElement;
    expect(card.className).not.toContain('running');
    expect(card.className).not.toContain('disabled');
    expect(container.querySelector('.repair-job-status')?.className).toBe(
      'repair-job-status enabled',
    );
  });

  it('marks a running job on both the card and the dot, and swaps Run for Stop', () => {
    const { container } = renderOps([job({ is_running: true })]);
    expect((container.querySelector('.repair-job-card') as HTMLElement).className).toContain(
      'running',
    );
    expect(container.querySelector('.repair-job-status')?.className).toContain('running');
    expect(container.querySelector('.repair-stop-btn')).not.toBeNull();
    expect(container.querySelector('.repair-run-btn')).toBeNull();
  });

  it('turns the glow edge into the progress bar while the job scans', () => {
    const { container } = render(
      <Operations
        jobs={[job({ is_running: true })]}
        error={false}
        progress={{ orphan_file_detector: { status: 'running', progress: 42 } }}
        runs={[]}
        onChanged={vi.fn()}
        onHelp={vi.fn()}
        onShowFindings={vi.fn()}
      />,
    );
    const edge = container.querySelector('.repair-tile-edge') as HTMLElement;
    expect(edge.className).toContain('running');
    expect(edge.style.width).toBe('42%');
  });

  it('says Dry Run instead of Auto-fix when the job is in dry-run mode', () => {
    const { container } = renderOps([job({ auto_fix: true, settings: { dry_run: true } })]);
    expect(container.querySelector('.repair-flow-badge.dryrun')?.textContent).toBe('Dry Run');
    cleanup();

    const second = renderOps([job({ auto_fix: true, settings: { dry_run: false } })]);
    expect(second.container.querySelector('.repair-flow-badge.autofix')?.textContent).toBe(
      'Auto-fix',
    );
  });

  it('the findings badge leads to those findings', () => {
    const { container, onShowFindings } = renderOps([job({ pending_findings_count: 372 })]);
    const badge = container.querySelector('.repair-tile-badge') as HTMLElement;
    expect(badge.textContent).toBe('372 open');
    fireEvent.click(badge);
    expect(onShowFindings).toHaveBeenCalledWith('orphan_file_detector');
  });

  it('falls back to the historical count and labels it as such', () => {
    // Pending is 0 but the last scan found things — a different claim.
    const { container } = renderOps([
      job({ pending_findings_count: 0, last_run: { findings_created: 372 } }),
    ]);
    const badge = container.querySelector('.repair-tile-badge') as HTMLElement;
    expect(badge.className).toContain('historical');
    expect(badge.textContent).toBe('372 last scan');
  });

  it('only renders a settings cog when the job HAS settings', () => {
    const { container } = renderOps([job()]);
    expect(container.querySelector('.repair-settings-btn')).toBeNull();
    cleanup();

    const second = renderOps([job({ settings: { dry_run: false } })]);
    expect(second.container.querySelector('.repair-settings-btn')).not.toBeNull();
  });

  it('renders the settings panel below the tile content, not beside the actions', () => {
    const { container } = renderOps([job({ settings: { dry_run: false } })]);
    const card = container.querySelector('.repair-job-card') as HTMLElement;
    const actions = card.querySelector('.repair-job-actions') as HTMLElement;
    const foot = card.querySelector('.repair-tile-foot') as HTMLElement;
    const button = card.querySelector('.repair-settings-btn') as HTMLElement;

    fireEvent.click(button);

    const panel = card.querySelector('.repair-job-settings') as HTMLElement;
    expect(actions.contains(panel)).toBe(false);
    expect(foot.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel.style.display).toBe('');
  });

  it('runs a job', async () => {
    const { container } = renderOps([job()]);
    fireEvent.click(container.querySelector('.repair-run-btn') as HTMLElement);
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('Job started', 'success'));
  });

  it('stops a running job and disables the button while it unwinds', async () => {
    routes({ 'orphan_file_detector/stop': { stopped: true } });
    const { container } = renderOps([job({ is_running: true })]);
    fireEvent.click(container.querySelector('.repair-stop-btn') as HTMLElement);
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('Stopping job…', 'success'));
    expect((container.querySelector('.repair-stop-btn') as HTMLButtonElement).disabled).toBe(true);
  });

  it('reports a stop that found nothing running', async () => {
    routes({ 'orphan_file_detector/stop': { stopped: false } });
    const { container } = renderOps([job({ is_running: true })]);
    fireEvent.click(container.querySelector('.repair-stop-btn') as HTMLElement);
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('Job is not running', 'info'));
  });
});

describe('the schedule on the tile face', () => {
  it('reads the interval in whole units rather than raw hours', () => {
    const { container } = renderOps([job({ interval_hours: 24 })]);
    expect(container.querySelector('.repair-tile-cadence')?.textContent).toBe('daily');
    cleanup();
    expect(
      renderOps([job({ interval_hours: 6 })]).container.querySelector('.repair-tile-cadence')
        ?.textContent,
    ).toBe('every 6 hours');
    cleanup();
    expect(
      renderOps([job({ interval_hours: 168 })]).container.querySelector('.repair-tile-cadence')
        ?.textContent,
    ).toBe('weekly');
  });

  it('is editable in place, and saves as hours', async () => {
    // The whole complaint: this was a bare "Interval (hours)" box behind a
    // gear, which read as "not configurable at all".
    const { container } = renderOps([job({ interval_hours: 24, settings: { dry_run: true } })]);
    fireEvent.click(container.querySelector('.repair-tile-cadence') as HTMLElement);

    fireEvent.change(container.querySelector('.repair-tile-cadence-edit input') as HTMLElement, {
      target: { value: '3' },
    });
    fireEvent.change(container.querySelector('.repair-tile-cadence-edit select') as HTMLElement, {
      target: { value: 'days' },
    });
    fireEvent.click(container.querySelector('.repair-tile-cadence-save') as HTMLElement);

    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/settings'))).toBe(true),
    );
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/settings'));
    // The other settings ride along — this endpoint replaces the whole blob.
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
      interval_hours: 72,
      settings: { dry_run: true },
    });
  });

  it('cancels without saving', () => {
    const { container } = renderOps([job()]);
    fireEvent.click(container.querySelector('.repair-tile-cadence') as HTMLElement);
    fireEvent.click(container.querySelector('.repair-tile-cadence-cancel') as HTMLElement);
    expect(container.querySelector('.repair-tile-cadence')).not.toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/settings'))).toBe(false);
  });

  it('says when the next run is due, without promising a clock time', () => {
    // The worker is a staleness queue, not a cron.
    const soon = new Date(Date.now() + 3 * 3600000).toISOString();
    const { container } = renderOps([job({ next_run: soon })]);
    expect(container.querySelector('.repair-tile-next')?.textContent).toBe('in about 3h');
  });

  it('calls out a job that is badly overdue', () => {
    const late = new Date(Date.now() - 40 * 3600000).toISOString();
    const { container } = renderOps([job({ interval_hours: 24, next_run: late })]);
    const next = container.querySelector('.repair-tile-next') as HTMLElement;
    expect(next.className).toContain('overdue');
    expect(next.textContent).toBe('overdue by 40h');
  });

  it('says a disabled job is off rather than inventing a next run', () => {
    const { container } = renderOps([job({ enabled: false, next_run: null })]);
    expect(container.querySelector('.repair-tile-next')?.textContent).toBe('off');
  });

  it('says a job that has never run has never run', () => {
    const { container } = renderOps([job({ next_run: null, last_run: null })]);
    expect(container.querySelector('.repair-tile-next')?.textContent).toBe('never run yet');
  });
});

describe('the dry-run warning', () => {
  const confirmSpy = vi.fn();

  beforeEach(() => {
    confirmSpy.mockReset();
    Object.assign(window, { showConfirmDialog: confirmSpy });
  });

  afterEach(() => {
    delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  });

  /** Opens the settings drawer and returns the dry_run control. */
  function dryRunControl(container: HTMLElement): HTMLInputElement | HTMLSelectElement {
    fireEvent.click(container.querySelector('.repair-settings-btn') as HTMLElement);
    return container.querySelector('.repair-job-settings [data-key="dry_run"]') as
      | HTMLInputElement
      | HTMLSelectElement;
  }

  function dialogOptions() {
    return confirmSpy.mock.calls[0][0] as {
      title?: string;
      message?: string;
      confirmText?: string;
      cancelText?: string;
      destructive?: boolean;
    };
  }

  it('warns with the strong wording when a file-writing job is taken off dry run', async () => {
    confirmSpy.mockResolvedValue(true);
    const { container } = renderOps([
      job({ settings: { dry_run: true }, writes_library_files: true }),
    ]);
    const box = dryRunControl(container) as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    const options = dialogOptions();
    expect(options.title).toBe('Turn Off Dry Run?');
    expect(options.message).toContain('directly manage your library');
    expect(options.message).toContain('move, retag, or rewrite files');
    expect(options.confirmText).toBe('Turn off dry run');
    expect(options.cancelText).toBe('Keep dry run');
    expect(options.destructive).toBe(true);
    // The flip was allowed through once confirmed.
    await waitFor(() => expect(box.checked).toBe(false));
  });

  it('keeps dry run on when the warning is dismissed', async () => {
    confirmSpy.mockResolvedValue(false);
    const { container } = renderOps([
      job({ settings: { dry_run: true }, writes_library_files: true }),
    ]);
    const box = dryRunControl(container) as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    // The draft never changed, and the box was flipped back.
    expect(box.checked).toBe(true);
  });

  it('uses a lighter note for finding-only jobs', async () => {
    confirmSpy.mockResolvedValue(true);
    const { container } = renderOps([job({ settings: { dry_run: true } })]);
    const box = dryRunControl(container) as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    const options = dialogOptions();
    expect(options.message).toContain('apply its fixes automatically');
    expect(options.message).not.toContain('rewrite files');
  });

  it('does not warn when dry run is turned back on', () => {
    confirmSpy.mockResolvedValue(true);
    const { container } = renderOps([
      job({ settings: { dry_run: false }, writes_library_files: true }),
    ]);
    const box = dryRunControl(container) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(box.checked).toBe(true);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('does not warn for unrelated settings', async () => {
    const { container } = renderOps([
      job({ settings: { dry_run: true, min_score: 0.5 }, writes_library_files: true }),
    ]);
    fireEvent.click(container.querySelector('.repair-settings-btn') as HTMLElement);
    const score = container.querySelector(
      '.repair-job-settings [data-key="min_score"]',
    ) as HTMLInputElement;
    fireEvent.change(score, { target: { value: '0.7' } });
    await waitFor(() => expect(score.value).toBe('0.7'));
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('guards the dropdown form of the setting too', async () => {
    confirmSpy.mockResolvedValue(true);
    const { container } = renderOps([
      job({
        settings: { dry_run: true },
        setting_options: { dry_run: [true, false] },
        writes_library_files: true,
      }),
    ]);
    fireEvent.click(container.querySelector('.repair-settings-btn') as HTMLElement);
    const select = container.querySelector(
      '.repair-job-settings select[data-key="dry_run"]',
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'false' } });
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(dialogOptions().message).toContain('directly manage your library');
  });

  it('reverts the dropdown when its warning is dismissed', async () => {
    confirmSpy.mockResolvedValue(false);
    const { container } = renderOps([
      job({
        settings: { dry_run: true },
        setting_options: { dry_run: [true, false] },
        writes_library_files: true,
      }),
    ]);
    fireEvent.click(container.querySelector('.repair-settings-btn') as HTMLElement);
    const select = container.querySelector(
      '.repair-job-settings select[data-key="dry_run"]',
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'false' } });
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(select.value).toBe('true');
  });

  it('keeps dry run on when the dialog host is unavailable', async () => {
    // Fails closed: no dialog, no flip.
    delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
    const { container } = renderOps([
      job({ settings: { dry_run: true }, writes_library_files: true }),
    ]);
    const box = dryRunControl(container) as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(box.checked).toBe(true));
  });
});
