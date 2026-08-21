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

import { useEffect, useRef, useState } from 'react';

import {
  DETECTED_SOURCE_SERVICE_LABELS,
  DETECTED_SOURCE_TAB_IDS,
  detectPlaylistUrl,
  isDetected,
} from '../-sync.url-detect';

/** The account tabs this sheet can send you to, in the order they read best. */
export const ADD_PLAYLIST_ACCOUNTS: readonly { tab: string; label: string; glyph: string }[] = [
  { tab: 'spotify', label: 'Spotify', glyph: '🎵' },
  { tab: 'tidal', label: 'Tidal', glyph: '🌊' },
  { tab: 'qobuz', label: 'Qobuz', glyph: '♫' },
  { tab: 'deezer', label: 'Deezer', glyph: '🎧' },
  { tab: 'listenbrainz-sync', label: 'ListenBrainz', glyph: '🧠' },
  { tab: 'lastfm-sync', label: 'Last.fm', glyph: '📻' },
  { tab: 'soulsync-discovery-sync', label: 'SoulSync Discovery', glyph: '✨' },
];

export interface AddPlaylistSheetProps {
  /**
   * Where the button that opened it sits, so it pops in AT the control rather
   * than dimming the page and taking over the middle of it. Same idiom as the
   * card's overflow menu — adding a playlist is a small act and a full-screen
   * dialog overstates it.
   */
  anchor: { top: number; left: number };
  /**
   * Open a tab and hand it a URL to load. `url` is omitted for the account and
   * file routes, which have nothing to parse.
   */
  onRoute: (tab: string, url?: string) => void;
  onClose: () => void;
}

export function AddPlaylistSheet({ anchor, onRoute, onClose }: AddPlaylistSheetProps) {
  const [input, setInput] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Deferred a tick, or the click that OPENED it closes it again.
    const onDocClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const id = setTimeout(() => document.addEventListener('click', onDocClick), 0);
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

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

  return (
    <div
      className="add-playlist-sheet"
      ref={ref}
      role="dialog"
      aria-label="Add a playlist"
      style={{ top: `${anchor.top}px`, left: `${anchor.left}px` }}
    >
      <div className="add-playlist-head">
        <h3>Add a playlist</h3>
        <button type="button" className="add-playlist-close" aria-label="Close" onClick={onClose}>
          &times;
        </button>
      </div>

        <div className="add-playlist-section">
          <label className="add-playlist-label" htmlFor="add-playlist-url">
            Paste a link
          </label>
          <div className="add-playlist-row">
            <input
              id="add-playlist-url"
              type="text"
              autoFocus
              value={input}
              placeholder="A playlist or album link from Spotify, Apple Music, Deezer or YouTube"
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
              className="add-playlist-submit"
              // Never disabled: a disabled button explains nothing, and the
              // whole point here is to SAY what is wrong with the link.
              onClick={submit}
            >
              Add
            </button>
          </div>
          {hint && (
            <p className={`add-playlist-hint${recognised ? ' add-playlist-hint--ok' : ''}`}>
              {hint}
            </p>
          )}
        </div>

        <div className="add-playlist-section">
          <span className="add-playlist-label">Or from a connected account</span>
          <div className="add-playlist-accounts">
            {ADD_PLAYLIST_ACCOUNTS.map((account) => (
              <button
                key={account.tab}
                type="button"
                className="add-playlist-account"
                onClick={() => {
                  onRoute(account.tab);
                  onClose();
                }}
              >
                <span aria-hidden="true">{account.glyph}</span>
                {account.label}
              </button>
            ))}
          </div>
        </div>

        <div className="add-playlist-section">
          <span className="add-playlist-label">Or from a file</span>
          <button
            type="button"
            className="add-playlist-file"
            onClick={() => {
              onRoute('import-file');
              onClose();
            }}
          >
            📄 Import CSV, TSV, TXT or M3U
          </button>
        </div>
    </div>
  );
}
