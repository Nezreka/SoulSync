import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { ByltSection } from '../-discover.bylt';

import { ByltSections } from './bylt-sections';

/**
 * Because You Listen To.
 *
 * The cases that matter are the deliberate absences: no click handler on the
 * cards, no empty-state box, no image placeholder in the header — plus the
 * field names, because these tracks use `name`/`artist` where every other card
 * on the page uses `artist_name`, and a shared type would quietly blank the
 * second line.
 */

afterEach(cleanup);

const section = (over: Partial<ByltSection> = {}): ByltSection => ({
  artist_name: 'Aphex Twin',
  artist_image: '/img/aphex.jpg',
  tracks: [
    { name: 'Xtal', artist: 'Aphex Twin', image_url: '/img/xtal.jpg' },
    { name: 'Tha', artist: 'Aphex Twin' },
  ],
  ...over,
});

describe('Because You Listen To', () => {
  it('renders one shelf per seed artist inside the container', () => {
    const { container } = render(
      <ByltSections sections={[section(), section({ artist_name: 'Autechre' })]} />,
    );
    expect(container.querySelector('#discover-bylt-sections')).not.toBeNull();
    const shelves = container.querySelectorAll('.discover-section.bylt-section');
    expect(shelves).toHaveLength(2);
    // Each shelf's grid id is by INDEX (10377).
    expect(container.querySelector('#bylt-carousel-0')).not.toBeNull();
    expect(container.querySelector('#bylt-carousel-1')).not.toBeNull();
  });

  it('renders NOTHING with no sections — not an empty-state box', () => {
    // renderEmptyState: false (10429): one of the few sections that opts out.
    const { container } = render(<ByltSections sections={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('puts the eyebrow above an h3 title', () => {
    // These shelves are sub-sections; an h2 would make each seed artist a peer
    // of the page's real sections.
    const { container } = render(<ByltSections sections={[section()]} />);
    const header = container.querySelector('.bylt-header > div')!;
    expect(header.children[0]).toHaveClass('discover-section-subtitle');
    expect(header.children[0].textContent).toBe('Because you listen to');
    expect(header.children[1].tagName).toBe('H3');
    expect(header.children[1].textContent).toBe('Aphex Twin');
  });

  it('omits the header image entirely when absent, and hides a broken one', () => {
    const { container, rerender } = render(
      <ByltSections sections={[section({ artist_image: undefined })]} />,
    );
    expect(container.querySelector('.bylt-artist-img')).toBeNull();

    rerender(<ByltSections sections={[section()]} />);
    const img = container.querySelector('.bylt-artist-img')!;
    expect(img).toHaveAttribute('src', '/img/aphex.jpg');
    fireEvent.error(img);
    expect(container.querySelector('.bylt-artist-img')).toBeNull();
  });

  it('reads name and artist — NOT the artist_name family', () => {
    const { container } = render(<ByltSections sections={[section()]} />);
    const card = container.querySelector('.ya-card.discover-album-card')!;
    expect(card.querySelector('.ya-card-name')!.textContent).toBe('Xtal');
    expect(card.querySelector('.ya-card-sub')!.textContent).toBe('Aphex Twin');
  });

  it('shows the placeholder only for artless tracks, and after a dead image', () => {
    const { container } = render(<ByltSections sections={[section()]} />);
    const placeholders = () =>
      [...container.querySelectorAll('.ya-card-placeholder')] as HTMLElement[];
    expect(placeholders()[0].style.display).toBe('none'); //  has art
    expect(placeholders()[1].style.display).toBe('flex'); //  artless

    fireEvent.error(container.querySelector('.ya-card-img img')!);
    expect(placeholders()[0].style.display).toBe('flex');
    expect((container.querySelector('.ya-card-img img') as HTMLElement).style.display).toBe('none');
  });

  it('gives the cards NO click behaviour', () => {
    // The vanilla card has no onclick (10386-10399); display only. Inventing
    // navigation here would be new behaviour, not a port.
    const { container } = render(<ByltSections sections={[section()]} />);
    const card = container.querySelector('.ya-card.discover-album-card') as HTMLElement;
    expect(card.onclick).toBeNull();
    expect(card.querySelector('a')).toBeNull();
    expect(card.querySelector('button')).toBeNull();
  });

  it('survives a section with NO tracks array, costing only that shelf', () => {
    // The vanilla maps section.tracks unguarded (10438) — one bad payload
    // aborts the loop and takes every LATER shelf's cards with it. The port
    // guards: the shelves are independent.
    const { container } = render(
      <ByltSections
        sections={[section({ tracks: undefined }), section({ artist_name: 'Autechre' })]}
      />,
    );
    expect(container.querySelector('#bylt-carousel-0')!.children).toHaveLength(0);
    expect(container.querySelector('#bylt-carousel-1')!.children).toHaveLength(2);
    expect(screen.getByText('Autechre')).toBeInTheDocument();
  });
});
