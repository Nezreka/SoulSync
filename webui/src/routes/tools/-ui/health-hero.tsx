/**
 * Library health — the one number the maintenance surface opens with.
 *
 * The old opener was four count pills (pending / resolved / dismissed /
 * auto-fixed). Those answer "how many rows are in a table", which is a
 * question nobody has. This answers "is my library alright, and if not, what
 * is dragging it down" — the score, then the contribution bar that decomposes
 * it, then the one button that fixes everything safe in a single decision.
 *
 * All the arithmetic lives in -tools.groups.ts so it can be pinned by tests;
 * this file is the rendering of it.
 */

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type { RepairJobRun } from '../-tools.types';

import {
  contributionSegments,
  findingsTrend,
  healthBandLabel,
  libraryHealth,
  sparklinePoints,
} from '../-tools.groups';
import { useToolsMedia } from '../-tools.media';

export interface HealthHeroProps {
  groups: readonly FindingGroup[];
  /** type slug → catalog row, for labels. */
  typeInfo: (findingType: string) => FindingTypeInfo | undefined;
  /** Library size, for the per-1,000-tracks normalisation. Null until the
   *  database stats land. */
  trackCount: number | null;
  /** what the library size is, said in its own words ("8,864 movies · 105,472
   *  episodes"). absent = tracks. */
  sizeLabel?: string | null;
  /** the groups haven't arrived yet: say so instead of scoring an empty
   *  list as a perfect library */
  loading?: boolean;
  runs: readonly RepairJobRun[];
  runningJobs: number;
  /** How many pending findings "Fix all safe" would touch. */
  safeCount: number;
  onFixAllSafe: () => void;
  fixAllBusy: boolean;
  /** Clicking a bar segment opens that group in the inbox. */
  onPickType: (findingType: string) => void;
}

const SPARK_WIDTH = 132;
const SPARK_HEIGHT = 30;

export function HealthHero({
  groups,
  typeInfo,
  trackCount,
  sizeLabel,
  loading = false,
  runs,
  runningJobs,
  safeCount,
  onFixAllSafe,
  fixAllBusy,
  onPickType,
}: HealthHeroProps) {
  const video = useToolsMedia() === 'video';
  const health = libraryHealth(groups, trackCount);
  const labelOf = (findingType: string) =>
    typeInfo(findingType)?.label || findingType.replace(/_/g, ' ');
  const segments = contributionSegments(groups, labelOf);
  const trend = findingsTrend(runs);

  const meta: string[] = [];
  if (sizeLabel) meta.push(sizeLabel);
  else if (trackCount) meta.push(`${trackCount.toLocaleString()} tracks`);
  meta.push(
    loading
      ? 'looking at your findings…'
      : health.pending > 0
        ? `${health.pending.toLocaleString()} open findings`
        : 'nothing outstanding',
  );
  if (runningJobs > 0) meta.push(`${runningJobs} job${runningJobs === 1 ? '' : 's'} running`);

  return (
    <div className="repair-health" id="repair-section-health">
      <div className="repair-health-score-block">
        <div className={`repair-health-score ${loading ? '' : health.band}`}>
          {loading ? '–' : health.score}
        </div>
        <div className={`repair-health-band ${loading ? '' : health.band}`}>
          {loading ? 'checking' : healthBandLabel(health.band)}
        </div>
      </div>

      <div className="repair-health-body">
        <div className="repair-health-meta">{meta.join(' · ')}</div>

        {segments.length > 0 ? (
          <div
            className="repair-health-bar"
            role="group"
            aria-label="What is affecting library health"
          >
            {segments.map((segment) => (
              <button
                type="button"
                key={segment.findingType}
                className={`repair-health-seg ${segment.severity}`}
                style={{ flexGrow: segment.percent }}
                title={`${segment.label} — ${segment.pending.toLocaleString()} open`}
                aria-label={`${segment.label}, ${segment.pending} open findings`}
                onClick={() => onPickType(segment.findingType)}
              />
            ))}
          </div>
        ) : loading ? null : (
          <div className="repair-health-bar-empty">Nothing is dragging your library down.</div>
        )}

        <div className="repair-health-actions">
          <button
            type="button"
            className="repair-health-fix-safe"
            disabled={safeCount === 0 || fixAllBusy}
            onClick={onFixAllSafe}
            title={
              safeCount === 0
                ? 'Nothing safe to fix right now'
                : video
                  ? "Fixes every finding that only changes SoulSync's data: no file is touched, nothing downloads"
                  : 'Fixes every finding that only writes metadata — never deletes or moves a file'
            }
          >
            {fixAllBusy
              ? 'Fixing…'
              : `Fix all safe${safeCount > 0 ? ` (${safeCount.toLocaleString()})` : ''}`}
          </button>
          <span className="repair-health-actions-note">
            {video
              ? 'Safe fixes never touch a file or start a download.'
              : 'Safe fixes never delete or move a file.'}
          </span>
        </div>
      </div>

      {trend.length > 1 ? (
        <div className="repair-health-trend" title="Findings created per run, last 20 runs">
          <svg
            width={SPARK_WIDTH}
            height={SPARK_HEIGHT}
            viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
            aria-hidden="true"
            preserveAspectRatio="none"
          >
            <polyline
              points={sparklinePoints(trend, SPARK_WIDTH, SPARK_HEIGHT)}
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
          <span className="repair-health-trend-label">findings / run</span>
        </div>
      ) : null}
    </div>
  );
}
