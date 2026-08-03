/**
 * Card chrome + the five launcher cards.
 *
 * Most of these assert the DOM CONTRACT rather than looks: the card ids that
 * helper.js anchors on, the `page` class that must NOT be present, and the fact
 * that each launcher calls the vanilla global instead of reimplementing a modal.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  BlacklistCard,
  ConfigMigrationCard,
  DiscoveryPoolCard,
  ManualLibraryMatchCard,
  MetadataCacheCard,
} from './launcher-cards';
import { ToolCard, ToolProgress, ToolsSection } from './tool-card';
import { ToolsPage } from './tools-page';

const fetchMock = vi.fn();

function jsonOnce(data: unknown, ok = true) {
  fetchMock.mockResolvedValueOnce({ ok, status: ok ? 200 : 500, json: async () => data } as never);
}

/**
 * Several cards fetch on mount. Tests that don't care about the result still
 * have to let those promises settle, or React logs an act() warning after the
 * test has already passed — noise that would hide a real one later.
 */
async function flush() {
  await act(async () => {});
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) } as never);
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ToolCard chrome', () => {
  it('keeps the card id — helper.js anchors its popovers on these', () => {
    const { container } = render(<ToolCard id="db-updater-card" title="Database Updater" />);
    expect(container.querySelector('#db-updater-card')).not.toBeNull();
    expect(container.querySelector('.tool-card')).not.toBeNull();
  });

  it('renders the help button only when the card declares a tool', async () => {
    const openToolHelpModal = vi.fn();
    vi.stubGlobal('openToolHelpModal', openToolHelpModal);
    Object.assign(window, { openToolHelpModal });

    const { container, rerender } = render(
      <ToolCard id="a-card" title="A" helpTool="db-updater" />,
    );
    const button = container.querySelector('.tool-help-button');
    expect(button).not.toBeNull();
    expect(button?.getAttribute('data-tool')).toBe('db-updater');

    fireEvent.click(button as Element);
    expect(openToolHelpModal).toHaveBeenCalledWith('db-updater');

    rerender(<ToolCard id="a-card" title="A" />);
    expect(container.querySelector('.tool-help-button')).toBeNull();
  });

  it('survives the help global being absent', () => {
    Object.assign(window, { openToolHelpModal: undefined });
    const { container } = render(<ToolCard id="a-card" title="A" helpTool="db-updater" />);
    expect(() =>
      fireEvent.click(container.querySelector('.tool-help-button') as Element),
    ).not.toThrow();
  });

  it('renders stats with their labels and values', () => {
    render(
      <ToolCard
        id="a-card"
        title="A"
        stats={[
          { label: 'Artists:', value: '12' },
          { label: 'Albums:', value: '3' },
        ]}
      />,
    );
    expect(screen.getByText('Artists:')).toBeTruthy();
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('Albums:')).toBeTruthy();
  });

  it('hides with an inline display:none, the way the Plex-only cards do', () => {
    const { container } = render(
      <ToolCard id="media-scan-card" title="Media Server Scan" hidden />,
    );
    const card = container.querySelector('#media-scan-card') as HTMLElement;
    expect(card.style.display).toBe('none');
  });

  it('omits the optional blocks entirely rather than rendering empty ones', () => {
    const { container } = render(<ToolCard id="a-card" title="A" />);
    expect(container.querySelector('.tool-card-info')).toBeNull();
    expect(container.querySelector('.tool-card-stats')).toBeNull();
    expect(container.querySelector('.tool-card-controls')).toBeNull();
    expect(container.querySelector('.tool-card-progress-section')).toBeNull();
  });
});

describe('ToolProgress', () => {
  it('writes the percent straight into the bar width', () => {
    const { container } = render(
      <ToolProgress
        phase="Scanning…"
        details="10 / 20 files"
        percent={50}
        phaseId="p"
        barId="b"
        detailsId="d"
      />,
    );
    const bar = container.querySelector('#b') as HTMLElement;
    expect(bar.style.width).toBe('50%');
    expect(screen.getByText('Scanning…')).toBeTruthy();
    expect(screen.getByText('10 / 20 files')).toBeTruthy();
  });

  it('takes an error colour for the red bar states', () => {
    const { container } = render(
      <ToolProgress
        phase="Error"
        details=""
        percent={0}
        phaseId="p"
        barId="b"
        detailsId="d"
        barColor="#ff4444"
      />,
    );
    expect((container.querySelector('#b') as HTMLElement).style.backgroundColor).toBe(
      'rgb(255, 68, 68)',
    );
  });
});

describe('ToolsSection', () => {
  it('wraps its cards in the grid the CSS expects', () => {
    const { container } = render(
      <ToolsSection title="Management">
        <ToolCard id="a-card" title="A" />
      </ToolsSection>,
    );
    expect(container.querySelector('.tools-section-title')?.textContent).toBe('Management');
    expect(container.querySelector('.tools-grid .tool-card')).not.toBeNull();
  });
});

describe('launcher cards call the vanilla modals', () => {
  it.each([
    [
      'discovery pool',
      <DiscoveryPoolCard key="d" />,
      'openDiscoveryPoolModal',
      'Open Discovery Pool',
    ],
    [
      'manual library match',
      <ManualLibraryMatchCard key="m" />,
      'openManualLibraryMatchTool',
      'Open Library Match',
    ],
    [
      'config migration',
      <ConfigMigrationCard key="c" />,
      'openConfigExportModal',
      'Export / Import Config',
    ],
    ['blacklist', <BlacklistCard key="b" />, 'openBlacklistModal', 'View Blacklist'],
  ])('%s', async (_name, element, globalName, label) => {
    const spy = vi.fn();
    Object.assign(window, { [globalName]: spy });
    render(element);
    fireEvent.click(screen.getByText(label));
    expect(spy).toHaveBeenCalled();
    await flush();
  });

  it('metadata cache opens both the browser and the health modal', async () => {
    const browse = vi.fn();
    const health = vi.fn();
    Object.assign(window, { openMetadataCacheModal: browse, openCacheHealthModal: health });
    render(<MetadataCacheCard />);
    fireEvent.click(screen.getByText('Browse Cache'));
    fireEvent.click(screen.getByText('Cache Health'));
    expect(browse).toHaveBeenCalled();
    expect(health).toHaveBeenCalled();
    await flush();
  });
});

describe('DiscoveryPoolCard', () => {
  it('shows the counters once they load', async () => {
    jsonOnce({ stats: { matched: 128, failed: 4 } });
    render(<DiscoveryPoolCard />);
    await waitFor(() => expect(screen.getByText('128')).toBeTruthy());
    expect(screen.getByText('4')).toBeTruthy();
  });

  it('KEEPS the em dashes when the pool fails to load', async () => {
    // Printing "0 matched" over a failed load reads as a real answer. The
    // vanilla throws into an empty catch and leaves the placeholder; so do we.
    jsonOnce({});
    const { container } = render(<DiscoveryPoolCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('#discovery-pool-matched-count')?.textContent).toBe('—');
    expect(container.querySelector('#discovery-pool-failed-count')?.textContent).toBe('—');
  });

  it('keeps the failed counter’s red pill styling', async () => {
    render(<DiscoveryPoolCard />);
    // The id sits ON the .stat-item-value span, exactly as in the markup — not
    // on a nested node — so the styling and the id are the same element.
    const failed = document.querySelector('#discovery-pool-failed-count') as HTMLElement;
    expect(failed.classList.contains('stat-item-value')).toBe(true);
    expect(failed.style.color).toBe('rgb(239, 68, 68)');
    expect(failed.style.backgroundColor).toBe('rgba(239, 68, 68, 0.15)');
    await flush();
  });
});

describe('MetadataCacheCard', () => {
  it('sums only the four first-party sources', async () => {
    jsonOnce({
      artists: { spotify: 1, itunes: 2, deezer: 3, beatport: 4, discogs: 100, musicbrainz: 100 },
      albums: { spotify: 5 },
      tracks: {},
      total_hits: 77,
    });
    const { container } = render(<MetadataCacheCard />);
    await waitFor(() =>
      expect(container.querySelector('#mcache-stat-artists')?.textContent).toBe('10'),
    );
    expect(container.querySelector('#mcache-stat-albums')?.textContent).toBe('5');
    expect(container.querySelector('#mcache-stat-tracks')?.textContent).toBe('0');
    expect(container.querySelector('#mcache-stat-hits')?.textContent).toBe('77');
  });

  it('stays at zero when the cache is not initialised', async () => {
    jsonOnce({}, false);
    const { container } = render(<MetadataCacheCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('#mcache-stat-artists')?.textContent).toBe('0');
  });
});

describe('BlacklistCard', () => {
  it('counts entries and ignores the success flag, like loadBlacklistCount', async () => {
    jsonOnce({ success: false, entries: [{ id: 1 }, { id: 2 }] });
    const { container } = render(<BlacklistCard />);
    await waitFor(() => expect(container.querySelector('#blacklist-count')?.textContent).toBe('2'));
  });
});

describe('stat ids land on the styled span', () => {
  it('never nests the id inside .stat-item-value', async () => {
    // The vanilla puts class AND id on the same span. Splitting them adds a DOM
    // node and leaves anything touching the element's class or style writing to
    // the wrong one — an artefact diff caught this, not these tests.
    const { container } = render(<ToolsPage />);
    await flush();
    const ids = [
      'discovery-pool-matched-count',
      'discovery-pool-failed-count',
      'mcache-stat-artists',
      'mcache-stat-albums',
      'mcache-stat-tracks',
      'mcache-stat-hits',
      'blacklist-count',
    ];
    for (const id of ids) {
      const el = container.querySelector(`#${id}`) as HTMLElement;
      expect(el, id).not.toBeNull();
      expect(el.classList.contains('stat-item-value'), id).toBe(true);
    }
  });
});

describe('ToolsPage shell', () => {
  it('carries the tools-page id but NOT the `page` class', async () => {
    // `.page { display: none }` in the shell — a React page wearing that class
    // renders invisible while every test still passes.
    const { container } = render(<ToolsPage />);
    const root = container.querySelector('#tools-page') as HTMLElement;
    expect(root).not.toBeNull();
    expect(root.classList.contains('page')).toBe(false);
    expect(root.classList.contains('page-shell')).toBe(true);
    expect(root.classList.contains('tools-page-container')).toBe(true);
    await flush();
  });

  it('renders the header copy verbatim', async () => {
    render(<ToolsPage />);
    expect(screen.getByText('Tools & Operations')).toBeTruthy();
    expect(
      screen.getByText('Database management, library scanning, metadata, backups'),
    ).toBeTruthy();
    await flush();
  });

  it('places each launcher card in its vanilla section', async () => {
    const { container } = render(<ToolsPage />);
    const sections = [...container.querySelectorAll('.tools-section')].map((section) => ({
      title: section.querySelector('.tools-section-title')?.textContent,
      cards: [...section.querySelectorAll('.tool-card')].map((card) => card.id),
    }));
    expect(sections).toEqual([
      {
        title: 'Metadata & Cache',
        cards: ['discovery-pool-card', 'manual-library-match-card'],
      },
      {
        title: 'Management',
        cards: ['config-migration-card', 'metadata-cache-card', 'blacklist-card'],
      },
    ]);
    await flush();
  });

  it('keeps every helper.js anchor id that this wave owns', async () => {
    const { container } = render(<ToolsPage />);
    for (const id of ['discovery-pool-card', 'metadata-cache-card', 'blacklist-card']) {
      expect(container.querySelector(`#${id}`)).not.toBeNull();
    }
    await flush();
  });
});
