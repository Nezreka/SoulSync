import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ByltSection } from '../-discover.bylt';

import { ByltSections } from './bylt-sections';

/**
 * Because You Listen To.
 *
 * The shelf used to be ten album-sized tiles with one whole-card click that
 * resolved an ALBUM from name strings. On the reported data that painted ten
 * copies of one album's artwork under two nearly identical headings, and a
 * click did something other than what the card showed.
 *
 * These pin the replacement: a list that exposes album and duration, named
 * native controls with accessible names, a truthful reason, missing tracks
 * said out loud, and no shelf-height card for a shelf with nothing in it.
 */

afterEach(cleanup);

const section = (over: Partial<ByltSection> = {}): ByltSection => ({
  seed_key: 'deezer:111',
  artist_name: 'Aphex Twin',
  artist_image: '/img/aphex.jpg',
  reason: { kind: 'direct', label: 'Artists similar to Aphex Twin' },
  presentation: 'full',
  requested: 2,
  resolved: 2,
  unavailable: 0,
  tracks: [
    {
      id: '1',
      name: 'Xtal',
      artist: 'Aphex Twin',
      album: 'Selected Ambient Works',
      image_url: '/img/xtal.jpg',
      duration_ms: 296000,
      relation: 'direct',
      relation_detail: 'Aphex Twin',
    },
    { id: '2', name: 'Tha', artist: 'Aphex Twin', album: 'Selected Ambient Works' },
  ],
  ...over,
});

describe('Because You Listen To', () => {
  it('renders one shelf per seed artist inside the container', () => {
    const { container } = render(
      <ByltSections
        sections={[section(), section({ seed_key: 'deezer:222', artist_name: 'Autechre' })]}
      />,
    );
    expect(container.querySelector('#discover-bylt-sections')).not.toBeNull();
    expect(container.querySelectorAll('.discover-section.bylt-section')).toHaveLength(2);
    expect(container.querySelector('#bylt-carousel-0')).not.toBeNull();
    expect(container.querySelector('#bylt-carousel-1')).not.toBeNull();
  });

  it('renders NOTHING with no sections — not an empty-state box', () => {
    const { container } = render(<ByltSections sections={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('puts the eyebrow above an h3 title, with the reason under it', () => {
    const { container } = render(<ByltSections sections={[section()]} />);
    const header = container.querySelector('.bylt-header > div')!;
    expect(header.children[0]).toHaveClass('discover-section-subtitle');
    expect(header.children[0].textContent).toBe('Because you listen to');
    expect(header.children[1].tagName).toBe('H3');
    expect(header.children[1].textContent).toBe('Aphex Twin');
    expect(header.children[2].textContent).toBe('Artists similar to Aphex Twin');
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

  it('shows album and duration on every row, not ten identical squares', () => {
    const { container } = render(<ByltSections sections={[section()]} />);
    const first = container.querySelectorAll('.bylt-track')[0];
    expect(first.querySelector('.bylt-track-name')!.textContent).toBe('Xtal');
    expect(first.textContent).toContain('Selected Ambient Works');
    expect(first.querySelector('.bylt-track-duration')!.textContent).toBe('4:56');
  });

  it('leaves an unknown duration EMPTY rather than claiming 0:00', () => {
    const { container } = render(<ByltSections sections={[section()]} />);
    const rows = container.querySelectorAll('.bylt-track');
    expect(rows[1].querySelector('.bylt-track-duration')!.textContent).toBe('');
  });

  it('keeps a long title discoverable through its title attribute', () => {
    const long = 'A'.repeat(120);
    const { container } = render(
      <ByltSections sections={[section({ tracks: [{ id: '1', name: long, artist: 'X' }] })]} />,
    );
    expect(container.querySelector('.bylt-track-name')).toHaveAttribute('title', long);
  });

  it('gives every action a real button with an accessible name', () => {
    const onPlayTrack = vi.fn();
    const onDownloadTrack = vi.fn();
    const onOpenAlbum = vi.fn();
    render(
      <ByltSections
        sections={[section()]}
        onPlayTrack={onPlayTrack}
        onDownloadTrack={onDownloadTrack}
        onOpenAlbum={onOpenAlbum}
      />,
    );
    fireEvent.click(screen.getByLabelText('Play Xtal by Aphex Twin'));
    fireEvent.click(screen.getByLabelText('Download Xtal by Aphex Twin'));
    fireEvent.click(screen.getAllByLabelText('Open the album Selected Ambient Works')[0]);
    expect(onPlayTrack).toHaveBeenCalledTimes(1);
    expect(onDownloadTrack).toHaveBeenCalledTimes(1);
    expect(onOpenAlbum).toHaveBeenCalledTimes(1);
  });

  it('never makes the row itself clickable, and never nests a button', () => {
    // "clicking a track has an unambiguous result": you do not click a track,
    // you click a labelled action.
    const { container } = render(
      <ByltSections sections={[section()]} onPlayTrack={vi.fn()} onOpenAlbum={vi.fn()} />,
    );
    const row = container.querySelector('.bylt-track') as HTMLElement;
    expect(row.tagName).toBe('LI');
    expect(row.onclick).toBeNull();
    expect(row.querySelector('button button')).toBeNull();
  });

  it('labels an owned track owned and offers no download for it', () => {
    render(
      <ByltSections
        sections={[
          section({
            tracks: [{ id: '1', name: 'Xtal', artist: 'Aphex Twin', owned: true }],
          }),
        ]}
        onDownloadTrack={vi.fn()}
      />,
    );
    expect(screen.getByText('In library')).toBeInTheDocument();
    expect(screen.queryByLabelText('Download Xtal by Aphex Twin')).toBeNull();
  });

  it('states why a track is here when it knows, and nothing when it does not', () => {
    const { container } = render(
      <ByltSections
        sections={[
          section({
            tracks: [
              { id: '1', name: 'A', artist: 'X', relation: 'genre', relation_detail: 'shoegaze' },
              { id: '2', name: 'B', artist: 'Y' },
            ],
          }),
        ]}
      />,
    );
    const whys = container.querySelectorAll('.bylt-track-why');
    expect(whys).toHaveLength(1);
    expect(whys[0].textContent).toBe('Shares shoegaze');
  });

  it('says what went missing instead of silently rendering fewer rows', () => {
    render(<ByltSections sections={[section({ requested: 10, resolved: 2, unavailable: 8 })]} />);
    expect(screen.getByText('8 of 10 are no longer available')).toBeInTheDocument();
  });

  it('shows a truthful module for a shelf with nothing in it', () => {
    // never a heading over one card consuming a full shelf's height
    const { container } = render(
      <ByltSections sections={[section({ presentation: 'insufficient', tracks: [] })]} />,
    );
    expect(container.querySelector('.bylt-track-list')).toBeNull();
    expect(screen.getByText(/Not enough to recommend from Aphex Twin/)).toBeInTheDocument();
  });

  it('survives a section with NO tracks array, costing only that shelf', () => {
    const { container } = render(
      <ByltSections
        sections={[
          section({ tracks: undefined }),
          section({ seed_key: 'deezer:222', artist_name: 'Autechre' }),
        ]}
      />,
    );
    expect(container.querySelector('#bylt-carousel-1')!.children).toHaveLength(2);
    expect(screen.getByText('Autechre')).toBeInTheDocument();
  });

  it('shows a per-row pending state and a per-row failure', () => {
    render(
      <ByltSections
        sections={[section()]}
        onPlayTrack={vi.fn()}
        pendingKey="deezer:111:1"
        errors={{ 'deezer:111:2': 'Could not play that track' }}
      />,
    );
    expect(screen.getByLabelText('Play Xtal by Aphex Twin')).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not play that track');
  });

  it('reports a stale set rather than looking like an empty one', () => {
    render(<ByltSections sections={[]} statusNote="Showing your last good set…" />);
    expect(screen.getByRole('status')).toHaveTextContent('Showing your last good set…');
  });

  it('says when the listening history is shared across profiles', () => {
    render(
      <ByltSections
        sections={[section()]}
        historyNote="Listening history is shared across profiles on this install."
      />,
    );
    expect(screen.getByText(/shared across profiles/)).toBeInTheDocument();
  });

  it('offers shelf-level play and download only when a shelf has tracks', () => {
    const onPlayShelf = vi.fn();
    const onDownloadShelf = vi.fn();
    const { rerender } = render(
      <ByltSections
        sections={[section()]}
        onPlayShelf={onPlayShelf}
        onDownloadShelf={onDownloadShelf}
      />,
    );
    fireEvent.click(screen.getByText('Download shelf'));
    expect(onDownloadShelf).toHaveBeenCalledWith(
      expect.objectContaining({ seed_key: 'deezer:111' }),
    );

    rerender(
      <ByltSections
        sections={[section({ tracks: [] })]}
        onPlayShelf={onPlayShelf}
        onDownloadShelf={onDownloadShelf}
      />,
    );
    expect(screen.queryByText('Download shelf')).toBeNull();
  });

  it('keys a shelf by seed identity so a rank change cannot swap two shelves', () => {
    const a = section({ seed_key: 'deezer:111', artist_name: 'Aphex Twin' });
    const b = section({ seed_key: 'deezer:222', artist_name: 'Autechre' });
    const { container, rerender } = render(<ByltSections sections={[a, b]} />);
    const first = container.querySelectorAll('.bylt-section')[0];
    rerender(<ByltSections sections={[b, a]} />);
    // the same DOM node must not be reused for a different seed
    expect(container.querySelectorAll('.bylt-section')[0]).not.toBe(first);
  });
});
