/**
 * The playlist card — artwork first, state carried quietly by the artwork.
 *
 * What this replaces: a row with the art tile, the name, an optional original
 * name, a meta row of up to five bordered chips (source badge, track count,
 * mirrored-ago, a discovery ratio, a coloured phase line, an export status),
 * then six icon-only buttons and a schedule select. Fourteen elements at the
 * same visual weight, which is why it read as a wall of controls rather than a
 * library.
 *
 * THE RESTING CARD IS ART, A NAME, ONE LINE AND A SCHEDULE. Everything else is
 * either gone or waits for a hover.
 *
 * State rides on the COVER rather than beside it:
 *   * a ring on the artwork's corner, its arc the coverage, its number the
 *     percentage — red for a failed run, pulsing for one in flight;
 *   * the cover desaturates while something is wrong, so an incomplete card
 *     reads differently even out of focus.
 *
 * A healthy card gets NEITHER. That is the point: a ring on every card would be
 * forty of them announcing that nothing is wrong, the same mistake as a full
 * green progress bar on a finished row. Problems sort to the front instead
 * (librarySortedRows), so nothing needs to shout to be found.
 */

import type { ReactNode } from 'react';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import {
  libraryCardState,
  libraryCoveragePct,
  libraryGap,
  libraryMissingCount,
  libraryTotal,
} from '../-sync.library';
import { PlaylistArt, PlaylistCollage, playlistCoverTiles } from './playlist-art';
import { SourceIcon } from './source-icon';

export interface PlaylistCardAction {
  label: string;
  onClick: () => void;
  /** Renders in the destructive tone — used for Retry after a failed run. */
  danger?: boolean;
}

export interface PlaylistCardProps {
  row: MirroredPlaylistRow;
  /** Display name, which may be a rename of the source's own. */
  name: string;
  /**
   * Hover text for the name. The old card carried a "↳ original name" line
   * under a renamed playlist; that is a whole extra line for something you
   * look at once, so it moved here.
   */
  nameTitle?: string;
  /** "synced 3h ago" — the tail of the meta line. */
  when: string;
  /** Current schedule, e.g. "Every 6 hours · next in 3h". */
  schedule: string;
  /**
   * Repeated-failure signal from the run history — the board's `!` / `⚠` glyph.
   * Distinct from the ring, which reports the CURRENT run: a playlist can be
   * sitting green right now and still have failed its last three scheduled runs.
   */
  health?: { level: 'ok' | 'warning' | 'failing'; tooltip: string };
  /**
   * Live narration, which REPLACES the meta line while it exists: the pipeline
   * phase with its percentage, or an export in flight. The resting meta line is
   * a summary; this is what is happening right now, and right now wins.
   */
  status?: ReactNode | null;
  onOpen: () => void;
  /**
   * The one thing this card most wants doing — omitted when there is nothing
   * honest to offer. A Beatport or file-backed mirror cannot be refreshed by
   * the pipeline at all (the endpoint 400s), and a button that fails on click
   * is worse than no button.
   */
  primary?: PlaylistCardAction | null;
  /** The overflow menu's trigger; the menu itself is the caller's. */
  onMore: (anchor: HTMLElement) => void;
  /**
   * Open the schedule picker. Omitted for sources the pipeline cannot refresh,
   * where the pill stays a plain label — offering a schedule that can never run
   * is the same lie as offering a Sync now that 400s.
   */
  onSchedule?: (anchor: HTMLElement) => void;
  /**
   * Whether a cadence is actually set, which decides if the pill is a LABEL or
   * just a control. Most playlists are unscheduled, so painting "Not scheduled"
   * on every one of them made the loudest repeated element on the page the one
   * carrying no information — and buried the handful that do have a cadence.
   * Unscheduled cards keep the pill for its hit box and reveal it on hover.
   *
   * Passed rather than read off the label: "Not scheduled" is display copy, and
   * matching against it would break the moment the wording changes.
   */
  scheduled?: boolean;
}

/**
 * The meta line: one plain sentence, never a row of chips.
 *
 * It says what is WRONG when something is, and what is true when nothing is.
 * The count only earns its place when it differs from the total — "140 tracks"
 * on a fully discovered playlist is more useful than "140 tracks · 140
 * discovered", which states the same fact twice.
 */
export function playlistCardMeta(row: MirroredPlaylistRow, when: string): string {
  const total = libraryTotal(row);
  const state = libraryCardState(row);
  const tracks = `${total} track${total === 1 ? '' : 's'}`;

  if (state === 'error') return `${tracks} · last run failed`;
  if (state === 'working') {
    const phase = row.pipeline_state?.phase || 'working';
    return `${tracks} · ${phase.toLowerCase()}`;
  }
  // "discovered", not "in library": the number is discovered_count, which
  // counts tracks the discovery step matched to a source — owning the file is a
  // different question the database answers with a separate `in_library`.
  // THE SHORTFALL, NAMED AND COUNTED. This line is where a gap is reported now
  // — the button went back to saying "Sync now", because it always ran the same
  // pipeline and three names for one action bought nothing.
  //
  // Stated as what is MISSING rather than what is present: "96 in library" made
  // you subtract to reach the number you would act on, and the meta line's job
  // is to say what is wrong when something is. The two gaps get different words
  // because they need different things done — one wants a match found, the
  // other wants a file fetched.
  if (state === 'short') {
    const missing = libraryMissingCount(row);
    return libraryGap(row) === 'ownership'
      ? `${tracks} · ${missing} not downloaded`
      : `${tracks} · ${missing} not found`;
  }

  return when ? `${tracks} · ${when}` : tracks;
}

/** What the hover action offers, given what the card is. */
export function playlistCardPrimaryLabel(row: MirroredPlaylistRow): string {
  switch (libraryCardState(row)) {
    case 'error':
      return 'Retry';
    case 'working':
      // NOT "Cancel": the pipeline controller has no cancel, and a button that
      // cannot do what it says is worse than one that offers less.
      return 'View progress';
    // NOT "Find N missing". It called the very same pipeline.run as Sync now,
    // so the label promised a narrower action than it performed, and the count
    // it carried now lives on the meta line where it does not have to compete
    // with a verb.
    default:
      return 'Sync now';
  }
}

export function PlaylistCard({
  row,
  name,
  nameTitle,
  when,
  schedule,
  health,
  status,
  onOpen,
  primary,
  onMore,
  onSchedule,
  scheduled,
}: PlaylistCardProps) {
  const state = libraryCardState(row);
  const pct = libraryCoveragePct(row);

  return (
    <div
      className="pl-card"
      id={`mirrored-card-${row.id}`}
      data-state={state}
      data-playlist-id={row.id}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="pl-card-art">
        <div className={`pl-card-art-img ${row.source ?? ''}`}>
          {/* A real poster beats a mosaic of the playlist's albums; the collage
              is the fallback for the sources that supply no poster at all. */}
          {row.image_url ? (
            <PlaylistArt url={row.image_url} fallback={<SourceIcon source={row.source} />} />
          ) : (
            <PlaylistCollage
              tiles={playlistCoverTiles(row)}
              fallback={<SourceIcon source={row.source} />}
            />
          )}
        </div>
        {state !== 'ok' && (
          <span
            className={`pl-ring pl-ring--${state}`}
            style={{ ['--pct' as string]: pct }}
            /* The number is IN the ring, so the arc never has to be read
               precisely — it is the glanceable half, the digits the exact one. */
            data-pct={`${pct}%`}
            aria-label={`${pct}% of tracks discovered`}
          />
        )}
      </div>

      <div className="pl-card-body">
        <div className="pl-card-name">
          {health && health.level !== 'ok' ? (
            <span
              className={`pl-card-health pl-card-health--${health.level}`}
              title={health.tooltip}
              aria-label={health.tooltip}
            >
              {health.level === 'failing' ? '!' : '⚠'}
            </span>
          ) : null}
          <SourceIcon source={row.source} />
          <b title={nameTitle ?? name}>{name}</b>
          {/* Organize-by-playlist was set-and-forget: nothing on the card said
              which playlists were in that mode, so finding out meant opening
              the overflow menu on each of thirty-eight. The tooltip names BOTH
              effects, because the second one is not implied by the setting's
              name and changes where failures go. */}
          {row.organize_by_playlist ? (
            <span
              className="pl-card-organize"
              title="Downloads into a folder named after this playlist. Missed tracks are downloaded directly instead of going to the wishlist."
              aria-label="Organized by playlist"
            >
              📁
            </span>
          ) : null}
        </div>
        <div className="pl-card-meta card-meta">
          {status ?? playlistCardMeta(row, when)}
        </div>
        {/* The schedule is a CONTROL, not a label: with Auto-Sync's board
            gone this is the only place a cadence gets set. It still reads as a
            quiet pill at rest — a dropdown sitting open on every card was the
            chrome the redesign removed. */}
        {onSchedule ? (
          <button
            type="button"
            className={`pl-card-pill pl-card-pill--action${scheduled ? '' : ' pl-card-pill--quiet'}`}
            title="Change how often this playlist syncs"
            onClick={(e) => {
              e.stopPropagation();
              onSchedule(e.currentTarget);
            }}
          >
            {schedule}
          </button>
        ) : (
          /* No onSchedule means this source cannot be refreshed by the
             pipeline at all. Saying "Not scheduled" would imply you could,
             and clicking it does nothing — so it says what is actually true. */
          <span
            className="pl-card-pill pl-card-pill--inert pl-card-pill--quiet"
            title="Auto-Sync cannot refresh this source, so it cannot be scheduled"
          >
            Can’t be scheduled
          </span>
        )}
      </div>

      {/* Under a veil, so the resting card is only art and words. */}
      <div className="pl-card-hover">
        {primary && (
          <button
            type="button"
            className={`pl-card-fix${primary.danger ? ' pl-card-fix--danger' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              primary.onClick();
            }}
          >
            {primary.label}
          </button>
        )}
        <button
          type="button"
          className="pl-card-more"
          title="More"
          aria-label={`More actions for ${name}`}
          onClick={(e) => {
            e.stopPropagation();
            onMore(e.currentTarget);
          }}
        >
          ⋯
        </button>
      </div>
    </div>
  );
}
