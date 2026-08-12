/**
 * The dashboard header — title block, the 17 worker orbs + Manage Workers
 * button, and the watchlist/wishlist quick-nav. Transcribed 1:1 from
 * index.html 2227-2624; the structural fidelity is pinned by the artefact
 * differential test against that region.
 *
 * Chrome vs behaviour: the ORB MARKUP is genuinely regular (same shape, per-orb
 * ids/classes/copy), so it is table-driven below — every difference is an
 * explicit field, not a convention. The BEHAVIOUR (state machines, gates,
 * toggle quirks) is one-function-per-provider in -dash.core.ts / -dash.header.ts,
 * per the P0's no-flattening rule.
 *
 * worker-orbs.js contract (it reads this DOM every frame while on the
 * dashboard): the container classes below are its WORKER_DEFS anchors, the
 * button's `active` class is its live-state read, and the nodes must STAY
 * MOUNTED — JioSaavn/Hydrabase hide via inline display, never conditional
 * render, so the orb layer's captured element references never go stale.
 */

import type { CSSProperties } from 'react';

import { useSyncExternalStore } from 'react';

import type { HeaderPill, HeaderPillId } from '../-dash.header';

import { useDashboardHeader } from '../-dash.header';
import { buildHelloStats, countBusyWorkers, greetingForHour } from '../-dash.hello';
import { lastDbStats, subscribeDbStats } from '../-dash.library';

interface OrbChrome {
  id: HeaderPillId;
  /** The preceding HTML comment in the vanilla markup — kept as documentation. */
  note: string;
  containerClass: string;
  containerId?: string;
  buttonClass: string;
  buttonId: string;
  buttonTitle: string;
  /** img logo, or the JioSaavn emoji span. */
  logo: { src: string; alt: string; className: string } | { emoji: string; className: string };
  spinnerClass: string;
  tooltipClass: string;
  tooltipId: string;
  contentClass: string;
  headerClass: string;
  headerText: string;
  bodyClass: string;
  bodyId: string;
  statusId: string;
  /** Hydrabase has no current/progress rows. */
  currentId?: string;
  progressId?: string;
}

const ORBS: OrbChrome[] = [
  {
    id: 'musicbrainz',
    note: 'MusicBrainz Enrichment Status Icon',
    containerClass: 'mb-button-container',
    buttonClass: 'musicbrainz-button',
    buttonId: 'musicbrainz-button',
    buttonTitle: 'MusicBrainz Library Enrichment',
    logo: { src: '/static/img/brands/musicbrainz.png', alt: 'MusicBrainz', className: 'mb-logo' },
    spinnerClass: 'mb-spinner',
    tooltipClass: 'musicbrainz-tooltip',
    tooltipId: 'musicbrainz-tooltip',
    // MusicBrainz alone uses the GENERIC tooltip classes and the mb- body id.
    contentClass: 'tooltip-content',
    headerClass: 'tooltip-header',
    headerText: '🎵 MusicBrainz Enrichment',
    bodyClass: 'tooltip-body',
    bodyId: 'mb-tooltip-body',
    statusId: 'mb-tooltip-status',
    currentId: 'mb-tooltip-current',
    progressId: 'mb-tooltip-progress',
  },
  {
    id: 'audiodb',
    note: 'AudioDB Enrichment Status Icon',
    containerClass: 'audiodb-button-container',
    buttonClass: 'audiodb-button',
    buttonId: 'audiodb-button',
    buttonTitle: 'AudioDB Artist Enrichment',
    logo: { src: '/static/img/brands/audiodb.png', alt: 'AudioDB', className: 'audiodb-logo' },
    spinnerClass: 'audiodb-spinner',
    tooltipClass: 'audiodb-tooltip',
    tooltipId: 'audiodb-tooltip',
    contentClass: 'audiodb-tooltip-content',
    headerClass: 'audiodb-tooltip-header',
    headerText: '🎶 AudioDB Enrichment',
    bodyClass: 'audiodb-tooltip-body',
    bodyId: 'audiodb-tooltip-body',
    statusId: 'audiodb-tooltip-status',
    currentId: 'audiodb-tooltip-current',
    progressId: 'audiodb-tooltip-progress',
  },
  {
    id: 'deezer',
    note: 'Deezer Enrichment Status Icon',
    containerClass: 'deezer-button-container',
    buttonClass: 'deezer-button',
    buttonId: 'deezer-button',
    buttonTitle: 'Deezer Library Enrichment',
    logo: { src: '/static/img/brands/deezer.png', alt: 'Deezer', className: 'deezer-logo' },
    spinnerClass: 'deezer-spinner',
    tooltipClass: 'deezer-tooltip',
    tooltipId: 'deezer-tooltip',
    contentClass: 'deezer-tooltip-content',
    headerClass: 'deezer-tooltip-header',
    headerText: '🎧 Deezer Enrichment',
    bodyClass: 'deezer-tooltip-body',
    bodyId: 'deezer-tooltip-body',
    statusId: 'deezer-tooltip-status',
    currentId: 'deezer-tooltip-current',
    progressId: 'deezer-tooltip-progress',
  },
  {
    id: 'jiosaavn',
    note: 'JioSaavn Enrichment Status Icon (hidden until experimental flag on)',
    containerClass: 'jiosaavn-button-container',
    buttonClass: 'jiosaavn-button',
    buttonId: 'jiosaavn-button',
    buttonTitle: 'JioSaavn Library Enrichment',
    logo: { emoji: '🎵', className: 'jiosaavn-logo' },
    spinnerClass: 'jiosaavn-spinner',
    tooltipClass: 'jiosaavn-tooltip',
    tooltipId: 'jiosaavn-tooltip',
    contentClass: 'jiosaavn-tooltip-content',
    headerClass: 'jiosaavn-tooltip-header',
    headerText: 'JioSaavn Enrichment',
    bodyClass: 'jiosaavn-tooltip-body',
    bodyId: 'jiosaavn-tooltip-body',
    statusId: 'jiosaavn-tooltip-status',
    currentId: 'jiosaavn-tooltip-current',
    progressId: 'jiosaavn-tooltip-progress',
  },
  {
    id: 'spotify',
    note: 'Spotify Enrichment Status Icon',
    containerClass: 'spotify-enrich-button-container',
    buttonClass: 'spotify-enrich-button',
    buttonId: 'spotify-enrich-button',
    buttonTitle: 'Spotify Library Enrichment',
    logo: {
      src: '/static/img/brands/spotify.png',
      alt: 'Spotify',
      className: 'spotify-enrich-logo',
    },
    spinnerClass: 'spotify-enrich-spinner',
    tooltipClass: 'spotify-enrich-tooltip',
    tooltipId: 'spotify-enrich-tooltip',
    contentClass: 'spotify-enrich-tooltip-content',
    headerClass: 'spotify-enrich-tooltip-header',
    headerText: 'Spotify Enrichment',
    bodyClass: 'spotify-enrich-tooltip-body',
    bodyId: 'spotify-enrich-tooltip-body',
    statusId: 'spotify-enrich-tooltip-status',
    currentId: 'spotify-enrich-tooltip-current',
    progressId: 'spotify-enrich-tooltip-progress',
  },
  {
    id: 'itunes',
    note: 'iTunes Enrichment Status Icon',
    containerClass: 'itunes-enrich-button-container',
    buttonClass: 'itunes-enrich-button',
    buttonId: 'itunes-enrich-button',
    buttonTitle: 'iTunes Library Enrichment',
    logo: { src: '/static/img/brands/itunes.png', alt: 'iTunes', className: 'itunes-enrich-logo' },
    spinnerClass: 'itunes-enrich-spinner',
    tooltipClass: 'itunes-enrich-tooltip',
    tooltipId: 'itunes-enrich-tooltip',
    contentClass: 'itunes-enrich-tooltip-content',
    headerClass: 'itunes-enrich-tooltip-header',
    headerText: 'iTunes Enrichment',
    bodyClass: 'itunes-enrich-tooltip-body',
    bodyId: 'itunes-enrich-tooltip-body',
    statusId: 'itunes-enrich-tooltip-status',
    currentId: 'itunes-enrich-tooltip-current',
    progressId: 'itunes-enrich-tooltip-progress',
  },
  {
    id: 'lastfm',
    note: 'Last.fm Enrichment Status Icon',
    containerClass: 'lastfm-enrich-button-container',
    buttonClass: 'lastfm-enrich-button',
    buttonId: 'lastfm-enrich-button',
    buttonTitle: 'Last.fm Library Enrichment',
    logo: { src: '/static/img/brands/lastfm.png', alt: 'Last.fm', className: 'lastfm-enrich-logo' },
    spinnerClass: 'lastfm-enrich-spinner',
    tooltipClass: 'lastfm-enrich-tooltip',
    tooltipId: 'lastfm-enrich-tooltip',
    contentClass: 'lastfm-enrich-tooltip-content',
    headerClass: 'lastfm-enrich-tooltip-header',
    headerText: 'Last.fm Enrichment',
    bodyClass: 'lastfm-enrich-tooltip-body',
    bodyId: 'lastfm-enrich-tooltip-body',
    statusId: 'lastfm-enrich-tooltip-status',
    currentId: 'lastfm-enrich-tooltip-current',
    progressId: 'lastfm-enrich-tooltip-progress',
  },
  {
    id: 'genius',
    note: 'Genius Enrichment Status Icon',
    containerClass: 'genius-enrich-button-container',
    buttonClass: 'genius-enrich-button',
    buttonId: 'genius-enrich-button',
    buttonTitle: 'Genius Library Enrichment',
    logo: { src: '/static/img/brands/genius.png', alt: 'Genius', className: 'genius-enrich-logo' },
    spinnerClass: 'genius-enrich-spinner',
    tooltipClass: 'genius-enrich-tooltip',
    tooltipId: 'genius-enrich-tooltip',
    contentClass: 'genius-enrich-tooltip-content',
    headerClass: 'genius-enrich-tooltip-header',
    headerText: 'Genius Enrichment',
    bodyClass: 'genius-enrich-tooltip-body',
    bodyId: 'genius-enrich-tooltip-body',
    statusId: 'genius-enrich-tooltip-status',
    currentId: 'genius-enrich-tooltip-current',
    progressId: 'genius-enrich-tooltip-progress',
  },
  {
    id: 'bandcamp',
    note: 'Bandcamp Enrichment Status Icon (experimental — off by default)',
    containerClass: 'bandcamp-enrich-button-container',
    buttonClass: 'bandcamp-enrich-button',
    buttonId: 'bandcamp-enrich-button',
    buttonTitle: 'Bandcamp Library Enrichment',
    logo: {
      src: '/static/img/brands/bandcamp.svg',
      alt: 'Bandcamp',
      className: 'bandcamp-enrich-logo',
    },
    spinnerClass: 'bandcamp-enrich-spinner',
    tooltipClass: 'bandcamp-enrich-tooltip',
    tooltipId: 'bandcamp-enrich-tooltip',
    contentClass: 'bandcamp-enrich-tooltip-content',
    headerClass: 'bandcamp-enrich-tooltip-header',
    headerText: 'Bandcamp Enrichment',
    bodyClass: 'bandcamp-enrich-tooltip-body',
    bodyId: 'bandcamp-enrich-tooltip-body',
    statusId: 'bandcamp-enrich-tooltip-status',
    currentId: 'bandcamp-enrich-tooltip-current',
    progressId: 'bandcamp-enrich-tooltip-progress',
  },
  {
    id: 'tidal',
    note: 'Tidal Enrichment Status Icon',
    containerClass: 'tidal-enrich-button-container',
    buttonClass: 'tidal-enrich-button',
    buttonId: 'tidal-enrich-button',
    buttonTitle: 'Tidal Library Enrichment',
    logo: { src: '/static/img/brands/tidal.svg', alt: 'Tidal', className: 'tidal-enrich-logo' },
    spinnerClass: 'tidal-enrich-spinner',
    tooltipClass: 'tidal-enrich-tooltip',
    tooltipId: 'tidal-enrich-tooltip',
    contentClass: 'tidal-enrich-tooltip-content',
    headerClass: 'tidal-enrich-tooltip-header',
    headerText: 'Tidal Enrichment',
    bodyClass: 'tidal-enrich-tooltip-body',
    bodyId: 'tidal-enrich-tooltip-body',
    statusId: 'tidal-enrich-tooltip-status',
    currentId: 'tidal-enrich-tooltip-current',
    progressId: 'tidal-enrich-tooltip-progress',
  },
  {
    id: 'qobuz',
    note: 'Qobuz Enrichment Status Icon',
    containerClass: 'qobuz-enrich-button-container',
    buttonClass: 'qobuz-enrich-button',
    buttonId: 'qobuz-enrich-button',
    buttonTitle: 'Qobuz Library Enrichment',
    logo: { src: '/static/img/brands/qobuz.svg', alt: 'Qobuz', className: 'qobuz-enrich-logo' },
    spinnerClass: 'qobuz-enrich-spinner',
    tooltipClass: 'qobuz-enrich-tooltip',
    tooltipId: 'qobuz-enrich-tooltip',
    contentClass: 'qobuz-enrich-tooltip-content',
    headerClass: 'qobuz-enrich-tooltip-header',
    headerText: 'Qobuz Enrichment',
    bodyClass: 'qobuz-enrich-tooltip-body',
    bodyId: 'qobuz-enrich-tooltip-body',
    statusId: 'qobuz-enrich-tooltip-status',
    currentId: 'qobuz-enrich-tooltip-current',
    progressId: 'qobuz-enrich-tooltip-progress',
  },
  {
    id: 'discogs',
    note: 'Discogs Enrichment Status Icon',
    containerClass: 'discogs-button-container',
    buttonClass: 'discogs-button',
    buttonId: 'discogs-button',
    buttonTitle: 'Discogs Library Enrichment',
    logo: { src: '/static/img/brands/discogs.svg', alt: 'Discogs', className: 'discogs-logo' },
    spinnerClass: 'discogs-spinner',
    tooltipClass: 'discogs-tooltip',
    tooltipId: 'discogs-tooltip',
    contentClass: 'discogs-tooltip-content',
    headerClass: 'discogs-tooltip-header',
    headerText: 'Discogs Enrichment',
    bodyClass: 'discogs-tooltip-body',
    bodyId: 'discogs-tooltip-body',
    statusId: 'discogs-tooltip-status',
    currentId: 'discogs-tooltip-current',
    progressId: 'discogs-tooltip-progress',
  },
  {
    id: 'amazon',
    note: 'Amazon Music Enrichment Status Icon',
    containerClass: 'amazon-enrich-button-container',
    buttonClass: 'amazon-enrich-button',
    buttonId: 'amazon-enrich-button',
    buttonTitle: 'Amazon Music Library Enrichment',
    // Amazon's logo is NOT under img/brands — /static/amazon.svg, verbatim.
    logo: { src: '/static/amazon.svg', alt: 'Amazon Music', className: 'amazon-enrich-logo' },
    spinnerClass: 'amazon-enrich-spinner',
    tooltipClass: 'amazon-enrich-tooltip',
    tooltipId: 'amazon-enrich-tooltip',
    contentClass: 'amazon-enrich-tooltip-content',
    headerClass: 'amazon-enrich-tooltip-header',
    headerText: 'Amazon Music Enrichment',
    bodyClass: 'amazon-enrich-tooltip-body',
    bodyId: 'amazon-enrich-tooltip-body',
    statusId: 'amazon-enrich-tooltip-status',
    currentId: 'amazon-enrich-tooltip-current',
    progressId: 'amazon-enrich-tooltip-progress',
  },
  {
    id: 'similar_artists',
    note: 'Similar Artists (MusicMap) Enrichment Status Icon',
    containerClass: 'similar-artists-enrich-button-container',
    buttonClass: 'similar-artists-enrich-button',
    buttonId: 'similar-artists-enrich-button',
    buttonTitle: 'Similar Artists (MusicMap) Enrichment',
    logo: {
      src: '/static/img/brands/musicmap.png',
      alt: 'Similar Artists',
      className: 'similar-artists-enrich-logo',
    },
    spinnerClass: 'similar-artists-enrich-spinner',
    tooltipClass: 'similar-artists-enrich-tooltip',
    tooltipId: 'similar-artists-enrich-tooltip',
    contentClass: 'similar-artists-enrich-tooltip-content',
    headerClass: 'similar-artists-enrich-tooltip-header',
    headerText: 'Similar Artists Enrichment',
    bodyClass: 'similar-artists-enrich-tooltip-body',
    bodyId: 'similar-artists-enrich-tooltip-body',
    statusId: 'similar-artists-enrich-tooltip-status',
    currentId: 'similar-artists-enrich-tooltip-current',
    progressId: 'similar-artists-enrich-tooltip-progress',
  },
  {
    id: 'hydrabase',
    note: 'Hydrabase P2P Mirror Status Icon',
    containerClass: 'hydrabase-button-container',
    containerId: 'hydrabase-button-container',
    buttonClass: 'hydrabase-button',
    buttonId: 'hydrabase-button',
    buttonTitle: 'Hydrabase P2P Mirror',
    // Class hydrabase-worker-logo, and NOT under img/brands — verbatim.
    logo: { src: '/static/hydrabase.png', alt: 'Hydrabase', className: 'hydrabase-worker-logo' },
    spinnerClass: 'hydrabase-spinner',
    tooltipClass: 'hydrabase-tooltip',
    tooltipId: 'hydrabase-tooltip',
    contentClass: 'hydrabase-tooltip-content',
    headerClass: 'hydrabase-tooltip-header',
    headerText: 'Hydrabase P2P Mirror',
    bodyClass: 'hydrabase-tooltip-body',
    bodyId: 'hydrabase-tooltip-body',
    statusId: 'hydrabase-tooltip-status',
    // No current/progress rows — the tooltip is status-only.
  },
  {
    id: 'repair',
    note: 'Library Repair Worker Status Icon',
    containerClass: 'repair-button-container',
    buttonClass: 'repair-button',
    buttonId: 'repair-button',
    buttonTitle: 'Library Maintenance',
    logo: { src: '/static/whisoul.png', alt: 'Repair', className: 'repair-logo' },
    spinnerClass: 'repair-spinner',
    tooltipClass: 'repair-tooltip',
    tooltipId: 'repair-tooltip',
    contentClass: 'repair-tooltip-content',
    headerClass: 'repair-tooltip-header',
    headerText: '🔧 Library Repair',
    bodyClass: 'repair-tooltip-body',
    bodyId: 'repair-tooltip-body',
    statusId: 'repair-tooltip-status',
    currentId: 'repair-tooltip-current',
    progressId: 'repair-tooltip-progress',
  },
  {
    id: 'soulid',
    note: 'SoulID Worker Status Icon',
    containerClass: 'soulid-button-container',
    buttonClass: 'soulid-button',
    buttonId: 'soulid-button',
    buttonTitle: 'SoulID Generator',
    logo: { src: '/static/trans2.png', alt: 'SoulID', className: 'soulid-logo' },
    spinnerClass: 'soulid-spinner',
    tooltipClass: 'soulid-tooltip',
    tooltipId: 'soulid-tooltip',
    contentClass: 'soulid-tooltip-content',
    headerClass: 'soulid-tooltip-header',
    headerText: 'SoulID Generator',
    bodyClass: 'soulid-tooltip-body',
    bodyId: 'soulid-tooltip-body',
    statusId: 'soulid-tooltip-status',
    currentId: 'soulid-tooltip-current',
    progressId: 'soulid-tooltip-progress',
  },
];

const HIDDEN: CSSProperties = { display: 'none' };

function Orb({
  chrome,
  pill,
  badge,
  hidden,
  onClick,
}: {
  chrome: OrbChrome;
  pill: HeaderPill;
  badge?: { count: number; visible: boolean };
  hidden?: boolean;
  onClick?: () => void;
}) {
  const buttonClass = pill.stateClass
    ? `${chrome.buttonClass} ${pill.stateClass}`
    : chrome.buttonClass;
  return (
    <div
      className={chrome.containerClass}
      id={chrome.containerId}
      style={hidden ? HIDDEN : undefined}
    >
      <button
        className={buttonClass}
        id={chrome.buttonId}
        title={chrome.buttonTitle}
        onClick={onClick}
      >
        {'src' in chrome.logo ? (
          <img src={chrome.logo.src} alt={chrome.logo.alt} className={chrome.logo.className} />
        ) : (
          <span className={chrome.logo.className} aria-hidden="true">
            {chrome.logo.emoji}
          </span>
        )}
        <div className={chrome.spinnerClass}></div>
        {badge ? (
          <span
            className="repair-badge"
            id="repair-findings-badge"
            style={badge.visible ? undefined : HIDDEN}
          >
            {badge.count}
          </span>
        ) : null}
      </button>
      <div className={chrome.tooltipClass} id={chrome.tooltipId}>
        <div className={chrome.contentClass}>
          <div className={chrome.headerClass}>{chrome.headerText}</div>
          <div className={chrome.bodyClass} id={chrome.bodyId}>
            <div className="tooltip-status">
              Status:{' '}
              <span
                id={chrome.statusId}
                style={pill.statusColor ? { color: pill.statusColor } : undefined}
              >
                {pill.status}
              </span>
            </div>
            {chrome.currentId ? (
              <div className="tooltip-current" id={chrome.currentId}>
                {pill.current}
              </div>
            ) : null}
            {chrome.progressId ? (
              <div className="tooltip-progress" id={chrome.progressId}>
                {pill.progress}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The hello strip — what replaced the "Music Dashboard" title block. The
 * header's words are now live facts instead of the page's own name: library
 * size from the db stats the library card publishes, busy-worker count read
 * straight off the orb pills, and the watchlist scan countdown the quick-nav
 * already receives. Zero fetches of its own. Each chip is a shortcut to the
 * place the number lives.
 */
function HelloStrip({
  pills,
  scanCountdown,
}: {
  pills: Record<string, { stateClass: string | null }>;
  scanCountdown?: string;
}) {
  const stats = useSyncExternalStore(subscribeDbStats, lastDbStats);
  const chips = buildHelloStats({
    tracks: stats?.tracks ?? null,
    artists: stats?.artists ?? null,
    busyWorkers: countBusyWorkers(pills),
    scanCountdown: scanCountdown ?? null,
  });

  return (
    <div className="header-text header-hello">
      <h2 className="hello-greeting">
        <img src="/static/dashboard.png" className="page-header-icon" alt="" />
        <span>{greetingForHour(new Date().getHours())}</span>
      </h2>
      <div className="hello-stats">
        {chips.map((chip) => (
          <button
            key={chip.id}
            type="button"
            className="hello-stat"
            onClick={() => {
              if (chip.page) void window.navigateToPage?.(chip.page);
              else window.openEnrichmentManager?.();
            }}
          >
            {chip.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function DashboardHeader() {
  const {
    pills,
    repairBadge,
    onOrbClick,
    jiosaavnVisible,
    hydrabaseVisible,
    watchlist,
    wishlistCount,
  } = useDashboardHeader();

  const wishlistClass =
    wishlistCount === null
      ? 'header-button wishlist-button'
      : wishlistCount === 0
        ? 'header-button wishlist-button wishlist-inactive'
        : 'header-button wishlist-button wishlist-active';

  return (
    <div className="dashboard-header">
      <div className="dashboard-header-sweep" aria-hidden="true">
        <span></span>
      </div>
      <HelloStrip pills={pills} scanCountdown={watchlist.countdown} />
      <div className="header-actions">
        {ORBS.map((chrome) => (
          <Orb
            key={chrome.id}
            chrome={chrome}
            pill={pills[chrome.id]}
            badge={chrome.id === 'repair' ? repairBadge : undefined}
            hidden={
              chrome.id === 'jiosaavn'
                ? !jiosaavnVisible
                : chrome.id === 'hydrabase'
                  ? !hydrabaseVisible
                  : undefined
            }
            // SoulID is display-only; the vanilla binds no click handler to it.
            onClick={chrome.id === 'soulid' ? undefined : () => onOrbClick(chrome.id)}
          />
        ))}
        {/* Manage Enrichment Workers — opens the full management modal */}
        <button
          className="em-manage-btn"
          id="manage-enrichment-btn"
          title="Manage enrichment workers — stats, unmatched items, manual matching"
          onClick={() => window.openEnrichmentManager?.()}
        >
          <span className="em-manage-btn-icon">
            <img src="/static/trans2.png" alt="SoulSync" className="em-manage-btn-logo" />
          </span>
          <span className="em-manage-btn-label">Manage Workers</span>
        </button>
      </div>
      {/* Watchlist / Wishlist quick-nav (top-right corner) */}
      <div className="header-quick-nav">
        <button
          className="header-button watchlist-button"
          id="watchlist-button"
          title={watchlist.title}
          onClick={() => void window.navigateToPage?.('watchlist')}
        >
          <span className="hero-btn-icon">👁️</span>
          <span className="hero-btn-label">Watchlist</span>
          <span
            className={watchlist.count > 0 ? 'hero-btn-badge has-items' : 'hero-btn-badge'}
            id="watchlist-badge"
          >
            {watchlist.count}
          </span>
          <span className="hero-btn-shimmer"></span>
        </button>
        <button
          className={wishlistClass}
          id="wishlist-button"
          onClick={() => {
            // The in-flight-download fast/slow path lives in init.js
            // (openWishlistFromHero) — it needs activeDownloadProcesses /
            // WishlistModalState / rehydrateModal, all script-scoped.
            if (window.openWishlistFromHero) void window.openWishlistFromHero();
            else void window.navigateToPage?.('wishlist');
          }}
        >
          <span className="hero-btn-icon">🎵</span>
          <span className="hero-btn-label">Wishlist</span>
          <span
            className={
              wishlistCount && wishlistCount > 0 ? 'hero-btn-badge has-items' : 'hero-btn-badge'
            }
            id="wishlist-badge"
          >
            {wishlistCount ?? 0}
          </span>
          <span className="hero-btn-shimmer"></span>
        </button>
      </div>
    </div>
  );
}
