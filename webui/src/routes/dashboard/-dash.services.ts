/**
 * P5 pure core — the Service Status card, the enrichment chips, and the
 * rate-monitor equalizer's per-bar reducer. Transcribed 1:1 from:
 * - shared-helpers.js: getMetadataSourceLabel / getMetadataSourcePresentation /
 *   updateServiceStatus (the dashboard half) / renderEnrichmentCards
 * - settings.js: formatRateLimitDuration
 * - api-monitor.js: the _RATE_GAUGE_* registries, _workerStatusLabel, and the
 *   per-bar math of _renderEqualizerBars (the DOM/animation halves live in the
 *   UI component; everything computable is here).
 */

// ── /status payload slices ───────────────────────────────────────────────────

export interface MetadataSourceStatus {
  source?: string;
  connected?: boolean;
  response_time?: number | null;
  [key: string]: unknown;
}

export interface SpotifyStatus {
  authenticated?: boolean;
  rate_limited?: boolean;
  rate_limit?: { remaining_seconds?: number } | null;
  post_ban_cooldown?: number;
  [key: string]: unknown;
}

export interface GenericServiceStatus {
  connected?: boolean;
  source?: string;
  type?: string;
  response_time?: number | null;
  [key: string]: unknown;
}

// ── getMetadataSourceLabel (shared-helpers.js:4100) ──────────────────────────

export function getMetadataSourceLabel(source: string | undefined): string {
  if (source === 'deezer') return 'Deezer';
  if (source === 'discogs') return 'Discogs';
  if (source === 'hydrabase') return 'Hydrabase';
  if (source === 'itunes') return 'iTunes';
  if (source === 'musicbrainz') return 'MusicBrainz';
  if (source === 'jiosaavn') return 'JioSaavn';
  if (source === 'spotify_free') return 'Spotify (no auth)';
  if (source === 'spotify') return 'Spotify';
  return 'Unmapped';
}

// ── formatRateLimitDuration (settings.js:5305) ───────────────────────────────

export function formatRateLimitDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '0s';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ── getMetadataSourcePresentation (shared-helpers.js:4112) ───────────────────

export interface MetadataSourcePresentation {
  statusClass: string;
  statusText: string;
  dotClass: string;
  dotTitle: string;
  sessionActive: boolean;
}

export function getMetadataSourcePresentation(
  metadataStatus: MetadataSourceStatus,
  spotifyStatus: SpotifyStatus,
): MetadataSourcePresentation {
  const source = metadataStatus?.source;
  const sourceLabel = getMetadataSourceLabel(source);
  const connected = metadataStatus?.connected === true;
  // 'spotify_free' (the no-auth composite) is part of the Spotify family for
  // session/rate-limit/cooldown display.
  const spotifyFamily = source === 'spotify' || source === 'spotify_free';
  const sessionActive = spotifyStatus?.authenticated === true || (spotifyFamily && connected);
  const rateLimited = !!(spotifyFamily && spotifyStatus?.rate_limited && spotifyStatus?.rate_limit);
  const cooldown = !!(spotifyFamily && (spotifyStatus?.post_ban_cooldown ?? 0) > 0);

  if (rateLimited) {
    const remaining = spotifyStatus.rate_limit?.remaining_seconds || 0;
    return {
      statusClass: 'rate-limited',
      statusText: `Spotify paused — ${formatRateLimitDuration(remaining)}`,
      dotClass: 'rate-limited',
      dotTitle: `Spotify paused — ${formatRateLimitDuration(remaining)} remaining`,
      sessionActive,
    };
  }

  if (cooldown) {
    const remaining = spotifyStatus.post_ban_cooldown!;
    return {
      statusClass: 'rate-limited',
      statusText: `Spotify recovering — ${formatRateLimitDuration(remaining)}`,
      dotClass: 'rate-limited',
      dotTitle: `Spotify recovering — ${formatRateLimitDuration(remaining)} cooldown`,
      sessionActive,
    };
  }

  if (source) {
    return {
      statusClass: connected ? 'connected' : 'disconnected',
      // Uniform state word; the ms lives in the card's Response row (D4).
      statusText: connected ? 'Connected' : 'Disconnected',
      dotClass: connected ? 'connected' : 'disconnected',
      dotTitle: connected ? sourceLabel : 'Disconnected',
      sessionActive,
    };
  }

  return {
    statusClass: 'disconnected',
    statusText: 'Disconnected',
    dotClass: 'disconnected',
    dotTitle: 'Disconnected',
    sessionActive,
  };
}

// ── updateServiceStatus, the dashboard-card half (shared-helpers.js:4165) ────

export interface ServiceCardView {
  indicatorClass: string;
  statusText: string;
  statusTextClass: string;
  responseText: string;
  /** New card title, or null = KEEP the previous title (the vanilla only
   *  rewrites the title when the payload carries a source). */
  title: string | null;
  /** metadata-source only: the service the Test button should test — the
   *  vanilla rewrites the button's onclick to the literal source. Null keeps
   *  the previous target. */
  testService: string | null;
}

function responseText(statusData: { response_time?: number | null } | undefined): string {
  const rt = statusData?.response_time;
  // Response time row — populated uniformly for every service card (D4).
  return rt !== undefined && rt !== null ? `Response: ${rt}ms` : 'Response: --';
}

export function metadataSourceCard(
  statusData: MetadataSourceStatus,
  spotifyStatus: SpotifyStatus,
): ServiceCardView {
  const presentation = getMetadataSourcePresentation(statusData || {}, spotifyStatus || {});
  return {
    indicatorClass: `service-card-indicator ${presentation.statusClass}`,
    statusText: presentation.statusText,
    statusTextClass: `service-card-status-text ${presentation.statusClass}`,
    responseText: responseText(statusData),
    title: statusData?.source ? getMetadataSourceLabel(statusData.source) : null,
    testService: statusData?.source || null,
  };
}

export function genericServiceCard(statusData: GenericServiceStatus): ServiceCardView {
  const connected = !!statusData?.connected;
  return {
    indicatorClass: connected
      ? 'service-card-indicator connected'
      : 'service-card-indicator disconnected',
    statusText: connected ? 'Connected' : 'Disconnected',
    statusTextClass: connected
      ? 'service-card-status-text connected'
      : 'service-card-status-text disconnected',
    responseText: responseText(statusData),
    title: null,
    testService: null,
  };
}

/** The download-source display names (shared-helpers.js:4234), verbatim. */
export const DOWNLOAD_SOURCE_NAMES: Record<string, string> = {
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  hifi: 'HiFi',
  deezer_dl: 'Deezer',
  amazon: 'Amazon',
  lidarr: 'Lidarr',
  soundcloud: 'SoundCloud',
  torrent: 'Torrent',
  usenet: 'Usenet',
  hybrid: 'Hybrid',
};

export function soulseekCard(statusData: GenericServiceStatus): ServiceCardView {
  const view = genericServiceCard(statusData);
  if (statusData?.source) {
    view.title = DOWNLOAD_SOURCE_NAMES[statusData.source] || 'Download Source';
  }
  return view;
}

// ── renderEnrichmentCards (shared-helpers.js:4287) → chip views ──────────────

export interface EnrichmentServicePayload {
  name?: string;
  configured?: boolean;
  running?: boolean;
  paused?: boolean;
  idle?: boolean;
  yield_reason?: string;
  calls_1h?: number;
  calls_24h?: number;
  daily_budget?: { used: number; limit: number; exhausted?: boolean } | null;
  [key: string]: unknown;
}

export interface EnrichmentChipView {
  key: string;
  statusClass: string;
  name: string;
  /** The inline activity span text, or null for none. */
  activity: string | null;
  /** Spotify's budget bar, or null. */
  budget: { pct: number; barClass: string } | null;
  /** The title attribute — tooltip lines joined with newlines. */
  tooltip: string;
  statusDisplay: string;
  /** Click-to-configure target on the settings page, or null (no click). */
  settingsSelector: string | null;
}

/** Service display order — the vanilla's serviceOrder registry, verbatim. */
export const ENRICHMENT_CHIP_ORDER = [
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
] as const;

/** Map service keys to their settings page selector for click-to-configure. */
export const CHIP_SETTINGS_SELECTORS: Record<string, string> = {
  spotify_enrichment: '.spotify-title',
  tidal_enrichment: '.tidal-title',
  qobuz_enrichment: '.qobuz-title',
  lastfm: '.lastfm-title',
  genius: '.genius-title',
  acoustid: '.acoustid-title',
  listenbrainz: '.listenbrainz-title',
};

export function enrichmentChips(
  enrichment: Record<string, EnrichmentServicePayload>,
  jiosaavnEnabled: boolean,
  bandcampEnabled: boolean,
): EnrichmentChipView[] {
  const chips: EnrichmentChipView[] = [];
  for (const key of ENRICHMENT_CHIP_ORDER) {
    if (key === 'jiosaavn_enrichment' && !jiosaavnEnabled) continue;
    if (key === 'bandcamp_enrichment' && !bandcampEnabled) continue;
    const svc = enrichment[key];
    if (!svc) continue;

    // Determine status class and text
    let statusClass: string;
    let statusLabel: string;
    if ('running' in svc) {
      if (!svc.configured) {
        statusClass = 'not-configured';
        statusLabel = 'Set up';
      } else if (svc.paused) {
        statusClass = 'paused';
        statusLabel = svc.yield_reason === 'downloads' ? 'Yielding' : 'Paused';
      } else if (svc.running) {
        statusClass = svc.idle ? 'idle' : 'running';
        statusLabel = svc.idle ? 'Idle' : 'Running';
      } else {
        statusClass = 'stopped';
        statusLabel = 'Stopped';
      }
    } else {
      statusClass = svc.configured ? 'running' : 'not-configured';
      statusLabel = svc.configured ? 'Ready' : 'Set up';
    }

    const selector = CHIP_SETTINGS_SELECTORS[key] ?? null;

    // Build activity display — human-readable, not cryptic numbers
    let activity: string | null = null;
    let budget: EnrichmentChipView['budget'] = null;
    const isSpotify = key === 'spotify_enrichment';

    if ('running' in svc && svc.configured) {
      const c24h = svc.calls_24h || 0;

      if (isSpotify && svc.daily_budget) {
        // Spotify: show budget usage prominently
        const b = svc.daily_budget;
        const pct = Math.min(100, Math.round((b.used / b.limit) * 100));
        const barClass = b.exhausted ? 'exhausted' : pct > 80 ? 'high' : '';
        activity = `${b.used.toLocaleString()} / ${b.limit.toLocaleString()}`;
        budget = { pct, barClass };
      } else if (c24h > 0) {
        // Other services: show 24h count
        activity = `${c24h.toLocaleString()} / 24h`;
      }
    }

    // Tooltip: full details including 1h breakdown
    let tooltipLines = [svc.name + ' — ' + statusLabel];
    if ('running' in svc && svc.configured) {
      const c1h = svc.calls_1h || 0;
      const c24h = svc.calls_24h || 0;
      if (c24h > 0 || c1h > 0) tooltipLines.push('Last hour: ' + c1h + ' · Last 24h: ' + c24h);
    }
    if (isSpotify && svc.daily_budget) {
      const b = svc.daily_budget;
      tooltipLines.push(
        'Daily budget: ' + b.used + ' / ' + b.limit + (b.exhausted ? ' (exhausted)' : ''),
      );
    }
    if (selector && statusClass === 'not-configured') {
      tooltipLines = ['Click to configure in Settings'];
    }

    const statusDisplay =
      statusClass === 'not-configured' && selector ? 'Configure →' : statusLabel;

    chips.push({
      key,
      statusClass,
      name: svc.name || '',
      activity,
      budget,
      tooltip: tooltipLines.join('\n'),
      statusDisplay,
      settingsSelector: selector,
    });
  }
  return chips;
}

// ── The rate-monitor equalizer (api-monitor.js) ──────────────────────────────

/** The FOURTH provider registry — the equalizer's services, verbatim order. */
export const RATE_GAUGE_SERVICES = [
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
] as const;

export type RateGaugeService = (typeof RATE_GAUGE_SERVICES)[number];

export const RATE_GAUGE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  itunes: 'Apple Music',
  deezer: 'Deezer',
  jiosaavn: 'JioSaavn',
  lastfm: 'Last.fm',
  genius: 'Genius',
  musicbrainz: 'MusicBrainz',
  audiodb: 'AudioDB',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  discogs: 'Discogs',
  amazon: 'Amazon Music',
};

export const RATE_GAUGE_COLORS: Record<string, string> = {
  spotify: '#1DB954',
  itunes: '#FC3C44',
  deezer: '#A238FF',
  jiosaavn: '#2BC5B4',
  lastfm: '#D51007',
  genius: '#FFFF64',
  musicbrainz: '#BA478F',
  audiodb: '#00BCD4',
  tidal: '#00FFFF',
  qobuz: '#FF6B35',
  discogs: '#D4A574',
  amazon: '#FF9900',
};

export const RATE_GAUGE_LOGOS: Record<string, string> = {
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  deezer: '/static/img/brands/deezer.png',
  jiosaavn: '/static/img/brands/jiosaavn.webp',
  lastfm: '/static/img/brands/lastfm.png',
  genius: '/static/img/brands/genius.png',
  musicbrainz: '/static/img/brands/musicbrainz.png',
  audiodb: '/static/audiodb.png',
  tidal: '/static/img/brands/tidal.svg',
  qobuz: '/static/img/brands/qobuz.svg',
  discogs: '/static/img/brands/discogs.svg',
  amazon: '/static/amazon.svg',
};

export function visibleRateGaugeServices(jiosaavnEnabled: boolean): readonly string[] {
  if (jiosaavnEnabled) return RATE_GAUGE_SERVICES;
  return RATE_GAUGE_SERVICES.filter((svc) => svc !== 'jiosaavn');
}

/** _workerStatusLabel (api-monitor.js:510), verbatim chain. */
export function workerStatusLabel(status: string, worker: { yield_reason?: string } = {}): string {
  if (status === 'not_configured') return 'Not configured';
  if (status === 'paused') return worker.yield_reason === 'downloads' ? 'Yielding' : 'Paused';
  if (status === 'idle') return 'Idle';
  if (status === 'running') return 'Running';
  return 'Stopped';
}

export interface RateServicePayload {
  cpm?: number;
  limit?: number;
  rate_limited?: boolean;
  rl_remaining?: number;
  worker?: {
    status?: string;
    yield_reason?: string;
    using_free?: boolean;
    daily_budget?: { used?: number; limit: number; exhausted?: boolean } | null;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

/** The reducer's carry-over half — what the vanilla keeps in `_eqDisplay`,
 *  plus edge sequence counters for the UI's imperative one-shots. */
export interface EqBarState {
  value: number;
  pct: number;
  peak?: number;
  peakAt?: number;
  rlTotal?: number;
  cooling?: boolean;
  /** Incremented on each upward step > 1 — the UI restarts .peak-flash on it. */
  flashSeq: number;
  /** Incremented when a ban ends — the UI restarts .recovered on it. */
  recoverSeq: number;
}

export const EQ_INITIAL: EqBarState = { value: 0, pct: 0.04, flashSeq: 0, recoverSeq: 0 };

export interface EqBarView extends EqBarState {
  /** Rounded display count. */
  rounded: number;
  /** Previous rounded count — the rolling-number animation's start. */
  prevRounded: number;
  /** The REAL (unclamped) ratio — drives danger + glow + embers. */
  realPct: number;
  /** --eq-glow value. */
  glow: number;
  peakVisible: boolean;
  wStatus: string;
  statusLabel: string;
  danger: boolean;
  active: boolean;
  rateLimited: boolean;
  rlRemaining: number;
  /** Cooldown column height in %, 0 when not cooling. */
  cooldownHeight: number;
  /** m:ss countdown, '' when not cooling. */
  cooldownTime: string;
  /** Ember spawn count for this frame; 0 = spawn none. The effects gates
   *  (_reduceEffectsActive/_maxPerfActive) are applied by the UI. */
  emberCount: number;
  budget: { pct: number; color: string; bridged: boolean; title: string } | null;
}

export function eqBarFrame(prev: EqBarState, d: RateServicePayload, nowTs: number): EqBarView {
  const value = d.cpm || 0;
  const max = d.limit || 60;
  // Clamp the visual fill to [4%, 100%] — the floor keeps idle bars visible.
  const pct = Math.max(0.04, Math.min(value / max, 1));
  const worker = d.worker || {};
  const wStatus = worker.status || 'stopped';
  const isRateLimited = d.rate_limited === true;

  // Peak-hold tick: sticks at the recent max, holds ~1.2s, then falls
  // 0.045/update until it rests on the live fill.
  let peak = prev.peak ?? pct;
  let peakAt = prev.peakAt ?? nowTs;
  if (pct >= peak) {
    peak = pct;
    peakAt = nowTs;
  } else if (nowTs - peakAt > 1200) {
    peak = Math.max(pct, peak - 0.045);
  }
  const peakVisible = peak > pct + 0.015 && peak > 0.06;

  const realPct = max > 0 ? value / max : 0;
  const glow = Math.min(1, realPct + 0.04);

  const rounded = Math.round(value);
  const prevRounded = Math.round(prev.value);

  // Peak-flash detector — only on real increases above the noise floor.
  const PEAK_JITTER_THRESHOLD = 1;
  const flashSeq = value - prev.value > PEAK_JITTER_THRESHOLD ? prev.flashSeq + 1 : prev.flashSeq;

  // Danger reads the REAL ratio, not the clamped one.
  const danger = realPct > 0.8 || isRateLimited;
  const active = value > 0 || wStatus === 'running';

  // Cooldown drain — latch the largest rl_remaining seen this ban.
  const rlRemaining = isRateLimited ? Math.max(0, Math.round(d.rl_remaining || 0)) : 0;
  let rlTotal = prev.rlTotal || 0;
  const cooling = rlRemaining > 0;
  let cooldownHeight = 0;
  let cooldownTime = '';
  let recoverSeq = prev.recoverSeq;
  if (cooling) {
    rlTotal = Math.max(rlTotal, rlRemaining);
    cooldownHeight = (rlRemaining / rlTotal) * 100;
    const m = Math.floor(rlRemaining / 60);
    const s = String(rlRemaining % 60).padStart(2, '0');
    cooldownTime = `${m}:${s}`;
  } else {
    rlTotal = 0;
    // Recovery moment: the ban just ended.
    if (prev.cooling) recoverSeq = prev.recoverSeq + 1;
  }

  // Embers in proportion to REAL traffic; suppressed during cooldown.
  const emberCount = !cooling && realPct > 0.03 ? (realPct > 0.6 ? 3 : realPct > 0.25 ? 2 : 1) : 0;

  // Daily-budget ring (Spotify): green → amber → red, purple once bridged.
  let budget: EqBarView['budget'] = null;
  const b = worker.daily_budget;
  if (b && b.limit > 0) {
    const bPct = Math.min(1, (b.used || 0) / b.limit);
    const bridged = !!worker.using_free && !!b.exhausted;
    budget = {
      pct: Math.max(bPct, 0.02),
      color: bridged ? '#a78bfa' : bPct < 0.7 ? '#4ade80' : bPct < 0.95 ? '#fbbf24' : '#ef4444',
      bridged,
      title: bridged
        ? `Daily budget spent — running on Spotify Free (${b.used}/${b.limit})`
        : `Daily API budget: ${b.used}/${b.limit}`,
    };
  }

  return {
    value,
    pct,
    peak,
    peakAt,
    rlTotal,
    cooling,
    flashSeq,
    recoverSeq,
    rounded,
    prevRounded,
    realPct,
    glow,
    peakVisible,
    wStatus,
    statusLabel: workerStatusLabel(wStatus, worker),
    danger,
    active,
    rateLimited: isRateLimited,
    rlRemaining,
    cooldownHeight,
    cooldownTime,
    emberCount,
    budget,
  };
}
