import type { LabelWatchState } from '../-label-detail.types';

import { catalogMetaLine } from '../-label-detail.helpers';

/**
 * The label's identity block and its two controls.
 *
 * Class names are the vanilla's, unchanged: the stylesheet is the same one, and
 * the watch button deliberately reuses `.library-artist-watchlist-btn` so a
 * followed label looks exactly like a followed artist.
 */
export function LabelHero({
  name,
  total,
  artistCount,
  watch,
  ready,
  busy,
  onToggleWatch,
  onSetBacklog,
}: {
  name: string;
  total: number;
  artistCount: number;
  watch: LabelWatchState;
  /** The first page has landed — before that the vanilla hid both controls. */
  ready: boolean;
  busy: boolean;
  onToggleWatch: () => void;
  onSetBacklog: (backlog: boolean) => void;
}) {
  return (
    <div className="label-detail-hero">
      <div className="label-detail-hero-art" id="label-detail-hero-art">
        🏷️
      </div>
      <div className="label-detail-hero-main">
        <div className="label-detail-eyebrow">Record Label</div>
        <h1 className="label-detail-name" id="label-detail-name">
          {name}
        </h1>
        {/* Empty until the first page resolves: the counts are unknown, and
            "0 releases · 0 artists" reads as an answer rather than a wait. */}
        <div className="label-detail-meta" id="label-detail-meta">
          {ready ? catalogMetaLine(total, artistCount) : ''}
        </div>
      </div>
      <div className="label-detail-hero-actions">
        <button
          className={`library-artist-watchlist-btn${watch.watching ? ' watching' : ''}`}
          id="label-detail-watch-btn"
          type="button"
          hidden={!ready}
          onClick={onToggleWatch}
        >
          <span className="watchlist-icon">👁️</span>
          <span className="watchlist-text">
            {busy ? 'Loading...' : watch.watching ? 'Watching...' : 'Add to Watchlist'}
          </span>
        </button>
        {/* Backlog only means something for a label you follow. */}
        <div className="label-detail-backlog" id="label-detail-backlog" hidden={!watch.watching}>
          <span className="label-detail-backlog-label">Monitor</span>
          <div className="label-detail-seg">
            <button
              type="button"
              data-backlog="0"
              className={watch.backlog ? '' : 'active'}
              onClick={() => onSetBacklog(false)}
            >
              New releases
            </button>
            <button
              type="button"
              data-backlog="1"
              className={watch.backlog ? 'active' : ''}
              onClick={() => onSetBacklog(true)}
            >
              Full backlog
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
