import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AlbumPoster,
  ArtistPoster,
  ConcertPoster,
  PosterRow,
  postersLine,
  useOnScreen,
} from './poster-row';

const DAY = { month: 'FEB', day: '19', weekday: 'FRI' };

describe('ConcertPoster', () => {
  it('is a gig poster: the date big, where, tickets, and how many more shows', () => {
    const { container } = render(
      <ConcertPoster
        artist="Calvin Harris"
        day={DAY}
        venue="Showgrounds"
        city="Bowen Hills"
        url="https://tickets.example/1"
        more={3}
        photo="/ch.jpg"
      />,
    );
    expect(
      screen.getByRole('article', { name: 'Live: Calvin Harris, FEB 19' }),
    ).toBeInTheDocument();
    expect(container.querySelector('.dsc-poster-day')?.textContent).toBe('19');
    expect(screen.getByText('FRI · Showgrounds, Bowen Hills')).toBeInTheDocument();
    const tickets = screen.getByText('Get tickets');
    expect(tickets).toHaveAttribute('href', 'https://tickets.example/1');
    expect(tickets).toHaveAttribute('target', '_blank');
    expect(screen.getByText('+3 more shows')).toBeInTheDocument();
    expect(
      (container.querySelector('.dsc-poster-photo') as HTMLElement).style.backgroundImage,
    ).toContain('/ch.jpg');
    // the colour comes from the name, and the type suits it
    const poster = container.querySelector('.dsc-poster') as HTMLElement;
    expect(poster.style.getPropertyValue('--poster-rgb')).toMatch(/^\d+, \d+, \d+$/);
    expect(poster.className).toMatch(/ink-(dark|light)/);
  });

  it('no link, no photo, one more show: still a poster', () => {
    const { container } = render(<ConcertPoster artist="A" day={DAY} more={1} />);
    expect(screen.queryByText('Get tickets')).toBeNull();
    expect(screen.getByText('+1 more show')).toBeInTheDocument();
    expect(container.querySelector('.dsc-poster-photo')).toBeNull();
    // no face to show: the date takes the space
    expect(container.querySelector('.dsc-poster')).toHaveClass('dsc-poster--typeset');
  });
});

describe('AlbumPoster', () => {
  it('the cover on the sleeve and the record, and one action', () => {
    const onOpen = vi.fn();
    const { container } = render(
      <AlbumPoster
        title="Blue"
        artist="Joni"
        tag="New album"
        art="/b.jpg"
        openLabel="Open album"
        onOpen={onOpen}
      />,
    );
    expect(
      (container.querySelector('.dsc-poster-vinyl-label') as HTMLElement).style.backgroundImage,
    ).toContain('/b.jpg');
    fireEvent.click(screen.getByText('Open album'));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});

describe('ArtistPoster', () => {
  it('your number one: the plays, the name behind them, a way in', () => {
    const { container } = render(
      <ArtistPoster
        name="Oliver Tree"
        plays={1265}
        photo="/o.jpg"
        href="/artist-detail/library/1"
      />,
    );
    expect(screen.getByText('1,265')).toBeInTheDocument();
    expect(container.querySelectorAll('.dsc-poster-type-row')).toHaveLength(4);
    expect(screen.getByText('View artist')).toHaveAttribute('href', '/artist-detail/library/1');
  });

  it('no link, no button', () => {
    render(<ArtistPoster name="X" plays={2} />);
    expect(screen.queryByText('View artist')).toBeNull();
  });
});

describe('postersLine', () => {
  it('says what is there', () => {
    expect(postersLine(['concert', 'album', 'artist'])).toBe(
      'A show near you, a new release and who you played most this week.',
    );
    expect(postersLine(['album'])).toBe('A new release.');
    expect(postersLine([])).toBe('');
  });
});

describe('PosterRow', () => {
  it('lays out the posters that exist and hides when there are none', () => {
    const { container, rerender } = render(
      <PosterRow>{[null, <ArtistPoster key="artist" name="X" plays={2} />, null]}</PosterRow>,
    );
    expect(container.querySelector('.dsc-posters-grid')).toHaveClass('dsc-posters-grid--1');
    expect(screen.getByText('Who you played most this week.')).toBeInTheDocument();
    rerender(<PosterRow>{[null, null]}</PosterRow>);
    expect(container.querySelector('.dsc-posters')).toBeNull();
  });
});

describe('useOnScreen', () => {
  it('is on without an observer, and follows one when there is', () => {
    expect(renderHook(() => useOnScreen<HTMLDivElement>()).result.current[1]).toBe(true);
    let fire: (hit: boolean) => void = () => {};
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
          fire = (hit) => cb([{ isIntersecting: hit }]);
        }
        observe() {}
        disconnect() {}
      },
    );
    try {
      const { result } = renderHook(() => useOnScreen<HTMLDivElement>());
      act(() => result.current[0](document.createElement('div')));
      act(() => fire(false));
      expect(result.current[1]).toBe(false);
      act(() => fire(true));
      expect(result.current[1]).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
