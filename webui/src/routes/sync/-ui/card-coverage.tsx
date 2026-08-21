/**
 * One coverage renderer for every card on this page.
 *
 * The page grew four ways of saying the same thing. Three of them lived in
 * card-progress.tsx alone — the sync writer's `♪ 20 / ✓ 14 / ✗ 2 / (80%)`, the
 * check-note pair `✓ 14 / ♪ 20` with no failures and no percentage, and the
 * unconditional slash-text line — and ListenBrainz painted a fourth. Which one
 * you saw depended on which tab you were standing on, for the same underlying
 * fact.
 *
 * IMPORTANT — this unifies PRESENTATION ONLY. Every number still comes from the
 * function that always produced it (syncCardCounts, checkNoteCounts,
 * slashTextProgressLine's arithmetic, lbCardProgressLine), because the formulas
 * genuinely differ and that difference is real, not drift:
 *
 *   discovery percentage = matched / total
 *   sync percentage      = (matched + failed) / total     <- counts failures as done
 *
 * So `percentage` is passed IN rather than derived here. A renderer that
 * recomputed it would quietly change what the sync line means.
 *
 * The bar is the better fact — it carries the total as well as the win, which
 * is the argument that took the redundant download chip off the dashboard's
 * sync row. The failure count survives as its own chip because nothing else on
 * the card conveys it and it is the only number here you would act on.
 */

export interface CardCoverageValue {
  total: number;
  matched: number;
  /** null for the check-note sources, which never counted failures. */
  failed: number | null;
  /**
   * The source's own percentage, already computed. null means "this source
   * never printed one" — the bar still fills from matched/total, but no number
   * is shown, so nothing appears that the card did not show before.
   */
  percentage: number | null;
}

export function coverageBarWidth(v: CardCoverageValue): number {
  const pct = v.percentage ?? (v.total > 0 ? Math.round((v.matched / v.total) * 100) : 0);
  // A source can report more matches than total mid-crawl; a bar wider than
  // its track would break the rounding on the right edge.
  return Math.max(0, Math.min(100, pct));
}

export function CardCoverage(value: CardCoverageValue) {
  const width = coverageBarWidth(value);
  const failed = value.failed ?? 0;
  return (
    <div className="playlist-card-coverage">
      <div
        className={`pcc-bar${failed > 0 ? ' pcc-bar--has-failures' : ''}`}
        role="progressbar"
        aria-valuenow={width}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <i style={{ width: `${width}%` }} />
      </div>
      <div className="pcc-legend">
        <span className="pcc-count">
          {value.matched} / {value.total}
        </span>
        {value.percentage !== null && <span className="pcc-pct">{value.percentage}%</span>}
        {failed > 0 && <span className="pcc-failed">✗ {failed}</span>}
      </div>
    </div>
  );
}
