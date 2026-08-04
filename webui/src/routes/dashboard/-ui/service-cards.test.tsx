/**
 * ServiceStatusCard — artefact differential against the live vanilla region +
 * the status-payload behaviours (presentation classes, keep-previous titles,
 * Test button targets, enrichment chips + click-to-configure).
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';
import { ServiceStatusCard } from './service-cards';

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.testDashboardConnection;
  delete window.getActiveMetadataSource;
  delete window.switchSettingsTab;
  delete window.isJiosaavnExperimentalEnabled;
  delete window.isBandcampExperimentalEnabled;
  delete window.SoulSyncWebRouter;
});

async function mountCard() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<ServiceStatusCard />);
  });
  return view!;
}

function fireStatus(payload: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:service-status', { detail: payload }));
  });
}

const FULL_PAYLOAD = {
  metadata_source: { source: 'deezer', connected: true, response_time: 42 },
  media_server: { connected: true, type: 'plex', response_time: 7 },
  soulseek: { connected: false, source: 'qobuz' },
  spotify: {},
};

describe('the artefact differential', () => {
  it('renders the vanilla services card 1:1 in its initial state', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card" data-card="services">'),
    );
    const view = await mountCard();
    compareTrees(vanilla, view.container.firstElementChild!, 'services');
  });
});

describe('status payloads', () => {
  it('drives the three cards, titles, and the ready flag', async () => {
    const view = await mountCard();
    const $ = (id: string) => view.container.querySelector<HTMLElement>(`#${id}`)!;
    expect($('metadata-source-service-card').dataset.statusReady).toBe('false');

    fireStatus(FULL_PAYLOAD);

    expect($('metadata-source-service-card').dataset.statusReady).toBe('true');
    expect($('metadata-source-title').textContent).toBe('Deezer');
    expect($('metadata-source-status-indicator').className).toBe(
      'service-card-indicator connected',
    );
    expect($('metadata-source-status-text').className).toBe('service-card-status-text connected');
    expect($('metadata-source-response-time').textContent).toBe('Response: 42ms');

    expect($('media-server-status-text').textContent).toBe('Connected');
    expect($('media-server-response-time').textContent).toBe('Response: 7ms');

    expect($('download-source-title').textContent).toBe('Qobuz');
    expect($('soulseek-status-text').textContent).toBe('Disconnected');
    expect($('soulseek-status-indicator').className).toBe('service-card-indicator disconnected');
  });

  it('keeps the previous titles when a payload has no source', async () => {
    const view = await mountCard();
    fireStatus(FULL_PAYLOAD);
    fireStatus({
      metadata_source: { connected: true },
      media_server: { connected: true },
      soulseek: { connected: true },
    });
    expect(view.container.querySelector('#metadata-source-title')!.textContent).toBe('Deezer');
    expect(view.container.querySelector('#download-source-title')!.textContent).toBe('Qobuz');
  });

  it('shows the spotify rate-limit presentation verbatim', async () => {
    const view = await mountCard();
    fireStatus({
      metadata_source: { source: 'spotify', connected: true },
      media_server: {},
      soulseek: {},
      spotify: { rate_limited: true, rate_limit: { remaining_seconds: 90 } },
    });
    expect(view.container.querySelector('#metadata-source-status-text')!.textContent).toBe(
      'Spotify paused — 1m 30s',
    );
    expect(view.container.querySelector('#metadata-source-status-indicator')!.className).toBe(
      'service-card-indicator rate-limited',
    );
  });
});

describe('the Test buttons', () => {
  it('metadata falls back to getActiveMetadataSource before any payload', async () => {
    const testConnection = vi.fn();
    window.testDashboardConnection = testConnection;
    window.getActiveMetadataSource = () => 'musicbrainz';
    const view = await mountCard();
    fireEvent.click(
      view.container.querySelector('#metadata-source-service-card .service-card-button')!,
    );
    expect(testConnection).toHaveBeenCalledWith('musicbrainz');
  });

  it('metadata uses the payload source once one arrives; the others are literals', async () => {
    const testConnection = vi.fn();
    window.testDashboardConnection = testConnection;
    const view = await mountCard();
    fireStatus(FULL_PAYLOAD);
    fireEvent.click(
      view.container.querySelector('#metadata-source-service-card .service-card-button')!,
    );
    fireEvent.click(
      view.container.querySelector('#media-server-service-card .service-card-button')!,
    );
    fireEvent.click(view.container.querySelector('#soulseek-service-card .service-card-button')!);
    expect(testConnection.mock.calls).toEqual([['deezer'], ['server'], ['soulseek']]);
  });
});

describe('the enrichment chips', () => {
  const CHIP_PAYLOAD = {
    ...FULL_PAYLOAD,
    enrichment: {
      musicbrainz: { name: 'MusicBrainz', running: true, configured: true, calls_24h: 12 },
      lastfm: { name: 'Last.fm', running: true, configured: false },
      jiosaavn_enrichment: { name: 'JioSaavn', running: true, configured: true },
    },
  };

  it('renders chips in order with classes, activity and the configure CTA', async () => {
    const view = await mountCard();
    fireStatus(CHIP_PAYLOAD);
    const chips = view.container.querySelectorAll('.enrichment-chip');
    expect(chips).toHaveLength(2); // jiosaavn gated off
    expect(chips[0].className).toBe('enrichment-chip status-running');
    expect(chips[0].querySelector('.enrichment-chip-name')!.textContent).toBe('MusicBrainz');
    expect(chips[0].querySelector('.enrichment-chip-activity')!.textContent).toBe('12 / 24h');
    expect(chips[1].className).toBe('enrichment-chip status-not-configured');
    expect(chips[1].querySelector('.enrichment-chip-status')!.textContent).toBe('Configure →');
    expect(chips[1].getAttribute('title')).toBe('Click to configure in Settings');
  });

  it('a live jiosaavn toggle replays the cached payload (the settings.js path)', async () => {
    const view = await mountCard();
    fireStatus(CHIP_PAYLOAD);
    expect(view.container.querySelectorAll('.enrichment-chip')).toHaveLength(2);
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: true } }),
      );
    });
    const names = Array.from(
      view.container.querySelectorAll('.enrichment-chip-name'),
      (el) => el.textContent,
    );
    expect(names).toEqual(['MusicBrainz', 'JioSaavn', 'Last.fm']);
  });

  it('click-to-configure walks the vanilla navigate → tab → scroll chain', async () => {
    vi.useFakeTimers();
    const navigateToPage = vi.fn(() => Promise.resolve(true));
    const switchSettingsTab = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage } as never;
    window.switchSettingsTab = switchSettingsTab;
    const view = await mountCard();
    fireStatus(CHIP_PAYLOAD);
    fireEvent.click(view.container.querySelectorAll('.enrichment-chip')[1]);
    expect(navigateToPage).toHaveBeenCalledWith('settings');
    expect(switchSettingsTab).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(50);
    });
    expect(switchSettingsTab).toHaveBeenCalledWith('connections');
  });

  it('a chip without a selector has no click handler', async () => {
    const navigateToPage = vi.fn(() => Promise.resolve(true));
    window.SoulSyncWebRouter = { navigateToPage } as never;
    const view = await mountCard();
    fireStatus(CHIP_PAYLOAD);
    fireEvent.click(view.container.querySelectorAll('.enrichment-chip')[0]); // musicbrainz
    expect(navigateToPage).not.toHaveBeenCalled();
  });
});

describe('the mount hydrate', () => {
  it('applies a fetched /status payload', async () => {
    fetchMock.mockImplementation(
      () =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => FULL_PAYLOAD,
        }) as never,
    );
    const view = await mountCard();
    expect(view.container.querySelector('#metadata-source-title')!.textContent).toBe('Deezer');
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain('/status');
  });
});
