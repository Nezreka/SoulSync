import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SeedArtist } from '../-discover.build-playlist';
import type { DownloadState } from '../-discover.download-bar';
import type { BuildPlaylistSectionProps } from './build-playlist';

import { AdventurousnessDial } from './adventurousness-dial';
import { BuildPlaylistSection } from './build-playlist';
import { DownloadBar, declarationToStyle } from './download-bar';

/**
 * The three page controls.
 *
 * The dial is the one with teeth: it animates, so it needs a frame loop, and
 * the loop has to stop costing anything when the page is not on screen. The
 * other two are about refusing impossible states — a sixth seed, an empty bar.
 */

afterEach(cleanup);

// ── The adventurousness dial ─────────────────────────────────────────────────

describe('the dial', () => {
  let frames: FrameRequestCallback[] = [];

  beforeEach(() => {
    frames = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  });

  // stubGlobal is not undone by restoreMocks, and a leaked reduced-motion
  // matchMedia silently kills the frame loop in every test after it.
  afterEach(() => vi.unstubAllGlobals());

  const dial = (over: Partial<Parameters<typeof AdventurousnessDial>[0]> = {}) => ({
    value: 0.3,
    onChange: vi.fn(),
    onCommit: vi.fn(),
    ...over,
  });

  it('labels its state from the value', () => {
    const { rerender } = render(<AdventurousnessDial {...dial({ value: 0.05 })} />);
    const label = () => document.getElementById('adv-wave-state')!.textContent;
    const first = label();
    rerender(<AdventurousnessDial {...dial({ value: 0.95 })} />);
    // The two ends must not read the same.
    expect(label()).not.toBe(first);
  });

  it('draws the wave into the vanilla path ids, area filled by the gradient', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const line = container.querySelector('#adv-wave-path')!.getAttribute('d')!;
    const area = container.querySelector('#adv-wave-area')!;
    expect(line.startsWith('M ')).toBe(true);
    // The area is the line closed down to the baseline — same shape, plus a lid.
    expect(area.getAttribute('d')!.startsWith(line)).toBe(true);
    expect(area.getAttribute('d')!.endsWith('Z')).toBe(true);
    // Filled by the GRADIENT, fading to nothing — not a solid colour. The
    // vanilla recolours only the top stop (95).
    expect(area).toHaveAttribute('fill', 'url(#adv-wave-fill)');
    const stop = container.querySelector('#adv-wave-fill-top')!;
    expect(stop.getAttribute('stop-color')).toBe(
      container.querySelector('#adv-wave-path')!.getAttribute('stroke'),
    );
    expect(stop.getAttribute('stop-opacity')).toBe('0.32');
  });

  it('strokes the line at 2.5 with round caps and its glow', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const path = container.querySelector('#adv-wave-path') as SVGPathElement;
    expect(path.getAttribute('stroke-width')).toBe('2.5');
    expect(path.getAttribute('stroke-linecap')).toBe('round');
    expect(path.style.filter).toContain('drop-shadow');
  });

  it('labels its two poles', () => {
    // index.html 4543-4546 — without them the dial never says what its ends
    // mean. The first draft dropped the footer entirely.
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const ends = container.querySelector('.adv-wave-ends')!;
    expect(ends.textContent).toContain('Safe — artists you already like');
    expect(ends.textContent).toContain('Adventurous — deep cuts');
  });

  it('sends the colour wash chasing the orb', () => {
    // The aura follows the orb's left calc (103-105); background alone leaves
    // it parked at the left edge.
    const { container } = render(<AdventurousnessDial {...dial({ value: 1 })} />);
    const aura = container.querySelector('#adv-wave-aura') as HTMLElement;
    expect(aura.style.left).toContain('calc(');
    expect(aura.style.background).toContain('radial-gradient');
  });

  it('advances the wave on each frame', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const before = container.querySelector('#adv-wave-path')!.getAttribute('d');
    act(() => frames.shift()!(0));
    expect(container.querySelector('#adv-wave-path')!.getAttribute('d')).not.toBe(before);
  });

  it('computes NOTHING while the wave is off screen', () => {
    // offsetParent said "the page is displayed", which is not the same as
    // "you can see it". An IntersectionObserver actually knows.
    let notify: ((entries: { isIntersecting: boolean }[]) => void) | undefined;
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(cb: (entries: { isIntersecting: boolean }[]) => void) {
          notify = cb;
        }
        observe() {}
        disconnect() {}
      },
    );
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const before = container.querySelector('#adv-wave-path')!.getAttribute('d');
    act(() => frames.shift()!(0));
    expect(container.querySelector('#adv-wave-path')!.getAttribute('d')).toBe(before);
    // Scrolled into view: it starts drawing again.
    act(() => notify!([{ isIntersecting: true }]));
    act(() => frames.shift()!(0));
    expect(container.querySelector('#adv-wave-path')!.getAttribute('d')).not.toBe(before);
  });

  it('reduced motion means no frame loop at all', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {} })),
    );
    render(<AdventurousnessDial {...dial()} />);
    expect(frames).toHaveLength(0);
  });

  it('cancels the loop on unmount', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame');
    const { unmount } = render(<AdventurousnessDial {...dial()} />);
    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  // M05. The dial used to be mousedown + window mousemove: no keyboard, no
  // touch, no announced value, nothing a screen reader could operate.
  it('is a native range control with an announced value', () => {
    const { container } = render(<AdventurousnessDial {...dial({ value: 0.5 })} />);
    const input = container.querySelector('.adv-wave-input') as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(input.type).toBe('range');
    expect(input.min).toBe('0');
    expect(input.max).toBe('1');
    expect(input.value).toBe('0.5');
    expect(input.getAttribute('aria-labelledby')).toBe('adv-wave-label');
    // Not just a number: the band name is what the label on screen says.
    expect(input.getAttribute('aria-valuetext')).toContain('Adventurous');
  });

  it('reports live on input and once the gesture settles', () => {
    vi.useFakeTimers();
    const p = dial();
    const { container } = render(<AdventurousnessDial {...p} />);
    const input = container.querySelector('.adv-wave-input') as HTMLInputElement;

    fireEvent.change(input, { target: { value: '0.75' } });
    expect(p.onChange).toHaveBeenLastCalledWith(0.75);
    expect(p.onCommit).not.toHaveBeenCalled();

    act(() => void vi.advanceTimersByTime(400));
    expect(p.onCommit).toHaveBeenCalledWith(0.75);
    vi.useRealTimers();
  });

  it('a held arrow key saves once, with the last value', () => {
    // Otherwise every repeat fires its own save and an older response can land
    // last, writing back a value the user already moved past.
    vi.useFakeTimers();
    const p = dial();
    const { container } = render(<AdventurousnessDial {...p} />);
    const input = container.querySelector('.adv-wave-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '0.4' } });
    act(() => void vi.advanceTimersByTime(100));
    fireEvent.change(input, { target: { value: '0.5' } });
    act(() => void vi.advanceTimersByTime(100));
    fireEvent.change(input, { target: { value: '0.6' } });
    act(() => void vi.advanceTimersByTime(400));
    expect(p.onCommit).toHaveBeenCalledTimes(1);
    expect(p.onCommit).toHaveBeenCalledWith(0.6);
    vi.useRealTimers();
  });

  it('says what it actually changes', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const help = container.querySelector('#adv-wave-help')!;
    expect(help.textContent).toContain('popular');
    // And the control points at it, so the explanation is announced too.
    expect(container.querySelector('.adv-wave-input')).toHaveAttribute(
      'aria-describedby',
      'adv-wave-help',
    );
  });

  it('rides the orb ON the wave, not at a fixed height', () => {
    // The handle sits on the line; a static `top` leaves it floating beside a
    // wave that moves under it.
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const top = () => (container.querySelector('#adv-wave-orb') as HTMLElement).style.top;
    expect(top()).toMatch(/%$/);
    const before = top();
    act(() => frames.shift()!(0));
    expect(top()).not.toBe(before);
  });

  it('insets the orb so it cannot be clipped at the ends', () => {
    const { container } = render(<AdventurousnessDial {...dial({ value: 1 })} />);
    const orb = container.querySelector('#adv-wave-orb') as HTMLElement;
    // A plain `left: 100%` puts the orb half outside the card's overflow.
    expect(orb.style.left).toContain('calc(');
    expect(orb.style.left).toContain('18px');
    // The outer glow pairs with the inner white ring (99-100), and currentColor
    // drives the pulsing ring animation (98).
    expect(orb.style.boxShadow).toContain('inset 0 0 0 2px');
    expect(orb.style.color).not.toBe('');
  });
});

// ── The download bar ─────────────────────────────────────────────────────────

describe('the download bar', () => {
  const state = (over: DownloadState = {}): DownloadState => ({
    p1: { name: 'Winter Mix', status: 'in_progress', imageUrl: null } as never,
    ...over,
  });

  it('hides with the class the vanilla toggles, rather than unmounting', () => {
    // The stylesheet owns the slide-out transition; unmounting skips it, and
    // the header's live count has nowhere to live between downloads.
    const { container } = render(<DownloadBar state={{}} onOpen={vi.fn()} />);
    const sidebar = container.querySelector('#discover-download-sidebar')!;
    expect(sidebar).toHaveClass('hidden');
    expect(container.querySelector('#discover-download-count')!.textContent).toBe('0');
  });

  it('shows itself and counts once something is downloading', () => {
    const { container } = render(<DownloadBar state={state()} onOpen={vi.fn()} />);
    expect(container.querySelector('#discover-download-sidebar')).not.toHaveClass('hidden');
    expect(container.querySelector('#discover-download-count')!.textContent).toBe('1');
  });

  it('shows a bubble per download, with an in-progress icon', () => {
    const { container } = render(<DownloadBar state={state()} onOpen={vi.fn()} />);
    expect(container.querySelectorAll('.discover-download-bubble')).toHaveLength(1);
    expect(container.querySelector('.discover-download-bubble-icon')!.textContent).toBe('⏳');
    expect(screen.getByText('Winter Mix')).toBeInTheDocument();
  });

  it('marks a completed download differently', () => {
    const { container } = render(
      <DownloadBar
        state={{ p1: { name: 'Winter Mix', status: 'completed', imageUrl: null } as never }}
        onOpen={vi.fn()}
      />,
    );
    expect(container.querySelector('.discover-download-bubble-card')).toHaveClass('completed');
    expect(container.querySelector('.discover-download-bubble-icon')!.textContent).toBe('✅');
  });

  it('opens a download by its playlist id', () => {
    const onOpen = vi.fn();
    const { container } = render(<DownloadBar state={state()} onOpen={onOpen} />);
    fireEvent.click(container.querySelector('.discover-download-bubble-card')!);
    expect(onOpen).toHaveBeenCalledWith('p1');
  });

  it('converts the vanilla CSS declaration into a React style object', () => {
    // `bubbleBackground` returns a string built for a style="…" attribute.
    // React ignores an unparsed string silently, so every bubble would render
    // bare with nothing in the console.
    expect(declarationToStyle("background-image: url('/a.jpg');")).toEqual({
      backgroundImage: "url('/a.jpg')",
    });
    // The gradient's own colons must survive the split.
    // A value containing its OWN colon must survive: splitting on the first
    // colon and keeping only the next piece silently truncates the url.
    expect(declarationToStyle("background-image: url('https://x/a.jpg');")).toEqual({
      backgroundImage: "url('https://x/a.jpg')",
    });
    expect(
      declarationToStyle('background: linear-gradient(135deg, rgba(29,185,84,0.3) 0%);'),
    ).toEqual({ background: 'linear-gradient(135deg, rgba(29,185,84,0.3) 0%)' });
    expect(declarationToStyle('nonsense')).toEqual({});
  });

  it('paints the cover onto the image layer', () => {
    const { container } = render(
      <DownloadBar
        state={{ p1: { name: 'Mix', status: 'in_progress', imageUrl: '/a.jpg' } as never }}
        onOpen={vi.fn()}
      />,
    );
    const layer = container.querySelector('.discover-download-bubble-image') as HTMLElement;
    expect(layer.style.backgroundImage).toContain('/a.jpg');
  });

  it('keeps the playlist id as a data hook', () => {
    const { container } = render(<DownloadBar state={state()} onOpen={vi.fn()} />);
    expect(container.querySelector('.discover-download-bubble-card')).toHaveAttribute(
      'data-playlist-id',
      'p1',
    );
  });
});

// ── Build a Playlist ─────────────────────────────────────────────────────────

describe('build a playlist', () => {
  const seed = (id: string, name: string): SeedArtist => ({ id, name }) as SeedArtist;

  function bp(over: Partial<BuildPlaylistSectionProps> = {}): BuildPlaylistSectionProps {
    return {
      query: '',
      results: [],
      selected: [],
      hasResults: false,
      onQueryChange: vi.fn(),
      onAdd: vi.fn(),
      onRemove: vi.fn(),
      onGenerate: vi.fn(),
      onDownload: vi.fn(),
      onSync: vi.fn(),
      onToggleInfo: vi.fn(),
      loaded: true,
      ...over,
    };
  }

  it('wraps itself in the section header, with the info toggle INSIDE the title', () => {
    // index.html 5004-5010: the "?" sits inside the h2, and the "How it works"
    // panel toggles `visible` between the header and the container.
    const p = bp();
    const { container } = render(<BuildPlaylistSection {...p} />);
    expect(screen.getByText('Build a Playlist')).toBeInTheDocument();
    expect(
      screen.getByText('Create a custom playlist from your favorite artists'),
    ).toBeInTheDocument();
    const panel = container.querySelector('#bp-info-panel')!;
    expect(panel).not.toHaveClass('visible');
    fireEvent.click(container.querySelector('.bp-info-toggle')!);
    expect(p.onToggleInfo).toHaveBeenCalled();
  });

  it('opens the info panel with the vanilla class and its full copy', () => {
    const { container } = render(<BuildPlaylistSection {...bp({ infoOpen: true })} />);
    const panel = container.querySelector('#bp-info-panel')!;
    expect(panel).toHaveClass('visible');
    expect(panel.querySelectorAll('.bp-info-content ol li')).toHaveLength(3);
    expect(screen.getByText(/more varied the playlist will be/)).toBeInTheDocument();
  });

  it('keeps the ids and classes the vanilla styling and handlers target', () => {
    // The first draft invented almost all of these, which type-checked and
    // would have rendered an unstyled column the vanilla could not find.
    const { container } = render(<BuildPlaylistSection {...bp()} />);
    for (const sel of [
      '.build-playlist-container',
      '.build-playlist-search-section',
      '.bp-search-input-wrapper',
      '#build-playlist-search',
      '#build-playlist-search-results.build-playlist-search-results',
      '.build-playlist-selected-section',
      '.bp-selected-header',
      '#bp-selected-counter.bp-selected-counter',
      '#build-playlist-selected-artists.build-playlist-selected-artists',
      '.build-playlist-actions',
      '#build-playlist-generate-btn.build-playlist-generate-btn',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });

  it('hints at what to do with nothing selected, and cannot generate', () => {
    const { container } = render(<BuildPlaylistSection {...bp()} />);
    expect(container.querySelector('.build-playlist-no-selection')).not.toBeNull();
    expect(screen.getByText('Search above to add seed artists')).toBeInTheDocument();
    expect(screen.getByText('Generate Playlist')).toBeDisabled();
    expect(container.querySelector('#bp-selected-counter')!.textContent).toBe('0 / 5');
  });

  it('lists the seeds and can generate from one', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')] })} />,
    );
    expect(container.querySelectorAll('.build-playlist-selected-artist')).toHaveLength(1);
    expect(container.querySelector('.build-playlist-no-selection')).toBeNull();
    expect(screen.getByText('Generate Playlist')).not.toBeDisabled();
    expect(container.querySelector('#bp-selected-counter')!.textContent).toBe('1 / 5');
  });

  it('shows the search spinner only while searching', () => {
    const { container, rerender } = render(<BuildPlaylistSection {...bp()} />);
    expect(container.querySelector('#bp-search-spinner')).toBeNull();
    rerender(<BuildPlaylistSection {...bp({ searching: true })} />);
    expect(container.querySelector('#bp-search-spinner')).not.toBeNull();
  });

  it('renders search results as the vanilla row, with its Add affordance', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ results: [seed('1', 'Aphex')] })} />,
    );
    const row = container.querySelector('.build-playlist-search-result')!;
    expect(row.querySelector('.bp-result-name')!.textContent).toBe('Aphex');
    expect(row.querySelector('.bp-result-add')!.textContent).toBe('+ Add');
  });

  it('locks generate and shows the loader while building', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')], generating: true })} />,
    );
    expect(screen.getByText('Generate Playlist')).toBeDisabled();
    expect(container.querySelector('#build-playlist-loading')).not.toBeNull();
  });

  it('adds, removes and generates', () => {
    const p = bp({ results: [seed('1', 'Aphex')], selected: [seed('2', 'BoC')] });
    render(<BuildPlaylistSection {...p} />);
    fireEvent.click(screen.getByText('Aphex'));
    fireEvent.click(screen.getByLabelText('Remove BoC'));
    fireEvent.click(screen.getByText('Generate Playlist'));
    expect(p.onAdd).toHaveBeenCalledWith(seed('1', 'Aphex'));
    expect(p.onRemove).toHaveBeenCalledWith('2');
    expect(p.onGenerate).toHaveBeenCalled();
  });

  it('shows the results wrapper only once there is a playlist', () => {
    const { container, rerender } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')] })} />,
    );
    expect(container.querySelector('#build-playlist-results-wrapper')).toBeNull();

    rerender(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')], hasResults: true })} />,
    );
    for (const sel of [
      '#build-playlist-results-wrapper',
      '#build-playlist-results-title',
      '#build-playlist-results-subtitle',
      '#build-playlist-sync-btn',
      '#build-playlist-metadata-display',
      '#build-playlist-results.discover-playlist-container.compact',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });

  it("shows the sync panel with THIS section's ids while syncing", () => {
    const { container } = render(
      <BuildPlaylistSection
        {...bp({
          selected: [seed('1', 'Aphex')],
          hasResults: true,
          syncing: true,
          syncProgress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
        })}
      />,
    );
    expect(container.querySelector('#build-playlist-sync-status')).not.toBeNull();
    expect(container.querySelector('#build-playlist-sync-percentage')!.textContent).toBe('50');
  });

  it('hides the sync panel when nothing is syncing', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')], hasResults: true })} />,
    );
    expect(container.querySelector('#build-playlist-sync-status')).toBeNull();
  });

  it('downloads and syncs the generated playlist', () => {
    const p = bp({ selected: [seed('1', 'Aphex')], hasResults: true });
    render(<BuildPlaylistSection {...p} />);
    fireEvent.click(screen.getByTitle('Download missing tracks'));
    fireEvent.click(screen.getByTitle('Sync to media server'));
    expect(p.onDownload).toHaveBeenCalled();
    expect(p.onSync).toHaveBeenCalled();
  });
});
