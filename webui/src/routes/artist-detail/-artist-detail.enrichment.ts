/**
 * Per-artist enrichment coverage rings, ported from
 * `renderArtistEnrichmentCoverage` (library.js:730).
 *
 * Geometry and animation timings are computed here so the component is a plain
 * render — the vanilla built the whole SVG as an innerHTML string.
 */

export interface EnrichmentService {
  name: string;
  key: string;
  color: string;
}

/** Declaration order is the on-screen order, so it is part of the contract. */
export const ENRICHMENT_SERVICES: readonly EnrichmentService[] = [
  { name: 'Spotify', key: 'spotify', color: '#1db954' },
  { name: 'MusicBrainz', key: 'musicbrainz', color: '#ba55d3' },
  { name: 'Deezer', key: 'deezer', color: '#a238ff' },
  { name: 'JioSaavn', key: 'jiosaavn', color: '#2bc5b4' },
  { name: 'Last.fm', key: 'lastfm', color: '#d51007' },
  { name: 'iTunes', key: 'itunes', color: '#fc3c44' },
  { name: 'AudioDB', key: 'audiodb', color: '#1a9fff' },
  { name: 'Discogs', key: 'discogs', color: '#D4A574' },
  { name: 'Genius', key: 'genius', color: '#ffff64' },
  { name: 'Tidal', key: 'tidal', color: '#00ffff' },
  { name: 'Qobuz', key: 'qobuz', color: '#4285f4' },
  { name: 'Bandcamp', key: 'bandcamp', color: '#1da0c3' },
] as const;

/** Ring radius, and the circumference every dash calculation is based on. */
export const RING_RADIUS = 20;
export const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/**
 * JioSaavn is experimental and hidden unless explicitly enabled. The vanilla
 * routes this through `filterJiosaavnServiceEntries` in shared-helpers.js;
 * that global stays the single source of truth, and this falls back to
 * excluding JioSaavn when it is unavailable (the safe default — the same
 * answer the helper gives when the flag is off).
 */
export function visibleEnrichmentServices(
  services: readonly EnrichmentService[] = ENRICHMENT_SERVICES,
): EnrichmentService[] {
  const filter = window.filterJiosaavnServiceEntries;
  if (typeof filter === 'function') {
    return filter([...services], 'key') as EnrichmentService[];
  }
  return services.filter((s) => s.key !== 'jiosaavn');
}

export interface EnrichmentRing {
  service: EnrichmentService;
  /** Raw coverage percentage from the payload; 0 when the key is absent. */
  pct: number;
  /** Rounded value shown inside the ring. */
  label: number;
  /** stroke-dashoffset — how much of the ring is left EMPTY. */
  offset: string;
  dashArray: string;
  /** Stagger, in seconds, as a fixed-2 string to match the vanilla exactly. */
  delay: string;
  /** The percentage text fades 0.3s after its ring starts. */
  pctDelay: string;
}

/**
 * The whole panel is hidden when there is no enrichment data at all, or when
 * the artist has no tracks — a coverage ring over zero tracks is meaningless.
 */
export function shouldShowEnrichment(enrichment: Record<string, unknown> | undefined): boolean {
  return Boolean(enrichment && enrichment.total_tracks);
}

export function buildEnrichmentRings(
  enrichment: Record<string, unknown>,
  services: EnrichmentService[] = visibleEnrichmentServices(),
): EnrichmentRing[] {
  return services.map((service, index) => {
    const pct = Number(enrichment[service.key] ?? 0) || 0;
    const offset = RING_CIRCUMFERENCE - (RING_CIRCUMFERENCE * pct) / 100;
    const delay = (index * 0.08).toFixed(2);
    return {
      service,
      pct,
      label: Math.round(pct),
      offset: offset.toFixed(1),
      dashArray: RING_CIRCUMFERENCE.toFixed(1),
      delay,
      pctDelay: (parseFloat(delay) + 0.3).toFixed(2),
    };
  });
}
