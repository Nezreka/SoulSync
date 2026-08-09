/**
 * The Browse-by-Genre modal.
 *
 * The four grid states are the point. Two of them — "the API sent nothing" and
 * "the request failed" — look almost identical on screen and are reached
 * differently, and a third ("the filter removed everything") looks empty but is
 * NOT retryable. Getting those confused shows a Retry button that cannot help,
 * or hides one that could.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  areGenreImagesLoaded,
  isGenreImageLoadingActive,
  resetGenreBrowserCache,
} from '../-beatport.genres';
import { GenreBrowserModal } from './genre-browser-modal';

function stubApi(routes: Record<string, unknown>, options: { ok?: boolean } = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      const key = Object.keys(routes).find((prefix) => url.startsWith(prefix));
      return new Response(JSON.stringify(key ? routes[key] : { success: false }), {
        status: options.ok === false ? 503 : 200,
      });
    }),
  );
  return calls;
}

const GENRES_URL = '/api/beatport/genres';
const IMAGE_URL = '/api/beatport/genre-image';

function makeGenres(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    name: `Genre ${i}`,
    slug: `g${i}`,
    id: i,
    url: `http://g${i}`,
  }));
}

/**
 * Three, which is UNDER the >5 image threshold — so these tests start no image
 * workers at all. That is deliberate: a background loader still delivering
 * after a test's last assertion updates React state outside act(), which is
 * noise at best and a masked failure at worst. Only the tests that are ABOUT
 * images use a list long enough to trigger them, and those wait for it to
 * drain.
 */
function fewGenres() {
  return makeGenres(3);
}

/** Six, so the >5 image threshold IS met. */
function sixGenres() {
  return makeGenres(6);
}

beforeEach(() => {
  resetGenreBrowserCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetGenreBrowserCache();
  delete window.showToast;
  document.body.style.overflow = '';
});

/**
 * The shell tests are about open/close, not about loading, so they hold the
 * genre request open forever. A load that resolves after the last assertion
 * updates React state outside act() — noise that hides real warnings.
 */
function stubPendingGenres() {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise(() => {})),
  );
}

describe('the modal shell', () => {
  it('renders nothing at all when closed', () => {
    const { container } = render(
      <GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('carries the active class, without which the overlay is display:none', () => {
    stubPendingGenres();
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    // style.css 33146-33163: the base rule hides it and `.active` shows it, so
    // rendering the overlay without the class renders an invisible modal.
    expect(document.querySelector('.genre-browser-modal-overlay.active')).not.toBeNull();
  });

  it('locks the page scroll while open and restores it after', () => {
    stubPendingGenres();
    const view = render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    expect(document.body.style.overflow).toBe('hidden');
    view.rerender(<GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    expect(document.body.style.overflow).toBe('');
  });

  it('closes on the overlay, on the × and on Escape — but not on the body', async () => {
    stubPendingGenres();
    const onClose = vi.fn();
    render(<GenreBrowserModal open onClose={onClose} onSelectGenre={vi.fn()} />);

    // 2264-2268 compares event.target to the overlay itself, so a click that
    // merely bubbles up from the container must not dismiss.
    fireEvent.click(document.querySelector('.genre-browser-modal-container') as Element);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector('.genre-browser-modal-overlay') as Element);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(document.querySelector('.genre-browser-modal-close') as Element);
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('stops listening for Escape once closed', () => {
    stubPendingGenres();
    const onClose = vi.fn();
    const view = render(<GenreBrowserModal open onClose={onClose} onSelectGenre={vi.fn()} />);
    view.rerender(<GenreBrowserModal open={false} onClose={onClose} onSelectGenre={vi.fn()} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('the four grid states', () => {
  it('shows the spinner copy while loading', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    expect(screen.getByText('🔍 Discovering current Beatport genres...')).toBeInTheDocument();
    expect(document.querySelector('.genre-browser-loading-spinner')).not.toBeNull();
  });

  it('renders a card per genre and toasts the count', async () => {
    stubApi({ [GENRES_URL]: { genres: fewGenres() } });
    const toasts: [string, string | undefined][] = [];
    window.showToast = (message, type) => toasts.push([message, type]);

    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));
    expect(toasts).toContainEqual(['Loaded 3 genres for browsing', 'success']);
  });

  it('offers RETRY when the API sent nothing', async () => {
    stubApi({ [GENRES_URL]: { genres: [] } });
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('⚠️ No genres available')).toBeInTheDocument());
    expect(screen.getByText('🔄 Retry')).toBeInTheDocument();
  });

  it('does NOT offer retry when the filter removed everything', async () => {
    // The other empty state. It is not retryable — the API answered, and it
    // answered with section headings. A Retry button here would do nothing.
    stubApi({ [GENRES_URL]: { genres: [{ name: 'Charts', slug: 'charts', id: 1 }] } });
    const toasts: string[] = [];
    window.showToast = (message) => toasts.push(message);

    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(toasts).toContain('Loaded 0 genres for browsing'));
    expect(screen.queryByText('🔄 Retry')).not.toBeInTheDocument();
    expect(screen.queryByText('⚠️ No genres available')).not.toBeInTheDocument();
    expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(0);
  });

  it('shows the failure message with the status line, and toasts it', async () => {
    stubApi({ [GENRES_URL]: {} }, { ok: false });
    const toasts: [string, string | undefined][] = [];
    window.showToast = (message, type) => toasts.push([message, type]);

    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Failed to load genres/)).toBeInTheDocument());
    // 2362-2364: this endpoint alone reports response.ok, and the status
    // reaches the user.
    expect(screen.getByText(/503/)).toBeInTheDocument();
    expect(toasts[0][1]).toBe('error');
  });

  it('retries the load when Retry is pressed', async () => {
    const calls = stubApi({ [GENRES_URL]: { genres: [] } });
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('🔄 Retry')).toBeInTheDocument());

    stubApi({ [GENRES_URL]: { genres: fewGenres() } });
    fireEvent.click(screen.getByText('🔄 Retry'));
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));
    expect(calls).toHaveLength(1);
  });
});

describe('the cards', () => {
  it('start on the fallback class and DROP it when a picture lands', async () => {
    stubApi({
      [GENRES_URL]: { genres: sixGenres() },
      [`${IMAGE_URL}/g0/0`]: { success: true, image_url: 'http://g0.jpg' },
    });
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(6));

    // The fallback class is what styles the emoji tile, so it is bound to the
    // image's absence rather than set once at render (2519, 2569).
    await waitFor(() =>
      expect(document.querySelector('[data-genre-slug="g0"] img')?.getAttribute('src')).toBe(
        'http://g0.jpg',
      ),
    );
    expect(
      document
        .querySelector('[data-genre-slug="g0"]')
        ?.classList.contains('genre-browser-card-fallback'),
    ).toBe(false);
    expect(
      document
        .querySelector('[data-genre-slug="g1"]')
        ?.classList.contains('genre-browser-card-fallback'),
    ).toBe(true);
    expect(
      document.querySelector('[data-genre-slug="g1"] .genre-browser-card-image')?.textContent,
    ).toBe('🎵');
    // Let the two workers drain before the test ends, so no result lands after
    // the last assertion — an update outside act() is noise at best and a
    // masked failure at worst.
    await waitFor(() => expect(areGenreImagesLoaded()).toBe(true));
  });

  it('does not fetch images at all for five genres or fewer', async () => {
    const calls = stubApi({
      [GENRES_URL]: { genres: makeGenres(5) },
    });
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(5));
    // 2433 is a strict `>`; five genres keep their emoji.
    expect(calls.filter((url) => url.startsWith(IMAGE_URL))).toHaveLength(0);
  });

  it('hands the whole genre to the selection callback', async () => {
    stubApi({ [GENRES_URL]: { genres: fewGenres() } });
    const onSelectGenre = vi.fn();
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={onSelectGenre} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));

    fireEvent.click(document.querySelector('[data-genre-slug="g0"]') as Element);
    // The slug, the id AND the name — the genre page needs all three, and the
    // vanilla reads them back off the dataset.
    expect(onSelectGenre).toHaveBeenCalledWith(
      expect.objectContaining({ slug: 'g0', id: 0, name: 'Genre 0' }),
    );
  });
});

describe('the search box', () => {
  it('filters by name as you type', async () => {
    stubApi({
      [GENRES_URL]: {
        genres: [
          { name: 'Tech House', slug: 'tech-house', id: 1 },
          { name: 'Techno', slug: 'techno', id: 2 },
          { name: 'Drum & Bass', slug: 'dnb', id: 3 },
        ],
      },
    });
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));

    fireEvent.change(screen.getByPlaceholderText('Search genres...'), {
      target: { value: 'tech' },
    });
    expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(2);
    expect(screen.queryByText('Drum & Bass')).not.toBeInTheDocument();
  });

  it('is cleared by a close, though the genres are not', async () => {
    stubApi({ [GENRES_URL]: { genres: fewGenres() } });
    const view = render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));
    fireEvent.change(screen.getByPlaceholderText('Search genres...'), {
      target: { value: 'Genre 1' },
    });
    expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(1);

    view.rerender(<GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    view.rerender(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    // 2319-2323: the filter resets, the data does not.
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));
    expect((screen.getByPlaceholderText('Search genres...') as HTMLInputElement).value).toBe('');
  });
});

describe('closing while the pictures are still arriving', () => {
  /** Holds each genre-image request open until the test releases it. */
  function gatedImages() {
    const pending: ((body: unknown) => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (!url.startsWith(IMAGE_URL)) {
          return Promise.resolve(
            new Response(JSON.stringify({ genres: sixGenres() }), { status: 200 }),
          );
        }
        return new Promise<Response>((resolve) => {
          pending.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 })));
        });
      }),
    );
    return pending;
  }

  it('PAUSES the workers rather than letting them run on', async () => {
    const pending = gatedImages();
    const view = render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(6));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(isGenreImageLoadingActive()).toBe(true);

    view.rerender(<GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    // 2327-2330. Without this the two workers keep scraping Beatport for a
    // modal nobody is looking at, one request every 100ms.
    expect(isGenreImageLoadingActive()).toBe(false);

    for (const resolve of pending) resolve({ success: false });
    await waitFor(() => expect(areGenreImagesLoaded()).toBe(false));
    // Paused, NOT complete — so reopening resumes rather than assuming there
    // is nothing left to fetch.
    expect(pending).toHaveLength(2);
  });
});

describe('reopening', () => {
  it('shows the cached genres INSTANTLY and does not re-request them', async () => {
    const calls = stubApi({ [GENRES_URL]: { genres: fewGenres() } });
    const view = render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() => expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3));
    const before = calls.filter((url) => url === GENRES_URL).length;

    view.rerender(<GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    view.unmount();
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);

    // 2298: no spinner and no request — the list is already known.
    expect(document.querySelectorAll('.genre-browser-card')).toHaveLength(3);
    expect(screen.queryByText('🔍 Discovering current Beatport genres...')).not.toBeInTheDocument();
    expect(calls.filter((url) => url === GENRES_URL)).toHaveLength(before);
  });

  it('keeps the pictures it already had', async () => {
    stubApi({
      [GENRES_URL]: { genres: sixGenres() },
      [`${IMAGE_URL}/g0/0`]: { success: true, image_url: 'http://g0.jpg' },
    });
    const view = render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    await waitFor(() =>
      expect(document.querySelector('[data-genre-slug="g0"] img')).not.toBeNull(),
    );
    await waitFor(() => expect(areGenreImagesLoaded()).toBe(true));

    view.rerender(<GenreBrowserModal open={false} onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    view.unmount();
    render(<GenreBrowserModal open onClose={vi.fn()} onSelectGenre={vi.fn()} />);
    // Reopening must not put the emoji back while it re-fetches.
    expect(document.querySelector('[data-genre-slug="g0"] img')?.getAttribute('src')).toBe(
      'http://g0.jpg',
    );
  });
});

describe('the modal class names', () => {
  it('all exist in the vanilla stylesheet', () => {
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    const required = [
      'genre-browser-modal-overlay',
      'genre-browser-modal-container',
      'genre-browser-modal-header',
      'genre-browser-modal-title',
      'genre-browser-modal-close',
      'genre-browser-close-icon',
      'genre-browser-modal-content',
      'genre-browser-search-section',
      'genre-browser-search-container',
      'genre-browser-search-input',
      'genre-browser-search-icon',
      'genre-browser-genres-section',
      'genre-browser-genres-grid',
      'genre-browser-loading-container',
      'genre-browser-loading-spinner',
      'genre-browser-loading-text',
      'genre-browser-card',
      'genre-browser-card-fallback',
      'genre-browser-card-image',
      'genre-browser-card-content',
      'genre-browser-card-title',
      'genre-browser-card-subtitle',
    ];
    for (const className of required) {
      expect(
        new RegExp(`\\.${className}[\\s,:{.[]`).test(css),
        `.${className} is not in static/style.css`,
      ).toBe(true);
    }
  });
});
