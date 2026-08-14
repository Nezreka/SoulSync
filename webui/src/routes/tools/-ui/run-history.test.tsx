/**
 * The Recent Runs card.
 *
 * The assertions that matter are the ones the old list failed: a failed run
 * has to show WHY (the reason has been recorded since phase 1 and nothing
 * displayed it), and a run has to lead somewhere — a row that says "5
 * findings" with no way to reach them is a dead end.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RepairJobRun } from '../-tools.types';

import { RunHistory } from './run-history';

/**
 * 9am TODAY, not a fixed calendar date.
 *
 * `dayLabel` compares a run's day key against the real clock, so it only says
 * "Today" for a run that actually happened today. This fixture was pinned to
 * 2026-08-12, which meant the grouping test below passed on exactly one day
 * and failed every day after — it broke on its own with no code change.
 */
function todayAt9am(): Date {
  const d = new Date();
  d.setHours(9, 0, 0, 0);
  return d;
}

const run = (over: Partial<RepairJobRun> = {}): RepairJobRun => ({
  id: 1,
  job_id: 'orphan_file_detector',
  display_name: 'Orphan File Detector',
  started_at: todayAt9am().toISOString(),
  finished_at: new Date(todayAt9am().getTime() + 12_000).toISOString(),
  duration_seconds: 12.3,
  items_scanned: 1234,
  findings_created: 0,
  auto_fixed: 0,
  errors: 0,
  status: 'completed',
  ...over,
});

function renderHistory(runs: RepairJobRun[] | null, error = false) {
  const onShowFindings = vi.fn();
  const onRefresh = vi.fn();
  const result = render(
    <RunHistory runs={runs} error={error} onShowFindings={onShowFindings} onRefresh={onRefresh} />,
  );
  return { ...result, onShowFindings, onRefresh };
}

/** A job chip by its label — run rows carry the same job name. */
function chip(container: HTMLElement, label: string): HTMLElement {
  const found = [...container.querySelectorAll('.repair-runs-chip')].find((node) =>
    node.textContent?.startsWith(label),
  );
  expect(found, `expected a chip for ${label}`).toBeTruthy();
  return found as HTMLElement;
}

afterEach(cleanup);

describe('states', () => {
  it('loads, errors and empties distinctly', () => {
    const { container } = renderHistory(null);
    expect(container.querySelector('.repair-loading')).not.toBeNull();
    cleanup();

    renderHistory(null, true);
    expect(screen.getByText('Error loading history')).toBeTruthy();
    cleanup();

    renderHistory([]);
    expect(screen.getByText('No runs yet')).toBeTruthy();
  });

  it('distinguishes "nothing matches the filter" from "nothing has ever run"', () => {
    renderHistory([run({ status: 'completed' })]);
    // No failures exist, so the failures toggle is not offered at all.
    expect(screen.queryByText('Failures only')).toBeNull();
  });
});

describe('rows', () => {
  it('marks a scan that found nothing differently from one that found something', () => {
    const { container } = renderHistory([
      run({ id: 1 }),
      run({ id: 2, findings_created: 5, job_id: 'b', display_name: 'Beta' }),
    ]);
    const rows = [...container.querySelectorAll('.repair-run')];
    expect(rows[0].className).toContain('quiet');
    expect(rows[1].className).toContain('success');
  });

  it('groups under day headings', () => {
    renderHistory([run({ id: 1 }), run({ id: 2, started_at: null })]);
    expect(screen.getByText('Today')).toBeTruthy();
    expect(screen.getByText('Undated')).toBeTruthy();
  });

  it('shows the recorded reason when a run failed', () => {
    renderHistory([
      run({ status: 'failed', errors: 1, error_text: 'AcoustID API key missing' }),
    ]);
    fireEvent.click(document.querySelector('.repair-run-head') as HTMLElement);
    expect(screen.getByText('AcoustID API key missing')).toBeTruthy();
  });

  it('says so when an older failed run recorded no reason', () => {
    // Runs from before phase 1 have nothing stored, and an empty panel reads
    // as broken.
    renderHistory([run({ status: 'failed', errors: 1, error_text: null })]);
    fireEvent.click(document.querySelector('.repair-run-head') as HTMLElement);
    expect(screen.getByText(/No reason was recorded/)).toBeTruthy();
  });

  it('does not show an error panel for a run that succeeded', () => {
    renderHistory([run({ findings_created: 2 })]);
    fireEvent.click(document.querySelector('.repair-run-head') as HTMLElement);
    expect(document.querySelector('.repair-run-error')).toBeNull();
  });

  it('opens one row at a time', () => {
    const { container } = renderHistory([
      run({ id: 1 }),
      run({ id: 2, job_id: 'b', display_name: 'Beta' }),
    ]);
    const heads = [...container.querySelectorAll('.repair-run-head')];
    fireEvent.click(heads[0]);
    fireEvent.click(heads[1]);
    expect(container.querySelectorAll('.repair-run.open')).toHaveLength(1);
  });

  it('leads somewhere — the row links into that job&rsquo;s findings', () => {
    const { onShowFindings } = renderHistory([run({ findings_created: 5 })]);
    fireEvent.click(document.querySelector('.repair-run-head') as HTMLElement);
    fireEvent.click(screen.getByText(/See this job/));
    expect(onShowFindings).toHaveBeenCalledWith('orphan_file_detector', 'Orphan File Detector');
  });
});

describe('filters', () => {
  const runs = [
    run({ id: 1, job_id: 'a', display_name: 'Alpha' }),
    run({ id: 2, job_id: 'b', display_name: 'Beta', status: 'failed', errors: 1 }),
    run({ id: 3, job_id: 'a', display_name: 'Alpha' }),
  ];

  it('offers a chip per job, busiest first, with counts', () => {
    const { container } = renderHistory(runs);
    const chips = [...container.querySelectorAll('.repair-runs-chip')].map((c) => c.textContent);
    expect(chips).toEqual(['All jobs3', 'Alpha2', 'Beta1']);
  });

  it('flags the chip of a job that has a failure', () => {
    const { container } = renderHistory(runs);
    const beta = [...container.querySelectorAll('.repair-runs-chip')].find((c) =>
      c.textContent?.startsWith('Beta'),
    );
    expect(beta?.className).toContain('has-failure');
  });

  it('scopes to one job, and clicking the same chip clears it', () => {
    const { container } = renderHistory(runs);
    // The run rows carry the name too, so scope the lookup to the chips.
    const alpha = chip(container, 'Alpha');
    fireEvent.click(alpha);
    expect(container.querySelectorAll('.repair-run')).toHaveLength(2);
    fireEvent.click(alpha);
    expect(container.querySelectorAll('.repair-run')).toHaveLength(3);
  });

  it('shows failures only', () => {
    const { container } = renderHistory(runs);
    fireEvent.click(screen.getByText('Failures only'));
    expect(container.querySelectorAll('.repair-run')).toHaveLength(1);
    expect(container.querySelector('.repair-run')?.className).toContain('failed');
  });

  it('says nothing matches rather than "no runs yet" when filters empty the list', () => {
    // Alpha never failed, so Alpha + failures-only is a real empty result —
    // and it must not claim maintenance has never run.
    const { container } = renderHistory(runs);
    fireEvent.click(screen.getByText('Failures only'));
    fireEvent.click(chip(container, 'Alpha'));
    expect(container.querySelectorAll('.repair-run')).toHaveLength(0);
    expect(screen.getByText('Nothing matches')).toBeTruthy();
  });

  it('refreshes on demand — a run that just finished is not in this list yet', () => {
    const { onRefresh, container } = renderHistory(runs);
    fireEvent.click(container.querySelector('.repair-runs-refresh') as HTMLElement);
    expect(onRefresh).toHaveBeenCalled();
  });
});
