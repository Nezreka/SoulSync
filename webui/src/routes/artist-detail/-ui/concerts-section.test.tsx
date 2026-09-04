import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ConcertsSection,
  formatEventDate,
  formatSetlistDate,
  placeLabel,
} from './concerts-section';

/**
 * Live dates and setlists on the artist page.
 *
 * The setlist half is the one that earns its place in a music app: a setlist is
 * a playlist somebody already made and tested on a live audience, so it gets a
 * Play button that resolves against your library first.
 *
 * The rule these tests mostly hold up: both providers are optional and
 * independent. A section that appears empty on every artist page, or that
 * disappears because the half you did not configure is missing, is worse than
 * no section.
 */

const SETLIST = {
  id: 'sl1',
  date: '14-08-2026',
  venue: 'Berghain',
  city: 'Berlin',
  country: 'Germany',
  songs: ['Xtal', 'Ageispolis', 'Windowlicker'],
  song_count: 3,
};

const EVENT = {
  id: 'e1',
  datetime: '2026-09-14T20:00:00',
  venue: 'Printworks',
  city: 'London',
  country: 'UK',
  url: 'https://ticketmaster.com/e/1',
  tickets_url: 'https://tix.example/1',
};

function stub(payload: unknown, extra?: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      const body = url.includes('/check-tracks') ? extra || { success: false } : payload;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

const CONFIGURED = {
  ticketmaster: { configured: true },
  setlistfm: { configured: true },
};

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.playTrackList;
  delete window.showToast;
});

describe('formatting', () => {
  it('reads setlist.fm dd-MM-yyyy rather than letting Date guess', () => {
    // Every parser reads 14-08-2026 as either nonsense or August in the wrong
    // slot, so the parts are reordered by hand.
    expect(formatSetlistDate('14-08-2026')).toContain('2026');
    expect(formatSetlistDate('14-08-2026')).toContain('Aug');
    expect(formatSetlistDate('nonsense')).toBe('nonsense');
    expect(formatSetlistDate(undefined)).toBe('');
  });

  it('leaves an unparseable event date alone instead of showing Invalid Date', () => {
    expect(formatEventDate('not-a-date')).toBe('not-a-date');
  });

  it('joins a place without leaving a stray comma for a missing half', () => {
    expect(placeLabel(['Berghain', 'Berlin'])).toBe('Berghain, Berlin');
    expect(placeLabel([undefined, 'Berlin'])).toBe('Berlin');
    expect(placeLabel(['Berghain', '  '])).toBe('Berghain');
    expect(placeLabel([undefined, undefined])).toBe('');
  });
});

describe('when nothing is set up', () => {
  it('renders nothing at all rather than an empty Live heading', async () => {
    // A permanent empty section is an advert for a feature the user declined.
    stub({ providers: { ticketmaster: { configured: false }, setlistfm: { configured: false } } });
    const { container } = render(<ConcertsSection artistName="Aphex Twin" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.querySelector('.artist-concerts-section')).toBeNull();
  });

  it('renders nothing when configured but the artist simply has no shows', async () => {
    stub({ providers: CONFIGURED, upcoming: [], setlists: [] });
    const { container } = render(<ConcertsSection artistName="Aphex Twin" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.querySelector('.artist-concerts-section')).toBeNull();
  });
});

describe('one provider is enough', () => {
  it('shows setlists when only setlist.fm answered', async () => {
    stub({
      providers: { ticketmaster: { configured: false }, setlistfm: { configured: true } },
      upcoming: [],
      setlists: [SETLIST],
    });
    render(<ConcertsSection artistName="Aphex Twin" />);
    await screen.findByText('Recent setlists');
    expect(screen.queryByText('Upcoming')).toBeNull();
  });

  it('shows upcoming dates when only ticketmaster answered', async () => {
    stub({
      providers: { ticketmaster: { configured: true }, setlistfm: { configured: false } },
      upcoming: [EVENT],
      setlists: [],
    });
    render(<ConcertsSection artistName="Aphex Twin" />);
    await screen.findByText('Upcoming');
    expect(screen.queryByText('Recent setlists')).toBeNull();
  });
});

describe('upcoming dates', () => {
  it('links to tickets when there are any, details otherwise', async () => {
    stub({ providers: CONFIGURED, upcoming: [EVENT, { ...EVENT, id: 'e2', tickets_url: '' }] });
    render(<ConcertsSection artistName="Aphex Twin" />);

    const tickets = await screen.findByText('Tickets');
    expect(tickets.getAttribute('href')).toBe('https://tix.example/1');
    expect(screen.getByText('Details').getAttribute('href')).toBe('https://ticketmaster.com/e/1');
  });

  it('never opens an external link with access to this page', async () => {
    stub({ providers: CONFIGURED, upcoming: [EVENT] });
    render(<ConcertsSection artistName="Aphex Twin" />);
    const link = await screen.findByText('Tickets');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(link.getAttribute('target')).toBe('_blank');
  });
});

describe('a setlist', () => {
  it('stays collapsed until asked, then lists the songs in order', async () => {
    stub({ providers: CONFIGURED, setlists: [SETLIST] });
    render(<ConcertsSection artistName="Aphex Twin" />);

    await screen.findByText('3 songs');
    expect(screen.queryByText('Windowlicker')).toBeNull();

    fireEvent.click(screen.getByText('3 songs'));
    const songs = screen.getAllByRole('listitem').filter((li) => li.tagName === 'LI');
    expect(songs.some((li) => li.textContent === 'Xtal')).toBe(true);
    expect(screen.getByText('Windowlicker')).toBeTruthy();
  });

  it('plays the songs you own from that show', async () => {
    window.playTrackList = vi.fn();
    window.showToast = vi.fn();
    stub(
      { providers: CONFIGURED, setlists: [SETLIST] },
      {
        success: true,
        owned_tracks: {
          Xtal: { owned: true, file_path: '/m/xtal.flac' },
          Ageispolis: { owned: true, file_path: '/m/ageis.flac' },
        },
      },
    );
    render(<ConcertsSection artistName="Aphex Twin" />);

    fireEvent.click(await screen.findByTitle(/Play the songs from this show/));
    await waitFor(() => expect(window.playTrackList).toHaveBeenCalled());

    const [queue, context] = (window.playTrackList as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(queue.map((t: { file_path: string }) => t.file_path))
      .toEqual(['/m/xtal.flac', '/m/ageis.flac']);
    expect(context).toContain('Berghain');
    // and says what it could not play, rather than quietly playing a short set
    expect(window.showToast).toHaveBeenCalledWith(
      expect.stringContaining('2 of 3'),
      'info',
    );
  });

  it('says so plainly when you own none of it', async () => {
    window.playTrackList = vi.fn();
    window.showToast = vi.fn();
    stub({ providers: CONFIGURED, setlists: [SETLIST] }, { success: true, owned_tracks: {} });
    render(<ConcertsSection artistName="Aphex Twin" />);

    fireEvent.click(await screen.findByTitle(/Play the songs from this show/));
    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(window.showToast).toHaveBeenCalledWith(
      expect.stringContaining("don't own any"),
      'info',
    );
    expect(window.playTrackList).not.toHaveBeenCalled();
  });
});
