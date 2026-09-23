import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BasicResult, BasicTrack, DownloadTarget } from '../-basic.types';
import type { BasicSearchController, BasicSearchState } from '../-basic.use-controller';

import { DEFAULT_FILTERS } from '../-basic.types';
import { IDLE_STATUS } from '../-basic.use-controller';
import { BasicSearch, EMPTY_PLACEHOLDER, FILTERED_OUT_PLACEHOLDER } from './basic-search';

// the enriched modal talks to the server as soon as it opens, keep it offline
vi.mock('../-basic.enriched', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../-basic.enriched')>()),
  fetchProviders: vi.fn(async () => [{ source: 'deezer', label: 'Deezer', active: true }]),
  searchProvider: vi.fn(async () => ({ albums: [], tracks: [] })),
}));

afterEach(cleanup);
beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    // no storage in this environment
  }
});

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'a.flac',
    size: 1024 * 1024,
    bitrate: 320,
    duration: 200_000,
    quality: 'mp3',
    free_upload_slots: 1,
    upload_speed: 1,
    queue_length: 0,
    sample_rate: null,
    bit_depth: null,
    artist: 'Aphex Twin',
    title: 'Xtal',
    album: 'SAW',
    track_number: 1,
    quality_score: 0.9,
    ...over,
  };
}

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

function renderPanel(
  over: Partial<BasicSearchState> = {},
  { active = true, visible = [] as BasicResult[] } = {},
) {
  const controller: BasicSearchController = {
    state: stateOf(over),
    visible,
    search: vi.fn(),
    cancel: vi.fn(),
    setFilters: vi.fn(),
    toggleSortOrder: vi.fn(),
    selectSource: vi.fn(),
  };
  const onDownload = vi.fn<(target: DownloadTarget) => void>();
  const view = render(
    <BasicSearch controller={controller} onDownload={onDownload} active={active} />,
  );
  return { ...view, controller, onDownload };
}

const input = (container: HTMLElement) =>
  container.querySelector('#downloads-search-input') as HTMLInputElement;

describe('visibility', () => {
  it('carries .active only when it is the shown panel', () => {
    expect(renderPanel().container.querySelector('#basic-search-section')?.className).toContain(
      'active',
    );
    cleanup();
    expect(
      renderPanel({}, { active: false }).container.querySelector('#basic-search-section')
        ?.className,
    ).not.toMatch(/\bactive\b/);
  });
});

describe('search field', () => {
  it('submits what was typed', () => {
    const { container, controller } = renderPanel();
    fireEvent.change(input(container), { target: { value: 'boards of canada' } });
    fireEvent.submit(input(container).closest('form')!);
    expect(controller.search).toHaveBeenCalledWith('boards of canada');
  });

  it('swaps Search for Cancel while a search runs, and locks the input', () => {
    const { container, controller } = renderPanel({
      searching: true,
      status: "Searching for 'x'...",
    });
    expect(container.querySelector('#downloads-search-btn')).toBeNull();
    expect(input(container).disabled).toBe(true);
    fireEvent.click(container.querySelector('#downloads-cancel-btn')!);
    expect(controller.cancel).toHaveBeenCalled();
  });

  it('does not stack a search on one already running', () => {
    const { container, controller } = renderPanel({ searching: true });
    fireEvent.submit(input(container).closest('form')!);
    expect(controller.search).not.toHaveBeenCalled();
  });

  it('adopts a query the input never saw, so handoffs show what they searched', () => {
    const { container } = renderPanel({ query: 'handed in' });
    expect(input(container).value).toBe('handed in');
  });
});

describe('source picker', () => {
  it('is a plain label with one source', () => {
    const { container } = renderPanel();
    const picker = container.querySelector('#bs-source-row');
    expect(picker?.textContent).toContain('Soulseek');
    expect(picker?.querySelector('select')).toBeNull();
  });

  it('is a real select with several, and reports the pick', () => {
    const { container, controller } = renderPanel({
      singleSource: false,
      activeSource: 'deezer_dl',
      sources: [
        { name: 'soulseek', display_name: 'Soulseek' },
        { name: 'deezer_dl', display_name: 'Deezer' },
      ],
    });
    const select = container.querySelector<HTMLSelectElement>('#bs-source-row select')!;
    expect(select.value).toBe('deezer_dl');
    expect(container.querySelector('#bs-source-row')?.textContent).toContain('Deezer');
    fireEvent.change(select, { target: { value: 'soulseek' } });
    expect(controller.selectSource).toHaveBeenCalledWith('soulseek');
  });
});

describe('toolbar', () => {
  const found = [track(), track({ result_type: 'track', title: 'Tha' })];

  it('stays hidden until a search finds something', () => {
    expect(renderPanel().container.querySelector('#filters-container')).toBeNull();
  });

  it('counts what came back and where from', () => {
    const { container } = renderPanel({ filtersVisible: true, results: found }, { visible: found });
    expect(container.querySelector('#filters-container')?.textContent).toContain(
      '2 results from Soulseek · 0 albums, 2 tracks',
    );
  });

  it('reports type, format and sort as filter changes', () => {
    const { container, controller } = renderPanel(
      { filtersVisible: true, results: found },
      { visible: found },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Albums' }));
    expect(controller.setFilters).toHaveBeenCalledWith({ type: 'album' });
    fireEvent.change(container.querySelector('select[aria-label="Format"]')!, {
      target: { value: 'flac' },
    });
    expect(controller.setFilters).toHaveBeenCalledWith({ format: 'flac' });
    fireEvent.change(container.querySelector('select[aria-label="Sort by"]')!, {
      target: { value: 'size' },
    });
    expect(controller.setFilters).toHaveBeenCalledWith({ sort: 'size' });
  });

  it('flips the order, and shows which way it is', () => {
    const { container, controller } = renderPanel(
      { filtersVisible: true, results: found, filters: { ...DEFAULT_FILTERS, reversed: true } },
      { visible: found },
    );
    const order = container.querySelector<HTMLButtonElement>('#sort-order-btn')!;
    expect(order.dataset.reversed).toBe('true');
    fireEvent.click(order);
    expect(controller.toggleSortOrder).toHaveBeenCalled();
  });
});

describe('the one message', () => {
  const text = (container: HTMLElement) =>
    container.querySelector('#search-status-text')?.textContent;

  it('invites a search before one has run', () => {
    expect(text(renderPanel().container)).toBe(EMPTY_PLACEHOLDER);
  });

  it('says what the controller says after a miss', () => {
    expect(text(renderPanel({ query: 'x', status: "No results found for 'x'" }).container)).toBe(
      "No results found for 'x'",
    );
  });

  it('says the filters hide everything, not that nothing was found', () => {
    const { container } = renderPanel(
      { query: 'x', results: [track()], filtersVisible: true, status: 'Found 1' },
      { visible: [] },
    );
    expect(text(container)).toBe(FILTERED_OUT_PLACEHOLDER);
  });
});

describe('the download chooser', () => {
  function openFor(result = track()) {
    const view = renderPanel(
      { query: 'x', results: [result], filtersVisible: true },
      { visible: [result] },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download Xtal' }));
    return view;
  }

  it('opens on Download, naming the file', () => {
    openFor();
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('Xtal');
    expect(dialog.textContent).toContain('Download as-is');
    expect(dialog.textContent).toContain('Enriched download');
  });

  it('defaults to enriched, and Continue opens the enriched flow instead of downloading', async () => {
    const { onDownload } = openFor();
    expect(
      screen.getByRole('radio', { name: /Enriched download/ }).getAttribute('aria-checked'),
    ).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Which track is this?')).toBeTruthy();
    expect(onDownload).not.toHaveBeenCalled();
  });

  it('as-is changes the button to Download and sends plain', () => {
    const { onDownload } = openFor();
    fireEvent.click(screen.getByRole('radio', { name: /Download as-is/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(onDownload).toHaveBeenCalledWith(expect.objectContaining({ kind: 'track' }));
  });

  it('remembers the last pick for next time', () => {
    openFor();
    fireEvent.click(screen.getByRole('radio', { name: /Download as-is/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    cleanup();
    openFor();
    expect(screen.getByRole('radio', { name: /Download as-is/ }).getAttribute('aria-checked')).toBe(
      'true',
    );
  });

  it('Cancel closes without downloading', () => {
    const { onDownload } = openFor();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onDownload).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('stays out of the other tabs', () => {
  it('the panel class never sets display, so .search-section can hide it', async () => {
    // jsdom applies no css, so this reads the rule itself. a display on .panel
    // beat style.css's `.search-section { display: none }` and the basic
    // search bar showed on every search tab
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const css = readFileSync(
      resolve(process.cwd(), 'src/routes/search/-ui/basic.module.css'),
      'utf8',
    );
    const panel = css.match(/\n\.panel \{([^}]*)\}/)?.[1] ?? '';
    expect(panel).toContain('--bs-surface');
    expect(panel).not.toMatch(/^\s*display\s*:/m);
  });
});
