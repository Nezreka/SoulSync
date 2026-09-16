import { useState } from 'react';

import type { ByltSection, ByltTrack } from '../-discover.bylt';

import {
  byltCarouselId,
  byltHasArtistImage,
  byltIsInsufficient,
  byltReasonLabel,
  byltRow,
  byltShelfKey,
  byltTracks,
  byltUnavailableNote,
  BYLT_CONTAINER_ID,
  BYLT_SUBTITLE,
} from '../-discover.bylt';

/**
 * Because You Listen To.
 *
 * The first port transcribed `_renderByltSection` (discover.js 10365-10380)
 * and `_renderByltTrackCard` (10382-10400) exactly: ten album-sized tiles per
 * shelf, no identity on a card, and one whole-card click that resolved the
 * ALBUM from name strings. On the reported data that painted ten copies of the
 * same album cover under two nearly identical headings, and clicking a track
 * opened something that was not the track.
 *
 * This renders the shelf as a LIST instead: title, artist, album and duration
 * on every row, with named actions. That is not decoration — it is what makes
 * an album-heavy shelf visibly album-heavy instead of ten identical squares,
 * and it gives each action an unambiguous meaning.
 *
 * Every control is a real `<button>`. Nothing is a clickable div, no button is
 * nested inside another, and the row itself has no click handler, so there is
 * never a question about what a click does.
 */

export interface ByltSectionsProps {
  sections: ByltSection[];
  /** 'Showing your last good set…' — only when there is something true to say. */
  statusNote?: string;
  /** e.g. listening history is shared across profiles on this install. */
  historyNote?: string | null;
  onPlayTrack?: (track: ByltTrack, section: ByltSection) => void;
  onDownloadTrack?: (track: ByltTrack, section: ByltSection) => void;
  onOpenAlbum?: (track: ByltTrack, section: ByltSection) => void;
  onPlayShelf?: (section: ByltSection) => void;
  onDownloadShelf?: (section: ByltSection) => void;
  /** `${seedKey}:${rowKey}` whose action is still resolving. */
  pendingKey?: string | null;
  /** Per-item failures, keyed the same way, shown next to the row. */
  errors?: Record<string, string>;
}

export function ByltSections({
  sections,
  statusNote,
  historyNote,
  onPlayTrack,
  onDownloadTrack,
  onOpenAlbum,
  onPlayShelf,
  onDownloadShelf,
  pendingKey = null,
  errors = {},
}: ByltSectionsProps) {
  // Blank on empty, not an empty-state box (10429-10430).
  if (sections.length === 0 && !statusNote) return null;

  return (
    <div id={BYLT_CONTAINER_ID}>
      {statusNote ? (
        <p className="bylt-status" role="status">
          {statusNote}
        </p>
      ) : null}
      {sections.map((section, idx) => (
        <ByltShelf
          key={byltShelfKey(section, idx)}
          section={section}
          idx={idx}
          onPlayTrack={onPlayTrack}
          onDownloadTrack={onDownloadTrack}
          onOpenAlbum={onOpenAlbum}
          onPlayShelf={onPlayShelf}
          onDownloadShelf={onDownloadShelf}
          pendingKey={pendingKey}
          errors={errors}
        />
      ))}
      {historyNote ? <p className="bylt-history-note">{historyNote}</p> : null}
    </div>
  );
}

function ByltShelf({
  section,
  idx,
  onPlayTrack,
  onDownloadTrack,
  onOpenAlbum,
  onPlayShelf,
  onDownloadShelf,
  pendingKey,
  errors,
}: {
  section: ByltSection;
  idx: number;
} & Omit<ByltSectionsProps, 'sections' | 'statusNote' | 'historyNote'>) {
  const [headBroken, setHeadBroken] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const tracks = byltTracks(section);
  const visibleTracks = expanded ? tracks : tracks.slice(0, 4);
  const seed = section.seed_key ?? String(idx);
  const missing = byltUnavailableNote(section);
  const reason = byltReasonLabel(section);
  const titleId = `bylt-title-${idx}`;

  return (
    <section className="discover-section bylt-section" aria-labelledby={titleId}>
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
            <h3 className="discover-section-title" id={titleId}>
              {section.artist_name}
            </h3>
            {/* The reason is the section's, built from what was actually
                selected. It never quotes a provider we do not have. */}
            {reason ? <p className="bylt-reason">{reason}</p> : null}
          </div>
        </div>
        {tracks.length > 0 && (onPlayShelf || onDownloadShelf) ? (
          <div className="bylt-shelf-actions">
            {onPlayShelf ? (
              <button
                type="button"
                className="btn btn--sm btn--secondary"
                onClick={() => onPlayShelf(section)}
              >
                ▶ Play what I own
              </button>
            ) : null}
            {onDownloadShelf ? (
              <button
                type="button"
                className="btn btn--sm btn--secondary"
                onClick={() => onDownloadShelf(section)}
              >
                Download shelf
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {missing ? (
        <p className="bylt-unavailable" role="status">
          {missing}
        </p>
      ) : null}

      {byltIsInsufficient(section) ? (
        // Not enough evidence for a shelf. Say so; do not leave one card
        // consuming a full shelf's height, and do not pad it with filler.
        <p className="bylt-insufficient">Not enough to recommend from {section.artist_name} yet.</p>
      ) : (
        <ul className="bylt-track-list" id={byltCarouselId(idx)}>
          {visibleTracks.map((track, i) => (
            <ByltTrackRow
              key={byltRow(track, i).key}
              track={track}
              section={section}
              index={i}
              rowKey={`${seed}:${byltRow(track, i).key}`}
              pending={pendingKey === `${seed}:${byltRow(track, i).key}`}
              error={errors?.[`${seed}:${byltRow(track, i).key}`]}
              onPlayTrack={onPlayTrack}
              onDownloadTrack={onDownloadTrack}
              onOpenAlbum={onOpenAlbum}
            />
          ))}
        </ul>
      )}
      {tracks.length > 4 && !byltIsInsufficient(section) ? (
        <button
          type="button"
          className="btn btn--sm btn--secondary bylt-expand"
          aria-expanded={expanded}
          aria-controls={byltCarouselId(idx)}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? 'Show less' : `View all ${tracks.length} tracks`}
        </button>
      ) : null}
    </section>
  );
}

function ByltTrackRow({
  track,
  section,
  index,
  rowKey,
  pending,
  error,
  onPlayTrack,
  onDownloadTrack,
  onOpenAlbum,
}: {
  track: ByltTrack;
  section: ByltSection;
  index: number;
  rowKey: string;
  pending?: boolean;
  error?: string;
  onPlayTrack?: (track: ByltTrack, section: ByltSection) => void;
  onDownloadTrack?: (track: ByltTrack, section: ByltSection) => void;
  onOpenAlbum?: (track: ByltTrack, section: ByltSection) => void;
}) {
  const [broken, setBroken] = useState(false);
  const row = byltRow(track, index);
  const showImage = Boolean(row.cover) && !broken;
  const label = row.artist ? `${row.title} by ${row.artist}` : row.title;

  return (
    <li className="bylt-track" data-row-key={rowKey}>
      <div className="bylt-track-art">
        {row.cover && (
          <img
            src={row.cover}
            alt=""
            loading="lazy"
            style={broken ? { display: 'none' } : undefined}
            onError={() => setBroken(true)}
          />
        )}
        <span className="ya-card-placeholder" style={{ display: showImage ? 'none' : 'flex' }}>
          ♫
        </span>
      </div>

      <div className="bylt-track-main">
        {/* title="" keeps a long name discoverable without a tooltip library */}
        <span className="bylt-track-name" title={row.title}>
          {row.title}
        </span>
        <span className="bylt-track-sub">
          <span title={row.artist}>{row.artist}</span>
          {row.album ? (
            <span className="bylt-track-album" title={row.album}>
              {row.album}
            </span>
          ) : null}
        </span>
        {row.why ? <span className="bylt-track-why">{row.why}</span> : null}
      </div>

      {/* EMPTY for an unknown length — "0:00" claims a fact we do not have. */}
      <span className="bylt-track-duration">{row.duration}</span>

      {row.owned ? (
        <span className="bylt-track-owned" title="Already in your library">
          In library
        </span>
      ) : null}

      <div className="bylt-track-actions">
        {onPlayTrack ? (
          <button
            type="button"
            className="btn btn--sm btn--secondary"
            aria-label={`Play ${label}`}
            disabled={pending}
            aria-busy={pending || undefined}
            onClick={() => onPlayTrack(track, section)}
          >
            {pending ? '…' : '▶'}
          </button>
        ) : null}
        {onDownloadTrack && !row.owned ? (
          <button
            type="button"
            className="btn btn--sm btn--secondary"
            aria-label={`Download ${label}`}
            onClick={() => onDownloadTrack(track, section)}
          >
            Download
          </button>
        ) : null}
        {onOpenAlbum && row.album ? (
          <button
            type="button"
            className="btn btn--sm btn--secondary"
            aria-label={`Open the album ${row.album}`}
            onClick={() => onOpenAlbum(track, section)}
          >
            Album
          </button>
        ) : null}
      </div>

      {error ? (
        <span className="bylt-track-error" role="alert">
          {error}
        </span>
      ) : null}
    </li>
  );
}
