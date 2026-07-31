import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BasicSearchController, BasicSearchState } from '../-basic.use-controller';
import type { BasicResultActions } from './basic-results';

import { DEFAULT_FILTERS } from '../-basic.types';
import { IDLE_STATUS } from '../-basic.use-controller';
import { BasicSearch, EMPTY_PLACEHOLDER, NO_RESULTS_PLACEHOLDER } from './basic-search';

afterEach(cleanup);

function stateOf(over: Partial<BasicSearchState> = {}): BasicSearchState {
  return {
    query: '',
    results: [],
    filters: DEFAULT_FILTERS,
    status: IDLE_STATUS,
    searching: false,
    sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
    activeSource: null,
    singleSource: true,
    filtersVisible: false,
    ...over,
  };
}

function noopActions(): BasicResultActions {
  return {
    onDownloadTrack: vi.fn(),
    onStreamTrack: vi.fn(),
    onMatchedTrack: vi.fn(),
    onDownloadAlbum: vi.fn(),
    onMatchedAlbum: vi.fn(),
    onDownloadAlbumTrack: vi.fn(),
    onStreamAlbumTrack: vi.fn(),
    onMatchedAlbumTrack: vi.fn(),
  };
}

function renderPanel(over: Partial<BasicSearchState> = {}, active = true) {
  const controller: BasicSearchController = {
    state: stateOf(over),
    visible: [],
    search: vi.fn(),
    cancel: vi.fn(),
    setFilters: vi.fn(),
    toggleSortOrder: vi.fn(),
    selectSource: vi.fn(),
  };
  const view = render(
    <BasicSearch controller={controller} actions={noopActions()} active={active} />,
  );
  return { ...view, controller };
}

const input = (container: HTMLElement) =>
  container.querySelector('#downloads-search-input') as HTMLInputElement;

describe('visibility', () => {
  it('carries .active only when it is the shown panel', () => {
    // `.search-section` is display:none until `.active`. jsdom applies no CSS,
    // so a panel that renders perfectly and is invisible passes every other
    // test in this file — this is the one that catches it.
    const { container } = renderPanel({}, true);
    expect(container.querySelector('#basic-search-section')?.className).toBe(
      'search-section active',
    );

    cleanup();
    const inactive = renderPanel({}, false);
    expect(inactive.container.querySelector('#basic-search-section')?.className).toBe(
      'search-section',
    );
  });
});

describe('search bar', () => {
  it('submits what was typed, on the button and on Enter', () => {
    const { container, controller } = renderPanel();
    fireEvent.change(input(container), { target: { value: 'aphex' } });

    fireEvent.click(container.querySelector('#downloads-search-btn') as HTMLElement);
    expect(controller.search).toHaveBeenCalledWith('aphex');

    fireEvent.keyDown(input(container), { key: 'Enter' });
    expect(controller.search).toHaveBeenCalledTimes(2);
  });

  it('ignores other keys', () => {
    const { container, controller } = renderPanel();
    fireEvent.keyDown(input(container), { key: 'a' });
    expect(controller.search).not.toHaveBeenCalled();
  });

  it('hides the cancel button until a search is running', () => {
    const { container } = renderPanel({ searching: false });
    expect(container.querySelector('#downloads-cancel-btn')?.className).toContain('hidden');

    cleanup();
    const busy = renderPanel({ searching: true });
    expect(busy.container.querySelector('#downloads-cancel-btn')?.className).not.toContain(
      'hidden',
    );
  });

  it('cancels rather than clearing — the ✕ here is not the enhanced bar"s', () => {
    // Same glyph, opposite job. The enhanced bar's ✕ clears the box; this one
    // aborts the request.
    const { container, controller } = renderPanel({ searching: true });
    fireEvent.change(input(container), { target: { value: 'aphex' } });
    fireEvent.click(container.querySelector('#downloads-cancel-btn') as HTMLElement);

    expect(controller.cancel).toHaveBeenCalled();
    expect(input(container).value).toBe('aphex');
  });

  it('locks the input and the button while searching', () => {
    const { container } = renderPanel({ searching: true });
    expect(input(container).disabled).toBe(true);
    expect((container.querySelector('#downloads-search-btn') as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('adopts a query the input never saw, so handoffs show what they searched', () => {
    // The wishlist "search manually" jump and the download widget both run a
    // search for a query typed somewhere else entirely.
    const { container } = renderPanel({ query: 'from the wishlist' });
    expect(input(container).value).toBe('from the wishlist');
  });
});

describe('status bar', () => {
  it('shows the status text', () => {
    const { container } = renderPanel({ status: 'Searching for ...' });
    expect(container.querySelector('#search-status-text')?.textContent).toBe('Searching for ...');
  });

  it('runs both animations only while searching', () => {
    const { container } = renderPanel({ searching: false });
    expect(container.querySelector('.spinner-animation')?.className).toContain('hidden');
    expect(container.querySelector('.dots-animation')?.className).toContain('hidden');

    cleanup();
    const busy = renderPanel({ searching: true });
    expect(busy.container.querySelector('.spinner-animation')?.className).not.toContain('hidden');
    expect(busy.container.querySelector('.dots-animation')?.className).not.toContain('hidden');
  });
});

describe('source chips', () => {
  it('disables the chips when there is nothing to choose between', () => {
    const { container } = renderPanel();
    const chip = container.querySelector('.bs-source-chip') as HTMLButtonElement;
    expect(chip.disabled).toBe(true);
    expect(chip.className).toContain('single');
    // Still marked active: the user needs to see WHICH source is being searched.
    expect(chip.className).toContain('active');
  });

  it('offers a real choice in hybrid mode', () => {
    const { container, controller } = renderPanel({
      singleSource: false,
      activeSource: 'soulseek',
      sources: [
        { name: 'soulseek', display_name: 'Soulseek' },
        { name: 'tidal', display_name: 'Tidal' },
      ],
    });

    const chips = container.querySelectorAll('.bs-source-chip');
    expect(chips).toHaveLength(2);
    expect((chips[0] as HTMLButtonElement).disabled).toBe(false);
    expect(chips[0].className).toContain('active');
    expect(chips[1].className).not.toContain('active');
    expect(chips[0].getAttribute('aria-selected')).toBe('true');

    fireEvent.click(chips[1]);
    expect(controller.selectSource).toHaveBeenCalledWith('tidal');
  });

  it('keeps the tablist semantics', () => {
    const { container } = renderPanel();
    expect(container.querySelector('#bs-source-row')?.getAttribute('role')).toBe('tablist');
    expect(container.querySelector('.bs-source-chip')?.getAttribute('role')).toBe('tab');
  });

  it('renders an empty row rather than nothing before the sources load', () => {
    const { container } = renderPanel({ sources: [] });
    expect(container.querySelector('#bs-source-row')).not.toBeNull();
    expect(container.querySelectorAll('.bs-source-chip')).toHaveLength(0);
  });
});

describe('filter pills', () => {
  it('stays hidden until a search finds something', () => {
    const { container } = renderPanel({ filtersVisible: false });
    expect(container.querySelector('#filters-container')?.className).toContain('hidden');

    cleanup();
    const shown = renderPanel({ filtersVisible: true });
    expect(shown.container.querySelector('#filters-container')?.className).not.toContain('hidden');
  });

  it('marks the active pill in each group', () => {
    const { container } = renderPanel({
      filtersVisible: true,
      filters: { type: 'album', format: 'flac', sort: 'size', reversed: false },
    });
    const active = [...container.querySelectorAll('.bs-filter-pill.active')].map((n) =>
      n.getAttribute('data-value'),
    );
    expect(active).toEqual(['album', 'flac', 'size']);
  });

  it('reports each pill click as its own filter change', () => {
    const { container, controller } = renderPanel({ filtersVisible: true });
    const pill = (type: string, value: string) =>
      container.querySelector(
        `.bs-filter-pill[data-filter-type="${type}"][data-value="${value}"]`,
      ) as HTMLElement;

    fireEvent.click(pill('type', 'album'));
    fireEvent.click(pill('format', 'mp3'));
    fireEvent.click(pill('sort', 'username'));

    expect(controller.setFilters).toHaveBeenNthCalledWith(1, { type: 'album' });
    expect(controller.setFilters).toHaveBeenNthCalledWith(2, { format: 'mp3' });
    expect(controller.setFilters).toHaveBeenNthCalledWith(3, { sort: 'username' });
  });

  it('offers every sort the vanilla did, minus the one no pill emitted', () => {
    const { container } = renderPanel({ filtersVisible: true });
    const sorts = [...container.querySelectorAll('.bs-filter-pill[data-filter-type="sort"]')].map(
      (n) => n.getAttribute('data-value'),
    );
    expect(sorts).toEqual([
      'relevance',
      'quality_score',
      'size',
      'title',
      'username',
      'bitrate',
      'duration',
    ]);
    expect(sorts).not.toContain('availability');
  });

  it('shows an arrow that agrees with the order', () => {
    // The vanilla rendered ↓ while the list was ascending.
    const { container, controller } = renderPanel({ filtersVisible: true });
    const arrow = container.querySelector('#sort-order-btn') as HTMLElement;
    expect(arrow.textContent).toBe('↓');
    expect(arrow.getAttribute('data-order')).toBe('desc');

    fireEvent.click(arrow);
    expect(controller.toggleSortOrder).toHaveBeenCalled();

    cleanup();
    const reversed = renderPanel({
      filtersVisible: true,
      filters: { ...DEFAULT_FILTERS, reversed: true },
    });
    const flipped = reversed.container.querySelector('#sort-order-btn') as HTMLElement;
    expect(flipped.textContent).toBe('↑');
    expect(flipped.getAttribute('data-order')).toBe('asc');
  });

  it('keeps the arrow out of the active-pill group', () => {
    // It carries no data-filter-type, so it never lights up as a sort key.
    const { container } = renderPanel({ filtersVisible: true });
    const arrow = container.querySelector('#sort-order-btn') as HTMLElement;
    expect(arrow.getAttribute('data-filter-type')).toBeNull();
    expect(arrow.className).not.toContain(' active');
  });
});

describe('results placeholder', () => {
  it('invites a search before one has run', () => {
    const { container } = renderPanel({ query: '' });
    expect(container.querySelector('.search-results-placeholder p')?.textContent).toBe(
      EMPTY_PLACEHOLDER,
    );
  });

  it('reports the miss after a search that found nothing', () => {
    const { container } = renderPanel({ query: 'zzzz' });
    expect(container.querySelector('.search-results-placeholder p')?.textContent).toBe(
      NO_RESULTS_PLACEHOLDER,
    );
  });
});
