import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  onDownloadNext: vi.fn(),
  onDownloadBatchNext: vi.fn(),
  ...over,
});

describe('AdlGroup', () => {
  it('heads the group with what it is, its name, where it is at and what failed', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    expect(container.querySelector('.dlc-gtitle')?.textContent).toBe('My Batch');
    // one album across the rows reads as an album, and says where it came from
    expect(container.querySelector('.dlc-eyebrow')?.textContent).toBe('Album · from wishlist');
    expect(container.querySelector('.dlc-gphase')?.textContent).toContain('3/10 tracks');
    expect(container.querySelector('.dlc-gbad')?.textContent).toBe('1 failed');
  });

  it('numbers an album batch like a tracklist, and only an album batch', () => {
    const two = [
      row({ task_id: 'a', batch_position: 1 }),
      row({ task_id: 'b', batch_position: 2 }),
    ];
    const { container } = render(<AdlGroup {...groupProps({ rows: two, allBatchRows: two })} />);
    expect(container.querySelector('.dlc-num')).not.toBeNull();
    cleanup();
    const mixed = [row({ task_id: 'a' }), row({ task_id: 'b', album: 'Other' })];
    const playlist = render(<AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />);
    expect(playlist.container.querySelector('.dlc-num')).toBeNull();
  });

  it('badges the cover with the artist photo, or uses the photo when there is no cover', () => {
    const withArt = [row({ task_id: 'a', artwork: '/c.jpg', artist_image: '/a.jpg' })];
    const { container } = render(
      <AdlGroup {...groupProps({ rows: withArt, allBatchRows: withArt })} />,
    );
    expect(container.querySelector('img.dlc-gart')?.getAttribute('src')).toBe('/c.jpg');
    expect(container.querySelector('.dlc-gart-badge .dlc-avatar')?.getAttribute('src')).toBe(
      '/a.jpg',
    );
    cleanup();
    const bare = [row({ task_id: 'a', artwork: '', artist_image: '/a.jpg' })];
    const noCover = render(<AdlGroup {...groupProps({ rows: bare, allBatchRows: bare })} />);
    expect(noCover.container.querySelector('img.dlc-gart')?.getAttribute('src')).toBe('/a.jpg');
    expect(noCover.container.querySelector('.dlc-gart-badge')).toBeNull();
  });

  it('tiles four covers for a playlist batch', () => {
    const mixed = ['1', '2', '3', '4'].map((n) =>
      row({ task_id: n, album: `Album ${n}`, artist: `Artist ${n}`, artwork: `/${n}.jpg` }),
    );
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />,
    );
    expect(container.querySelectorAll('.dlc-mosaic img')).toHaveLength(4);
    cleanup();
    // one album is one cover, never a mosaic of the same image
    const album = ['1', '2', '3', '4'].map((n) => row({ task_id: n, artwork: `/${n}.jpg` }));
    const one = render(<AdlGroup {...groupProps({ rows: album, allBatchRows: album })} />);
    expect(one.container.querySelector('.dlc-mosaic')).toBeNull();
  });

  it('never offers the music search on an audiobook batch', () => {
    const failed = [row({ status: 'failed', error: 'No releases found' })];
    const { container } = render(
      <AdlGroup
        {...groupProps({
          batch: batch({ batch_type: 'audiobook', playlist_id: 'audiobooks' }),
          rows: failed,
          allBatchRows: failed,
        })}
      />,
    );
    expect(container.querySelector('.dlc-eyebrow')?.textContent).toContain('Audiobook');
    expect(container.querySelector('.dlc-row .dlc-btn-primary')).toBeNull();
  });

  it('starts open for a live batch and folded for a terminal one', () => {
    const live = render(<AdlGroup {...groupProps()} />);
    expect(live.container.querySelectorAll('.dlc-group-rows .dlc-row')).toHaveLength(1);
    cleanup();
    const done = render(
      <AdlGroup {...groupProps({ batch: batch({ phase: 'complete', active: 0, queued: 0 }) })} />,
    );
    expect(done.container.querySelector('.dlc-group-rows')).toBeNull();
  });

  it('toggles on header click and via keyboard', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    const header = container.querySelector('.dlc-ghead') as HTMLElement;
    fireEvent.click(header);
    expect(container.querySelector('.dlc-group-rows')).toBeNull();
    fireEvent.keyDown(header, { key: 'Enter' });
    expect(container.querySelector('.dlc-group-rows')).not.toBeNull();
  });

  it('renders group rows compact, without the redundant batch fact', () => {
    const { container } = render(<AdlGroup {...groupProps()} />);
    const groupRow = container.querySelector('.dlc-group-rows .dlc-row') as HTMLElement;
    expect(groupRow.className).toContain('dlc-row-compact');
    fireEvent.click(groupRow);
    // the group header already says which batch this is
    expect(container.querySelector('.dlc-facts')?.textContent ?? '').not.toContain('My Batch');
  });

  it('says when the status filter is hiding rows instead of lying about size', () => {
    const seven = Array.from({ length: 7 }, (_, i) => row({ task_id: `t${i}` }));
    const { container } = render(
      <AdlGroup {...groupProps({ rows: [row()], allBatchRows: seven })} />,
    );
    expect(container.querySelector('.dlc-note')?.textContent).toBe(
      '6 more hidden by the current filter.',
    );
  });

  const pick = (container: HTMLElement, label: string) => {
    fireEvent.click(container.querySelector('.dlc-ghead .dlc-more') as HTMLElement);
    fireEvent.click(screen.getByRole('menuitem', { name: label }));
  };

  it('runs the batch Download Next action from the menu without toggling', () => {
    const props = groupProps();
    const { container } = render(<AdlGroup {...props} />);
    pick(container, 'Download this batch next');
    expect(props.onDownloadBatchNext).toHaveBeenCalledWith(props.batch);
    expect(container.querySelector('.dlc-group-rows')).not.toBeNull();
  });

  it('keeps open, filter and cancel as header actions that do not toggle', () => {
    const props = groupProps();
    const { container } = render(<AdlGroup {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(props.onOpenModal).toHaveBeenCalled();
    pick(container, 'Show only this batch');
    expect(props.onFilter).toHaveBeenCalled();
    pick(container, 'Cancel batch');
    expect(props.onCancel).toHaveBeenCalled();
    // none of those clicks may have collapsed the group
    expect(container.querySelector('.dlc-group-rows')).not.toBeNull();
  });

  it('has no cancel or download-next on a terminal batch', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ batch: batch({ phase: 'complete' }) })} />,
    );
    fireEvent.click(container.querySelector('.dlc-ghead .dlc-more') as HTMLElement);
    expect(screen.queryByRole('menuitem', { name: 'Cancel batch' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Download this batch next' })).toBeNull();
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

  it('uses batch_position, not server recency order, for the queued preview', () => {
    const rows = [
      row({ task_id: 'q2', status: 'queued', title: 'Second', batch_position: 5 }),
      row({ task_id: 'q1', status: 'queued', title: 'First', batch_position: 4 }),
    ];
    const { container } = render(<AdlGroup {...groupProps({ rows, allBatchRows: rows })} />);
    const fold = container.querySelector('.dlc-fold') as HTMLElement;
    expect(fold.textContent).toContain('next: First');
  });
  it('shows moving + broken rows in full and folds queued/done to one line each', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />,
    );
    const titles = [...container.querySelectorAll('.dlc-title')].map((t) => t.textContent);
    // in-flight first, then the broken ones (cancelled folds into broken)
    expect(titles).toEqual(['Moving', 'Broke', 'Bailed']);
    const folds = [...container.querySelectorAll('.dlc-fold')].map((f) => f.textContent);
    expect(folds[0]).toContain('2 queued');
    expect(folds[0]).toContain('next: Next Up');
    expect(folds[1]).toContain('2 done');
  });

  it('expands a fold in place and collapses it again', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed })} />,
    );
    const queuedFold = container.querySelector('.dlc-fold') as HTMLElement;
    fireEvent.click(queuedFold);
    let titles = [...container.querySelectorAll('.dlc-title')].map((t) => t.textContent);
    expect(titles).toContain('Next Up');
    expect(titles).toContain('After That');
    // done stays folded independently
    expect(titles).not.toContain('Finished');
    fireEvent.click(queuedFold);
    titles = [...container.querySelectorAll('.dlc-title')].map((t) => t.textContent);
    expect(titles).toEqual(['Moving', 'Broke', 'Bailed']);
  });

  it('renders every row plainly when bucketing is off (status/batch filter)', () => {
    const { container } = render(
      <AdlGroup {...groupProps({ rows: mixed, allBatchRows: mixed, bucketed: false })} />,
    );
    expect(container.querySelectorAll('.dlc-fold')).toHaveLength(0);
    expect(container.querySelectorAll('.dlc-row')).toHaveLength(mixed.length);
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
    const header = container.querySelector('.dlc-sub-head') as HTMLElement;
    expect(header.textContent).toContain('Romance (Deluxe Edition)');
    expect(header.textContent).toContain('4 done · 1 failed');
    expect([...container.querySelectorAll('.dlc-title')].map((t) => t.textContent)).toEqual([
      'Lone Single',
    ]);

    // five rows start folded; a short run (three or fewer) starts open
    fireEvent.click(header);
    expect(container.querySelectorAll('.dlc-sub-group .dlc-row')).toHaveLength(5);
    fireEvent.click(header);
    expect(container.querySelectorAll('.dlc-sub-group .dlc-row')).toHaveLength(0);
  });

  it('splits history into days with real labels, newest first', () => {
    const stamp = (msAgo: number) =>
      new Date(Date.now() - msAgo).toISOString().slice(0, 19).replace('T', ' ');
    const rows = [
      row({
        task_id: 'h1',
        batch_id: '',
        status: 'completed',
        album: 'A',
        is_persistent_history: true,
        created_at: stamp(60_000),
      }),
      row({
        task_id: 'h2',
        batch_id: '',
        status: 'completed',
        album: 'B',
        is_persistent_history: true,
        created_at: stamp(30 * 3_600_000),
      }),
    ];
    const { container } = render(<AdlEarlierGroup rows={rows} onCancelRow={vi.fn()} />);
    const labels = [...container.querySelectorAll('.dlc-section-label')].map((l) => l.textContent);
    expect(labels[0]).toBe('Today');
    expect(labels).toHaveLength(2);
  });

  it('labels a podcast run as a podcast', () => {
    const eps = [1, 2].map((i) =>
      row({
        task_id: `p${i}`,
        batch_id: '',
        status: 'completed',
        album: 'Show',
        artist: 'NPR',
        event_type: 'podcast',
      }),
    );
    const { container } = render(<AdlEarlierGroup rows={eps} onCancelRow={vi.fn()} />);
    expect(container.querySelector('.dlc-sub-head .dlc-eyebrow')?.textContent).toBe('Podcast');
    // a short run starts open
    expect(container.querySelectorAll('.dlc-sub-group .dlc-row')).toHaveLength(2);
  });

  it('keeps batchless in-flight rows plain and on top, never inside a fold', () => {
    const rows = [
      row({ task_id: 'l1', batch_id: '', status: 'downloading', album: 'Some Album' }),
      row({ task_id: 'h1', batch_id: '', status: 'completed', album: 'Some Album' }),
      row({ task_id: 'h2', batch_id: '', status: 'completed', album: 'Some Album' }),
    ];
    const { container } = render(<AdlEarlierGroup rows={rows} onCancelRow={vi.fn()} />);
    // the live row renders in full; the two settled ones fold
    expect(container.querySelectorAll(':scope > .dlc-earlier > .dlc-list > .dlc-row')).toHaveLength(
      1,
    );
    expect(container.querySelector('.dlc-sub-summary')?.textContent).toBe('2 done');
  });

  it('folds past the item cap counting albums as one item', () => {
    const many = Array.from({ length: EARLIER_FOLD + 5 }, (_, i) =>
      row({ task_id: `h${i}`, batch_id: '', album: `Album ${i}`, status: 'completed' }),
    );
    const { container } = render(<AdlEarlierGroup rows={many} onCancelRow={vi.fn()} />);
    expect(container.querySelectorAll('.dlc-row')).toHaveLength(EARLIER_FOLD);
    const more = container.querySelector('.dlc-more-link') as HTMLElement;
    expect(more.textContent).toBe('Show 5 more');
    fireEvent.click(more);
    expect(container.querySelectorAll('.dlc-row')).toHaveLength(many.length);
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
    onDownloadNext: vi.fn(),
    onDownloadBatchNext: vi.fn(),
    onNavigate: vi.fn(),
    ...over,
  });

  it('routes batched rows into their group and the rest into Earlier', () => {
    const loose = row({ task_id: 'h1', batch_id: '', status: 'completed' });
    const { container } = render(
      <AdlGroupedList {...listProps({ rows: [row(), loose], allRows: [row(), loose] })} />,
    );
    expect(container.querySelectorAll('.dlc-group')).toHaveLength(1);
    expect(container.querySelector('.dlc-earlier .dlc-title')?.textContent).toBe('Xtal');
  });

  it('rows referencing a dead batch fall back to Earlier instead of vanishing', () => {
    const orphan = row({ task_id: 'o1', batch_id: 'gone', status: 'completed' });
    const { container } = render(
      <AdlGroupedList {...listProps({ rows: [orphan], allRows: [orphan], batches: [] })} />,
    );
    expect(container.querySelectorAll('.dlc-group')).toHaveLength(0);
    expect(container.querySelectorAll('.dlc-earlier .dlc-row')).toHaveLength(1);
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
    const groups = [...container.querySelectorAll('.dlc-gtitle')].map((g) => g.textContent);
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
    const groups = [...container.querySelectorAll('.dlc-gtitle')].map((g) => g.textContent);
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
