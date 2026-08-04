/**
 * P5 pure core — literal assertions against the vanilla originals
 * (shared-helpers.js service cards + chips, settings.js duration format,
 * api-monitor.js equalizer math).
 */

import { describe, expect, it } from 'vitest';

import {
  CHIP_SETTINGS_SELECTORS,
  DOWNLOAD_SOURCE_NAMES,
  ENRICHMENT_CHIP_ORDER,
  EQ_INITIAL,
  enrichmentChips,
  eqBarFrame,
  formatRateLimitDuration,
  genericServiceCard,
  getMetadataSourceLabel,
  getMetadataSourcePresentation,
  metadataSourceCard,
  RATE_GAUGE_LABELS,
  RATE_GAUGE_LOGOS,
  RATE_GAUGE_SERVICES,
  soulseekCard,
  visibleRateGaugeServices,
  workerStatusLabel,
} from './-dash.services';

describe('getMetadataSourceLabel', () => {
  it('maps every source, spotify_free included, else Unmapped', () => {
    expect(getMetadataSourceLabel('deezer')).toBe('Deezer');
    expect(getMetadataSourceLabel('discogs')).toBe('Discogs');
    expect(getMetadataSourceLabel('hydrabase')).toBe('Hydrabase');
    expect(getMetadataSourceLabel('itunes')).toBe('iTunes');
    expect(getMetadataSourceLabel('musicbrainz')).toBe('MusicBrainz');
    expect(getMetadataSourceLabel('jiosaavn')).toBe('JioSaavn');
    expect(getMetadataSourceLabel('spotify_free')).toBe('Spotify (no auth)');
    expect(getMetadataSourceLabel('spotify')).toBe('Spotify');
    expect(getMetadataSourceLabel('tidal')).toBe('Unmapped');
    expect(getMetadataSourceLabel(undefined)).toBe('Unmapped');
  });
});

describe('formatRateLimitDuration', () => {
  it('matches the vanilla shapes', () => {
    expect(formatRateLimitDuration(0)).toBe('0s');
    expect(formatRateLimitDuration(null)).toBe('0s');
    expect(formatRateLimitDuration(45)).toBe('45s');
    expect(formatRateLimitDuration(90)).toBe('1m 30s');
    expect(formatRateLimitDuration(3900)).toBe('1h 5m');
  });
});

describe('getMetadataSourcePresentation', () => {
  it('rate-limited outranks everything for the spotify family', () => {
    const p = getMetadataSourcePresentation(
      { source: 'spotify', connected: true },
      { rate_limited: true, rate_limit: { remaining_seconds: 90 } },
    );
    expect(p.statusClass).toBe('rate-limited');
    expect(p.statusText).toBe('Spotify paused — 1m 30s');
    expect(p.dotTitle).toBe('Spotify paused — 1m 30s remaining');
  });

  it('spotify_free is part of the spotify family', () => {
    const p = getMetadataSourcePresentation(
      { source: 'spotify_free', connected: true },
      { rate_limited: true, rate_limit: { remaining_seconds: 60 } },
    );
    expect(p.statusText).toBe('Spotify paused — 1m 0s');
    // connected in the family also counts as an active session
    expect(p.sessionActive).toBe(true);
  });

  it('cooldown comes after rate-limited', () => {
    const p = getMetadataSourcePresentation({ source: 'spotify' }, { post_ban_cooldown: 120 });
    expect(p.statusText).toBe('Spotify recovering — 2m 0s');
    expect(p.dotTitle).toBe('Spotify recovering — 2m 0s cooldown');
  });

  it('a non-spotify source ignores spotify rate limits', () => {
    const p = getMetadataSourcePresentation(
      { source: 'deezer', connected: true },
      { rate_limited: true, rate_limit: { remaining_seconds: 90 } },
    );
    expect(p.statusClass).toBe('connected');
    expect(p.statusText).toBe('Connected');
    expect(p.dotTitle).toBe('Deezer');
  });

  it('sourceless payloads are Disconnected', () => {
    const p = getMetadataSourcePresentation({}, {});
    expect(p).toEqual({
      statusClass: 'disconnected',
      statusText: 'Disconnected',
      dotClass: 'disconnected',
      dotTitle: 'Disconnected',
      sessionActive: false,
    });
  });
});

describe('the service card views', () => {
  it('metadata card carries presentation classes + source title + test target', () => {
    const view = metadataSourceCard({ source: 'deezer', connected: true, response_time: 42 }, {});
    expect(view.indicatorClass).toBe('service-card-indicator connected');
    expect(view.statusTextClass).toBe('service-card-status-text connected');
    expect(view.responseText).toBe('Response: 42ms');
    expect(view.title).toBe('Deezer');
    expect(view.testService).toBe('deezer');
  });

  it('a sourceless metadata payload keeps title and test target (null)', () => {
    const view = metadataSourceCard({}, {});
    expect(view.title).toBeNull();
    expect(view.testService).toBeNull();
  });

  it('generic cards use the flat connected/disconnected chain', () => {
    expect(genericServiceCard({ connected: true }).statusText).toBe('Connected');
    expect(genericServiceCard({}).indicatorClass).toBe('service-card-indicator disconnected');
    // response_time 0 is a real measurement, not missing — but null IS missing
    expect(genericServiceCard({ connected: true, response_time: 0 }).responseText).toBe(
      'Response: 0ms',
    );
    expect(genericServiceCard({ connected: true, response_time: null }).responseText).toBe(
      'Response: --',
    );
    expect(genericServiceCard({}).responseText).toBe('Response: --');
  });

  it('soulseek maps the download source display names', () => {
    expect(soulseekCard({ connected: true, source: 'deezer_dl' }).title).toBe('Deezer');
    expect(soulseekCard({ connected: true, source: 'hifi' }).title).toBe('HiFi');
    expect(soulseekCard({ connected: true, source: 'unknown-thing' }).title).toBe(
      'Download Source',
    );
    expect(soulseekCard({ connected: true }).title).toBeNull();
    expect(Object.keys(DOWNLOAD_SOURCE_NAMES)).toHaveLength(12);
  });
});

describe('enrichmentChips', () => {
  it('keeps the vanilla display order and skips gated experimental keys', () => {
    expect(ENRICHMENT_CHIP_ORDER).toEqual([
      'musicbrainz',
      'spotify_enrichment',
      'itunes_enrichment',
      'deezer_enrichment',
      'jiosaavn_enrichment',
      'bandcamp_enrichment',
      'tidal_enrichment',
      'qobuz_enrichment',
      'lastfm',
      'genius',
      'audiodb',
      'acoustid',
      'listenbrainz',
    ]);
    const enrichment = {
      jiosaavn_enrichment: { name: 'JioSaavn', running: true, configured: true },
      bandcamp_enrichment: { name: 'Bandcamp', running: true, configured: true },
      musicbrainz: { name: 'MusicBrainz', running: true, configured: true },
    };
    expect(enrichmentChips(enrichment, false, false).map((chip) => chip.key)).toEqual([
      'musicbrainz',
    ]);
    expect(enrichmentChips(enrichment, true, true).map((chip) => chip.key)).toEqual([
      'musicbrainz',
      'jiosaavn_enrichment',
      'bandcamp_enrichment',
    ]);
  });

  it('walks the worker status chain', () => {
    const chip = (svc: Record<string, unknown>) =>
      enrichmentChips({ musicbrainz: { name: 'MB', ...svc } }, false, false)[0];
    expect(chip({ running: true, configured: false }).statusClass).toBe('not-configured');
    expect(chip({ running: true, configured: false }).statusDisplay).toBe('Set up');
    expect(chip({ running: false, configured: true, paused: true }).statusDisplay).toBe('Paused');
    expect(
      chip({ running: false, configured: true, paused: true, yield_reason: 'downloads' })
        .statusDisplay,
    ).toBe('Yielding');
    expect(chip({ running: true, configured: true }).statusClass).toBe('running');
    expect(chip({ running: true, configured: true, idle: true }).statusDisplay).toBe('Idle');
    expect(chip({ running: false, configured: true }).statusDisplay).toBe('Stopped');
  });

  it('workerless services are Ready / Set up', () => {
    const chips = enrichmentChips(
      {
        acoustid: { name: 'AcoustID', configured: true },
        listenbrainz: { name: 'ListenBrainz', configured: false },
      },
      false,
      false,
    );
    expect(chips[0].statusDisplay).toBe('Ready');
    expect(chips[0].statusClass).toBe('running');
    // not-configured WITH a settings selector renders the configure CTA
    expect(chips[1].statusDisplay).toBe('Configure →');
    expect(chips[1].tooltip).toBe('Click to configure in Settings');
    expect(chips[1].settingsSelector).toBe('.listenbrainz-title');
  });

  it('builds the spotify budget bar and the 24h activity', () => {
    const chips = enrichmentChips(
      {
        spotify_enrichment: {
          name: 'Spotify',
          running: true,
          configured: true,
          calls_1h: 5,
          calls_24h: 120,
          daily_budget: { used: 850, limit: 1000 },
        },
        lastfm: { name: 'Last.fm', running: true, configured: true, calls_24h: 1234 },
        genius: { name: 'Genius', running: true, configured: true, calls_24h: 0 },
      },
      false,
      false,
    );
    expect(chips[0].activity).toBe('850 / 1,000');
    expect(chips[0].budget).toEqual({ pct: 85, barClass: 'high' });
    expect(chips[0].tooltip).toBe(
      'Spotify — Running\nLast hour: 5 · Last 24h: 120\nDaily budget: 850 / 1000',
    );
    expect(chips[1].activity).toBe('1,234 / 24h');
    expect(chips[2].activity).toBeNull();
  });

  it('flags the exhausted budget bar', () => {
    const chip = enrichmentChips(
      {
        spotify_enrichment: {
          name: 'Spotify',
          running: true,
          configured: true,
          daily_budget: { used: 1000, limit: 1000, exhausted: true },
        },
      },
      false,
      false,
    )[0];
    expect(chip.budget).toEqual({ pct: 100, barClass: 'exhausted' });
    expect(chip.tooltip).toBe('Spotify — Running\nDaily budget: 1000 / 1000 (exhausted)');
  });

  it('exposes the settings selector registry verbatim', () => {
    expect(CHIP_SETTINGS_SELECTORS).toEqual({
      spotify_enrichment: '.spotify-title',
      tidal_enrichment: '.tidal-title',
      qobuz_enrichment: '.qobuz-title',
      lastfm: '.lastfm-title',
      genius: '.genius-title',
      acoustid: '.acoustid-title',
      listenbrainz: '.listenbrainz-title',
    });
  });
});

describe('the equalizer registries', () => {
  it('keep the vanilla service order and labels', () => {
    expect(RATE_GAUGE_SERVICES).toEqual([
      'spotify',
      'itunes',
      'deezer',
      'jiosaavn',
      'lastfm',
      'genius',
      'musicbrainz',
      'audiodb',
      'tidal',
      'qobuz',
      'discogs',
      'amazon',
    ]);
    expect(RATE_GAUGE_LABELS.itunes).toBe('Apple Music');
    expect(RATE_GAUGE_LABELS.amazon).toBe('Amazon Music');
    expect(RATE_GAUGE_LOGOS.audiodb).toBe('/static/audiodb.png');
    expect(RATE_GAUGE_LOGOS.amazon).toBe('/static/amazon.svg');
  });

  it('drops jiosaavn unless the experimental flag is on', () => {
    expect(visibleRateGaugeServices(false)).not.toContain('jiosaavn');
    expect(visibleRateGaugeServices(false)).toHaveLength(11);
    expect(visibleRateGaugeServices(true)).toContain('jiosaavn');
  });
});

describe('workerStatusLabel', () => {
  it('matches the vanilla chain', () => {
    expect(workerStatusLabel('not_configured')).toBe('Not configured');
    expect(workerStatusLabel('paused')).toBe('Paused');
    expect(workerStatusLabel('paused', { yield_reason: 'downloads' })).toBe('Yielding');
    expect(workerStatusLabel('idle')).toBe('Idle');
    expect(workerStatusLabel('running')).toBe('Running');
    expect(workerStatusLabel('anything-else')).toBe('Stopped');
  });
});

describe('eqBarFrame', () => {
  it('clamps the fill to [4%, 100%] but keeps the real ratio for danger/glow', () => {
    const idle = eqBarFrame(EQ_INITIAL, { cpm: 0, limit: 60 }, 0);
    expect(idle.pct).toBe(0.04);
    expect(idle.realPct).toBe(0);
    expect(idle.glow).toBe(0.04);
    expect(idle.danger).toBe(false);
    expect(idle.active).toBe(false);

    const maxed = eqBarFrame(EQ_INITIAL, { cpm: 120, limit: 60 }, 0);
    expect(maxed.pct).toBe(1);
    expect(maxed.realPct).toBe(2);
    expect(maxed.glow).toBe(1);
    expect(maxed.danger).toBe(true);
  });

  it('defaults the limit to 60 and stopped workers', () => {
    const view = eqBarFrame(EQ_INITIAL, { cpm: 30 }, 0);
    expect(view.pct).toBe(0.5);
    expect(view.wStatus).toBe('stopped');
    expect(view.statusLabel).toBe('Stopped');
    // running worker alone makes the bar active even at 0 cpm
    expect(eqBarFrame(EQ_INITIAL, { cpm: 0, worker: { status: 'running' } }, 0).active).toBe(true);
  });

  it('peak-hold: sticks at the max, holds 1200ms, then decays 0.045/update', () => {
    const first = eqBarFrame(EQ_INITIAL, { cpm: 30, limit: 60 }, 1000);
    expect(first.peak).toBe(0.5);
    expect(first.peakAt).toBe(1000);
    // Within the hold window the peak stays put while the fill drops.
    const held = eqBarFrame(first, { cpm: 6, limit: 60 }, 2000);
    expect(held.peak).toBe(0.5);
    expect(held.peakVisible).toBe(true);
    // Past the hold window it decays by 0.045.
    const decayed = eqBarFrame(held, { cpm: 6, limit: 60 }, 2300);
    expect(decayed.peak).toBeCloseTo(0.455, 10);
    // And never below the live fill.
    const floor = eqBarFrame({ ...held, peak: 0.12, peakAt: 0 }, { cpm: 6, limit: 60 }, 5000);
    expect(floor.peak).toBe(0.1);
  });

  it('flashes only on an upward step above the jitter threshold', () => {
    const base = eqBarFrame(EQ_INITIAL, { cpm: 5, limit: 60 }, 0);
    expect(base.flashSeq).toBe(1); // 5 - 0 > 1
    const flat = eqBarFrame(base, { cpm: 5.5, limit: 60 }, 0);
    expect(flat.flashSeq).toBe(1); // +0.5 is jitter
    const spike = eqBarFrame(flat, { cpm: 20, limit: 60 }, 0);
    expect(spike.flashSeq).toBe(2);
    const drop = eqBarFrame(spike, { cpm: 1, limit: 60 }, 0);
    expect(drop.flashSeq).toBe(2);
  });

  it('cooldown latches the largest rl_remaining as the denominator and drains', () => {
    const banStart = eqBarFrame(
      EQ_INITIAL,
      { cpm: 0, limit: 60, rate_limited: true, rl_remaining: 300 },
      0,
    );
    expect(banStart.cooling).toBe(true);
    expect(banStart.rlTotal).toBe(300);
    expect(banStart.cooldownHeight).toBe(100);
    expect(banStart.cooldownTime).toBe('5:00');
    expect(banStart.rateLimited).toBe(true);
    expect(banStart.danger).toBe(true);
    expect(banStart.emberCount).toBe(0); // suppressed during cooldown

    const draining = eqBarFrame(
      banStart,
      { cpm: 0, limit: 60, rate_limited: true, rl_remaining: 150 },
      0,
    );
    expect(draining.rlTotal).toBe(300);
    expect(draining.cooldownHeight).toBe(50);
    expect(draining.cooldownTime).toBe('2:30');

    const over = eqBarFrame(draining, { cpm: 0, limit: 60 }, 0);
    expect(over.cooling).toBe(false);
    expect(over.rlTotal).toBe(0);
    expect(over.recoverSeq).toBe(1); // the ban just ended
    expect(over.cooldownTime).toBe('');
    // A later calm frame does NOT re-fire the recovery.
    expect(eqBarFrame(over, { cpm: 0, limit: 60 }, 0).recoverSeq).toBe(1);
  });

  it('spawns embers by traffic tier, real ratio only', () => {
    const tier = (cpm: number) => eqBarFrame(EQ_INITIAL, { cpm, limit: 100 }, 0).emberCount;
    expect(tier(2)).toBe(0); // realPct 0.02 ≤ 0.03
    expect(tier(10)).toBe(1);
    expect(tier(30)).toBe(2);
    expect(tier(70)).toBe(3);
  });

  it('builds the spotify budget ring with the vanilla color bands', () => {
    const budget = (used: number, extra: Record<string, unknown> = {}) =>
      eqBarFrame(
        EQ_INITIAL,
        { cpm: 0, limit: 60, worker: { daily_budget: { used, limit: 100 }, ...extra } },
        0,
      ).budget;
    expect(budget(10)!.color).toBe('#4ade80');
    expect(budget(80)!.color).toBe('#fbbf24');
    expect(budget(99)!.color).toBe('#ef4444');
    expect(budget(1)!.pct).toBe(0.02); // the 2% visual floor
    expect(budget(50)!.title).toBe('Daily API budget: 50/100');

    const bridged = eqBarFrame(
      EQ_INITIAL,
      {
        cpm: 0,
        limit: 60,
        worker: {
          using_free: true,
          daily_budget: { used: 100, limit: 100, exhausted: true },
        },
      },
      0,
    ).budget!;
    expect(bridged.bridged).toBe(true);
    expect(bridged.color).toBe('#a78bfa');
    expect(bridged.title).toBe('Daily budget spent — running on Spotify Free (100/100)');

    expect(eqBarFrame(EQ_INITIAL, { cpm: 0, limit: 60 }, 0).budget).toBeNull();
  });

  it('tracks the rolling-counter endpoints', () => {
    const view = eqBarFrame({ ...EQ_INITIAL, value: 4.4 }, { cpm: 9.6, limit: 60 }, 0);
    expect(view.prevRounded).toBe(4);
    expect(view.rounded).toBe(10);
  });
});
