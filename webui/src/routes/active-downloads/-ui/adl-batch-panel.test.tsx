import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdlBatch, AdlBatchHistoryEntry, AdlDownload } from '../-adl.types';

import { AdlBatchCard, AdlBatchEmpty, AdlBatchHistory, AdlBatchPanel } from './adl-batch-panel';

afterEach(cleanup);

const batch = (over: Partial<AdlBatch> = {}): AdlBatch =>
  ({
    batch_id: 'b1',
    playlist_id: 'p1',
    batch_name: 'My Batch',
    source_page: 'wishlist',
    phase: 'downloading',
    total: 10,
    completed: 3,
    failed: 1,
    active: 2,
    queued: 4,
    ...over,
  }) as AdlBatch;

const track = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    batch_id: 'b1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'downloading',
    progress: 40,
    track_index: 0,
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

const cardProps = (over: Partial<Parameters<typeof AdlBatchCard>[0]> = {}) => ({
  batch: batch(),
  tracks: [] as AdlDownload[],
  expanded: false,
  filtered: false,
  opacity: 1,
  samples: [],
  onToggle: vi.fn(),
  onFilter: vi.fn(),
  onCancel: vi.fn(),
  onOpenModal: vi.fn(),
  ...over,
});

const panelProps = (over: Record<string, unknown> = {}) => ({
  batches: [batch()],
  downloads: [] as AdlDownload[],
  history: [] as AdlBatchHistoryEntry[],
  expandedBatches: new Set<string>(),
  filterBatchId: null,
  batchOpacity: () => 1,
  samplesFor: () => [],
  onToggleBatch: vi.fn(),
  onFilterBatch: vi.fn(),
  onCancelBatch: vi.fn(),
  onOpenBatchModal: vi.fn(),
  onOpenFullHistory: vi.fn(),
  onNavigate: vi.fn(),
  ...over,
});

// ── The stopPropagation guards ────────────────────────────────────────────
// The whole card is a click target that expands it. Every control sitting on
// top of it must stop the event, or using that control ALSO toggles the card
// underneath — which looks like the card randomly opening and closing.

describe('controls on the card do not also toggle the card', () => {
  it('opening the download modal does not expand the card', () => {
    const props = cardProps();
    const { container } = render(<AdlBatchCard {...props} />);
    fireEvent.click(container.querySelector('.adl-batch-card-name') as HTMLElement);
    expect(props.onOpenModal).toHaveBeenCalledTimes(1);
    expect(props.onToggle).not.toHaveBeenCalled();
  });

  it('filtering to the batch does not expand the card', () => {
    const props = cardProps();
    const { container } = render(<AdlBatchCard {...props} />);
    fireEvent.click(container.querySelector('.adl-batch-card-filter') as HTMLElement);
    expect(props.onFilter).toHaveBeenCalledTimes(1);
    expect(props.onToggle).not.toHaveBeenCalled();
  });

  it('cancelling does not expand the card', () => {
    const props = cardProps();
    const { container } = render(<AdlBatchCard {...props} />);
    fireEvent.click(container.querySelector('.adl-batch-card-cancel') as HTMLElement);
    expect(props.onCancel).toHaveBeenCalledTimes(1);
    expect(props.onToggle).not.toHaveBeenCalled();
  });

  it('but the card body itself still toggles', () => {
    const props = cardProps();
    const { container } = render(<AdlBatchCard {...props} />);
    fireEvent.click(container.querySelector('.adl-batch-card-top') as HTMLElement);
    expect(props.onToggle).toHaveBeenCalledTimes(1);
  });

  it('opening full history does not collapse the history section', () => {
    const onOpenFullHistory = vi.fn();
    const { container } = render(
      <AdlBatchHistory
        history={[{ playlist_name: 'Old', total_tracks: 5 } as AdlBatchHistoryEntry]}
        onOpenFullHistory={onOpenFullHistory}
      />,
    );
    const section = container.querySelector('.adl-batch-history-section') as HTMLElement;
    expect(section.className).not.toContain('expanded');
    fireEvent.click(container.querySelector('.library-history-btn') as HTMLElement);
    expect(onOpenFullHistory).toHaveBeenCalledTimes(1);
    expect(section.className).not.toContain('expanded');
  });
});

describe('the batch card', () => {
  it('hides Cancel on a finished batch', () => {
    // There is nothing left to cancel, and the endpoint would 404.
    const { container } = render(
      <AdlBatchCard {...cardProps({ batch: batch({ phase: 'complete' }) })} />,
    );
    expect(container.querySelector('.adl-batch-card-cancel')).toBeNull();
    // The filter button stays — a finished batch is still worth isolating.
    expect(container.querySelector('.adl-batch-card-filter')).not.toBeNull();
  });

  it('marks itself expanded and filtered through classes', () => {
    const { container } = render(
      <AdlBatchCard {...cardProps({ expanded: true, filtered: true })} />,
    );
    const card = container.querySelector('.adl-batch-card') as HTMLElement;
    expect(card.className).toContain('expanded');
    expect(card.className).toContain('filtered');
    expect(card.className).toContain('phase-downloading');
  });

  it('carries the batch id for the stylesheet and the tests', () => {
    const { container } = render(<AdlBatchCard {...cardProps()} />);
    expect(container.querySelector('[data-batch-id="b1"]')).not.toBeNull();
  });

  it('lists tracks only once expanded', () => {
    const tracks = [track(), track({ task_id: 't2', title: 'Tha', track_index: 1 })];
    const collapsed = render(<AdlBatchCard {...cardProps({ tracks })} />);
    expect(collapsed.container.querySelectorAll('.adl-batch-track-row')).toHaveLength(0);
    cleanup();

    const open = render(<AdlBatchCard {...cardProps({ tracks, expanded: true })} />);
    expect(open.container.querySelectorAll('.adl-batch-track-row')).toHaveLength(2);
    // Numbered from the server's track_index, 1-based.
    expect(
      [...open.container.querySelectorAll('.adl-batch-track-idx')].map((n) => n.textContent),
    ).toEqual(['1', '2']);
  });

  it('explains an album batch with no tracks yet', () => {
    // Track rows do not exist until staging finishes; without this the panel
    // reads as broken.
    const { container } = render(
      <AdlBatchCard
        {...cardProps({ expanded: true, batch: batch({ phase: 'album_downloading' }) })}
      />,
    );
    expect(container.querySelector('.adl-batch-release-note')?.textContent).toContain(
      'Track matching starts after staging',
    );
  });

  it('falls back to an initial when no track has artwork', () => {
    const { container } = render(<AdlBatchCard {...cardProps()} />);
    const fallback = container.querySelector('.adl-batch-card-thumb-fallback');
    expect(fallback?.textContent).toBe('M');
    expect(container.querySelector('img.adl-batch-card-thumb')).toBeNull();
  });

  it('falls back when the artwork fails to load, without an onerror string', () => {
    // The vanilla built this as an onerror attribute containing nested escaped
    // HTML; a batch name with a quote in it could break the markup.
    const { container } = render(
      <AdlBatchCard {...cardProps({ tracks: [track({ artwork: '/art.jpg' })] })} />,
    );
    const img = container.querySelector('img.adl-batch-card-thumb') as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute('onerror')).toBeNull();

    fireEvent.error(img);
    expect(container.querySelector('img.adl-batch-card-thumb')).toBeNull();
    expect(container.querySelector('.adl-batch-card-thumb-fallback')).not.toBeNull();
  });

  it('survives a batch name that would have broken the old markup', () => {
    const nasty = '"><img src=x onerror=alert(1)>';
    const { container } = render(
      <AdlBatchCard {...cardProps({ batch: batch({ batch_name: nasty }) })} />,
    );
    expect(container.querySelector('.adl-batch-card-name')?.textContent).toBe(nasty);
    // Rendered as text, never parsed as markup.
    expect(container.querySelectorAll('img')).toHaveLength(0);
  });

  it('names an unnamed batch rather than rendering blank', () => {
    const { container } = render(
      <AdlBatchCard {...cardProps({ batch: batch({ batch_name: '' }) })} />,
    );
    expect(container.querySelector('.adl-batch-card-name')?.textContent).toBe('Download');
    expect(container.querySelector('.adl-batch-card-thumb-fallback')?.textContent).toBe('D');
  });

  it('shows the currently downloading track', () => {
    const { container } = render(
      <AdlBatchCard {...cardProps({ tracks: [track({ status: 'downloading' })] })} />,
    );
    expect(container.querySelector('.adl-batch-card-now')?.textContent).toContain('Xtal');
  });

  it('applies a dimming opacity only when told to', () => {
    const dim = render(<AdlBatchCard {...cardProps({ opacity: 0.4 })} />);
    expect((dim.container.querySelector('.adl-batch-card') as HTMLElement).style.opacity).toBe(
      '0.4',
    );
    cleanup();
    const full = render(<AdlBatchCard {...cardProps({ opacity: 1 })} />);
    expect((full.container.querySelector('.adl-batch-card') as HTMLElement).style.opacity).toBe('');
  });

  it('renders the three progress segments', () => {
    const { container } = render(<AdlBatchCard {...cardProps()} />);
    expect(container.querySelectorAll('.adl-batch-seg')).toHaveLength(3);
    const widths = [...container.querySelectorAll('.adl-batch-seg')].map(
      (el) => (el as HTMLElement).style.width,
    );
    // 10 total: 3 done, 1 failed, 2 active — and they cannot exceed 100%.
    const sum = widths.reduce((total, w) => total + Number.parseFloat(w || '0'), 0);
    expect(sum).toBeLessThanOrEqual(100.0001);
  });
});

describe('the panel', () => {
  it('counts only unfinished batches in the title', () => {
    // A completed batch still renders as a card, but the header count is about
    // what is running.
    const { container } = render(
      <AdlBatchPanel
        {...panelProps({
          batches: [batch(), batch({ batch_id: 'b2', phase: 'complete' })],
        })}
      />,
    );
    expect(container.querySelector('.adl-batch-panel-title')?.textContent).toBe('Batches (1)');
    expect(container.querySelectorAll('.adl-batch-card')).toHaveLength(2);
  });

  it('drops the count entirely when nothing is running', () => {
    const { container } = render(
      <AdlBatchPanel {...panelProps({ batches: [batch({ phase: 'complete' })] })} />,
    );
    expect(container.querySelector('.adl-batch-panel-title')?.textContent).toBe('Batches');
  });

  it('collapses and re-expands', () => {
    const { container } = render(<AdlBatchPanel {...panelProps()} />);
    const panel = container.querySelector('#adl-batch-panel') as HTMLElement;
    expect(panel.className).not.toContain('collapsed');
    fireEvent.click(container.querySelector('#adl-batch-collapse') as HTMLElement);
    expect(panel.className).toContain('collapsed');
    fireEvent.click(container.querySelector('#adl-batch-collapse') as HTMLElement);
    expect(panel.className).not.toContain('collapsed');
  });

  it('gives each card only its own tracks', () => {
    const { container } = render(
      <AdlBatchPanel
        {...panelProps({
          batches: [batch(), batch({ batch_id: 'b2' })],
          downloads: [track(), track({ task_id: 't2', batch_id: 'b2' }), track({ task_id: 't3' })],
          expandedBatches: new Set(['b1']),
        })}
      />,
    );
    const first = container.querySelector('[data-batch-id="b1"]') as HTMLElement;
    const second = container.querySelector('[data-batch-id="b2"]') as HTMLElement;
    expect(first.querySelectorAll('.adl-batch-track-row')).toHaveLength(2);
    // b2 is not expanded, so it shows none regardless.
    expect(second.querySelectorAll('.adl-batch-track-row')).toHaveLength(0);
  });

  it('routes each card action to the right batch', () => {
    const props = panelProps({
      batches: [batch(), batch({ batch_id: 'b2', batch_name: 'Other' })],
    });
    const { container } = render(<AdlBatchPanel {...props} />);
    const second = container.querySelector('[data-batch-id="b2"]') as HTMLElement;

    fireEvent.click(second.querySelector('.adl-batch-card-filter') as HTMLElement);
    expect(props.onFilterBatch).toHaveBeenCalledWith('b2');

    fireEvent.click(second.querySelector('.adl-batch-card-top') as HTMLElement);
    expect(props.onToggleBatch).toHaveBeenCalledWith('b2');

    fireEvent.click(second.querySelector('.adl-batch-card-cancel') as HTMLElement);
    expect(props.onCancelBatch).toHaveBeenCalledWith(expect.objectContaining({ batch_id: 'b2' }));

    fireEvent.click(second.querySelector('.adl-batch-card-name') as HTMLElement);
    expect(props.onOpenBatchModal).toHaveBeenCalledWith(
      expect.objectContaining({ batch_id: 'b2' }),
    );
  });

  it('shows the empty state with no batches, and routes its links in-app', () => {
    const props = panelProps({ batches: [] });
    const { container } = render(<AdlBatchPanel {...props} />);
    expect(container.querySelector('.adl-batch-empty-title')?.textContent).toBe(
      'Nothing downloading',
    );
    const links = [...container.querySelectorAll('.adl-batch-empty-links a')];
    expect(links.map((a) => a.textContent)).toEqual(['Search', 'Sync', 'Wishlist']);
    fireEvent.click(links[2]);
    expect(props.onNavigate).toHaveBeenCalledWith('wishlist');
  });

  it('hides the history section entirely when there is none', () => {
    const { container } = render(<AdlBatchPanel {...panelProps()} />);
    expect(container.querySelector('#adl-batch-history-section')).toBeNull();
  });
});

describe('the history rail', () => {
  const entry = (over: Partial<AdlBatchHistoryEntry> = {}): AdlBatchHistoryEntry =>
    ({
      playlist_name: 'Old Batch',
      source_page: 'sync',
      total_tracks: 10,
      tracks_downloaded: 8,
      tracks_failed: 2,
      completed_at: new Date(Date.now() - 3600_000).toISOString(),
      ...over,
    }) as AdlBatchHistoryEntry;

  it('renders nothing at all when empty', () => {
    const { container } = render(<AdlBatchHistory history={[]} onOpenFullHistory={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the downloaded/total tally and the failure count', () => {
    const { container } = render(
      <AdlBatchHistory history={[entry()]} onOpenFullHistory={vi.fn()} />,
    );
    const stats = container.querySelector('.adl-batch-history-stats')?.textContent;
    expect(stats).toContain('8/10');
    expect(stats).toContain('2 failed');
  });

  it('omits the failure count when nothing failed', () => {
    const { container } = render(
      <AdlBatchHistory history={[entry({ tracks_failed: 0 })]} onOpenFullHistory={vi.fn()} />,
    );
    expect(container.querySelector('.adl-batch-history-stats')?.textContent).not.toContain(
      'failed',
    );
  });

  it('expands and collapses on the header', () => {
    const { container } = render(
      <AdlBatchHistory history={[entry()]} onOpenFullHistory={vi.fn()} />,
    );
    const section = container.querySelector('.adl-batch-history-section') as HTMLElement;
    fireEvent.click(container.querySelector('.adl-batch-history-header') as HTMLElement);
    expect(section.className).toContain('expanded');
  });

  it('keys rows uniquely, because the same batch name recurs', () => {
    // Counting the rows proves nothing here: React renders duplicate-keyed
    // children anyway and only warns. The warning IS the failure — duplicate
    // keys make React reuse the wrong instance when the list reorders.
    const errors: string[] = [];
    const spy = vi
      .spyOn(console, 'error')
      .mockImplementation((...args: unknown[]) => errors.push(String(args[0])));

    const { container } = render(
      <AdlBatchHistory history={[entry(), entry(), entry()]} onOpenFullHistory={vi.fn()} />,
    );

    spy.mockRestore();
    expect(errors.filter((e) => e.includes('same key'))).toEqual([]);
    expect(container.querySelectorAll('.adl-batch-history-item')).toHaveLength(3);
  });

  it('names an unnamed entry', () => {
    const { container } = render(
      <AdlBatchHistory
        history={[entry({ playlist_name: '', source_page: '' })]}
        onOpenFullHistory={vi.fn()}
      />,
    );
    expect(container.querySelector('.adl-batch-history-name')?.textContent).toContain('Unknown');
  });
});
