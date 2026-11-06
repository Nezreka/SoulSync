/**
 * The playlist card: the cover first, the words under it, controls on hover.
 *
 * What it replaced: a row with a 64px tile, the name, a meta row of chips,
 * a % ring on the cover and six icon-only buttons. Fourteen elements at the
 * same visual weight, which read as a wall of controls rather than a library.
 *
 * At rest the card is the cover, the name, where it comes from, one status
 * line and its schedule. The status line says what is wrong when something
 * is ("24 not found", "Last runs failed") and what is happening while a run
 * goes, with a progress bar along the cover's bottom edge. A healthy card
 * gets a quiet check and nothing else. Play, the primary action and ⋯ sit on
 * the cover and appear on hover (always, on touch).
 */

import type { ReactNode } from 'react';

import { useState } from 'react';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import {
  libraryCardState,
  libraryMissingCount,
  libraryOwned,
  libraryOwnedKnown,
  libraryTotal,
} from '../-sync.library';
import { needsMatchLabel } from '../-sync.mirrored';
import { isLikedPlaylist } from '../../../features/playlists/user-playlists';
import { LikedArt, PlaylistArt, PlaylistCollage, playlistCoverTiles } from './playlist-art';
import styles from './playlists.module.css';
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
  /**
   * the name it lands on the server under, when that is not `name`: two
   * mirrors that would write one server playlist get distinct names
   * (core/playlists/sync_names).
   */
  syncsAs?: string;
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
  /**
   * select mode (#1219). while the tab is selecting, a click on the card
   * toggles it instead of opening it, the hover actions stay hidden, and a
   * check sits on the art corner. `selected` says whether this one is in.
   */
  selecting?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
  /** play the whole playlist from the card. a disc on the art, on hover. */
  onPlay?: () => Promise<unknown>;
  /**
   * open the wing it pool for this playlist. the card's one loud line, and
   * only when tracks need a pick: nothing downloads them until someone does,
   * and the old card never said so (#1721).
   */
  onNeedsMatch?: () => void;
  /**
   * The live run's progress, 0-100, for the bar along the cover's bottom edge.
   * Only while something is running; a finished card carries no bar.
   */
  progress?: number | null;
  /** where it comes from, as people say it ("Spotify", "Deezer"). */
  sourceLabel?: string;
  /**
   * Whether something is running right now (a pipeline phase, a discovery, an
   * export). `status` alone can't say: it also carries finished phases like
   * "Discovered 30/30". Defaults to the row's own pipeline state.
   */
  live?: boolean;
  /** this copy sits in the Needs attention shelf, repeating a grid card */
  inShelf?: boolean;
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
  // THE SHORTFALL, NAMED AND COUNTED. This line is where a gap is reported —
  // the button says "Sync & download" whatever the state, because it always ran the
  // same pipeline and three names for one action bought nothing.
  //
  // Stated as what is MISSING rather than what is present: this line's job is
  // to say what is wrong when something is, and a count you have to subtract to
  // reach is not that.
  //
  // Only DISCOVERY is reported. A "not downloaded" count needs
  // in_library_count, which is not accurate enough to show — see libraryOwned.
  if (state === 'short') return `${tracks} · ${libraryMissingCount(row)} not found`;

  // Fully discovered, but the files are not all here. Reported only when the
  // sync matcher has checked EVERY track of this playlist — see
  // libraryOwnedKnown. Playlists not synced since the flag existed say nothing
  // rather than claiming you own none of them.
  //
  // DISPLAY ONLY: this does not make the card "short", put a ring on it, or
  // move it into Needs attention. Whether an unowned track is a problem to fix
  // or a normal resting state depends on how the library is used, and that is
  // not settled — so the card states the fact and leaves the verdict alone.
  if (libraryOwnedKnown(row) && libraryOwned(row) < total) {
    return `${tracks} · ${total - libraryOwned(row)} not downloaded`;
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
    // NOT "Find N missing". It called the very same pipeline.run as this,
    // so the label promised a narrower action than it performed, and the count
    // it carried now lives on the meta line where it does not have to compete
    // with a verb. "Sync & download", not "Sync now" (#1404): three buttons
    // said sync and did three different things. this one does all of it.
    default:
      return row.is_request_list ? 'Process requests' : 'Sync & download';
  }
}

const PLAY_ICON = (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path
      d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.6-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8 0 0 0 8 5.2z"
      fill="currentColor"
    />
  </svg>
);

const CHECK_ICON = (
  <svg
    viewBox="0 0 24 24"
    width="14"
    height="14"
    fill="none"
    stroke="currentColor"
    strokeWidth="3"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M5 12l5 5 9-10" />
  </svg>
);

export function PlaylistCard({
  row,
  name,
  nameTitle,
  syncsAs,
  when,
  schedule,
  health,
  status,
  onOpen,
  primary,
  onMore,
  onSchedule,
  scheduled,
  selecting,
  selected,
  onToggleSelect,
  onPlay,
  onNeedsMatch,
  progress,
  sourceLabel,
  live,
  inShelf,
}: PlaylistCardProps) {
  const [starting, setStarting] = useState(false);
  const state = libraryCardState(row);
  const needsMatch = onNeedsMatch && !status ? needsMatchLabel(row) : null;
  // in select mode the whole card is the checkbox. one hit target the size
  // of the card beats a 16px box you have to aim for forty times over.
  const activate = selecting && onToggleSelect ? onToggleSelect : onOpen;
  const working = live ?? state === 'working';
  const failing = !working && (state === 'error' || health?.level === 'failing');
  const warning = !working && !failing && health?.level === 'warning';
  // where it comes from, and the two quiet facts that change how it behaves.
  // each a span so the marks keep the tooltips that explain them
  const metaParts: ReactNode[] = [];
  if (row.is_request_list) {
    metaParts.push(
      <span
        key="request"
        className="plc-request"
        title={
          row.auto_remove_fulfilled
            ? 'Request List: server audio download only (no server playlist created). Fulfilled Spotify tracks are automatically removed.'
            : 'Request List: server audio download only (no server playlist created).'
        }
      >
        Request list
      </span>,
    );
  }
  if (sourceLabel) metaParts.push(<span key="source">{sourceLabel}</span>);
  if (row.organize_by_playlist) {
    // the tooltip names BOTH effects: the second is not implied by the
    // setting's name and changes where failures go
    metaParts.push(
      <span
        key="organize"
        className="plc-organize"
        title="Downloads into a folder named after this playlist. Missed tracks are downloaded directly instead of going to the wishlist."
      >
        organized
      </span>,
    );
  }
  // fully discovered but not all downloaded: the line says so, and a check
  // beside it would contradict the words
  const undownloaded = libraryOwnedKnown(row) && libraryOwned(row) < libraryTotal(row);
  const pct = typeof progress === 'number' ? Math.max(0, Math.min(100, progress)) : null;

  let statusClass = styles.status;
  if (working) statusClass += ` ${styles.statusRun}`;
  else if (failing) statusClass += ` ${styles.statusFail}`;
  else if (warning || state === 'short') statusClass += ` ${styles.statusAttn}`;

  return (
    <div
      className={`plc ${styles.card}${selected ? ` plc--selected ${styles.cardSelected}` : ''}`}
      // the shelf repeats a card the grid also shows; only the grid one owns the id
      id={inShelf ? undefined : `mirrored-card-${row.id}`}
      data-shelf={inShelf ? 'true' : undefined}
      data-state={state}
      data-playlist-id={row.id}
      onClick={activate}
      role={selecting ? 'checkbox' : 'button'}
      aria-checked={selecting ? Boolean(selected) : undefined}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          activate();
        }
      }}
    >
      <div className={styles.art}>
        <div className={`pl-card-art-img ${row.source ?? ''}`}>
          {/* A real poster beats a mosaic of the playlist's albums; the collage
              is the fallback for the sources that supply no poster at all. */}
          {row.image_url ? (
            <PlaylistArt url={row.image_url} fallback={<SourceIcon source={row.source} />} />
          ) : isLikedPlaylist(row) ? (
            <LikedArt />
          ) : (
            <PlaylistCollage
              tiles={playlistCoverTiles(row)}
              fallback={<SourceIcon source={row.source} />}
            />
          )}
        </div>
        {selecting ? (
          <span
            className={`plc-check ${styles.check}${selected ? ` ${styles.checkOn}` : ''}`}
            aria-hidden="true"
          >
            {CHECK_ICON}
          </span>
        ) : (
          <>
            <button
              type="button"
              className={`plc-more ${styles.kebab}`}
              title="More"
              aria-label={`More actions for ${name}`}
              onClick={(e) => {
                e.stopPropagation();
                onMore(e.currentTarget);
              }}
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="currentColor"
                aria-hidden="true"
              >
                <circle cx="5" cy="12" r="2" />
                <circle cx="12" cy="12" r="2" />
                <circle cx="19" cy="12" r="2" />
              </svg>
            </button>
            {primary && (
              <button
                type="button"
                className={`plc-primary ${styles.prim}${
                  primary.danger ? ` plc-primary--danger ${styles.primDanger}` : ''
                }${working && !primary.danger ? ` ${styles.primLive}` : ''}`}
                onClick={(e) => {
                  e.stopPropagation();
                  primary.onClick();
                }}
              >
                {primary.label}
              </button>
            )}
            {onPlay ? (
              <button
                type="button"
                className={`plc-play ${styles.fab}`}
                aria-label={`Play ${name}`}
                title="Play"
                disabled={starting}
                aria-busy={starting || undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  if (starting) return;
                  setStarting(true);
                  void onPlay().finally(() => setStarting(false));
                }}
              >
                {starting ? <span className={styles.spin} aria-hidden="true" /> : PLAY_ICON}
              </button>
            ) : null}
          </>
        )}
        {working && pct !== null ? (
          <span
            className={`pl-card-progress ${styles.progress}`}
            role="progressbar"
            aria-label={`${name} progress`}
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span style={{ width: `${pct}%` }} />
          </span>
        ) : null}
      </div>

      <div className={`plc-name ${styles.name}`} title={nameTitle ?? name}>
        {name}
      </div>
      {metaParts.length ? (
        <div className={styles.meta}>
          {metaParts.flatMap((part, i) => (i === 0 ? [part] : [' · ', part]))}
        </div>
      ) : null}
      {syncsAs ? (
        <div
          className={`plc-syncs-as ${styles.meta}`}
          title="Another mirror has the same name, so this one gets its own playlist on the server"
        >
          On the server as &ldquo;{syncsAs}&rdquo;
        </div>
      ) : null}
      <div className={`plc-status ${statusClass}`}>
        {working ? <span className={styles.spin} aria-hidden="true" /> : null}
        {failing ? (
          <span className={styles.failIcon} aria-hidden="true">
            !
          </span>
        ) : null}
        {!working && !failing && !warning && state === 'ok' && !undownloaded ? (
          <span className={`plc-ok ${styles.okIcon}`}>{CHECK_ICON}</span>
        ) : null}
        <span
          className={health && health.level !== 'ok' ? 'plc-health' : undefined}
          title={health && health.level !== 'ok' ? health.tooltip : undefined}
        >
          {status ??
            (failing
              ? health?.level === 'failing'
                ? 'Last runs failed'
                : 'Last run failed'
              : warning
                ? health?.tooltip
                : playlistCardMeta(row, when))}
        </span>
      </div>
      {needsMatch ? (
        <button
          type="button"
          className={`plc-guesses ${styles.lineBtn} ${styles.guess}`}
          title="Wing It matched these on a best guess. Confirm or fix them"
          onClick={(e) => {
            e.stopPropagation();
            if (selecting) {
              activate();
              return;
            }
            onNeedsMatch?.();
          }}
        >
          {needsMatch}
        </button>
      ) : null}
      {/* The schedule is a CONTROL, not a label: it is where a cadence gets
          set from the card. Quiet at rest. */}
      {onSchedule ? (
        <button
          type="button"
          className={`plc-schedule${scheduled ? ' plc-schedule--set' : ''} ${styles.lineBtn} ${
            scheduled ? styles.schedOn : styles.sched
          }`}
          title="Change how often this playlist syncs"
          onClick={(e) => {
            e.stopPropagation();
            if (selecting) {
              activate();
              return;
            }
            onSchedule(e.currentTarget);
          }}
        >
          {schedule}
        </button>
      ) : (
        /* No onSchedule means this source cannot be refreshed by the
           pipeline at all, so it says what is actually true. */
        <span
          className={`plc-schedule plc-schedule--inert ${styles.schedInert}`}
          title="Auto-Sync cannot refresh this source, so it cannot be scheduled"
        >
          Can’t be scheduled
        </span>
      )}
    </div>
  );
}
