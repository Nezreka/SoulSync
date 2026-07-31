import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SeedArtist } from '../-discover.build-playlist';
import type { DownloadState } from '../-discover.download-bar';
import type { BuildPlaylistSectionProps } from './build-playlist';

import { AdventurousnessDial } from './adventurousness-dial';
import { BuildPlaylistSection } from './build-playlist';
import { DownloadBar } from './download-bar';

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
    // jsdom leaves offsetParent null; the loop's visibility guard reads it.
    vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body);
  });

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

  it('draws a wave path and an area beneath it', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const line = container.querySelector('.adv-wave-line')!.getAttribute('d')!;
    const area = container.querySelector('.adv-wave-area')!.getAttribute('d')!;
    expect(line.startsWith('M ')).toBe(true);
    // The area is the line closed down to the baseline — same shape, plus a lid.
    expect(area.startsWith(line)).toBe(true);
    expect(area.endsWith('Z')).toBe(true);
  });

  it('advances the wave on each frame', () => {
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const before = container.querySelector('.adv-wave-line')!.getAttribute('d');
    act(() => frames.shift()!(0));
    expect(container.querySelector('.adv-wave-line')!.getAttribute('d')).not.toBe(before);
  });

  it('computes NOTHING while the page is off screen', () => {
    // The rAF keeps ticking; a background tab must not rebuild a 91-point path
    // sixty times a second.
    vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(null);
    const { container } = render(<AdventurousnessDial {...dial()} />);
    const before = container.querySelector('.adv-wave-line')!.getAttribute('d');
    act(() => frames.shift()!(0));
    expect(container.querySelector('.adv-wave-line')!.getAttribute('d')).toBe(before);
  });

  it('cancels the loop on unmount', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame');
    const { unmount } = render(<AdventurousnessDial {...dial()} />);
    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it('reports a value live while dragging and once on release', () => {
    const p = dial();
    const { container } = render(<AdventurousnessDial {...p} />);
    const track = container.querySelector('#adv-wave-track')!;
    vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      width: 200,
    } as DOMRect);

    fireEvent.mouseDown(track, { clientX: 100 });
    expect(p.onChange).toHaveBeenLastCalledWith(0.5);

    fireEvent.mouseMove(window, { clientX: 150 });
    expect(p.onChange).toHaveBeenLastCalledWith(0.75);
    expect(p.onCommit).not.toHaveBeenCalled();

    fireEvent.mouseUp(window, { clientX: 150 });
    expect(p.onCommit).toHaveBeenCalledWith(0.75);
  });

  it('ignores the pointer once the drag has ended', () => {
    const p = dial();
    const { container } = render(<AdventurousnessDial {...p} />);
    const track = container.querySelector('#adv-wave-track')!;
    vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      width: 200,
    } as DOMRect);
    fireEvent.mouseDown(track, { clientX: 20 });
    fireEvent.mouseUp(window, { clientX: 20 });
    (p.onChange as ReturnType<typeof vi.fn>).mockClear();
    fireEvent.mouseMove(window, { clientX: 180 });
    expect(p.onChange).not.toHaveBeenCalled();
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
  });
});

// ── The download bar ─────────────────────────────────────────────────────────

describe('the download bar', () => {
  const state = (over: DownloadState = {}): DownloadState => ({
    p1: { name: 'Winter Mix', status: 'in_progress', imageUrl: null } as never,
    ...over,
  });

  it('is absent with nothing downloading', () => {
    // A transient overlay, not permanent chrome — an empty one covers content
    // for nothing.
    const { container } = render(<DownloadBar state={{}} onOpen={vi.fn()} />);
    expect(container.querySelector('#discover-download-bar')).toBeNull();
  });

  it('shows a bubble per download, with an in-progress icon', () => {
    const { container } = render(<DownloadBar state={state()} onOpen={vi.fn()} />);
    expect(container.querySelectorAll('.discover-download-bubble')).toHaveLength(1);
    expect(container.querySelector('.discover-download-icon')!.textContent).toBe('⏳');
    expect(screen.getByText('Winter Mix')).toBeInTheDocument();
  });

  it('marks a completed download differently', () => {
    const { container } = render(
      <DownloadBar
        state={{ p1: { name: 'Winter Mix', status: 'completed', imageUrl: null } as never }}
        onOpen={vi.fn()}
      />,
    );
    expect(container.querySelector('.discover-download-bubble')).toHaveClass('completed');
    expect(container.querySelector('.discover-download-icon')!.textContent).toBe('✅');
  });

  it('opens a download by its playlist id', () => {
    const onOpen = vi.fn();
    const { container } = render(<DownloadBar state={state()} onOpen={onOpen} />);
    fireEvent.click(container.querySelector('.discover-download-bubble')!);
    expect(onOpen).toHaveBeenCalledWith('p1');
  });

  it('keeps the playlist id as a data hook', () => {
    const { container } = render(<DownloadBar state={state()} onOpen={vi.fn()} />);
    expect(container.querySelector('.discover-download-bubble')).toHaveAttribute(
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
      dropdownOpen: false,
      selected: [],
      loaded: true,
      onQueryChange: vi.fn(),
      onAdd: vi.fn(),
      onRemove: vi.fn(),
      onGenerate: vi.fn(),
      onDownload: vi.fn(),
      ...over,
    };
  }

  it('hints at what to do with nothing selected, and cannot generate', () => {
    render(<BuildPlaylistSection {...bp()} />);
    expect(screen.getByText('Search above to add seed artists')).toBeInTheDocument();
    expect(screen.getByText('Generate')).toBeDisabled();
    expect(screen.getByText('0 / 5')).toBeInTheDocument();
  });

  it('lists the seeds and can generate from one', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')] })} />,
    );
    expect(container.querySelectorAll('.bp-seed')).toHaveLength(1);
    expect(screen.getByText('Generate')).not.toBeDisabled();
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
  });

  it('closes the search box at the cap', () => {
    // The generator takes five; offering a sixth would be a promise it drops.
    const selected = Array.from({ length: 5 }, (_, i) => seed(String(i), `A${i}`));
    const { container } = render(<BuildPlaylistSection {...bp({ selected })} />);
    expect(container.querySelector('#build-playlist-input')).toBeDisabled();
    expect(screen.getByText('5 / 5')).toBeInTheDocument();
  });

  it('locks input and generate while a playlist is building', () => {
    const { container } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')], generating: true })} />,
    );
    expect(container.querySelector('#build-playlist-input')).toBeDisabled();
    expect(screen.getByText('Generate')).toBeDisabled();
  });

  it('shows the dropdown only with results to show', () => {
    const { container, rerender } = render(
      <BuildPlaylistSection {...bp({ dropdownOpen: true, results: [] })} />,
    );
    expect(container.querySelector('#build-playlist-dropdown')).toBeNull();
    rerender(
      <BuildPlaylistSection {...bp({ dropdownOpen: true, results: [seed('1', 'Aphex')] })} />,
    );
    expect(container.querySelector('#build-playlist-dropdown')).not.toBeNull();
  });

  it('adds, removes and generates', () => {
    const p = bp({
      dropdownOpen: true,
      results: [seed('1', 'Aphex')],
      selected: [seed('2', 'BoC')],
    });
    render(<BuildPlaylistSection {...p} />);
    fireEvent.click(screen.getByText('Aphex'));
    fireEvent.click(screen.getByLabelText('Remove BoC'));
    fireEvent.click(screen.getByText('Generate'));
    expect(p.onAdd).toHaveBeenCalledWith(seed('1', 'Aphex'));
    expect(p.onRemove).toHaveBeenCalledWith('2');
    expect(p.onGenerate).toHaveBeenCalled();
  });

  it('shows the result panel only once there are tracks', () => {
    const { container, rerender } = render(
      <BuildPlaylistSection {...bp({ selected: [seed('1', 'Aphex')], trackCount: 0 })} />,
    );
    expect(container.querySelector('#build-playlist-result')).toBeNull();

    rerender(
      <BuildPlaylistSection
        {...bp({
          selected: [seed('1', 'Aphex')],
          trackCount: 40,
          metadata: { total_tracks: 40, similar_artists_count: 12, albums_count: 25 } as never,
        })}
      />,
    );
    expect(screen.getByText('Custom Playlist')).toBeInTheDocument();
    expect(container.querySelectorAll('.bp-stat')).toHaveLength(3);
    expect(screen.getByText('Similar Artists')).toBeInTheDocument();
  });

  it('downloads the generated playlist', () => {
    const p = bp({ selected: [seed('1', 'Aphex')], trackCount: 40 });
    render(<BuildPlaylistSection {...p} />);
    fireEvent.click(screen.getByText('Download playlist'));
    expect(p.onDownload).toHaveBeenCalled();
  });
});
