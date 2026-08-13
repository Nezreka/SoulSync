/**
 * Library Maintenance hero — shell, jobs tab, history tab.
 *
 * The job-card assertions lean on the two contracts the P0 flagged: the
 * `data-job-id` the socket handler still needs, and the card-class / dot-class
 * asymmetry that is easy to "tidy" into a bug.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MaintenanceHero } from './maintenance-hero';

const fetchMock = vi.fn();
const toastSpy = vi.fn();

/**
 * Route by URL, preferring the LONGEST matching key.
 *
 * NB the per-job routes must be keyed by something the list route cannot
 * shadow: `/api/repair/jobs` is a PREFIX of `/api/repair/jobs/<id>/stop` and is
 * the longer of the two against a bare `/stop` key, so the stop call would be
 * handed the job list. That made one test fail and — worse — made its
 * `stopped: false` twin pass for entirely the wrong reason.
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

const job = (over: Record<string, unknown> = {}) => ({
  job_id: 'orphan_file_detector',
  display_name: 'Orphan File Detector',
  description: 'Finds files with no database row',
  enabled: true,
  is_running: false,
  interval_hours: 24,
  ...over,
});

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
  vi.useRealTimers();
});

describe('hero shell', () => {
  it('offers the four section anchors in scroll order', async () => {
    const { container } = render(<MaintenanceHero />);
    await flush();
    const links = [...container.querySelectorAll('.repair-section-link')];
    expect(links.map((link) => link.getAttribute('data-section'))).toEqual([
      'health',
      'findings',
      'operations',
      'history',
    ]);
  });

  it('renders every section at once — the nav jumps, it does not hide', async () => {
    // Tabs made the findings you came for a room you had to know to enter,
    // and nothing on screen told you whether the library was alright at all.
    const { container } = render(<MaintenanceHero />);
    await flush();
    expect(container.querySelector('#repair-section-health')).not.toBeNull();
    expect(container.querySelector('#repair-section-findings')).not.toBeNull();
    expect(container.querySelector('#repair-section-operations')).not.toBeNull();
    expect(container.querySelector('#repair-section-history')).not.toBeNull();
  });

  it('a nav button scrolls its section into view', async () => {
    const scrollSpy = vi.fn();
    const { container } = render(<MaintenanceHero />);
    await flush();
    const section = container.querySelector('#repair-section-history') as HTMLElement;
    section.scrollIntoView = scrollSpy;

    fireEvent.click(container.querySelector('[data-section="history"]') as Element);
    expect(scrollSpy).toHaveBeenCalled();
  });

  it('reflects the master toggle from the status payload', async () => {
    routes({ '/api/repair/status': { enabled: true } });
    const { container } = render(<MaintenanceHero />);
    await waitFor(() =>
      expect((container.querySelector('#repair-master-toggle') as HTMLInputElement).checked).toBe(
        true,
      ),
    );
    expect(container.querySelector('#repair-master-label')?.textContent).toBe('Enabled');
  });

  it('hides the findings badge at zero and shows it otherwise', async () => {
    routes({ '/api/repair/status': { enabled: true, findings_pending: 0 } });
    const { container } = render(<MaintenanceHero />);
    await flush();
    expect(
      (container.querySelector('#repair-findings-tab-badge') as HTMLElement).style.display,
    ).toBe('none');
    cleanup();

    routes({ '/api/repair/status': { enabled: true, findings_pending: 12 } });
    const second = render(<MaintenanceHero />);
    await waitFor(() =>
      expect(second.container.querySelector('#repair-findings-tab-badge')?.textContent).toBe('12'),
    );
    expect(
      (second.container.querySelector('#repair-findings-tab-badge') as HTMLElement).style.display,
    ).toBe('');
  });

  it('toasts when the master toggle fails', async () => {
    fetchMock.mockImplementation((url: string) =>
      url === '/api/repair/toggle'
        ? Promise.resolve({ ok: false, status: 500, json: async () => ({}) } as never)
        : Promise.resolve({ ok: true, status: 200, json: async () => ({}) } as never),
    );
    const { container } = render(<MaintenanceHero />);
    await flush();
    fireEvent.click(container.querySelector('#repair-master-toggle') as Element);
    await flush();
    expect(toastSpy).toHaveBeenCalledWith('Error toggling maintenance worker', 'error');
  });
});

describe('the operations section', () => {
  it('renders the job containers — the tiles have their own suite', async () => {
    routes({ '/api/repair/jobs': { jobs: [job()] } });
    const { container } = render(<MaintenanceHero />);
    await waitFor(() =>
      expect(
        container.querySelector('.repair-job-card[data-job-id="orphan_file_detector"]'),
      ).not.toBeNull(),
    );
  });
});

describe('the history section', () => {
  const run = {
    job_id: 'orphan_file_detector',
    display_name: 'Orphan File Detector',
    status: 'completed',
    duration_seconds: 12.34,
    items_scanned: 1234,
    findings_created: 5,
    auto_fixed: 2,
    started_at: '2026-08-03T09:00:00',
    finished_at: '2026-08-03T09:01:00',
  };

  it('loads on mount — the hero needs the runs for its trend line too', async () => {
    routes({ '/api/repair/history': { runs: [run] } });
    render(<MaintenanceHero />);
    await flush();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/history'))).toBe(true);
  });
});

describe('job help overlay', () => {
  it('opens from the ? button with badges and current settings', async () => {
    routes({
      '/api/repair/jobs': {
        jobs: [
          job({
            auto_fix: true,
            interval_hours: 6,
            help_text: 'What it does.\n\nSettings:\n- one thing\n- another thing',
            settings: { _section_a: 'A', dry_run: false, threshold: 0.5 },
          }),
        ],
      },
    });
    const { container } = render(<MaintenanceHero />);
    await waitFor(() => expect(container.querySelector('.repair-help-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.repair-help-btn') as Element);

    expect(container.querySelector('#repair-help-overlay')).not.toBeNull();
    const badges = [...container.querySelectorAll('.repair-help-badges .repair-flow-badge')].map(
      (b) => b.textContent,
    );
    expect(badges).toEqual(['Auto-fix', 'Every 6h', 'Enabled']);
    // the "Settings:" paragraph becomes a list with the leading "- " stripped
    const items = [...container.querySelectorAll('.repair-help-setting-item')].map(
      (i) => i.textContent,
    );
    expect(items).toEqual(['one thing', 'another thing']);
    // section sentinels are skipped in the summary; booleans read Yes/No
    const keys = [...container.querySelectorAll('.repair-help-setting-key')].map(
      (k) => k.textContent,
    );
    expect(keys).toEqual(['Dry Run', 'Threshold']);
    expect(
      [...container.querySelectorAll('.repair-help-setting-val')].map((v) => v.textContent),
    ).toEqual(['No', '0.5']);
  });

  it('says Scan Only for a job with no auto-fix', async () => {
    routes({ '/api/repair/jobs': { jobs: [job({ auto_fix: false, enabled: false })] } });
    const { container } = render(<MaintenanceHero />);
    await waitFor(() => expect(container.querySelector('.repair-help-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.repair-help-btn') as Element);
    const badges = [...container.querySelectorAll('.repair-help-badges .repair-flow-badge')].map(
      (b) => b.textContent,
    );
    expect(badges).toEqual(['Scan Only', 'Every 24h', 'Disabled']);
  });

  it('closes on the backdrop and the × button', async () => {
    routes({ '/api/repair/jobs': { jobs: [job()] } });
    const { container } = render(<MaintenanceHero />);
    await waitFor(() => expect(container.querySelector('.repair-help-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.repair-help-btn') as Element);
    fireEvent.click(container.querySelector('.repair-help-close') as Element);
    expect(container.querySelector('#repair-help-overlay')).toBeNull();

    fireEvent.click(container.querySelector('.repair-help-btn') as Element);
    fireEvent.click(container.querySelector('#repair-help-overlay') as Element);
    expect(container.querySelector('#repair-help-overlay')).toBeNull();
  });
});

// ── P6: live socket frames ───────────────────────────────────────────────────

describe('live repair frames', () => {
  it('moves the tab badge and the master toggle from a pushed status frame', async () => {
    routes({ '/api/repair/status': { enabled: false, findings_pending: 0 } });
    render(<MaintenanceHero />);
    await flush();

    const badge = document.getElementById('repair-findings-tab-badge') as HTMLElement;
    const toggle = document.getElementById('repair-master-toggle') as HTMLInputElement;
    expect(badge.style.display).toBe('none');
    expect(toggle.checked).toBe(false);

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:repair-status', {
          detail: { enabled: true, findings_pending: 12 },
        }),
      );
    });

    expect(badge.textContent).toBe('12');
    expect(badge.style.display).toBe('');
    expect(toggle.checked).toBe(true);
    expect(document.getElementById('repair-master-label')?.textContent).toBe('Enabled');
  });

  it('hides the badge again when a frame drops the count to zero', async () => {
    routes({ '/api/repair/status': { enabled: true, findings_pending: 5 } });
    render(<MaintenanceHero />);
    await flush();
    expect(
      (document.getElementById('repair-findings-tab-badge') as HTMLElement).style.display,
    ).toBe('');

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:repair-status', { detail: { enabled: true, findings_pending: 0 } }),
      );
    });
    expect(
      (document.getElementById('repair-findings-tab-badge') as HTMLElement).style.display,
    ).toBe('none');
  });

  it('MERGES a partial job-progress frame instead of replacing the map', async () => {
    // The vanilla iterates Object.entries(data) and touches only the jobs named
    // in the frame. Replacing the map would blank every other job's live panel
    // the moment one job reported.
    routes({
      '/api/repair/jobs': {
        jobs: [job(), job({ job_id: 'dead_file_cleaner', display_name: 'Dead File Cleaner' })],
      },
      '/api/repair/progress': {
        orphan_file_detector: { status: 'running', progress: 40, phase: 'Scanning' },
        dead_file_cleaner: { status: 'running', progress: 10, phase: 'Starting' },
      },
    });
    render(<MaintenanceHero />);
    await waitFor(() =>
      expect(document.querySelectorAll('.repair-progress-phase')).toHaveLength(2),
    );

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:repair-progress', {
          detail: { orphan_file_detector: { status: 'running', progress: 90, phase: 'Nearly' } },
        }),
      );
    });

    const cards = document.querySelectorAll('.repair-job-card');
    const orphan = [...cards].find(
      (card) => (card as HTMLElement).dataset.jobId === 'orphan_file_detector',
    ) as HTMLElement;
    const dead = [...cards].find(
      (card) => (card as HTMLElement).dataset.jobId === 'dead_file_cleaner',
    ) as HTMLElement;

    expect(orphan.querySelector('.repair-progress-phase')?.textContent).toBe('Nearly');
    // The untouched job keeps its own frame.
    expect(dead.querySelector('.repair-progress-phase')?.textContent).toBe('Starting');
  });

  it('ignores an empty progress frame', async () => {
    routes({
      '/api/repair/jobs': { jobs: [job()] },
      '/api/repair/progress': {
        orphan_file_detector: { status: 'running', progress: 40, phase: 'Scanning' },
      },
    });
    render(<MaintenanceHero />);
    await waitFor(() => expect(document.querySelector('.repair-progress-phase')).not.toBeNull());

    await act(async () => {
      window.dispatchEvent(new CustomEvent('ss:repair-progress', { detail: {} }));
    });
    expect(document.querySelector('.repair-progress-phase')?.textContent).toBe('Scanning');
  });
});
