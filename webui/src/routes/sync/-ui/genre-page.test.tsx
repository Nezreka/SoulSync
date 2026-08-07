/**
 * The genre page shell and its hero slider.
 *
 * The hero is the main hero's twin — same classes, same shape — so the
 * assertions worth having are the places it is NOT: relative urls, a different
 * artist field, and a third line that shows the label rather than a fixed
 * caption.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { resetBeatportModalLatch } from '../-beatport.downloads';
import { GenrePage } from './genre-page';

const GENRE = { name: 'Tech House', slug: 'tech-house', id: 11 };
const HERO_URL = '/api/beatport/genre/tech-house/11/hero';

function makeEnv(): BeatportDownloadEnv {
  return {
    showToast: vi.fn(),
    showLoadingOverlay: vi.fn(),
    hideLoadingOverlay: vi.fn(),
    setOverlayMessage: vi.fn(),
    openDownloadModal: vi.fn(),
    registerDownload: vi.fn(),
    now: () => 1700000000000,
    random: () => 0.5,
    schedule: vi.fn(),
    sleep: async () => {},
  };
}

function stubApi(routes: Record<string, unknown>, options: { ok?: boolean } = {}) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const key = Object.keys(routes).find((prefix) => url.startsWith(prefix));
      return new Response(JSON.stringify(key ? routes[key] : { success: false }), {
        status: options.ok === false ? 503 : 200,
      });
    }),
  );
  return calls;
}

const RELEASE = {
  title: 'Nights',
  artists_string: 'Frank Ocean, Guest',
  label: 'Blonded',
  // RELATIVE — this endpoint alone returns paths rather than links.
  url: '/release/nights/1234',
  image_url: 'http://art.jpg',
};

beforeEach(() => {
  resetBeatportModalLatch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetBeatportModalLatch();
  delete window.showToast;
});

describe('the genre page shell', () => {
  it('shows the genre name and a working Back button', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    const onBack = vi.fn();
    render(<GenrePage genre={GENRE} onBack={onBack} env={makeEnv()} />);
    expect(document.querySelector('.genre-page-title')?.textContent).toBe('Tech House');
    fireEvent.click(screen.getByText(/Back to Genres/));
    expect(onBack).toHaveBeenCalled();
  });

  it('carries the Top 100 button', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    expect(document.getElementById('genre-top100-btn')).not.toBeNull();
  });

  it('names the genre in its loading copy', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    // 2822's block, not 2717's — the load starts at once, so the named one is
    // what a user actually sees.
    expect(screen.getByText('🎠 Loading Tech House hero releases...')).toBeInTheDocument();
  });
});

describe('the genre hero slider', () => {
  it('renders a slide per release, with the LABEL on the third line', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());

    // artists_string, not artist — the field name differs from the main hero.
    expect(document.querySelector('.beatport-rebuild-artist-name')?.textContent).toBe(
      'Frank Ocean, Guest',
    );
    // The main hero's third line is the fixed caption 'New on Beatport'.
    expect(document.querySelector('.beatport-rebuild-album-name')?.textContent).toBe('Blonded');
  });

  it("falls back to '<Genre> Hero Release' when there is no label", async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [{ ...RELEASE, label: undefined }] } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-rebuild-album-name')?.textContent).toBe(
        'Tech House Hero Release',
      ),
    );
  });

  it('ABSOLUTISES the relative url before anything uses it', async () => {
    const calls = stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());

    const slide = document.querySelector('.beatport-rebuild-slide') as HTMLElement;
    expect(slide.getAttribute('data-url')).toBe('https://www.beatport.com/release/nights/1234');

    fireEvent.click(slide);
    // And it is the absolute one that reaches the scraper — a relative path
    // would arrive with no host.
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/beatport/release-metadata')).toBe(true),
    );
    const post = calls.find((c) => c.url === '/api/beatport/release-metadata');
    expect(JSON.parse(post?.body as string)).toEqual({
      release_url: 'https://www.beatport.com/release/nights/1234',
    });
  });

  it('leaves an already-absolute url alone', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [{ ...RELEASE, url: 'https://other/x' }] },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(
        (document.querySelector('.beatport-rebuild-slide') as HTMLElement).getAttribute('data-url'),
      ).toBe('https://other/x'),
    );
  });

  it('leaves an url-less release unclickable and without a pointer', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [{ ...RELEASE, url: undefined }] },
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());

    const slide = document.querySelector('.beatport-rebuild-slide') as HTMLElement;
    expect(slide.style.cursor).toBe('');
    fireEvent.click(slide);
    expect(env.showToast).not.toHaveBeenCalled();
  });

  it('paints the artwork on the slide and marks it clickable', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());
    const slide = document.querySelector('.beatport-rebuild-slide') as HTMLElement;
    expect(slide.style.getPropertyValue('--slide-bg-image')).toBe("url('http://art.jpg')");
    expect(slide.style.cursor).toBe('pointer');
  });

  it('toasts the count with the genre name', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE, { ...RELEASE, title: 'B' }] } });
    const toasts: [string, string | undefined][] = [];
    window.showToast = (message, type) => toasts.push([message, type]);
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(toasts).toContainEqual(['Loaded 2 Tech House releases', 'success']));
  });

  it('reads its failure text from `message`, not `error`', async () => {
    // 2835. Every other loader reads `error`; copying that here would show the
    // generic fallback for every backend-reported failure.
    stubApi({ [HERO_URL]: { success: false, message: 'genre is empty', error: 'IGNORED' } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('genre is empty')).toBeInTheDocument());
    expect(screen.queryByText('IGNORED')).not.toBeInTheDocument();
    expect(screen.getByText('❌ Failed to load Tech House releases')).toBeInTheDocument();
  });

  it('reports the status line when the request itself fails', async () => {
    stubApi({ [HERO_URL]: {} }, { ok: false });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText(/503/)).toBeInTheDocument());
  });

  it('retries from the error block', async () => {
    stubApi({ [HERO_URL]: { success: false, message: 'down' } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('🔄 Retry')).toBeInTheDocument());

    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] } });
    fireEvent.click(screen.getByText('🔄 Retry'));
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());
  });

  it('re-loads when a DIFFERENT genre is shown', async () => {
    const calls = stubApi({
      '/api/beatport/genre/': { success: true, releases: [RELEASE] },
    });
    const view = render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());

    view.rerender(
      <GenrePage
        genre={{ name: 'Techno', slug: 'techno', id: 6 }}
        onBack={vi.fn()}
        env={makeEnv()}
      />,
    );
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/genre/techno/6/hero'))).toBe(true),
    );
  });
});

describe('the genre page class names', () => {
  it('all exist in the vanilla stylesheet', () => {
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    const required = [
      'genre-page-content',
      'genre-page-header',
      'genre-back-button',
      'back-icon',
      'genre-page-title',
      'genre-hero-slider-container',
      'genre-loading-container',
      'genre-loading-spinner',
      'genre-loading-text',
      'genre-error-container',
      'genre-error-text',
      'genre-error-details',
      'genre-retry-button',
      'genre-nav-buttons-section',
      'genre-nav-buttons-container',
      'beatport-nav-button',
      'beatport-nav-icon',
      'top100-icon',
      'beatport-nav-text',
    ];
    for (const className of required) {
      expect(
        new RegExp(`\\.${className}[\\s,:{.[]`).test(css),
        `.${className} is not in static/style.css`,
      ).toBe(true);
    }
  });
});
