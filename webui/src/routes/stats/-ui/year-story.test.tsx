import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { YearInListening } from '../-year.types';

const FULL_YEAR: YearInListening = {
  period: { start: '2025-09-01', end: '2026-08-14', label: 'Sep 2025 — Aug 2026', months: 12 },
  has_data: true,
  totals: { plays: 1247, minutes: 4320, artists: 88, albums: 210, tracks: 640, active_days: 233 },
  months: [
    { month: '2025-09', label: 'Sep 2025', plays: 0, minutes: 0, top_artist: null },
    { month: '2025-10', label: 'Oct 2025', plays: 140, minutes: 500, top_artist: 'Autumn Band' },
    { month: '2026-01', label: 'Jan 2026', plays: 300, minutes: 1100, top_artist: 'Winter Band' },
  ],
  top_artists: [
    { name: 'Winter Band', plays: 410, months_on_top: 5, id: '11', image_url: '/img/winter.jpg' },
    { name: 'Autumn Band', plays: 190, months_on_top: 2, id: '12', image_url: '/img/autumn.jpg' },
  ],
  top_albums: [{ name: 'Cold', artist: 'Winter Band', plays: 120, image_url: '/img/cold.jpg' }],
  top_tracks: [
    {
      name: 'The One Song',
      artist: 'Winter Band',
      album: 'Cold',
      plays: 61,
      first_played: '2025-10-04 09:00:00',
      last_played: '2026-07-30 22:00:00',
      artist_id: '11',
      image_url: '/img/cold.jpg',
    },
  ],
  discoveries: [
    { name: 'Brand New Act', first_played: '2026-02-01', plays: 34, id: '99', image_url: '/img/new.jpg' },
  ],
  peak_day: { date: '2026-05-20', plays: 47 },
  top_hour: { hour: 22, plays: 300 },
};

function serveYear(year: Partial<YearInListening> & { success?: boolean } = {}) {
  server.use(
    http.get('/api/stats/year', () => HttpResponse.json({ success: true, ...FULL_YEAR, ...year })),
  );
}

/**
 * Mounted through the REAL router at `?story=year`, not as a bare component.
 *
 * Two things only this proves: that the URL alone opens the story (the whole
 * reason the open state lives in search params), and that the artist links
 * resolve against a route that actually exists — a bare render cannot render
 * a <Link> at all.
 */
function renderStory(initialEntries = ['/stats?story=year']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

async function advanceTo(label: string) {
  const next = await screen.findByRole('button', { name: 'Next' });
  for (let i = 0; i < 16; i += 1) {
    if (screen.queryByText(label)) return;
    fireEvent.click(next);
  }
  throw new Error(`never reached the slide showing "${label}"`);
}

describe('YearStory', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    server.use(
      http.get('/api/stats/cached', () => HttpResponse.json({ success: true })),
      http.get('/api/listening-stats/status', () => HttpResponse.json({ success: true })),
      http.get('/api/stats/db-storage', () => HttpResponse.json({ success: true, tables: [] })),
      http.get('/api/stats/library-disk-usage', () => HttpResponse.json({ success: true })),
    );
    serveYear();
  });

  it('opens from the URL alone', async () => {
    renderStory();

    expect(await screen.findByRole('dialog', { name: 'Your Year in Listening' })).toBeInTheDocument();
  });

  it('stays shut when the param is absent', async () => {
    renderStory(['/stats']);

    await screen.findByTestId('stats-page');

    expect(screen.queryByRole('dialog', { name: 'Your Year in Listening' })).not.toBeInTheDocument();
  });

  it('opens on the period, so the reader knows what window this is', async () => {
    renderStory();

    expect(await screen.findByText('Sep 2025 — Aug 2026')).toBeInTheDocument();
    expect(screen.getByText('Your Year in Listening')).toBeInTheDocument();
  });

  it('walks the whole story forward and lands on the card', async () => {
    renderStory();
    await screen.findByText('Your Year in Listening');

    // 10 slides for this payload: opening, totals, months, top-artist,
    // countdown, top-albums, top-track, discoveries, when, card.
    expect(screen.getByText('1 / 10')).toBeInTheDocument();

    await advanceTo('That was your year');

    expect(screen.getByRole('button', { name: 'Save image' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('shows the headline numbers rather than raw milliseconds', async () => {
    renderStory();
    await screen.findByText('Your Year in Listening');
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }));

    // The visible text counts UP, so the settled value is asserted through
    // the aria-label — which is also what a screen reader gets, deliberately,
    // rather than a number that is still moving.
    expect(await screen.findByLabelText('1,247')).toBeInTheDocument();
    expect(screen.getByText('3 days')).toBeInTheDocument();
  });

  it('counts the headline up to its real value', async () => {
    renderStory();
    await screen.findByText('Your Year in Listening');
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }));

    const counter = await screen.findByLabelText('1,247');
    expect(counter).not.toHaveTextContent('1,247');   // it starts from zero

    // countUpDuration scales with magnitude — ~1.45s for a number this size,
    // which is past waitFor's 1s default.
    await waitFor(() => expect(counter).toHaveTextContent('1,247'), { timeout: 4000 });
  });

  it('names the number one artist and how much of the year they owned', async () => {
    renderStory();
    await advanceTo('Your number one');

    expect(screen.getByText('Winter Band')).toBeInTheDocument();
    expect(screen.getByText('Top artist in 5 of your 12 months.')).toBeInTheDocument();
  });

  it('collapses to a single slide that says so when there is nothing to tell', async () => {
    serveYear({ has_data: false, totals: { plays: 0, minutes: 0, artists: 0, albums: 0, tracks: 0, active_days: 0 } });
    renderStory();

    await screen.findByText('Your Year in Listening');

    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.getByText(/once SoulSync has some listening history/i)).toBeInTheDocument();
    // Never a dead "Next" that goes nowhere — the only forward action closes.
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });

  it('cannot be walked backwards past the opening', async () => {
    renderStory();
    await screen.findByText('Your Year in Listening');

    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
  });

  it('closes on Escape, and drops the param so a reload does not reopen it', async () => {
    const { history } = renderStory();
    await screen.findByText('Your Year in Listening');

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: 'Your Year in Listening' }),
      ).not.toBeInTheDocument(),
    );
    expect(history.location.search).not.toContain('story');
  });

  it('advances on the right arrow key', async () => {
    renderStory();
    await screen.findByText('Your Year in Listening');

    fireEvent.keyDown(window, { key: 'ArrowRight' });

    await waitFor(() => expect(screen.getByText('2 / 10')).toBeInTheDocument());
  });

  it('surfaces a failure instead of pretending the year is empty', async () => {
    server.use(
      http.get('/api/stats/year', () =>
        HttpResponse.json({ success: false, error: 'listening history unavailable' }),
      ),
    );
    renderStory();

    expect(await screen.findByText('listening history unavailable')).toBeInTheDocument();
  });

  it('restores page scrolling when it unmounts', async () => {
    const { unmount } = renderStory();
    await screen.findByText('Your Year in Listening');
    expect(document.body.style.overflow).toBe('hidden');

    unmount();

    expect(document.body.style.overflow).not.toBe('hidden');
  });

  // ── artwork and links ──────────────────────────────────────────────────

  it('shows the number one artist their own portrait', async () => {
    renderStory();
    await advanceTo('Your number one');

    const portrait = document.querySelector('img[src="/img/winter.jpg"]');
    expect(portrait).toBeTruthy();
  });

  it('makes every discovery a link into artist detail', async () => {
    // The whole ask: a discovery you cannot click is a dead end.
    renderStory();
    await advanceTo('You found them this year');

    const link = screen.getByRole('link', { name: /Brand New Act/ });
    expect(link).toHaveAttribute('href', '/artist-detail/library/99');
  });

  it('gives discoveries their artwork too', async () => {
    renderStory();
    await advanceTo('You found them this year');

    expect(document.querySelector('img[src="/img/new.jpg"]')).toBeTruthy();
  });

  it('renders a row with no artwork as a shape, not a hole', async () => {
    // A patchy library must not produce a ragged grid.
    serveYear({
      discoveries: [{ name: 'Artless Act', first_played: '2026-02-01', plays: 4, id: '77' }],
    });
    renderStory();
    await advanceTo('You found them this year');

    expect(screen.getByRole('link', { name: /Artless Act/ })).toBeInTheDocument();
    expect(screen.getByText('A', { selector: 'div' })).toBeInTheDocument();
  });

  it('does not link an artist it could not resolve an id for', async () => {
    // Linking to /artist-detail/library/undefined would 404 the user out of
    // their own story.
    serveYear({
      discoveries: [{ name: 'Unresolved', first_played: '2026-02-01', plays: 4 }],
    });
    renderStory();
    await advanceTo('You found them this year');

    expect(screen.queryByRole('link', { name: /Unresolved/ })).not.toBeInTheDocument();
    expect(screen.getByText('Unresolved')).toBeInTheDocument();
  });

  it('shows the album wall with its covers', async () => {
    renderStory();
    await advanceTo('The albums you lived in');

    expect(screen.getByText('Cold')).toBeInTheDocument();
    expect(document.querySelector('img[src="/img/cold.jpg"]')).toBeTruthy();
  });

  // ── playback ───────────────────────────────────────────────────────────

  it('plays the album when its card is clicked', async () => {
    const playTrackList = vi.fn();
    window.playTrackList = playTrackList;
    server.use(
      http.get('/api/stats/album-tracks/55', () =>
        HttpResponse.json({
          success: true,
          tracks: [{ id: 1, title: 'Track One', file_path: '/m/1.flac' }],
        }),
      ),
    );
    serveYear({
      top_albums: [
        { name: 'Cold', artist: 'Winter Band', plays: 120, id: '55', image_url: '/img/cold.jpg' },
      ],
    });
    renderStory();
    await advanceTo('The albums you lived in');

    fireEvent.click(screen.getByRole('button', { name: 'Play Cold' }));

    await waitFor(() =>
      expect(playTrackList).toHaveBeenCalledWith(
        [{ id: 1, title: 'Track One', file_path: '/m/1.flac' }],
        'Cold',
      ),
    );
  });

  it('says so rather than silently doing nothing when nothing is owned', async () => {
    const showToast = vi.fn();
    window.showToast = showToast;
    window.playTrackList = vi.fn();
    server.use(
      http.get('/api/stats/album-tracks/55', () =>
        HttpResponse.json({ success: true, tracks: [] }),
      ),
    );
    serveYear({
      top_albums: [{ name: 'Cold', artist: 'Winter Band', plays: 120, id: '55' }],
    });
    renderStory();
    await advanceTo('The albums you lived in');

    fireEvent.click(screen.getByRole('button', { name: 'Play Cold' }));

    await waitFor(() =>
      expect(showToast).toHaveBeenCalledWith('No owned tracks for Cold yet', 'info'),
    );
    expect(window.playTrackList).not.toHaveBeenCalled();
  });

  it('does not offer play on an album it could not resolve', async () => {
    // A play control that does nothing is worse than no control.
    serveYear({
      top_albums: [{ name: 'Unmatched', artist: 'Someone', plays: 9 }],
    });
    renderStory();
    await advanceTo('The albums you lived in');

    expect(screen.queryByRole('button', { name: /Play Unmatched/ })).not.toBeInTheDocument();
    expect(screen.getByText('Unmatched')).toBeInTheDocument();
  });

  // ── the card studio ────────────────────────────────────────────────────

  it('offers all four layouts, three sizes and four themes', async () => {
    // A studio, not a template — this is the whole difference from Spotify's
    // one fixed composition.
    renderStory();
    await advanceTo('That was your year');

    for (const layout of ['Stack', 'Poster', 'Mosaic', 'Minimal']) {
      expect(screen.getByRole('button', { name: new RegExp(layout) })).toBeInTheDocument();
    }
    // Exact names: /Post/ would also match the "Poster" LAYOUT chip.
    for (const size of ['Post 4:5', 'Square 1:1', 'Story 9:16']) {
      expect(screen.getByRole('button', { name: size })).toBeInTheDocument();
    }
    for (const theme of ['Midnight', 'Ink', 'Sunset', 'Paper']) {
      expect(screen.getByRole('button', { name: theme })).toBeInTheDocument();
    }
  });

  it('resizes the canvas when a different aspect is picked', async () => {
    renderStory();
    await advanceTo('That was your year');
    const canvas = screen.getByLabelText('Your year card preview') as HTMLCanvasElement;
    expect(canvas.height).toBe(1350);

    fireEvent.click(screen.getByRole('button', { name: 'Story 9:16' }));

    await waitFor(() => expect(canvas.height).toBe(1920));
  });

  it('lets the user choose which numbers appear', async () => {
    renderStory();
    await advanceTo('That was your year');
    const albums = screen.getByRole('button', { name: 'Albums' });
    expect(albums).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(albums);

    await waitFor(() => expect(albums).toHaveAttribute('aria-pressed', 'true'));
  });

  it('will not let the card be emptied of numbers', async () => {
    renderStory();
    await advanceTo('That was your year');

    // Turn off all four defaults; the last one must refuse.
    for (const label of ['Plays', 'Listening time', 'Artists', 'Days with music']) {
      fireEvent.click(screen.getByRole('button', { name: label }));
    }

    const pressed = ['Plays', 'Listening time', 'Artists', 'Days with music'].filter(
      (label) => screen.getByRole('button', { name: label }).getAttribute('aria-pressed') === 'true',
    );
    expect(pressed).toHaveLength(1);
  });

  it('marks the chosen layout as pressed', async () => {
    renderStory();
    await advanceTo('That was your year');

    fireEvent.click(screen.getByRole('button', { name: /Mosaic/ }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Mosaic/ })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    );
    expect(screen.getByRole('button', { name: /Stack/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('offers copy as well as save', async () => {
    // On desktop the real share gesture is paste, not "find the downloaded
    // file and drag it somewhere".
    renderStory();
    await advanceTo('That was your year');

    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument();
  });
});
