import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverMix } from '../-discover.mixes';

import { decadeMix } from '../-discover.decade-shelf';
import { DiscoverMixCard, MixShelf } from './mix-shelf';

/**
 * The mix card and the shelves built from it.
 *
 * One card serves four sections, so the cases that matter are the two cover
 * paths (mosaic vs a section's own solid cover), the track count's two sources,
 * and the fact that each shelf renders only ITS OWN mixes — the registry holds
 * every section's, and rendering all of them is how the others leak in.
 */

afterEach(cleanup);

const track = (cover: string) => ({ album: { images: [{ url: cover }] }, name: 't' });

const mix = (over: Partial<DiscoverMix> = {}): DiscoverMix => ({
  key: 'mix_a',
  title: 'Your Mix',
  tracks: [track('/a.jpg'), track('/b.jpg')],
  ...over,
});

describe('the mix card', () => {
  it('puts the title on the name line and the count on the meta line', () => {
    // Which line holds which matters: swapped, both strings are still on the
    // card and it reads as a mix called "2 tracks".
    const { container } = render(<DiscoverMixCard mix={mix()} onOpen={vi.fn()} />);
    expect(container.querySelector('.mix-card-name')!.textContent).toBe('Your Mix');
    expect(container.querySelector('.mix-card-meta')!.textContent).toBe('2 tracks');
  });

  it('counts from trackCount when the tracks have not loaded', () => {
    // Lazily-loaded sections know the size before they have the rows.
    render(<DiscoverMixCard mix={mix({ tracks: undefined, trackCount: 40 })} onOpen={vi.fn()} />);
    expect(screen.getByText('40 tracks')).toBeInTheDocument();
  });

  it('always builds four mosaic tiles, padding with the placeholder', () => {
    // A ragged grid of two tiles looks broken; the pad is deliberate.
    const { container } = render(<DiscoverMixCard mix={mix()} onOpen={vi.fn()} />);
    const tiles = [...container.querySelectorAll('.mix-card-tile')] as HTMLElement[];
    expect(tiles).toHaveLength(4);
    expect(tiles[0].style.backgroundImage).toContain('/a.jpg');
    expect(tiles[1].style.backgroundImage).toContain('/b.jpg');
    expect(tiles[3].style.backgroundImage).toContain('placeholder-album.png');
  });

  it('dedupes covers before capping at four', () => {
    // A mix whose first tracks share an album still gets four DISTINCT tiles
    // when a later track differs.
    const { container } = render(
      <DiscoverMixCard
        mix={mix({
          tracks: [track('/a.jpg'), track('/a.jpg'), track('/b.jpg'), track('/c.jpg')],
        })}
        onOpen={vi.fn()}
      />,
    );
    const tiles = [...container.querySelectorAll('.mix-card-tile')] as HTMLElement[];
    const urls = tiles.map((t) => t.style.backgroundImage);
    expect(urls[0]).toContain('/a.jpg');
    expect(urls[1]).toContain('/b.jpg');
    expect(urls[2]).toContain('/c.jpg');
  });

  it("uses a section's own solid cover instead of a mosaic", () => {
    const { container } = render(
      <DiscoverMixCard
        mix={mix({ tracks: undefined, trackCount: 9, coverHtml: '<span>1990s</span>' })}
        onOpen={vi.fn()}
      />,
    );
    const cover = container.querySelector('.mix-card-cover--solid')!;
    expect(container.querySelectorAll('.mix-card-tile')).toHaveLength(0);
    // The section's own nodes stay DIRECT children of the cover (4842). The
    // wrapper is display:contents, so it doesn't lay out.
    const inner = cover.querySelector('.mix-card-cover-inner')!;
    expect(inner.children[0].tagName).toBe('SPAN');
    expect(inner.children[0].textContent).toBe('1990s');
  });

  it('keeps a play affordance on both cover kinds', () => {
    const { container, rerender } = render(
      <DiscoverMixCard mix={mix()} onOpen={vi.fn()} onPlay={vi.fn()} />,
    );
    expect(container.querySelector('.mix-card-play')).not.toBeNull();
    rerender(
      <DiscoverMixCard
        mix={mix({ coverHtml: '<span>x</span>' })}
        onOpen={vi.fn()}
        onPlay={vi.fn()}
      />,
    );
    expect(container.querySelector('.mix-card-play')).not.toBeNull();
  });

  it('opens BY KEY, and carries the key as a data hook', () => {
    const onOpen = vi.fn();
    const { container } = render(<DiscoverMixCard mix={mix()} onOpen={onOpen} />);
    const card = container.querySelector('.discover-mix-card')!;
    expect(card).toHaveAttribute('data-mix-key', 'mix_a');
    fireEvent.click(container.querySelector('.mix-card-open')!);
    // The modal resolves the mix out of the shared registry, which is keyed.
    expect(onOpen).toHaveBeenCalledWith('mix_a');
  });

  // M04: the card was a div with an onClick, so a keyboard could not reach it
  // at all, and the ▶ glyph was decoration that opened the same modal.
  it('open and play are real buttons, and play does not open', () => {
    const onOpen = vi.fn();
    const onPlay = vi.fn();
    const { container } = render(<DiscoverMixCard mix={mix()} onOpen={onOpen} onPlay={onPlay} />);
    const open = container.querySelector('.mix-card-open')!;
    const play = container.querySelector('.mix-card-play')!;
    expect(open.tagName).toBe('BUTTON');
    expect(play.tagName).toBe('BUTTON');
    expect(play).toHaveAttribute('aria-label', 'Play Your Mix');
    // Not nested: activating play must not also open the details.
    expect(open.contains(play)).toBe(false);
    fireEvent.click(play);
    expect(onPlay).toHaveBeenCalledWith('mix_a');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a resolving card disables its own play button', () => {
    const onPlay = vi.fn();
    const { container } = render(
      <DiscoverMixCard mix={mix()} onOpen={vi.fn()} onPlay={onPlay} playing />,
    );
    const play = container.querySelector('.mix-card-play') as HTMLButtonElement;
    expect(play.disabled).toBe(true);
    fireEvent.click(play);
    expect(onPlay).not.toHaveBeenCalled();
  });

  it('no play handler means no play button, not a dead one', () => {
    const { container } = render(<DiscoverMixCard mix={mix()} onOpen={vi.fn()} />);
    expect(container.querySelector('.mix-card-play')).toBeNull();
  });
});

describe('the shelf', () => {
  const shelf = (over: Partial<Parameters<typeof MixShelf>[0]> = {}) => ({
    id: 'your-mixes-section' as const,
    title: 'Your Mixes',
    mixes: [mix()],
    loaded: true,
    gridId: 'your-mixes-grid',
    onOpenMix: vi.fn(),
    ...over,
  });

  it('renders into the grid the hydration pass targets', () => {
    const { container } = render(<MixShelf {...shelf()} />);
    expect(container.querySelector('#your-mixes-grid')).not.toBeNull();
    expect(container.querySelector('.discover-mixes-grid')).not.toBeNull();
  });

  it('lets another section supply its own grid class', () => {
    const { container } = render(
      <MixShelf
        {...shelf({
          id: 'year-mixes-section',
          gridId: 'year-mixes-grid',
          gridClassName: 'discover-grid',
        })}
      />,
    );
    expect(container.querySelector('#year-mixes-grid.discover-grid')).not.toBeNull();
    expect(container.querySelector('.discover-mixes-grid')).toBeNull();
  });

  it('renders only the mixes it was given', () => {
    // The registry holds every section's mixes so the modal can resolve any
    // key; a shelf that rendered all of them would show the others' cards.
    const { container } = render(
      <MixShelf {...shelf({ mixes: [mix(), mix({ key: 'mix_b', title: 'Second' })] })} />,
    );
    expect(container.querySelectorAll('.discover-mix-card')).toHaveLength(2);
    expect(screen.getByText('Second')).toBeInTheDocument();
  });

  it('vanishes when it has no mixes', () => {
    const { container } = render(<MixShelf {...shelf({ mixes: [] })} />);
    expect(container.querySelector('.discover-section')).toBeNull();
  });

  it('renders a section header and optional actions', () => {
    render(
      <MixShelf
        {...shelf({ subtitle: 'Fresh playlists', actions: <button type="button">Go</button> })}
      />,
    );
    expect(screen.getByText('Your Mixes')).toBeInTheDocument();
    expect(screen.getByText('Fresh playlists')).toBeInTheDocument();
    expect(screen.getByText('Go')).toBeInTheDocument();
  });

  it('renders a decade the way decadeMix describes it', () => {
    // The decade shelf feeds this same card, which is why the card had to take
    // its count from trackCount rather than assuming loaded tracks.
    render(
      <MixShelf
        {...shelf({
          id: 'year-mixes-section',
          gridId: 'year-mixes-grid',
          mixes: [decadeMix({ year: 1990, track_count: 120 })],
        })}
      />,
    );
    expect(screen.getByText('1990s')).toBeInTheDocument();
    expect(screen.getByText('120 tracks')).toBeInTheDocument();
  });
});
