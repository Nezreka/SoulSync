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
  /** play the mix straight from the shelf. omitted where nothing can play. */
  onPlay?: (key: string) => void;
  /** this card's play is still resolving against the library. */
  playing?: boolean;
}

/**
 * Two real controls, not one clickable div.
 *
 * the card used to be a div with an onClick and a decorative ▶ glyph that
 * opened the same modal. a keyboard couldn't reach either. now the title/cover
 * is a button that opens details (its ::after covers the whole card), and the
 * play button is a sibling on top of it, so nothing is nested inside anything.
 */
export function DiscoverMixCard({ mix, onOpen, onPlay, playing }: DiscoverMixCardProps) {
  const count = mixTrackCount(mix);
  return (
    <div className="discover-mix-card" data-mix-key={mix.key}>
      {mixUsesSolidCover(mix) ? (
        <div className="mix-card-cover mix-card-cover--solid">
          {/* display:contents on the wrapper, so the cover html's nodes stay
              DIRECT flex children of the cover the way the vanilla built them
              (4842). a laid-out wrapper would break height:100% and any child
              selector. */}
          <div
            className="mix-card-cover-inner"
            dangerouslySetInnerHTML={{ __html: mix.coverHtml as string }}
          />
          <MixCardPlay mix={mix} onPlay={onPlay} playing={playing} />
        </div>
      ) : (
        <div className="mix-card-cover">
          {mixCoverTiles(mix.tracks).map((cover, i) => (
            <div
              className="mix-card-tile"
              key={`${cover}:${i}`}
              style={{ backgroundImage: `url('${cover}')` }}
            />
          ))}
          <MixCardPlay mix={mix} onPlay={onPlay} playing={playing} />
        </div>
      )}
      <button type="button" className="mix-card-open" onClick={() => onOpen(mix.key)}>
        <span className="mix-card-name">{mix.title}</span>
        <span className="mix-card-meta">{count} tracks</span>
      </button>
    </div>
  );
}

function MixCardPlay({
  mix,
  onPlay,
  playing,
}: {
  mix: DiscoverMix;
  onPlay?: (key: string) => void;
  playing?: boolean;
}) {
  if (!onPlay) return null;
  return (
    <button
      type="button"
      className="mix-card-play"
      aria-label={`Play ${mix.title}`}
      title={`Play ${mix.title}`}
      disabled={playing}
      aria-busy={playing || undefined}
      onClick={(e) => {
        e.stopPropagation();
        onPlay(mix.key);
      }}
    >
      {playing ? '…' : '▶'}
    </button>
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
  onPlayMix?: (key: string) => void;
  /** which mix key is currently resolving, if any. */
  playingKey?: string | null;
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
  onPlayMix,
  playingKey,
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
          <DiscoverMixCard
            key={mix.key}
            mix={mix}
            onOpen={onOpenMix}
            onPlay={onPlayMix}
            playing={playingKey === mix.key}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}
