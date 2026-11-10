/**
 * Add a playlist — one entry point for what is currently twelve tabs.
 *
 * The sync page is organised around WHERE a playlist came from: fifteen tabs,
 * of which Spotify and Deezer each appear twice and the four paste-a-URL tabs
 * differ, at the input step, not at all. But provenance is the thing a user
 * cares about least once the playlist is in — and picking the wrong tab first
 * is how a perfectly good Deezer link earns "Invalid Deezer playlist URL".
 *
 * So the question changes from "which service is this?" (the page's problem) to
 * "what do you want to add?" (the user's). Three ways in:
 *
 *   * paste a link      — one field; the service is DETECTED, not chosen
 *   * a connected account — jump to that account's list
 *   * a file            — jump to the importer
 *
 * This sheet ROUTES, it does not parse. Every link still lands on the tab that
 * already owns that service, with its own loader, its own progress narration
 * and its own already-loaded dedupe. That is deliberate: routing is reversible
 * and testable, whereas moving four bespoke loaders in here would be a rewrite
 * of the part of this page that actually works.
 */

import { useRef, useState } from 'react';

import {
  DETECTED_SOURCE_SERVICE_LABELS,
  DETECTED_SOURCE_TAB_IDS,
  detectPlaylistUrl,
  isDetected,
} from '../-sync.url-detect';
import styles from './playlists.module.css';
import { SourceIcon } from './source-icon';
import { usePopoverDismiss } from './use-popover-dismiss';

interface AddSource {
  /** the tab it opens, which is also the key for its logo (SourceIcon) */
  tab: string;
  label: string;
  desc: string;
}

/** The account tabs this sheet can send you to, in the order they read best. */
export const ADD_PLAYLIST_ACCOUNTS: readonly AddSource[] = [
  { tab: 'spotify', label: 'Spotify', desc: 'Your Spotify playlists' },
  { tab: 'tidal', label: 'Tidal', desc: 'Your Tidal playlists' },
  { tab: 'qobuz', label: 'Qobuz', desc: 'Your Qobuz playlists' },
  { tab: 'deezer', label: 'Deezer', desc: 'Your Deezer playlists' },
  {
    tab: 'ytmusic',
    label: 'YouTube Music',
    desc: 'Your YouTube Music playlists',
  },
  {
    tab: 'listenbrainz-sync',
    label: 'ListenBrainz',
    desc: 'Your ListenBrainz playlists',
  },
  {
    tab: 'lastfm-sync',
    label: 'Last.fm',
    desc: 'Your Last.fm radio playlists',
  },
  {
    tab: 'soulsync-discovery-sync',
    label: 'SoulSync Discovery',
    desc: 'SoulSync Discovery playlists',
  },
];

/** Charts and files: not an account, still a source. */
export const ADD_PLAYLIST_OTHER: readonly AddSource[] = [
  {
    tab: 'beatport',
    label: 'Beatport',
    desc: 'Browse by genre, top 100s',
  },
  {
    tab: 'import-file',
    label: 'Import a file',
    desc: 'CSV, TSV, TXT or M3U',
  },
];

export interface AddPlaylistSheetProps {
  /**
   * Open a tab and hand it a URL to load. `url` is omitted for the account and
   * file routes, which have nothing to parse.
   */
  onRoute: (tab: string, url?: string) => void;
  /** Start fresh: a new playlist made here, or a new request list. */
  onNew?: (kind: 'user' | 'requests') => void;
  onClose: () => void;
}

export function AddPlaylistSheet({ onRoute, onNew, onClose }: AddPlaylistSheetProps) {
  const [input, setInput] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  usePopoverDismiss({ ref, onClose });

  const trimmed = input.trim();
  const detection = detectPlaylistUrl(trimmed);
  const recognised = isDetected(detection);

  /*
   * While typing, only ever say something ENCOURAGING — naming the service the
   * moment it is recognisable. Errors wait for a submit, because telling
   * someone their link is wrong while they are still halfway through pasting it
   * is just noise.
   */
  const hint = recognised
    ? `${DETECTED_SOURCE_SERVICE_LABELS[detection.source]} recognised`
    : submitted && trimmed
      ? detection.error
      : null;

  const submit = () => {
    setSubmitted(true);
    if (!recognised) return;
    onRoute(DETECTED_SOURCE_TAB_IDS[detection.source], trimmed);
    onClose();
  };

  const tile = (source: AddSource) => (
    <button
      key={source.tab}
      type="button"
      className={styles.tile}
      onClick={() => {
        onRoute(source.tab);
        onClose();
      }}
    >
      <span className={`${styles.badge} ${styles.badgeLogo}`} aria-hidden="true">
        <SourceIcon source={source.tab} />
      </span>
      <span>
        <span className={styles.tileName}>{source.label}</span>
        <span className={styles.tileDesc}>{source.desc}</span>
      </span>
    </button>
  );

  return (
    <>
      <div className={styles.scrim} aria-hidden="true" />
      <div
        className={`pl-add-sheet ${styles.sheet}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label="Add a playlist"
      >
        <div className={styles.sheetHead}>
          <h3 className={styles.sheetTitle}>Add a playlist</h3>
          <button
            type="button"
            className={`${styles.iconBtn} ${styles.iconBtnSm}`}
            aria-label="Close"
            onClick={onClose}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        </div>

        <label className={styles.linkBox}>
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
            <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
          </svg>
          <input
            id="add-playlist-url"
            type="text"
            autoFocus
            aria-label="Paste a link"
            value={input}
            placeholder="Paste a playlist or album link"
            onChange={(e) => {
              setInput(e.target.value);
              setSubmitted(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
            }}
          />
          <button
            type="button"
            className={styles.go}
            // Never disabled: a disabled button explains nothing, and the
            // whole point here is to SAY what is wrong with the link.
            onClick={submit}
          >
            Add
          </button>
        </label>
        <p
          className={`${styles.hint}${
            recognised ? ` ${styles.hintOk}` : hint ? ` ${styles.hintBad}` : ''
          }`}
        >
          {hint ?? ''}
        </p>

        <div className={styles.groupLabel}>From your accounts</div>
        <div className={styles.tiles}>{ADD_PLAYLIST_ACCOUNTS.map(tile)}</div>

        <div className={styles.groupLabel}>Charts and files</div>
        <div className={styles.tiles}>{ADD_PLAYLIST_OTHER.map(tile)}</div>

        {onNew ? (
          <>
            <div className={styles.groupLabel}>Start fresh</div>
            <div className={styles.tiles}>
              <button
                type="button"
                className={styles.tile}
                onClick={() => {
                  onNew('user');
                  onClose();
                }}
              >
                <span className={styles.badge} aria-hidden="true">
                  +
                </span>
                <span>
                  <span className={styles.tileName}>New playlist</span>
                  <span className={styles.tileDesc}>Yours to edit, in My Playlists</span>
                </span>
              </button>
              <button
                type="button"
                className={styles.tile}
                onClick={() => {
                  onNew('requests');
                  onClose();
                }}
              >
                <span className={styles.badge} aria-hidden="true">
                  +
                </span>
                <span>
                  <span className={styles.tileName}>New request list</span>
                  <span className={styles.tileDesc}>Downloads tracks, no server playlist</span>
                </span>
              </button>
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}
