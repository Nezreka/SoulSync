import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BlacklistModalProps } from './blacklist-modal';

import { BlacklistModal } from './blacklist-modal';

afterEach(cleanup);

function props(over: Partial<BlacklistModalProps> = {}): BlacklistModalProps {
  return {
    query: '',
    results: null,
    entries: [{ id: 1, artist_name: 'Nickelback', created_at: '2026-07-01T10:00:00Z' }],
    listPhase: 'ready',
    onQueryChange: vi.fn(),
    onBlock: vi.fn(),
    onUnblock: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
}

describe('Blocked Artists modal', () => {
  it('renders the vanilla shell and closes from backdrop, ✕ and the footer', () => {
    const p = props();
    const { container } = render(<BlacklistModal {...p} />);
    const overlay = container.querySelector('#discovery-blacklist-modal-overlay')!;
    expect(overlay).toHaveClass('modal-overlay');
    expect(overlay.querySelector('.discover-blacklist-modal-header h2')!.textContent).toBe(
      'Blocked Artists',
    );
    expect(overlay.querySelector('.discover-blacklist-modal-header p')!.textContent).toBe(
      "These artists won't appear in any discovery playlist across all sources",
    );
    fireEvent.click(container.querySelector('.discover-blacklist-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(overlay);
    fireEvent.click(container.querySelector('.watch-all-close')!);
    fireEvent.click(screen.getByText('Close'));
    expect(p.onClose).toHaveBeenCalledTimes(3);
  });

  it('keeps the results dropdown out of the DOM until it has an answer', () => {
    // display:none in the vanilla (5075); a null prop is the port's version.
    const { container, rerender } = render(<BlacklistModal {...props()} />);
    expect(container.querySelector('#dbl-search-results')).toBeNull();
    rerender(<BlacklistModal {...props({ results: [] })} />);
    expect(container.querySelector('#dbl-search-results .dbl-search-empty')!.textContent).toBe(
      'No artists found',
    );
  });

  it('reports typing and renders result rows with the 🎤 art fallback', () => {
    const p = props({
      results: [{ name: 'Aphex Twin', image_url: '/img/a.jpg' }, { name: 'Autechre' }],
    });
    const { container } = render(<BlacklistModal {...p} />);
    fireEvent.change(container.querySelector('#dbl-search-input')!, { target: { value: 'ap' } });
    expect(p.onQueryChange).toHaveBeenCalledWith('ap');
    const items = [...container.querySelectorAll('.dbl-search-item')];
    expect(items[0].querySelector('img.dbl-search-img')).toHaveAttribute('src', '/img/a.jpg');
    expect(items[1].querySelector('.dbl-search-img-placeholder')!.textContent).toBe('🎤');
    expect(items[1].querySelector('.dbl-search-name')!.textContent).toBe('Autechre');
    expect(items[0].querySelector('.dbl-search-action')!.textContent).toBe('Block');
    fireEvent.click(items[1]);
    expect(p.onBlock).toHaveBeenCalledExactlyOnceWith('Autechre');
  });

  it('words the three list states in the ONE empty class', () => {
    const { container, rerender } = render(<BlacklistModal {...props({ listPhase: 'loading' })} />);
    const empty = () => container.querySelector('#dbl-list .discover-blacklist-empty');
    expect(empty()!.textContent).toBe('Loading...');
    rerender(<BlacklistModal {...props({ listPhase: 'error' })} />);
    expect(empty()!.textContent).toBe('Failed to load');
    rerender(<BlacklistModal {...props({ entries: [] })} />);
    expect(empty()!.textContent).toBe('No blocked artists yet — search above to block one');
  });

  it('renders entries with the locale date, blank when absent, and unblocks', () => {
    const p = props({
      entries: [
        { id: 1, artist_name: 'Nickelback', created_at: '2026-07-01T10:00:00Z' },
        { id: 2, artist_name: 'Creed' },
      ],
    });
    const { container } = render(<BlacklistModal {...p} />);
    const rows = [...container.querySelectorAll('.discover-blacklist-item')];
    expect(rows[0].querySelector('.discover-blacklist-name')!.textContent).toBe('Nickelback');
    expect(rows[0].querySelector('.discover-blacklist-date')!.textContent).toBe(
      new Date('2026-07-01T10:00:00Z').toLocaleDateString(),
    );
    // No timestamp → blank, never "Invalid Date".
    expect(rows[1].querySelector('.discover-blacklist-date')!.textContent).toBe('');
    const btn = rows[1].querySelector('.discover-blacklist-remove')!;
    expect(btn).toHaveAttribute('title', 'Unblock');
    fireEvent.click(btn);
    expect(p.onUnblock).toHaveBeenCalledExactlyOnceWith(p.entries[1]);
  });
});
