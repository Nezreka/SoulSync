import { describe, expect, it } from 'vitest';

import { summarizeRun } from './-wishlist.run';

describe('summarizeRun', () => {
  it('says nothing without a status', () => {
    expect(summarizeRun(null)).toBeNull();
    expect(summarizeRun({})).toBeNull();
  });

  it('counts the library check while a run is getting ready', () => {
    const run = summarizeRun({
      phase: 'analysis',
      analysis_progress: { total: 40, processed: 10 },
    });
    expect(run).toMatchObject({ stage: 'preparing', fraction: 0.25 });
    expect(run?.label).toBe('Getting ready · 10 of 40 tracks');
    expect(summarizeRun({ phase: 'queued' })?.label).toBe('Getting ready');
  });

  it('counts done and failed while downloading', () => {
    const run = summarizeRun({
      phase: 'downloading',
      tasks: [
        { status: 'completed' },
        { status: 'not_found' },
        { status: 'searching' },
        { status: 'cancelled' },
      ],
    });
    expect(run).toMatchObject({
      stage: 'downloading',
      total: 4,
      done: 1,
      failed: 1,
      fraction: 0.75,
    });
    expect(run?.label).toBe('Downloading 4 tracks · 1 done · 1 failed');
  });

  it('leaves failed out when nothing failed, and singularizes', () => {
    expect(summarizeRun({ phase: 'downloading', tasks: [{ status: 'queued' }] })?.label).toBe(
      'Downloading 1 track · 0 done',
    );
  });

  it('knows a finished and a stopped run', () => {
    expect(summarizeRun({ phase: 'complete', tasks: [{ status: 'completed' }] })).toMatchObject({
      stage: 'finished',
      fraction: 1,
    });
    expect(summarizeRun({ phase: 'cancelled', tasks: [] })).toMatchObject({
      stage: 'stopped',
      label: 'Stopped',
    });
  });
});
