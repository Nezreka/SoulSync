import {
  buildEnrichmentRings,
  RING_RADIUS,
  shouldShowEnrichment,
  visibleEnrichmentServices,
} from '../-artist-detail.enrichment';

interface Props {
  enrichment: Record<string, unknown> | undefined;
}

/**
 * Per-artist enrichment coverage rings (renderArtistEnrichmentCoverage).
 *
 * The vanilla wrote inline `animation:` shorthands rather than CSS classes, and
 * they are reproduced verbatim: the stagger is what makes the twelve rings
 * sweep in one after another instead of snapping into place together.
 *
 * Hidden entirely when the artist has no tracks — a coverage ring over zero
 * tracks is a meaningless 0%.
 */
export function EnrichmentCoverage({ enrichment }: Props) {
  if (!shouldShowEnrichment(enrichment)) return null;

  const rings = buildEnrichmentRings(
    enrichment as Record<string, unknown>,
    visibleEnrichmentServices(),
  );

  return (
    <div className="artist-enrichment-coverage" id="artist-enrichment-coverage">
      <div className="artist-enrich-title">Enrichment Coverage</div>
      <div className="artist-enrich-grid">
        {rings.map((ring) => (
          <div className="artist-enrich-circle" key={ring.service.key}>
            <div
              className="artist-enrich-ring"
              style={{ '--ring-color': ring.service.color } as React.CSSProperties}
            >
              <svg viewBox="0 0 48 48">
                <circle className="ring-bg" cx="24" cy="24" r={RING_RADIUS} />
                <circle
                  className="ring-fill"
                  cx="24"
                  cy="24"
                  r={RING_RADIUS}
                  stroke={ring.service.color}
                  strokeDasharray={ring.dashArray}
                  style={
                    {
                      '--ring-circ': ring.dashArray,
                      '--ring-offset': ring.offset,
                      strokeDashoffset: ring.offset,
                      animation: `ringFillIn 1s cubic-bezier(0.4,0,0.2,1) ${ring.delay}s both`,
                    } as React.CSSProperties
                  }
                />
              </svg>
              <span
                className="ring-pct"
                style={{ animation: `ringPctFade 0.8s ease ${ring.pctDelay}s both` }}
              >
                {ring.label}
              </span>
            </div>
            <span className="artist-enrich-label">{ring.service.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
