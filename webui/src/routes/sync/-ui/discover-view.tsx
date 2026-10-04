/**
 * Discover view — the acquisition/curation pipeline.
 *
 * Read-only composition over the existing seams: the old scattered header
 * tools (Match Review, Wing It Pool, Library Match) become STAGES in a
 * pipeline, and the Beatport and SoulSync Discovery panels render as-is
 * beneath them. Nothing here reimplements a pool or a panel — the stage
 * cards call `runSyncHeaderAction`, the same implementation the vanilla
 * header buttons ran, and the two panels bring their own markup.
 */

import type { ReactNode } from 'react';

import { useOverviewModel } from './overview/use-overview-model';
import { runSyncHeaderAction } from './sync-shell';

import './discover-view.css';

export interface DiscoverViewProps {
  /** The existing BeatportTab panel node, rendered as the Charts stage. */
  beatport: ReactNode;
  /** The existing SoulsyncDiscoveryTab panel node, the Discovered stage. */
  discovery: ReactNode;
  onOpenAutoSync: () => void;
  onActivity: () => void;
}

interface ReviewStage {
  title: string;
  description: string;
  cta: string;
  /** Key into runSyncHeaderAction — the vanilla seam, not a new action. */
  actionKey: string;
}

const REVIEW_STAGES: readonly ReviewStage[] = [
  {
    title: 'Match Review',
    description: 'Tracks the matcher flagged — verify or re-match them.',
    cta: 'Open review',
    actionKey: 'discovery-pool',
  },
  {
    title: 'Wing It Pool',
    description: 'Best-effort guesses waiting for your call.',
    cta: 'Open pool',
    actionKey: 'wing-it-pool',
  },
  {
    title: 'Library Match',
    description: 'Manually link source tracks to library tracks.',
    cta: 'Open matcher',
    actionKey: 'library-match',
  },
];

export function DiscoverView({
  beatport,
  discovery,
  onOpenAutoSync,
  onActivity,
}: DiscoverViewProps) {
  // The count only — the discovered rows themselves stay in the Library.
  const { discovered } = useOverviewModel(true);
  const discoveredCount = discovered.length;

  return (
    <div className="pl-discover">
      <section aria-label="Review queues">
        <div className="pl-discover-stage-head">
          <h3 className="pl-section-title">Review queues</h3>
          <p className="pl-caption">Curate what automation could not decide on its own.</p>
        </div>
        <div className="pl-discover-cards">
          {REVIEW_STAGES.map((stage) => (
            <div key={stage.actionKey} className="pl-discover-card">
              <h4>{stage.title}</h4>
              <p className="pl-caption">{stage.description}</p>
              <button
                type="button"
                className="pl-discover-cta"
                onClick={() =>
                  runSyncHeaderAction(stage.actionKey, onOpenAutoSync, onActivity)
                }
              >
                {stage.cta}
              </button>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Discovered">
        <div className="pl-discover-stage-head">
          <h3 className="pl-section-title">Discovered</h3>
          <p className="pl-caption">
            {discoveredCount} playlists with tracks waiting
          </p>
        </div>
        {discovery}
      </section>

      <section aria-label="Charts">
        <div className="pl-discover-stage-head">
          <h3 className="pl-section-title">Charts</h3>
          <p className="pl-caption">Beatport charts, ready to mirror.</p>
        </div>
        {beatport}
      </section>
    </div>
  );
}
