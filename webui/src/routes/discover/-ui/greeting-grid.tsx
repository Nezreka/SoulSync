import type { QuickTile } from '../-discover.greeting';

import { greeting } from '../-discover.greeting';
import { mixCoverLayout } from '../-discover.mixes';

/**
 * the top of discover: "good evening, boulder" and a grid of the things you
 * go back to. flow is the first tile and plays on one tap; every other tile
 * opens its mix, with a play button that shows on hover.
 */

export interface GreetingGridProps {
  name?: string | null;
  hour: number;
  tiles: QuickTile[];
  onOpenMix: (key: string) => void;
  onPlayMix: (key: string) => void;
  onPlayFlow: () => void;
  /** flow is being built and handed to the player */
  flowBusy?: boolean;
  playingKey?: string | null;
}

function PlayGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
    </svg>
  );
}

export function GreetingGrid({
  name,
  hour,
  tiles,
  onOpenMix,
  onPlayMix,
  onPlayFlow,
  flowBusy = false,
  playingKey = null,
}: GreetingGridProps) {
  const first = (name ?? '').trim().split(/\s+/)[0];
  return (
    <section className="dsc-greeting" aria-label="Jump back in">
      <h1 className="dsc-greeting-title">
        {greeting(hour)}
        {first ? `, ${first}` : ''}
      </h1>
      <div className="dsc-quick-grid">
        {tiles.map((tile) =>
          tile.kind === 'flow' ? (
            <button
              key="flow"
              type="button"
              className="dsc-quick-tile dsc-quick-tile--flow"
              onClick={onPlayFlow}
              disabled={flowBusy}
              aria-busy={flowBusy || undefined}
            >
              <span className="dsc-flow-orb" aria-hidden="true" />
              <span className="dsc-quick-text">
                <span className="dsc-quick-name">Flow</span>
                <span className="dsc-quick-sub">
                  {flowBusy ? 'Starting…' : 'Your music, and what it leads to'}
                </span>
              </span>
              <span className="dsc-quick-play" aria-hidden="true">
                <PlayGlyph />
              </span>
            </button>
          ) : (
            <QuickMixTile
              key={tile.mix.key}
              tileKey={tile.mix.key}
              title={tile.mix.title}
              cover={coverOf(tile.mix.tracks)}
              playing={playingKey === tile.mix.key}
              onOpen={onOpenMix}
              onPlay={onPlayMix}
            />
          ),
        )}
      </div>
    </section>
  );
}

/** one cover for a small tile: the first real one, or none. */
export function coverOf(tracks: unknown[] | undefined): string | null {
  const layout = mixCoverLayout(tracks);
  if (layout.kind === 'grid') return layout.covers[0];
  if (layout.kind === 'single') return layout.cover;
  return null;
}

function QuickMixTile({
  tileKey,
  title,
  cover,
  playing,
  onOpen,
  onPlay,
}: {
  tileKey: string;
  title: string;
  cover: string | null;
  playing: boolean;
  onOpen: (key: string) => void;
  onPlay: (key: string) => void;
}) {
  return (
    <div className="dsc-quick-tile" data-mix-key={tileKey}>
      <button type="button" className="dsc-quick-open" onClick={() => onOpen(tileKey)}>
        <span
          className="dsc-quick-art"
          aria-hidden="true"
          style={cover ? { backgroundImage: `url('${cover}')` } : undefined}
        />
        <span className="dsc-quick-name">{title}</span>
      </button>
      <button
        type="button"
        className="dsc-quick-play"
        aria-label={`Play ${title}`}
        title={`Play ${title}`}
        disabled={playing}
        aria-busy={playing || undefined}
        onClick={() => onPlay(tileKey)}
      >
        <PlayGlyph />
      </button>
    </div>
  );
}
