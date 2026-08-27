import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdlBatch, AdlBatchHistoryEntry, AdlDownload } from '../-adl.types';

import {
  AdlEarlierGroup,
  AdlGroup,
  AdlGroupedList,
  AdlRecentHistory,
  EARLIER_FOLD,
} from './adl-groups';

afterEach(cleanup);

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'downloading',
    progress: 50,
    error: null,
    verification_status: null,
    batch_id: 'b1',
    batch_name: 'My Batch',
    batch_source: '',
    playlist_id: 'p1',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

const batch = (over: Partial<AdlBatch> = {}): AdlBatch => ({
  batch_id: 'b1',
  playlist_id: 'p1',
  batch_name: 'My Batch',
  source_page: 'wishlist',
  phase: 'downloading',
  total: 10,
  completed: 3,
  failed: 1,
  active: 1,
  queued: 5,
  ...over,
});

const groupProps = (over: Record<string, unknown> = {}) => ({
  batch: batch(),
  rows: [row()],
  allBatchRows: [row()],
  bucketed: true,
  filtered: false,
  opacity: 1,
  samples: [],
  onFilter: vi.fn(),
  onCancel: vi.fn(),
  onOpenModal: vi.fn(),
  onCancelRow: vi.fn(),
  ...over,
});

describe('AdlGroup', () => {
  it('promotes the batch card into a header with name, phase and stat line', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    expect(container.querySelector('.adl-group-name')?.textContent).toBe('My Batch');
    expect(container.querySelector('.adl-group-source')?.textContent).toBe('wishlist');
    expect(container.querySelector('.adl-group-phase')?.textContent).toContain('3/10 tracks');
    expect(container.querySelector('.adl-group-stats')?.textContent).toBe(
      '3 done · 1 failed · 1 active · 5 queued',
    );
  });

  it('starts open for a live batch and folded for a terminal one', () => {
    const live = render(<AdlGroup {...groupProps()} />);
    expect(live.container.querySelectorAll('.adl-group-rows .adl-row')).toHaveLength(1);
    cleanup();
    const done = render(
      <AdlGroup {...groupProps({ batch: batch({ phase: 'complete', active: 0, queued: 0 }) })} />,
    );
    expect(done.container.querySelector('.adl-group-rows')).toBeNull();
  });

  it('toggles on header click and via keyboard', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    const header = container.querySelector('.adl-group-header') as HTMLElement;
    fireEvent.click(header);
    expect(container.querySelector('.adl-group-rows')).toBeNull();
    fireEvent.keyDown(header, { key: 'Enter' });
    expect(container.querySelector('.adl-group-rows')).not.toBeNull();
  });

  it('renders group rows compact, without the redundant batch line', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    const groupRow = container.querySelector('.adl-group-rows .adl-row') as HTMLElement;
    expect(groupRow.className).toContain('adl-row-compact');
    // the group header already says which batch this is
    expect(groupRow.querySelector('.adl-row-batch')).toBeNull();
  });

  it('says when the status filter is hiding rows instead of lying about size', () => {
    const seven = Array.from({ length: 7 }, (_, i) => row({ task_id: `t${i}` }));
    const { container } = render(
      <AdlGroup {...groupProps({ rows: [row()], allBatchRows: seven })} />,
    );
    expect(container.querySelector('.adl-group-note')?.textContent).toBe(
      '6 more hidden by the current filter.',
    );
  });

  it('keeps modal-open, filter and cancel as header actions that do not toggle', () => {
    const props = groupProps();
    const { container } = render(<AdlGroup {...props} />);
    fireEvent.click(container.querySelector('.adl-group-name') as HTMLElement);
    expect(props.onOpenModal).toHaveBeenCalled();
    fireEvent.click(container.querySelector('.adl-group-filter') as HTMLElement);
    expect(props.onFilter).toHaveBeenCalled();
    fireEvent.click(container.querySelector('.adl-group-cancel') as HTMLElement);
    expect(props.onCancel).toHaveBeenCalled();
    // none of those clicks may have collapsed the group
    expect(container.querySelector('.adl-group-rows')).not.toBeNull();
  });

  it('has no cancel action on a terminal batch', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ batch: batch({ phase: 'complete' }) })} />,
    );
    expect(container.querySelector('.adl-group-cancel')).toBeNull();
  });
});

describe('bucketed group rows', () => {
  const mixed = [
    row({ task_id: 'a1', status: 'downloading', title: 'Moving' }),
    row({ task_id: 'f1', status: 'failed', title: 'Broke' }),
    row({ task_id: 'q1', status: 'queued', title: 'Next Up' }),
    row({ task_id: 'q2', status: 'queued', title: 'After That' }),
    row({ task_id: 'c1', status: 'completed', title: 'Finished' }),
    row({ task_id: 'c2', status: 'completed', title: 'Also Done' }),
    row({ task_id: 'x1', status: 'cancelled', title: 'Bailed' }),
  ];

  it('shows moving + broken rows in full and folds queued/done to one line each', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />,
    );
    const titles = [...container.querySelectorAll('.adl-row-title')].map((t) => t.textContent);
    // in-flight first, then the broken ones (cancelled folds into broken)
    expect(titles).toEqual(['Moving', 'Broke', 'Bailed']);
    const folds = [...container.querySelectorAll('.adl-bucket-fold')].map((f) => f.textContent);
    expect(folds[0]).toContain('2 queued');
    expect(folds[0]).toContain('next: Next Up');
    expect(folds[1]).toContain('2 done');
  });

  it('expands a fold in place and collapses it again', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />,
    );
    const queuedFold = container.querySelector('.adl-bucket-fold') as HTMLElement;
    fireEvent.click(queuedFold);
    let titles = [...container.querySelectorAll('.adl-row-title')].map((t) => t.textContent);
    expect(titles).toContain('Next Up');
    expect(titles).toContain('After That');
    // done stays folded independently
    expect(titles).not.toContain('Finished');
    fireEvent.click(queuedFold);
    titles = [...container.querySelectorAll('.adl-row-title')].map((t) => t.textContent);
    expect(titles).toEqual(['Moving', 'Broke', 'Bailed']);
  });

  it('renders every row plainly when bucketing is off (status/batch filter)', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed, bucketed: false })} />,
    );
    expect(container.querySelectorAll('.adl-bucket-fold')).toHaveLength(0);
    expect(container.querySelectorAll('.adl-row')).toHaveLength(mixed.length);
  });
});

describe('AdlEarlierGroup', () => {
  it('folds a same-album history run into one line and expands in place', () => {
    const albumRun = Array.from({ length: 5 }, (_, i) =>
      row({
        task_id: `h${i}`,
        batch_id: '',
        status: i === 4 ? 'failed' : 'completed',
        artist: 'Fontaines D.C.',
        album: 'Romance (Deluxe Edition)',
        title: `Track ${i}`,
      }),
    );
    const single = row({
      task_id: 's1',
      batch_id: '',
      status: 'completed',
      album: 'One-Off',
      title: 'Lone Single',
    });
    const { container } = render(
      <AdlEarlierGroup rows={[...albumRun, single]} onCancelRow={vi.fn()} />,
    );
    // the run is one line; the singleton stays a plain row
    const header = container.querySelector('.adl-earlier-album-header') as HTMLElement;
    expect(header.textContent).toContain('Romance (Deluxe Edition)');
    expect(header.textContent).toContain('4 done · 1 failed');
    expect([...container.querySelectorAll('.adl-row-title')].map((t) => t.textContent)).toEqual([
      'Lone Single',
    ]);

    fireEvent.click(header);
    expect(container.querySelectorAll('.adl-earlier-album .adl-row')).toHaveLength(5);
    fireEvent.click(header);
    expect(container.querySelectorAll('.adl-earlier-album .adl-row')).toHaveLength(0);
  });

  it('keeps batchless in-flight rows plain and on top, never inside a fold', () => {
    const rows = [
      row({ task_id: 'l1', batch_id: '', status: 'downloading', album: 'Some Album' }),
      row({ task_id: 'h1', batch_id: '', status: 'completed', album: 'Some Album' }),
      row({ task_id: 'h2', batch_id: '', status: 'completed', album: 'Some Album' }),
    ];
    const { container } = render(<AdlEarlierGroup rows={rows} onCancelRow={vi.fn()} />);
    // the live row renders in full; the two settled ones fold
    expect(container.querySelectorAll(':scope > .adl-earlier > .adl-row')).toHaveLength(1);
    expect(container.querySelector('.adl-earlier-album-summary')?.textContent).toBe('2 done');
  });

  it('folds past the item cap counting albums as one item', () => {
    const many = Array.from({ length: EARLIER_FOLD + 5 }, (_, i) =>
      row({ task_id: `h${i}`, batch_id: '', album: `Album ${i}`, status: 'completed' }),
    );
    const { container } = render(<AdlEarlierGroup rows={many} onCancelRow={vi.fn()} />);
    expect(container.querySelectorAll('.adl-row')).toHaveLength(EARLIER_FOLD);
    const more = container.querySelector('.adl-earlier-more') as HTMLElement;
    expect(more.textContent).toBe('Show 5 more');
    fireEvent.click(more);
    expect(container.querySelectorAll('.adl-row')).toHaveLength(many.length);
  });

  it('renders nothing at all when empty', () => {
    const { container } = render(<AdlEarlierGroup rows={[]} onCancelRow={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('AdlGroupedList', () => {
  const listProps = (over: Record<string, unknown> = {}) => ({
    rows: [row()],
    allRows: [row()],
    batches: [batch()],
    history: [] as AdlBatchHistoryEntry[],
    filterBatchId: null,
    statusFiltered: false,
    batchOpacity: () => 1,
    samplesFor: () => [],
    onFilterBatch: vi.fn(),
    onCancelBatch: vi.fn(),
    onOpenBatchModal: vi.fn(),
    onOpenFullHistory: vi.fn(),
    onCancelRow: vi.fn(),
    onNavigate: vi.fn(),
    ...over,
  });

  it('routes batched rows into their group and the rest into Earlier', () => {
    const loose = row({ task_id: 'h1', batch_id: '', status: 'completed' });
    const { container } = render(
      <AdlGroupedList {...listProps({ rows: [row(), loose], allRows: [row(), loose] })} />,
    );
    expect(container.querySelectorAll('.adl-group')).toHaveLength(1);
    expect(container.querySelector('.adl-earlier .adl-row-title')?.textContent).toBe('Xtal');
    expect(container.querySelector('.adl-section-header')?.textContent).toBe('Earlier (1)');
  });

  it('rows referencing a dead batch fall back to Earlier instead of vanishing', () => {
    const orphan = row({ task_id: 'o1', batch_id: 'gone', status: 'completed' });
    const { container } = render(
      <AdlGroupedList {...listProps({ rows: [orphan], allRows: [orphan], batches: [] })} />,
    );
    expect(container.querySelectorAll('.adl-group')).toHaveLength(0);
    expect(container.querySelectorAll('.adl-earlier .adl-row')).toHaveLength(1);
  });

  it('a batch filter narrows to that group and hides the tail sections', () => {
    const other = batch({ batch_id: 'b2', batch_name: 'Other' });
    const { container } = render(
      <AdlGroupedList
        {...listProps({
          batches: [batch(), other],
          filterBatchId: 'b2',
          rows: [],
          allRows: [],
          history: [{ playlist_name: 'Done earlier' } as AdlBatchHistoryEntry],
        })}
      />,
    );
    const groups = [...container.querySelectorAll('.adl-group-name')].map((g) => g.textContent);
    expect(groups).toEqual(['Other']);
    expect(container.querySelector('#adl-batch-history-section')).toBeNull();
  });

  it('hides zero-match groups under a status chip instead of touring every batch', () => {
    const failedRow = row({ task_id: 'f1', batch_id: 'b2', status: 'failed' });
    const other = batch({ batch_id: 'b2', batch_name: 'Other' });
    const { container } = render(
      <AdlGroupedList
        {...listProps({
          batches: [batch(), other],
          rows: [failedRow],
          allRows: [row(), failedRow],
          statusFiltered: true,
        })}
      />,
    );
    const groups = [...container.querySelectorAll('.adl-group-name')].map((g) => g.textContent);
    expect(groups).toEqual(['Other']);
  });

  it('shows the hero empty state with somewhere to go when nothing exists', () => {
    const onNavigate = vi.fn();
    const { container } = render(
      <AdlGroupedList {...listProps({ rows: [], allRows: [], batches: [], onNavigate })} />,
    );
    expect(container.querySelector('.adl-empty-title')?.textContent).toBe('Nothing downloading');
    fireEvent.click(container.querySelector('.adl-empty-links a') as HTMLElement);
    expect(onNavigate).toHaveBeenCalledWith('search');
  });
});

describe('AdlRecentHistory', () => {
  const entry = (over: Partial<AdlBatchHistoryEntry> = {}): AdlBatchHistoryEntry => ({
    playlist_name: 'Liked Songs',
    tracks_downloaded: 9,
    tracks_failed: 1,
    total_tracks: 10,
    completed_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    source_page: 'sync',
    ...over,
  });

  it('is hidden entirely with no history', () => {
    const { container } = render(<AdlRecentHistory history={[]} onOpenFullHistory={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it('lists entries with counts, failure note and age', () => {
    const { container } = render(
      <AdlRecentHistory history={[entry()]} onOpenFullHistory={vi.fn()} />,
    );
    expect(container.querySelector('.adl-batch-history-name')?.textContent).toContain(
      'Liked Songs',
    );
    expect(container.querySelector('.adl-batch-history-stats')?.textContent).toBe('9/10 1 failed');
    expect(container.querySelector('.adl-batch-history-date')?.textContent).toBe('3h ago');
  });

  it('opens the full history modal without toggling the fold', () => {
    const onOpen = vi.fn();
    const { container } = render(
      <AdlRecentHistory history={[entry()]} onOpenFullHistory={onOpen} />,
    );
    fireEvent.click(container.querySelector('.library-history-btn') as HTMLElement);
    expect(onOpen).toHaveBeenCalled();
    expect(container.querySelector('.adl-batch-history-section')?.className).not.toContain(
      'expanded',
    );
  });
});
