/**
 * The five sections, end to end: a stubbed API response goes in, cards come
 * out, and a click reaches the download bridge with the right arguments.
 *
 * The click assertions check WHICH handler and WHICH payload, because every
 * section renders convincingly either way — a hype pick wired to the chart
 * handler would still draw a hype pick, and would scrape the wrong endpoint on
 * click.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { resetBeatportModalLatch } from '../-beatport.downloads';
import { resetBeatportSectionCache } from '../-beatport.use-section';
import {
  BeatportDJChartsSection,
  BeatportFeaturedChartsSection,
  BeatportHeroSection,
  BeatportHypePicksSection,
  BeatportNewReleasesSection,
} from './beatport-sections';

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

/** Answers per URL prefix, so one stub serves a whole section's flow. */
function stubApi(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      const key = Object.keys(routes).find((prefix) => url.startsWith(prefix));
      return new Response(JSON.stringify(key ? routes[key] : { success: false }), { status: 200 });
    }),
  );
  return calls;
}

beforeEach(() => {
  resetBeatportSectionCache();
  resetBeatportModalLatch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetBeatportSectionCache();
  resetBeatportModalLatch();
});

/* ── Hero ─────────────────────────────────────────────────────────────────── */

describe('the hero section', () => {
  const TRACK = {
    title: 'Nights',
    artist: 'Frank Ocean',
    url: 'http://beatport/release/1',
    image_url: 'http://art.jpg',
  };

  it('paints its artwork ON THE SLIDE, where the CSS selector looks', async () => {
    stubApi({ '/api/beatport/hero-tracks': { success: true, tracks: [TRACK] } });
    render(<BeatportHeroSection env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('Nights')).toBeInTheDocument());

    const slide = document.querySelector('.beatport-rebuild-slide') as HTMLElement;
    // `.beatport-rebuild-slide[data-image]::before` reads --slide-bg-image. On
    // a child, the selector never matches and the hero renders bare.
    expect(slide.getAttribute('data-image')).toBe('http://art.jpg');
    expect(slide.style.getPropertyValue('--slide-bg-image')).toBe("url('http://art.jpg')");
  });

  it('opens the release when the slide is clicked', async () => {
    const calls = stubApi({
      '/api/beatport/hero-tracks': { success: true, tracks: [TRACK] },
      '/api/beatport/release-metadata': {
        success: true,
        tracks: [{ name: 'A', artists: ['Frank Ocean'] }],
        album: { name: 'Blonde', images: [] },
        artist: { name: 'Frank Ocean' },
      },
    });
    const env = makeEnv();
    render(<BeatportHeroSection env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-rebuild-slide')).not.toBeNull());

    fireEvent.click(document.querySelector('.beatport-rebuild-slide') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    expect(calls).toContain('/api/beatport/release-metadata');
    // A release, not a chart: the seventh argument decides the folder layout.
    expect((env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0][6]).toBe(
      'artist_album',
    );
  });

  it('defaults the click payload the way the vanilla builds it', async () => {
    stubApi({
      '/api/beatport/hero-tracks': { success: true, tracks: [{ url: 'http://r' }] },
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    const env = makeEnv();
    render(<BeatportHeroSection env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-rebuild-slide')).not.toBeNull());

    fireEvent.click(document.querySelector('.beatport-rebuild-slide') as Element);
    // The hero builds a release object with 'Unknown Title' defaults (134-137),
    // and that string reaches the toast.
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('Loading Unknown Title...', 'info'),
    );
  });

  it('leaves an url-less slide unclickable and without a pointer cursor', async () => {
    stubApi({ '/api/beatport/hero-tracks': { success: true, tracks: [{ title: 'x' }] } });
    const env = makeEnv();
    render(<BeatportHeroSection env={env} />);
    await waitFor(() => expect(screen.getByText('x')).toBeInTheDocument());

    const slide = document.querySelector('.beatport-rebuild-slide') as HTMLElement;
    expect(slide.style.cursor).toBe('');
    fireEvent.click(slide);
    expect(env.showToast).not.toHaveBeenCalled();
  });

  it('sets the pointer cursor on a clickable one', async () => {
    stubApi({ '/api/beatport/hero-tracks': { success: true, tracks: [TRACK] } });
    render(<BeatportHeroSection env={makeEnv()} />);
    await waitFor(() => expect(document.querySelector('.beatport-rebuild-slide')).not.toBeNull());
    expect((document.querySelector('.beatport-rebuild-slide') as HTMLElement).style.cursor).toBe(
      'pointer',
    );
  });

  it('keeps its loading block when the load fails, as the vanilla does', async () => {
    stubApi({ '/api/beatport/hero-tracks': { success: false } });
    render(<BeatportHeroSection env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('🎯 Loading Fresh Beatport Tracks...')).toBeInTheDocument(),
    );
    expect(document.querySelector('.beatport-rebuild-slide')).toBeNull();
  });
});

/* ── New releases ─────────────────────────────────────────────────────────── */

describe('the new-releases section', () => {
  const RELEASE = { title: 'R', artist: 'A', label: 'L', url: 'http://r' };

  it('renders its cards under the vanilla track id', async () => {
    stubApi({ '/api/beatport/new-releases': { success: true, releases: [RELEASE] } });
    render(<BeatportNewReleasesSection env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('R')).toBeInTheDocument());
    expect(document.getElementById('beatport-releases-slider-track')).not.toBeNull();
    expect(document.getElementById('beatport-releases-slider-indicators')).not.toBeNull();
  });

  it('pads the slide to ten with CAPTIONED filler', async () => {
    stubApi({ '/api/beatport/new-releases': { success: true, releases: [RELEASE] } });
    render(<BeatportNewReleasesSection env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelectorAll('.beatport-release-placeholder')).toHaveLength(9),
    );
    // The releases filler carries copy; the hype-picks filler is a bare icon.
    expect(screen.getAllByText('More Releases')).toHaveLength(9);
  });

  it('opens the release on click', async () => {
    stubApi({
      '/api/beatport/new-releases': { success: true, releases: [RELEASE] },
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    const env = makeEnv();
    render(<BeatportNewReleasesSection env={env} />);
    await waitFor(() => expect(screen.getByText('R')).toBeInTheDocument());

    fireEvent.click(document.querySelector('.beatport-release-card') as Element);
    await waitFor(() => expect(env.showToast).toHaveBeenCalledWith('Loading R...', 'info'));
  });

  it('shows the error block with the message the loader chose', async () => {
    stubApi({ '/api/beatport/new-releases': { success: false, error: 'rate limited' } });
    render(<BeatportNewReleasesSection env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    expect(screen.getByText('rate limited')).toBeInTheDocument();
  });

  it('does not bind a click to a placeholder-url card', async () => {
    stubApi({
      '/api/beatport/new-releases': { success: true, releases: [{ title: 'R', url: '#' }] },
    });
    const env = makeEnv();
    render(<BeatportNewReleasesSection env={env} />);
    await waitFor(() => expect(screen.getByText('R')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.beatport-release-card') as Element);
    expect(env.showToast).not.toHaveBeenCalled();
  });
});

/* ── Hype picks ───────────────────────────────────────────────────────────── */

describe('the hype-picks section', () => {
  it('pads with a BARE ICON, not the releases copy', async () => {
    stubApi({
      '/api/beatport/hype-picks': { success: true, releases: [{ title: 'H', url: 'http://h' }] },
    });
    render(<BeatportHypePicksSection env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelectorAll('.beatport-hype-pick-placeholder')).toHaveLength(9),
    );
    expect(screen.queryByText('More Releases')).not.toBeInTheDocument();
  });

  it('uses its OWN slider slug, so its classes are not the releases ones', async () => {
    // The slug drives every class the slider emits. Borrowing the releases
    // config would render hype picks under `beatport-releases-*` — unstyled in
    // the places the two stylesheets differ, and silent, since a class that is
    // not in the CSS is not an error.
    stubApi({
      '/api/beatport/hype-picks': { success: true, releases: [{ title: 'H', url: 'http://h' }] },
    });
    render(<BeatportHypePicksSection env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-hype-picks-slider-container')).not.toBeNull(),
    );
    expect(document.querySelector('.beatport-hype-picks-slide')).not.toBeNull();
    expect(document.querySelector('.beatport-releases-slider-container')).toBeNull();
  });

  it('uses its own error copy, not the releases section’s', async () => {
    stubApi({ '/api/beatport/hype-picks': { success: true, releases: [] } });
    render(<BeatportHypePicksSection env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('❌ Error Loading Hype Picks')).toBeInTheDocument(),
    );
    expect(screen.getByText('No hype picks available')).toBeInTheDocument();
  });

  it('opens the release, not a chart', async () => {
    stubApi({
      '/api/beatport/hype-picks': { success: true, releases: [{ title: 'H', url: 'http://h' }] },
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    const env = makeEnv();
    render(<BeatportHypePicksSection env={env} />);
    await waitFor(() => expect(screen.getByText('H')).toBeInTheDocument());
    fireEvent.click(document.querySelector('.beatport-hype-pick-card') as Element);
    await waitFor(() => expect(env.showToast).toHaveBeenCalledWith('Loading H...', 'info'));
  });
});

/* ── The two chart sections ───────────────────────────────────────────────── */

describe('the featured-charts section', () => {
  const CHART = { name: 'Peak Hour', creator: 'DJ X', url: 'http://c', image: 'http://i.jpg' };

  it('scrapes with the FEATURED prefix on click', async () => {
    const calls = stubApi({
      '/api/beatport/featured-charts': { success: true, charts: [CHART] },
      '/api/beatport/chart/extract': { success: true, tracks: [{ title: 'T' }] },
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'T' }] },
    });
    const env = makeEnv();
    render(<BeatportFeaturedChartsSection env={env} />);
    await waitFor(() => expect(screen.getByText('Peak Hour')).toBeInTheDocument());

    fireEvent.click(document.querySelector('.beatport-chart-card') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    expect(calls).toContain('/api/beatport/chart/extract');
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    // A compilation under the playlist context, credited to Various Artists.
    expect(args[1]).toBe('Peak Hour - DJ X');
    expect(args[6]).toBe('playlist');
  });

  it('binds a click even to an url-less card, unlike the release sections', async () => {
    // 1158 attaches unconditionally, so the url test happens inside the handler
    // and the user gets a toast. The three release sections refuse to bind at
    // all, which leaves an url-less card inert and silent — the two behaviours
    // look identical until the url is missing.
    stubApi({
      '/api/beatport/featured-charts': {
        success: true,
        charts: [{ name: 'No URL', creator: 'X' }],
      },
    });
    const env = makeEnv();
    render(<BeatportFeaturedChartsSection env={env} />);
    await waitFor(() => expect(screen.getByText('No URL')).toBeInTheDocument());

    fireEvent.click(document.querySelector('.beatport-chart-card') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('No chart URL available', 'error'),
    );
  });

  it('shows no error block on failure — just its placeholder', async () => {
    stubApi({ '/api/beatport/featured-charts': { success: false, error: 'down' } });
    render(<BeatportFeaturedChartsSection env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('📊 Loading Featured Charts...')).toBeInTheDocument(),
    );
    // The backend's message is deliberately NOT surfaced: this section has no
    // error renderer.
    expect(screen.queryByText('down')).not.toBeInTheDocument();
    expect(document.querySelector('.beatport-chart-card')).toBeNull();
  });

  it('uses ten cards per slide', async () => {
    stubApi({
      '/api/beatport/featured-charts': {
        success: true,
        charts: Array.from({ length: 11 }, (_, i) => ({ ...CHART, name: `C${i}` })),
      },
    });
    render(<BeatportFeaturedChartsSection env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelectorAll('.beatport-charts-slide')).toHaveLength(2),
    );
    // Charts do NOT pad, unlike releases and hype picks.
    expect(document.querySelectorAll('.beatport-chart-card')).toHaveLength(11);
  });
});

describe('the DJ-charts section', () => {
  const CHART = { name: 'Warmup', creator: 'DJ Y', url: 'http://d', image: 'http://i.jpg' };

  it('uses its own class family and three cards per slide', async () => {
    stubApi({
      '/api/beatport/dj-charts': {
        success: true,
        charts: Array.from({ length: 4 }, (_, i) => ({ ...CHART, name: `D${i}` })),
      },
    });
    render(<BeatportDJChartsSection env={makeEnv()} />);
    await waitFor(() => expect(document.querySelectorAll('.beatport-dj-card')).toHaveLength(4));
    // 3 per slide, not the 10 every other grid section uses.
    expect(document.querySelectorAll('.beatport-dj-slide')).toHaveLength(2);
  });

  it('scrapes with the DJ prefix, which is the only wire difference', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(url);
        if (url === '/api/beatport/chart/extract') {
          expect(JSON.parse(init?.body as string).chart_name).toBe('DJ Chart: Warmup');
          return new Response(JSON.stringify({ success: true, tracks: [{ title: 'T' }] }), {
            status: 200,
          });
        }
        if (url.startsWith('/api/beatport/enrich-tracks')) {
          return new Response(JSON.stringify({ success: true, tracks: [{ title: 'T' }] }), {
            status: 200,
          });
        }
        return new Response(JSON.stringify({ success: true, charts: [CHART] }), { status: 200 });
      }),
    );
    const env = makeEnv();
    render(<BeatportDJChartsSection env={env} />);
    await waitFor(() => expect(screen.getByText('Warmup')).toBeInTheDocument());

    fireEvent.click(document.querySelector('.beatport-dj-card') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    expect(calls).toContain('/api/beatport/chart/extract');
  });

  it('binds unconditionally too, and toasts the DJ-specific copy', async () => {
    stubApi({ '/api/beatport/dj-charts': { success: true, charts: [{ name: 'No URL' }] } });
    const env = makeEnv();
    render(<BeatportDJChartsSection env={env} />);
    await waitFor(() => expect(screen.getByText('No URL')).toBeInTheDocument());

    fireEvent.click(document.querySelector('.beatport-dj-card') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('No DJ chart URL available', 'error'),
    );
  });

  it('keeps its placeholder on failure', async () => {
    stubApi({ '/api/beatport/dj-charts': { success: true, charts: [] } });
    render(<BeatportDJChartsSection env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('🎧 Loading DJ Charts...')).toBeInTheDocument());
    expect(document.querySelector('.beatport-dj-card')).toBeNull();
  });
});

/* ── The section keys ─────────────────────────────────────────────────────── */

describe('the five section cache keys', () => {
  it('are distinct, so one loaded section never satisfies another', async () => {
    const calls = stubApi({
      '/api/beatport/hero-tracks': { success: true, tracks: [{ title: 'h', url: 'http://h' }] },
      '/api/beatport/new-releases': { success: true, releases: [{ title: 'r' }] },
      '/api/beatport/hype-picks': { success: true, releases: [{ title: 'p' }] },
      '/api/beatport/featured-charts': { success: true, charts: [{ name: 'c' }] },
      '/api/beatport/dj-charts': { success: true, charts: [{ name: 'd' }] },
    });
    const env = makeEnv();
    render(
      <>
        <BeatportHeroSection env={env} />
        <BeatportNewReleasesSection env={env} />
        <BeatportHypePicksSection env={env} />
        <BeatportFeaturedChartsSection env={env} />
        <BeatportDJChartsSection env={env} />
      </>,
    );
    // A shared key would leave four of the five empty, and each renders
    // convincingly on its own — so this is asserted on all five at once.
    await waitFor(() => expect(screen.getByText('d')).toBeInTheDocument());
    for (const text of ['h', 'r', 'p', 'c', 'd']) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(new Set(calls).size).toBe(5);
  });

  it('do not cross-hydrate on a REMOUNT, which is where a shared key shows', async () => {
    // On a first mount a shared key is invisible: both sections start loading
    // before either has cached anything, so both fetch and both render their
    // own state. The damage only appears on the tab switch BACK, when the
    // second section hydrates from the first's cached items — hype picks
    // drawing the new-releases payload, and never re-fetching to correct it.
    const routes = {
      '/api/beatport/new-releases': { success: true, releases: [{ title: 'r-only' }] },
      '/api/beatport/hype-picks': { success: true, releases: [{ title: 'p-only' }] },
      '/api/beatport/featured-charts': { success: true, charts: [{ name: 'c-only' }] },
      '/api/beatport/dj-charts': { success: true, charts: [{ name: 'd-only' }] },
    };
    stubApi(routes);
    const env = makeEnv();
    const view = (
      <>
        <BeatportNewReleasesSection env={env} />
        <BeatportHypePicksSection env={env} />
        <BeatportFeaturedChartsSection env={env} />
        <BeatportDJChartsSection env={env} />
      </>
    );
    const first = render(view);
    await waitFor(() => expect(screen.getByText('d-only')).toBeInTheDocument());
    first.unmount();

    render(view);
    for (const text of ['r-only', 'p-only', 'c-only', 'd-only']) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
  });
});
