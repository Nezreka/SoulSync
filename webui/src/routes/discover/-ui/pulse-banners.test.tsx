import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { WeekSummary } from '../-discover.pulse';

import { daysLabel, SpotlightBanner, TasteGapBanner, WeekBanner } from './pulse-banners';

const WEEK: WeekSummary = {
  plays: 2333,
  change: -55,
  artists: 361,
  streak: 8,
  topArtist: { name: 'Oliver Tree', play_count: 265, image_url: '/img/ot.jpg' },
  topGenre: 'Electronic',
  days: [
    { date: '2026-09-27', plays: 649, share: 649 / 758, label: 'S' },
    { date: '2026-09-28', plays: 758, share: 1, label: 'M' },
  ],
  topTracks: [{ name: 'MANSA MUSA', artist: 'ero808' }],
};

describe('WeekBanner', () => {
  it('says the week in plain words', () => {
    render(<WeekBanner week={WEEK} onPlayTop={() => {}} />);
    expect(screen.getByText('2,333')).toBeInTheDocument();
    expect(screen.getByText('↓ 55%')).toBeInTheDocument();
    expect(screen.getByText('361 artists · 8-day streak · mostly electronic')).toBeInTheDocument();
    expect(screen.getByText('Oliver Tree')).toBeInTheDocument();
    expect(screen.getByText('265 plays')).toBeInTheDocument();
  });

  it('draws one bar per day, the busiest full height', () => {
    const { container } = render(<WeekBanner week={WEEK} onPlayTop={() => {}} />);
    const bars = [...container.querySelectorAll<HTMLElement>('.dsc-week-bar')];
    expect(bars.map((b) => b.style.height)).toEqual(['86%', '100%']);
    expect(container.querySelector('.dsc-week-chart')).toHaveAttribute(
      'aria-label',
      daysLabel(WEEK),
    );
    expect(daysLabel(WEEK)).toBe('Plays per day: 2026-09-27 649, 2026-09-28 758');
  });

  it('plays the top tracks, and says so while it starts', () => {
    const onPlayTop = vi.fn();
    const { rerender } = render(<WeekBanner week={WEEK} onPlayTop={onPlayTop} />);
    fireEvent.click(screen.getByText('Play your top tracks'));
    expect(onPlayTop).toHaveBeenCalledTimes(1);
    rerender(<WeekBanner week={WEEK} onPlayTop={onPlayTop} playing />);
    expect(screen.getByText('Starting…').closest('button')).toBeDisabled();
  });

  it('has no play button with no tracks to play, and no change chip with no week before', () => {
    render(<WeekBanner week={{ ...WEEK, topTracks: [], change: null }} onPlayTop={() => {}} />);
    expect(screen.queryByText('Play your top tracks')).toBeNull();
    expect(document.querySelector('.dsc-week-change')).toBeNull();
    expect(screen.getByText('Your stats').closest('a')).toHaveAttribute('href', '/stats');
  });

  it('an increase reads up', () => {
    render(<WeekBanner week={{ ...WEEK, change: 12 }} onPlayTop={() => {}} />);
    expect(screen.getByText('↑ 12%')).toHaveClass('up');
  });
});

describe('TasteGapBanner', () => {
  it('names the genre and explores it', () => {
    const onExplore = vi.fn();
    render(
      <TasteGapBanner
        gap={{ genre: 'Electronic', playedPct: 16.7, ownedPct: 5.9, ratio: 3 }}
        onExplore={onExplore}
      />,
    );
    expect(screen.getByText('Electronic')).toBeInTheDocument();
    expect(screen.getByText('You play it 3× more than you collect it')).toBeInTheDocument();
    expect(screen.getByText('17%')).toBeInTheDocument();
    expect(screen.getByText('5.9%')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Explore electronic'));
    expect(onExplore).toHaveBeenCalledWith('Electronic');
  });

  it('draws both bars on one scale', () => {
    const { container } = render(
      <TasteGapBanner
        gap={{ genre: 'Electronic', playedPct: 20, ownedPct: 5, ratio: 4 }}
        onExplore={() => {}}
      />,
    );
    const [played, owned] = [...container.querySelectorAll<HTMLElement>('.dsc-gap-fill')];
    // 20 / 23 and 5 / 23 of the track
    expect(played.style.width).toBe('87%');
    expect(owned.style.width).toBe('22%');
  });
});

describe('SpotlightBanner', () => {
  it('shows the release and opens it from the cover or the button', () => {
    const onOpen = vi.fn();
    render(
      <SpotlightBanner
        spotlight={{
          album: {
            album_name: 'I Wrote You A Letter',
            artist_name: 'M83',
            album_cover_url: 'https://cdn.example/cover.jpg',
            album_type: 'album',
            release_date: '2026-09-25',
          },
          index: 1,
          plays: 3,
          reason: 'You played M83 3 times this week',
        }}
        onOpen={onOpen}
      />,
    );
    expect(screen.getByText('New album · Sep 25')).toBeInTheDocument();
    expect(screen.getByText('You played M83 3 times this week')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Open album'));
    fireEvent.click(screen.getByLabelText('Open I Wrote You A Letter'));
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
});
