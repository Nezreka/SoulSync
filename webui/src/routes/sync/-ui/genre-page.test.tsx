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

  it('overrides the stylesheet: the page root is inline display:block', () => {
    // style.css ships `.genre-page-content { display: none }` as the base
    // state; the vanilla made it visible with an inline style.display
    // (2772). Lose the inline style and the WHOLE page renders invisible
    // while its data loads and toasts behind it - which jsdom cannot see,
    // so this pins the inline style itself.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    const root = document.querySelector('.genre-page-content') as HTMLElement;
    expect(root.style.display).toBe('block');
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

  it('STAYS on the genre page when the hero fails, keeping the block reachable', async () => {
    // The declared divergence. In the vanilla this loader rethrows (2862), the
    // Promise.all in handleGenreBrowserCardClick rejects, and showGenreListView
    // bounces the user to the grid — so the block it just rendered is never
    // seen and its Retry is dead. Here the page stays put.
    const onBack = vi.fn();
    stubApi({ [HERO_URL]: { success: false, message: 'down' } });
    render(<GenrePage genre={GENRE} onBack={onBack} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('🔄 Retry')).toBeInTheDocument());
    // Still on the genre page, and nothing navigated away.
    expect(document.querySelector('.genre-page-title')?.textContent).toBe('Tech House');
    expect(onBack).not.toHaveBeenCalled();
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
    // Scoped to the two HERO urls: a `/api/beatport/genre/` wildcard would also
    // feed the top-10 releases list, which would then render a second element
    // with the same title and make getByText ambiguous.
    const calls = stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      '/api/beatport/genre/techno/6/hero': { success: true, releases: [RELEASE] },
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

const LISTS_URL = '/api/beatport/genre/tech-house/11/top-10-lists';
const TRACKS_URL = '/api/beatport/genre/tech-house/11/tracks';

const LISTS = {
  success: true,
  has_hype_section: true,
  beatport_top10: [{ title: 'DeepHouseAnthem', artist: 'A', label: 'L', url: 'http://b' }],
  hype_top10: [{ title: 'H', artist: 'B', label: 'L2' }],
};

describe('the genre top-10 lists', () => {
  it('renders both lists under GENRE ids, with genre-flavoured copy', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] }, [LISTS_URL]: LISTS });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );

    expect(document.getElementById('genre-beatport-hype10-list')).not.toBeNull();
    // The ids must NOT collide with the homepage lists, which share every class.
    expect(document.getElementById('beatport-top10-list')).toBeNull();
    // 3175 keeps the casing; 3176/3184/3225 lower-case it.
    expect(screen.getByText('🏆 Tech House Top 10 Lists')).toBeInTheDocument();
    expect(screen.getByText('Current trending tech house tracks')).toBeInTheDocument();
    expect(screen.getByText('Most popular tech house tracks')).toBeInTheDocument();
    expect(screen.getByText("Editor's trending tech house picks")).toBeInTheDocument();
  });

  it('cleans the card text, as the homepage lists do', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] }, [LISTS_URL]: LISTS });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-top10-card-title')?.textContent).toBe(
        'Deep House Anthem',
      ),
    );
  });

  it('files the download under the GENRE-prefixed chart name', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: LISTS,
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'x' }] },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );

    fireEvent.click(document.getElementById('genre-beatport-top10-list') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    expect((env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0][1]).toBe(
      'Tech House Beatport Top 10',
    );
  });

  it('files the hype list under its own name', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: LISTS,
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'x' }] },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-hype10-list')).not.toBeNull(),
    );

    fireEvent.click(document.getElementById('genre-beatport-hype10-list') as Element);
    await waitFor(() => expect(env.registerDownload).toHaveBeenCalled());
    expect(env.registerDownload).toHaveBeenCalledWith(
      'Tech House Hype Top 10',
      '',
      expect.any(String),
    );
  });

  it('REMOVES the hype column outright when there is no hype section', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: { ...LISTS, has_hype_section: false },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );

    // 3259's comment is explicit: no else branch, the column is gone rather
    // than empty — and the grid collapses to one centred track (3179).
    expect(document.getElementById('genre-beatport-hype10-list')).toBeNull();
    const container = document.querySelector('.beatport-top10-container') as HTMLElement;
    expect(container.style.gridTemplateColumns).toBe('1fr');
    expect(container.style.maxWidth).toBe('700px');
  });

  it('also removes it when the flag is set but the list is empty', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: { ...LISTS, has_hype_section: true, hype_top10: [] },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );
    // 3219 requires BOTH.
    expect(document.getElementById('genre-beatport-hype10-list')).toBeNull();
  });

  it('leaves the grid alone when there IS a hype section', async () => {
    stubApi({ [HERO_URL]: { success: true, releases: [RELEASE] }, [LISTS_URL]: LISTS });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-hype10-list')).not.toBeNull(),
    );
    expect(
      (document.querySelector('.beatport-top10-container') as HTMLElement).getAttribute('style'),
    ).toBeNull();
  });

  it('renders an EMPTY beatport list rather than treating it as a failure', async () => {
    // 3137 tests `data.success` alone — no length check, exactly like the
    // homepage twin. An empty list is a list, not an error.
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: { success: true, has_hype_section: false, beatport_top10: [], hype_top10: [] },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );
    expect(screen.queryByText('❌ Error Loading Top 10 Lists')).not.toBeInTheDocument();
    expect(document.querySelector('.beatport-top10-card')).toBeNull();
  });

  it('SWALLOWS its failure and stays on the page, unlike the hero', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: { success: false, error: 'lists are down' },
    });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('❌ Error Loading Top 10 Lists')).toBeInTheDocument(),
    );
    expect(screen.getByText('Could not load Top 10 tracks for Tech House')).toBeInTheDocument();
    expect(screen.getByText('lists are down')).toBeInTheDocument();
    // The hero still rendered — the two sections fail independently.
    expect(screen.getByText('Nights')).toBeInTheDocument();
  });
});

describe('the genre Top 100 button', () => {
  it('scrapes the genre tracks endpoint and files it as "<Genre> Top 100"', async () => {
    const calls = stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: LISTS,
      [TRACKS_URL]: { success: true, tracks: [{ title: 'T' }] },
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'T+' }] },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);

    fireEvent.click(document.getElementById('genre-top100-btn') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    expect(calls.some((c) => c.url.startsWith(TRACKS_URL))).toBe(true);
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(args[1]).toBe('Tech House Top 100');
    expect(args[6]).toBe('playlist');
    expect(env.showLoadingOverlay).toHaveBeenCalledWith('Scraping Tech House Top 100...');
    // The ENRICHED tracks, not the scraped ones — the stub deliberately
    // returns a different payload so the two cannot be confused.
    expect((args[2] as { name: string }[])[0].name).toBe('T+');
  });

  it('is latched — a second press while the first is in flight is swallowed', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let scrapes = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.startsWith(TRACKS_URL)) {
          scrapes++;
          await gate;
          return new Response(JSON.stringify({ success: true, tracks: [{ title: 'T' }] }), {
            status: 200,
          });
        }
        if (url.startsWith(HERO_URL)) {
          return new Response(JSON.stringify({ success: true, releases: [RELEASE] }), {
            status: 200,
          });
        }
        return new Response(JSON.stringify({ success: true, tracks: [{ title: 'T' }] }), {
          status: 200,
        });
      }),
    );
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);

    const button = document.getElementById('genre-top100-btn') as Element;
    fireEvent.click(button);
    await waitFor(() => expect(scrapes).toBe(1));
    fireEvent.click(button);
    expect(scrapes).toBe(1);
    release();
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalledTimes(1));
  });

  it('reports an empty chart with the genre in the message', async () => {
    stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: LISTS,
      [TRACKS_URL]: { success: true, tracks: [] },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);

    fireEvent.click(document.getElementById('genre-top100-btn') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith(
        'Error loading Tech House Top 100: No tracks found in Tech House Top 100',
        'error',
      ),
    );
  });
});

const REL_URL = '/api/beatport/genre/tech-house/11/top-10-releases';
const TOP_RELEASE = {
  title: 'Blonde',
  artist: 'Frank Ocean',
  label: 'Blonded',
  url: 'http://r',
  image_url: 'https://cdn/image_size/95x95/a.jpg',
};

describe('the genre top-10 releases', () => {
  function withReleases(extra: Record<string, unknown> = {}) {
    return stubApi({
      [HERO_URL]: { success: true, releases: [RELEASE] },
      [LISTS_URL]: LISTS,
      [REL_URL]: { success: true, releases: [TOP_RELEASE] },
      ...extra,
    });
  }

  it('renders the cards under a genre-named header and its own id', async () => {
    withReleases();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-releases-top10-list')).not.toBeNull(),
    );
    expect(screen.getByText('💿 Top 10 Tech House Releases')).toBeInTheDocument();
    expect(screen.getByText('Most popular albums and EPs for Tech House')).toBeInTheDocument();
    // Must not collide with the homepage list, which shares every class.
    expect(document.getElementById('beatport-releases-top10-list')).toBeNull();
  });

  it('reuses the homepage card, upscaled background and all', async () => {
    withReleases();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );
    const card = document.querySelector('.beatport-releases-top10-card') as HTMLElement;
    expect(card.style.backgroundImage).toContain('image_size/500x500/a.jpg');
    expect(card.querySelector('img')?.getAttribute('src')).toBe(
      'https://cdn/image_size/95x95/a.jpg',
    );
    // Not cleaned, exactly as on the homepage.
    expect(document.querySelector('.beatport-releases-top10-card-title')?.textContent).toBe(
      'Blonde',
    );
  });

  it('REGISTERS the download bubble — the one line the vanilla dropped', async () => {
    withReleases({
      '/api/beatport/release-metadata': {
        success: true,
        tracks: [{ name: 'A', artists: ['X'] }],
        album: { name: 'Blonde', images: [{ url: 'http://album.jpg' }] },
        artist: { name: 'Frank Ocean' },
      },
    });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );

    fireEvent.click(document.querySelector('.beatport-releases-top10-card') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    // handleGenreReleaseCardClick is a byte-for-byte copy of the homepage
    // handler with registerBeatportDownload missing, so a genre release
    // downloads today with no progress bubble at all. Restored — see the note
    // on GenreTop10Releases.
    expect(env.registerDownload).toHaveBeenCalledWith(
      'Blonde',
      'http://album.jpg',
      expect.any(String),
    );
  });

  it('binds every card, so an url-less release still gets its toast', async () => {
    withReleases({ [REL_URL]: { success: true, releases: [{ title: 'x' }] } });
    const env = makeEnv();
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={env} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );
    fireEvent.click(document.querySelector('.beatport-releases-top10-card') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('No release URL available', 'error'),
    );
  });

  it('keeps its placeholder for an empty list, as the vanilla does', async () => {
    withReleases({ [REL_URL]: { success: true, releases: [] } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    // Wait for a SIBLING section to finish first. The placeholder is also the
    // pre-load state, so asserting it directly passes on the first tick —
    // before the response has even arrived — and proves nothing.
    await waitFor(() =>
      expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull(),
    );

    // 3475 bails rather than rendering an error OR an empty section.
    expect(screen.getByText('💿 Loading Top 10 releases...')).toBeInTheDocument();
    expect(document.querySelector('.beatport-releases-top10-error')).toBeNull();
    expect(screen.queryByText('💿 Top 10 Tech House Releases')).not.toBeInTheDocument();
  });

  it('DROPS the genre name from the error header, unlike the success one', async () => {
    withReleases({ [REL_URL]: { success: false, error: 'releases are down' } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    // 3628 says '💿 Top 10 Releases' with no genre, and the subtitle changes.
    expect(screen.getByText('💿 Top 10 Releases')).toBeInTheDocument();
    expect(screen.getByText('Error loading releases')).toBeInTheDocument();
    expect(screen.getByText('releases are down')).toBeInTheDocument();
    expect(screen.queryByText('💿 Top 10 Tech House Releases')).not.toBeInTheDocument();
  });

  it('fails independently of the hero and the lists', async () => {
    withReleases({ [REL_URL]: { success: false, error: 'down' } });
    render(<GenrePage genre={GENRE} onBack={vi.fn()} env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    expect(screen.getByText('Nights')).toBeInTheDocument();
    expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull();
  });
});

describe('the genre page class names', () => {
  /**
   * DELIBERATELY NOT IN THE LIST BELOW, because they are not in style.css
   * either — checked by plain substring, not just the anchored pattern:
   *
   *   genre-top10-lists-container, genre-top10-loading-container,
   *   genre-top10-error, error-detail
   *
   * The vanilla emits all four and none has a rule, so the genre page's top-10
   * wrapper, its loading block and its error block are unstyled today. The
   * inner lists look right only because their CONTENT uses the
   * `beatport-top10-*` classes, which do exist.
   *
   * `error-detail` is the near-miss worth noting: the hero's error block uses
   * `genre-error-details` (plural, and styled at 33560) while the top-10 one
   * uses `error-detail`. The port transcribes both as-is — inventing CSS here
   * would change the appearance of a page nobody asked me to redesign.
   */
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
