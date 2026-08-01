import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BatchRow } from '../-discover.your-albums-actions';
import type { YourAlbumsBatchModalProps } from './your-albums-batch-modal';

import { initialBatchProgress, reduceBatchEvent } from '../-discover.your-albums-actions';
import { YourAlbumsBatchModal } from './your-albums-batch-modal';

afterEach(cleanup);

const row = (index: number, over: Partial<BatchRow> = {}): BatchRow =>
  ({
    album_name: `Album ${index}`,
    artist_name: 'Aphex Twin',
    release_date: '1992-11-09',
    total_tracks: 13,
    image_url: '/img/a.jpg',
    _src: { id: `sp${index}`, source: 'spotify' },
    _index: index,
    ...over,
  }) as BatchRow;

function props(over: Partial<YourAlbumsBatchModalProps> = {}): YourAlbumsBatchModalProps {
  return {
    rows: [row(0), row(2)], // non-contiguous _index — the join key, not a position
    selected: [0, 2],
    phase: 'select',
    progress: null,
    onToggleRow: vi.fn(),
    onSelectAll: vi.fn(),
    onSubmit: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
}

describe('Your Albums batch modal — select phase', () => {
  it('renders the discog shell and becomes visible AFTER mount', () => {
    const { container } = render(<YourAlbumsBatchModal {...props()} />);
    const overlay = container.querySelector('#your-albums-batch-modal-overlay')!;
    // The effect has run by assertion time; the class must END UP present so
    // the CSS transition has a frame to animate from.
    expect(overlay).toHaveClass('discog-modal-overlay', 'visible');
    expect(container.querySelector('.discog-modal-title')!.textContent).toBe(
      'Add Missing Albums to Wishlist',
    );
    expect(container.querySelector('.discog-modal-artist')!.textContent).toBe(
      '2 albums missing from your library',
    );
    // The library modal fills .discog-filters; this one renders it EMPTY.
    expect(container.querySelector('.discog-filters')!.children).toHaveLength(0);
  });

  it('has NO backdrop close — only Cancel and the ✕', () => {
    const p = props();
    const { container } = render(<YourAlbumsBatchModal {...p} />);
    fireEvent.click(container.querySelector('#your-albums-batch-modal-overlay')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('.discog-modal-close')!);
    fireEvent.click(container.querySelector('.discog-cancel-btn')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  it('renders cards as label+checkbox joined on _index, with the meta line', () => {
    const p = props({ selected: [2] });
    const { container } = render(<YourAlbumsBatchModal {...p} />);
    const cards = [...container.querySelectorAll('.discog-card')] as HTMLElement[];
    expect(cards).toHaveLength(2);
    // Stagger by FILTERED position, not by _index.
    expect(cards[1].style.animationDelay).toBe('0.03s');
    expect(cards[0].querySelector('.discog-card-meta')!.textContent).toBe(
      'Aphex Twin · 1992 · 13 tracks · spotify',
    );
    const boxes = [...container.querySelectorAll('.your-albums-batch-cb')] as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([false, true]);
    fireEvent.click(boxes[0]);
    expect(p.onToggleRow).toHaveBeenCalledWith(0, true);
    fireEvent.click(boxes[1]);
    expect(p.onToggleRow).toHaveBeenCalledWith(2, false);
  });

  it('falls back to the 🎵 placeholder without art', () => {
    const { container } = render(
      <YourAlbumsBatchModal {...props({ rows: [row(0, { image_url: '' })] })} />,
    );
    expect(container.querySelector('.discog-card-art img')).toBeNull();
    expect(container.querySelector('.discog-card-art-placeholder')!.textContent).toBe('🎵');
  });

  it('selects and deselects all', () => {
    const p = props();
    render(<YourAlbumsBatchModal {...p} />);
    fireEvent.click(screen.getByText('Select All'));
    expect(p.onSelectAll).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByText('Deselect All'));
    expect(p.onSelectAll).toHaveBeenCalledWith(false);
  });

  it('tallies the footer from the SELECTED rows and disables submit at zero', () => {
    const p = props();
    const { container, rerender } = render(<YourAlbumsBatchModal {...p} />);
    expect(container.querySelector('.discog-footer-info')!.textContent).toBe(
      '2 albums · 26 tracks',
    );
    const submit = container.querySelector('#your-albums-batch-submit-btn')!;
    expect(submit.querySelector('#your-albums-batch-submit-text')!.textContent).toBe(
      'Add 2 to Wishlist',
    );
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(p.onSubmit).toHaveBeenCalledOnce();

    rerender(<YourAlbumsBatchModal {...props({ selected: [] })} />);
    expect(container.querySelector('.discog-footer-info')!.textContent).toBe('0 albums');
    // The subtitle counts what is MISSING, not what is checked (1796).
    expect(container.querySelector('.discog-modal-artist')!.textContent).toBe(
      '2 albums missing from your library',
    );
    expect(container.querySelector('#your-albums-batch-submit-text')!.textContent).toBe(
      'Select albums',
    );
    expect(container.querySelector('#your-albums-batch-submit-btn')).toBeDisabled();
  });
});

describe('Your Albums batch modal — running and done', () => {
  const runningProps = () => {
    const rows = [row(0), row(2)];
    return props({
      rows,
      selected: [0, 2],
      phase: 'running' as const,
      progress: initialBatchProgress(rows),
    });
  };

  it('swaps grid+filter bar for the progress list, processing line, no submit', () => {
    const { container } = render(<YourAlbumsBatchModal {...runningProps()} />);
    expect(container.querySelector('.discog-grid')).toBeNull();
    expect(container.querySelector('.discog-filter-bar')).toBeNull();
    expect(container.querySelector('#your-albums-batch-submit-btn')).toBeNull();
    expect(container.querySelector('.discog-footer-info')!.textContent).toBe(
      'Processing... this may take a moment',
    );
    const items = [...container.querySelectorAll('.discog-progress-item')];
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveClass('active');
    expect(items[0].id).toBe('your-albums-batch-prog-spotify-sp0');
    expect(items[0].querySelector('.discog-prog-status')!.textContent).toBe('Waiting...');
    expect(items[0].querySelector('.discog-prog-icon .discog-spinner')).not.toBeNull();
  });

  it('renders reduced events: done rows get ✓, error rows get ✗', () => {
    const p = runningProps();
    let progress = p.progress!;
    progress = reduceBatchEvent(
      progress,
      { album_id: 'sp0', status: 'done', tracks_added: 10, tracks_skipped: 3 },
      p.rows,
    );
    progress = reduceBatchEvent(
      progress,
      { album_id: 'sp2', status: 'error', message: 'no source' },
      p.rows,
    );
    const { container } = render(<YourAlbumsBatchModal {...p} progress={progress} />);
    const items = [...container.querySelectorAll('.discog-progress-item')];
    expect(items[0]).toHaveClass('done');
    expect(items[0]).not.toHaveClass('active');
    expect(items[0].querySelector('.discog-prog-status')!.textContent).toBe('10 added · 3 skipped');
    expect(items[0].querySelector('.discog-prog-icon')!.textContent).toBe('✓');
    expect(items[1]).toHaveClass('error');
    expect(items[1].querySelector('.discog-prog-status')!.textContent).toBe('Error: no source');
    expect(items[1].querySelector('.discog-prog-icon')!.textContent).toBe('✗');
  });

  it('done: totals in the footer, submit back but disabled and reading Done', () => {
    const p = runningProps();
    const progress = reduceBatchEvent(
      p.progress!,
      { status: 'complete', total_added: 13, total_skipped: 4 },
      p.rows,
    );
    const { container } = render(<YourAlbumsBatchModal {...p} phase="done" progress={progress} />);
    expect(container.querySelector('.discog-footer-info')!.textContent).toBe(
      '13 tracks added to wishlist · 4 skipped',
    );
    const submit = container.querySelector('#your-albums-batch-submit-btn')!;
    expect(submit).toBeDisabled();
    expect(submit.querySelector('#your-albums-batch-submit-text')!.textContent).toBe('Done');
  });
});
