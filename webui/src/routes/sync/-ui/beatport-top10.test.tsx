/**
 * The three top-10 lists.
 *
 * The assertions worth having are the ones about what makes them DIFFERENT:
 * the two track lists load and fail as a unit and clean their text; the
 * releases list does neither. And the track lists' click target is the whole
 * container, which is the easiest thing in this region to lose — the cards
 * themselves look inert in beatport-ui.js.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { resetBeatportModalLatch } from '../-beatport.downloads';
import { resetBeatportSectionCache } from '../-beatport.use-section';
import { BeatportTop10Lists, BeatportTop10Releases, TrackTop10List } from './beatport-top10';

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

const LISTS_URL = '/api/beatport/homepage/top-10-lists';
const RELEASES_URL = '/api/beatport/homepage/top-10-releases-cards';

beforeEach(() => {
  resetBeatportSectionCache();
  resetBeatportModalLatch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetBeatportSectionCache();
  resetBeatportModalLatch();
});

/* ── The two track lists ──────────────────────────────────────────────────── */

describe('the two top-10 track lists', () => {
  const LISTS = {
    success: true,
    beatport_top10: [{ title: 'DeepHouseAnthem', artist: 'MoBlack', label: 'L', url: 'http://b' }],
    hype_top10: [{ title: 'HypeTrack', artist: 'B', label: 'L2', rank: 7 }],
  };

  it('fills BOTH lists from ONE request', async () => {
    const calls = stubApi({ [LISTS_URL]: LISTS });
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());
    expect(document.querySelector('.beatport-hype10-card')).not.toBeNull();
    expect(calls.filter((url) => url === LISTS_URL)).toHaveLength(1);
  });

  it('CLEANS the text, which the releases list does not', async () => {
    stubApi({ [LISTS_URL]: LISTS });
    render(<BeatportTop10Lists env={makeEnv()} />);
    // The scraped strings arrive concatenated; the vanilla cleans them at
    // render time (1669-1671), and the container click then reads these very
    // strings back out as the download's metadata.
    await waitFor(() =>
      expect(document.querySelector('.beatport-top10-card-title')?.textContent).toBe(
        'Deep House Anthem',
      ),
    );
    expect(document.querySelector('.beatport-top10-card-artist')?.textContent).toBe('Mo Black');
  });

  it("prefers the API's rank over the list position", async () => {
    stubApi({ [LISTS_URL]: LISTS });
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() => expect(document.querySelector('.beatport-hype10-card')).not.toBeNull());
    // First card, but rank 7 — a backend that ranked out of order keeps its
    // numbers.
    expect(document.querySelector('.beatport-hype10-card-rank')?.textContent).toBe('7');
    expect(document.querySelector('.beatport-top10-card-rank')?.textContent).toBe('1');
  });

  it('falls back to an icon when a track has no artwork', async () => {
    stubApi({ [LISTS_URL]: LISTS });
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-top10-card-placeholder')?.textContent).toBe('🎵'),
    );
    // Different icon per list, not a shared one.
    expect(document.querySelector('.beatport-hype10-card-placeholder')?.textContent).toBe('🔥');
  });

  it('queues the whole list when the CONTAINER is clicked', async () => {
    stubApi({
      [LISTS_URL]: LISTS,
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'Deep House Anthem' }] },
    });
    const env = makeEnv();
    render(<BeatportTop10Lists env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());

    // The cards have no handler of their own — the container is the button, and
    // that wiring lives in sync-services.js, not beatport-ui.js.
    fireEvent.click(document.getElementById('beatport-top10-list') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(args[1]).toBe('Beatport Top 10');
    expect(args[6]).toBe('playlist');
  });

  it('sends the ENRICHED tracks to the modal, not the ones on screen', async () => {
    stubApi({
      [LISTS_URL]: LISTS,
      // Deliberately different from the rendered track: an enrichment stub that
      // echoes the input cannot tell "enriched" from "skipped enrichment".
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'ENRICHED' }] },
    });
    const env = makeEnv();
    render(<BeatportTop10Lists env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());

    fireEvent.click(document.getElementById('beatport-top10-list') as Element);
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalled());
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((args[2] as { name: string }[])[0].name).toBe('ENRICHED');
  });

  it('swallows a second container click while the first is in flight', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let enrichCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.startsWith('/api/beatport/enrich-tracks')) {
          enrichCalls++;
          await gate;
          return new Response(JSON.stringify({ success: true, tracks: [{ title: 'x' }] }), {
            status: 200,
          });
        }
        return new Response(JSON.stringify(LISTS), { status: 200 });
      }),
    );
    const env = makeEnv();
    render(<BeatportTop10Lists env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());

    const list = document.getElementById('beatport-top10-list') as Element;
    fireEvent.click(list);
    await waitFor(() => expect(enrichCalls).toBe(1));
    // The same latch the Top 100 buttons use — an impatient double click must
    // not queue the list twice.
    fireEvent.click(list);
    expect(enrichCalls).toBe(1);
    release();
    await waitFor(() => expect(env.openDownloadModal).toHaveBeenCalledTimes(1));
  });

  it('shows the LOADED hype subtitle, not the one index.html starts with', async () => {
    stubApi({ [LISTS_URL]: LISTS });
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() => expect(document.querySelector('.beatport-hype10-card')).not.toBeNull());
    // 1706 replaces "Editor's hottest trending picks" with this shorter line
    // when the data lands, so the loaded state is the one the port renders.
    expect(document.querySelector('.beatport-hype10-list-subtitle')?.textContent).toBe(
      "Editor's trending picks",
    );
    expect(document.querySelector('.beatport-top10-list-subtitle')?.textContent).toBe(
      'Most popular tracks on Beatport',
    );
  });

  it('files the hype list under its own name', async () => {
    stubApi({
      [LISTS_URL]: LISTS,
      '/api/beatport/enrich-tracks': { success: true, tracks: [{ title: 'HypeTrack' }] },
    });
    const env = makeEnv();
    render(<BeatportTop10Lists env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-hype10-card')).not.toBeNull());

    fireEvent.click(document.getElementById('beatport-hype10-list') as Element);
    await waitFor(() => expect(env.registerDownload).toHaveBeenCalled());
    expect(env.registerDownload).toHaveBeenCalledWith('Hype Top 10', '', expect.any(String));
  });

  it('opens the overlay with the count already in it', async () => {
    stubApi({
      [LISTS_URL]: LISTS,
      '/api/beatport/enrich-tracks': { success: true, tracks: [] },
    });
    const env = makeEnv();
    render(<BeatportTop10Lists env={env} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());

    fireEvent.click(document.getElementById('beatport-top10-list') as Element);
    // 4925 — every other flow opens with a 'Scraping …' line instead.
    await waitFor(() =>
      expect(env.showLoadingOverlay).toHaveBeenCalledWith('Fetching track metadata... (0/1)'),
    );
  });

  it('writes the SAME error block into BOTH lists', async () => {
    stubApi({ [LISTS_URL]: { success: false, error: 'beatport is down' } });
    render(<BeatportTop10Lists env={makeEnv()} />);
    // 1753-1754: one endpoint, one failure, two identical blocks — and the list
    // headers are replaced along with the content.
    await waitFor(() => expect(document.querySelectorAll('.beatport-top10-error')).toHaveLength(2));
    expect(screen.getAllByText('beatport is down')).toHaveLength(2);
    expect(document.querySelector('.beatport-top10-list-header')).toBeNull();
  });

  it('uses its own copy when the fetch throws', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('socket hang up'))),
    );
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() => expect(screen.getAllByText('Failed to load top 10 lists')).toHaveLength(2));
  });

  it('keeps the loading placeholder for a successful but EMPTY response', async () => {
    // 1615 tests `data.success` ALONE — no length check, unlike every slider.
    // An empty list is therefore not an error: the populate call bails and the
    // static markup stays.
    stubApi({ [LISTS_URL]: { success: true, beatport_top10: [], hype_top10: [] } });
    render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('🎵 Loading Beatport Top 10...')).toBeInTheDocument(),
    );
    expect(document.querySelector('.beatport-top10-error')).toBeNull();
    expect(screen.getByText('🔥 Loading Hype Top 10...')).toBeInTheDocument();
  });

  it('does not re-request on a remount', async () => {
    const calls = stubApi({ [LISTS_URL]: LISTS });
    const first = render(<BeatportTop10Lists env={makeEnv()} />);
    await waitFor(() => expect(document.querySelector('.beatport-top10-card')).not.toBeNull());
    first.unmount();

    render(<BeatportTop10Lists env={makeEnv()} />);
    // These endpoints scrape Beatport; the vanilla loads them once per session
    // behind beatportContentState.loaded.
    expect(document.querySelector('.beatport-top10-card')).not.toBeNull();
    expect(calls).toHaveLength(1);
  });
});

/* ── Top 10 releases ──────────────────────────────────────────────────────── */

describe('the top-10 releases list', () => {
  const RELEASES = {
    success: true,
    releases: [
      {
        title: 'DeepHouseAnthem',
        artist: 'A',
        label: 'L',
        url: 'http://r',
        image_url: 'https://cdn/image_size/95x95/a.jpg',
      },
    ],
  };

  it('does NOT clean its text, unlike the two track lists', async () => {
    stubApi({ [RELEASES_URL]: RELEASES });
    render(<BeatportTop10Releases env={makeEnv()} />);
    // Same file, same shape of data, no cleanTrackText (1807-1809). Cleaning it
    // here would be an improvement, and would change what the user sees.
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card-title')?.textContent).toBe(
        'DeepHouseAnthem',
      ),
    );
  });

  it('paints an UPSCALED background inline while the thumbnail stays 95px', async () => {
    stubApi({ [RELEASES_URL]: RELEASES });
    render(<BeatportTop10Releases env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );
    const card = document.querySelector('.beatport-releases-top10-card') as HTMLElement;
    // Asserted on the parts rather than the whole string: the CSSOM re-
    // serialises what it is given (spaces inside rgba(), double quotes), so an
    // exact match would be pinning jsdom's formatter, not the value. The exact
    // string beatportCardBackground produces is pinned in -beatport.core.test.
    expect(card.style.backgroundImage).toContain('image_size/500x500/a.jpg');
    expect(card.style.backgroundImage).not.toContain('95x95');
    expect(card.style.backgroundImage).toContain('linear-gradient');
    expect(card.style.backgroundSize).toBe('cover');
    // Only the background is upscaled.
    expect(card.querySelector('img')?.getAttribute('src')).toBe(
      'https://cdn/image_size/95x95/a.jpg',
    );
  });

  it('sets no background at all when there is no artwork', async () => {
    stubApi({ [RELEASES_URL]: { success: true, releases: [{ title: 'x' }] } });
    render(<BeatportTop10Releases env={makeEnv()} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-placeholder')).toBeNull(),
    );
    const card = document.querySelector('.beatport-releases-top10-card') as HTMLElement;
    expect(card.getAttribute('style')).toBeNull();
    expect(card.querySelector('.beatport-releases-top10-card-placeholder')?.textContent).toBe('💿');
  });

  it('opens a release per CARD, not per container', async () => {
    stubApi({
      [RELEASES_URL]: RELEASES,
      '/api/beatport/release-metadata': { success: false, error: 'nope' },
    });
    const env = makeEnv();
    render(<BeatportTop10Releases env={env} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );

    fireEvent.click(document.querySelector('.beatport-releases-top10-card') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('Loading DeepHouseAnthem...', 'info'),
    );
  });

  it('wires every card, so an url-less release reaches its toast', async () => {
    // 1834 has no url test at all — the only one of the four release-card call
    // sites that does not, and therefore the only place the handler's own
    // 'No release URL available' toast can be seen.
    stubApi({ [RELEASES_URL]: { success: true, releases: [{ title: 'x' }] } });
    const env = makeEnv();
    render(<BeatportTop10Releases env={env} />);
    await waitFor(() =>
      expect(document.querySelector('.beatport-releases-top10-card')).not.toBeNull(),
    );

    fireEvent.click(document.querySelector('.beatport-releases-top10-card') as Element);
    await waitFor(() =>
      expect(env.showToast).toHaveBeenCalledWith('No release URL available', 'error'),
    );
  });

  it('has its OWN error block, not the shared track-list one', async () => {
    stubApi({ [RELEASES_URL]: { success: false, error: 'nope' } });
    render(<BeatportTop10Releases env={makeEnv()} />);
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    expect(document.querySelector('.beatport-releases-top10-error')).not.toBeNull();
    expect(document.querySelector('.beatport-top10-error')).toBeNull();
  });

  it('keeps its placeholder for a successful but empty response', async () => {
    stubApi({ [RELEASES_URL]: { success: true, releases: [] } });
    render(<BeatportTop10Releases env={makeEnv()} />);
    await waitFor(() =>
      expect(screen.getByText('💿 Loading Top 10 Releases...')).toBeInTheDocument(),
    );
    expect(document.querySelector('.beatport-releases-top10-error')).toBeNull();
  });
});

describe('TrackTop10List on its own', () => {
  it('takes an id, a subtitle and a chart name from its caller', () => {
    // The genre page reuses this component with all three overridden; the
    // homepage supplies none of them and gets the defaults.
    render(
      <TrackTop10List
        variant="beatport"
        tracks={[{ title: 'T', artist: 'A', label: 'L' }]}
        env={makeEnv()}
        listId="genre-beatport-top10-list"
        subtitle="Most popular tech house tracks"
        chartName="Tech House Beatport Top 10"
      />,
    );
    expect(document.getElementById('genre-beatport-top10-list')).not.toBeNull();
    expect(document.getElementById('beatport-top10-list')).toBeNull();
    expect(document.querySelector('.beatport-top10-list-subtitle')?.textContent).toBe(
      'Most popular tech house tracks',
    );
    // The card markup is unchanged by the overrides.
    expect(document.querySelector('.beatport-top10-card-title')?.textContent).toBe('T');
  });

  it('falls back to the homepage id and copy when given none', () => {
    render(<TrackTop10List variant="hype" tracks={[]} env={makeEnv()} />);
    expect(document.getElementById('beatport-hype10-list')).not.toBeNull();
    expect(document.querySelector('.beatport-hype10-list-subtitle')?.textContent).toBe(
      "Editor's trending picks",
    );
  });
});

/* ── Artefacts ────────────────────────────────────────────────────────────── */

describe('the top-10 class names', () => {
  it('all exist in the vanilla stylesheet', () => {
    // A missing class renders unstyled rather than failing, so these are
    // checked against the real file.
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    const required = [
      'beatport-top10-container',
      'beatport-top10-list',
      'beatport-top10-list-header',
      'beatport-top10-list-title',
      'beatport-top10-list-subtitle',
      'beatport-top10-tracks',
      'beatport-top10-card',
      'beatport-top10-card-rank',
      'beatport-top10-card-artwork',
      'beatport-top10-card-placeholder',
      'beatport-top10-card-info',
      'beatport-top10-card-title',
      'beatport-top10-card-artist',
      'beatport-top10-card-label',
      'beatport-top10-loading',
      'beatport-top10-loading-content',
      'beatport-top10-error',
      'beatport-hype10-list',
      'beatport-hype10-list-header',
      'beatport-hype10-list-title',
      'beatport-hype10-list-subtitle',
      'beatport-hype10-tracks',
      'beatport-hype10-card',
      'beatport-hype10-card-rank',
      'beatport-hype10-card-artwork',
      'beatport-hype10-card-placeholder',
      'beatport-hype10-card-info',
      'beatport-hype10-card-title',
      'beatport-hype10-card-artist',
      'beatport-hype10-card-label',
      'beatport-hype10-loading',
      'beatport-hype10-loading-content',
      'beatport-releases-top10-list',
      'beatport-releases-top10-tracks',
      'beatport-releases-top10-card',
      'beatport-releases-top10-card-rank',
      'beatport-releases-top10-card-artwork',
      'beatport-releases-top10-card-placeholder',
      'beatport-releases-top10-card-info',
      'beatport-releases-top10-card-title',
      'beatport-releases-top10-card-artist',
      'beatport-releases-top10-card-label',
      'beatport-releases-top10-loading',
      'beatport-releases-top10-loading-content',
      'beatport-releases-top10-error',
    ];
    for (const className of required) {
      expect(
        new RegExp(`\\.${className}[\\s,:{.[]`).test(css),
        `.${className} is not in static/style.css`,
      ).toBe(true);
    }
  });
});
