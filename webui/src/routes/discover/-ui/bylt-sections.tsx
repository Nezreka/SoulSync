import { useState } from 'react';

import type { ByltSection, ByltTrack } from '../-discover.bylt';

import {
  byltCarouselId,
  byltHasArtistImage,
  byltTrackCard,
  BYLT_CONTAINER_ID,
  BYLT_SUBTITLE,
} from '../-discover.bylt';

/**
 * Because You Listen To — one shelf per seed artist.
 *
 * Transcribed from `_renderByltSection` (discover.js 10365-10380) and
 * `_renderByltTrackCard` (10382-10400), both read end to end.
 *
 * Two things here are deliberate absences, not omissions:
 *
 *   - the track cards have NO click handler — the vanilla renders them purely
 *     as display, and inventing navigation for them would be new behaviour;
 *   - with no sections the container renders NOTHING — the vanilla passes
 *     `renderEmptyState: false` and leaves it blank, one of the few sections
 *     that opts out of the shared empty state.
 */

export interface ByltSectionsProps {
  sections: ByltSection[];
  /**
   * Open a track's album in the download modal. OPTIONAL and NEW BEHAVIOUR:
   * the vanilla tiles are inert (10386-10399 has no onclick) — Boulder asked
   * for them to behave like the Deep Cuts cards, which resolve name+artist
   * to the album and open the shared modal.
   */
  onOpenTrack?: (track: ByltTrack) => void;
}

export function ByltSections({ sections, onOpenTrack }: ByltSectionsProps) {
  // Blank on empty, not an empty-state box (10429-10430).
  if (sections.length === 0) return null;

  return (
    <div id={BYLT_CONTAINER_ID}>
      {sections.map((section, idx) => (
        <ByltShelf
          key={`${section.artist_name ?? ''}:${idx}`}
          section={section}
          idx={idx}
          onOpenTrack={onOpenTrack}
        />
      ))}
    </div>
  );
}

function ByltShelf({
  section,
  idx,
  onOpenTrack,
}: {
  section: ByltSection;
  idx: number;
  onOpenTrack?: (track: ByltTrack) => void;
}) {
  const [headBroken, setHeadBroken] = useState(false);
  return (
    <div className="discover-section bylt-section">
      <div className="discover-section-header">
        <div className="bylt-header">
          {/* Omitted entirely when absent — no placeholder (10370). A broken
              url hides the same way the vanilla's onerror does. */}
          {byltHasArtistImage(section) && !headBroken && (
            <img
              className="bylt-artist-img"
              src={section.artist_image}
              alt=""
              onError={() => setHeadBroken(true)}
            />
          )}
          <div>
            {/* The eyebrow sits ABOVE the title, and the title is an h3 —
                these shelves are sub-sections, not peers of the page's h2s. */}
            <div className="discover-section-subtitle">{BYLT_SUBTITLE}</div>
            <h3 className="discover-section-title">{section.artist_name}</h3>
          </div>
        </div>
      </div>
      <div className="discover-grid" id={byltCarouselId(idx)}>
        {(section.tracks ?? []).map((track, i) => (
          <ByltTrackCard key={`${track.name ?? ''}:${i}`} track={track} onOpen={onOpenTrack} />
        ))}
      </div>
    </div>
  );
}

function ByltTrackCard({
  track,
  onOpen,
}: {
  track: ByltTrack;
  onOpen?: (track: ByltTrack) => void;
}) {
  const [broken, setBroken] = useState(false);
  const card = byltTrackCard(track);
  const showImage = Boolean(card.image) && !broken;
  return (
    // The vanilla card has no onclick (10386-10399); the click is NEW, added
    // at Boulder's request so these behave like every other album card.
    <div
      className="ya-card discover-album-card"
      style={onOpen ? { cursor: 'pointer' } : undefined}
      onClick={onOpen ? () => onOpen(track) : undefined}
    >
      <div className="ya-card-img">
        {card.image && (
          <img
            src={card.image}
            alt=""
            loading="lazy"
            style={broken ? { display: 'none' } : undefined}
            onError={() => setBroken(true)}
          />
        )}
        <div className="ya-card-placeholder" style={{ display: showImage ? 'none' : 'flex' }}>
          ♫
        </div>
      </div>
      <div className="ya-card-gradient" />
      <div className="ya-card-info">
        <div className="ya-card-name">{card.title}</div>
        <div className="ya-card-sub">{card.subtitle}</div>
      </div>
    </div>
  );
}
