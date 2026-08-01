import type { DiscoverSectionId } from '../-discover.layout';
import type { DiscoverMix } from '../-discover.mixes';

import { mixCoverTiles, mixTrackCount, mixUsesSolidCover } from '../-discover.mixes';
import { DiscoverSection } from './discover-section';

/**
 * The mix card, and the shelves built out of it.
 *
 * Transcribed from discover.js 4838-4861 for the card.
 *
 * One card serves Your Mixes, the decade shelf, Last.fm Radio and ListenBrainz —
 * the vanilla renders all four through `_buildMixCard`, and the sections differ
 * only in their header and which grid they render into. What they must NOT share
 * is the grid element: the registry holds every section's mixes so the modal can
 * resolve any key, and rendering `Object.values(registry)` into one shelf is
 * exactly how the other sections leak into Your Mixes.
 */

export interface DiscoverMixCardProps {
  mix: DiscoverMix;
  onOpen: (key: string) => void;
}

export function DiscoverMixCard({ mix, onOpen }: DiscoverMixCardProps) {
  const count = mixTrackCount(mix);
  return (
    <div className="discover-mix-card" data-mix-key={mix.key} onClick={() => onOpen(mix.key)}>
      {mixUsesSolidCover(mix) ? (
        <div
          className="mix-card-cover mix-card-cover--solid"
          // The cover html's nodes are DIRECT children of the cover in the
          // vanilla (4842), with the play overlay beside them — a wrapper div
          // would break any child selector or height:100% the cover relies on.
          dangerouslySetInnerHTML={{
            __html: `${mix.coverHtml as string}<div class="mix-card-play">▶</div>`,
          }}
        />
      ) : (
        <div className="mix-card-cover">
          {mixCoverTiles(mix.tracks).map((cover, i) => (
            <div
              className="mix-card-tile"
              key={`${cover}:${i}`}
              style={{ backgroundImage: `url('${cover}')` }}
            />
          ))}
          <div className="mix-card-play">▶</div>
        </div>
      )}
      <div className="mix-card-name">{mix.title}</div>
      <div className="mix-card-meta">{count} tracks</div>
    </div>
  );
}

export interface MixShelfProps {
  id: DiscoverSectionId;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** ONLY this section's mixes — never the whole registry. */
  mixes: DiscoverMix[];
  loaded: boolean;
  /** The grid's DOM id, which the cover-hydration pass targets. */
  gridId: string;
  /**
   * The grid's class. Your Mixes uses `discover-mixes-grid`; the other sections
   * build their own container, so the vanilla's `_renderMixGrid` renders into
   * whatever the caller supplies rather than owning a class of its own.
   */
  gridClassName?: string;
  actions?: React.ReactNode;
  onOpenMix: (key: string) => void;
}

export function MixShelf({
  id,
  title,
  subtitle,
  mixes,
  loaded,
  gridId,
  gridClassName = 'discover-mixes-grid',
  actions,
  onOpenMix,
}: MixShelfProps) {
  return (
    <DiscoverSection
      id={id}
      title={title}
      subtitle={subtitle}
      actions={actions}
      count={mixes.length}
      loaded={loaded}
    >
      <div className={gridClassName} id={gridId}>
        {mixes.map((mix) => (
          <DiscoverMixCard key={mix.key} mix={mix} onOpen={onOpenMix} />
        ))}
      </div>
    </DiscoverSection>
  );
}
