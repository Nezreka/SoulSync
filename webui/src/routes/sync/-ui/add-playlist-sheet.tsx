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

/** a public playlist a name search found (#2305) */
interface FoundPlaylist {
  id: string;
  title: string;
  creator: string;
  track_count: number;
  image_url: string;
  link: string;
  source: string;
}

/** words, not a link: nothing in it that a url has */
export function looksLikeSearch(text: string): boolean {
  const t = text.trim();
  return t.length >= 2 && !/[/:]|www\.|\.[a-z]{2,}\b/i.test(t);
}

/**
 * Public playlists by name (#2305). Typed words that aren't a link are a
 * search; a result is added by handing its link to the same route a pasted
 * link takes, so nothing about importing is new.
 */
type SearchSource = '' | 'deezer' | 'spotify';

/** the first songs of a found playlist, to look before adding it */
function usePreview(p: FoundPlaylist | null): {
  songs: { title: string; artist: string }[];
  total: number;
  state: 'idle' | 'busy' | 'done';
} {
  const [out, setOut] = useState<{
    songs: { title: string; artist: string }[];
    total: number;
    state: 'idle' | 'busy' | 'done';
  }>({ songs: [], total: 0, state: 'idle' });
  useEffect(() => {
    if (!p) {
      setOut({ songs: [], total: 0, state: 'idle' });
      return;
    }
    setOut({ songs: [], total: 0, state: 'busy' });
    const stop = new AbortController();
    fetch(
      `/api/playlists/public-preview?source=${encodeURIComponent(p.source)}&id=${encodeURIComponent(p.id)}`,
      { signal: stop.signal },
    )
      .then((r) => (r.ok ? r.json() : { songs: [], total: 0 }))
      .then((d: { songs?: { title: string; artist: string }[]; total?: number }) =>
        setOut({ songs: d.songs ?? [], total: d.total ?? 0, state: 'done' }),
      )
      .catch(() => {
        if (!stop.signal.aborted) setOut({ songs: [], total: 0, state: 'done' });
      });
    return () => stop.abort();
  }, [p]);
  return out;
}

function usePublicPlaylists(
  text: string,
  source: SearchSource = '',
): {
  found: FoundPlaylist[];
  state: 'idle' | 'busy' | 'done';
} {
  const [found, setFound] = useState<FoundPlaylist[]>([]);
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle');
  const query = looksLikeSearch(text) ? text.trim() : '';
  useEffect(() => {
    if (!query) {
      setFound([]);
      setState('idle');
      return;
    }
    setState('busy');
    const stop = new AbortController();
    // wait for a pause in typing
    const timer = window.setTimeout(() => {
      const only = source ? `&source=${source}` : '';
      fetch(`/api/playlists/public-search?q=${encodeURIComponent(query)}${only}`, {
        signal: stop.signal,
      })
        .then((r) => (r.ok ? r.json() : { playlists: [] }))
        .then((d: { playlists?: FoundPlaylist[] }) => {
          setFound(d.playlists ?? []);
          setState('done');
        })
        .catch(() => {
          if (stop.signal.aborted) return;
          setFound([]);
          setState('done');
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      stop.abort();
    };
  }, [query, source]);
  return { found, state };
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
  const searching = looksLikeSearch(trimmed);
  const [source, setSource] = useState<SearchSource>('');
  const { found, state: searchState } = usePublicPlaylists(trimmed, source);
  // a found playlist opened to look at its songs before adding it
  const [looking, setLooking] = useState<FoundPlaylist | null>(null);
  const preview = usePreview(looking);
  const hint = recognised
    ? `${DETECTED_SOURCE_SERVICE_LABELS[detection.source]} recognised`
    : searching
      ? searchState === 'busy'
        ? 'Searching public playlists…'
        : found.length
          ? 'Pick one to add it'
          : 'No public playlists by that name'
      : submitted && trimmed
        ? detection.error
        : null;

  const pick = (p: FoundPlaylist) => {
    const d = detectPlaylistUrl(p.link);
    if (!isDetected(d)) return;
    onRoute(DETECTED_SOURCE_TAB_IDS[d.source], p.link);
    onClose();
  };

  const submit = () => {
    // words are a search, already running: enter is not a bad link
    if (searching) return;
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
            aria-label="Paste a link or search by name"
            value={input}
            placeholder="Paste a link, or search public playlists by name"
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
            recognised ? ` ${styles.hintOk}` : hint && !searching ? ` ${styles.hintBad}` : ''
          }`}
        >
          {hint ?? ''}
        </p>

        {searching ? (
          <div className={styles.foundFilter} role="group" aria-label="Search on">
            {(
              [
                ['', 'All'],
                ['deezer', 'Deezer'],
                ['spotify', 'Spotify'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key || 'all'}
                type="button"
                className={styles.foundPill}
                aria-pressed={source === key}
                onClick={() => {
                  setSource(key);
                  setLooking(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}

        {searching && looking ? (
          <div className={styles.foundLook} aria-label={`Songs in ${looking.title}`}>
            <div className={styles.foundRow}>
              {looking.image_url ? (
                <img className={styles.foundArt} src={looking.image_url} alt="" />
              ) : (
                <span className={styles.foundArt} aria-hidden="true" />
              )}
              <span className={styles.foundText}>
                <span className={styles.tileName}>{looking.title}</span>
                <span className={styles.tileDesc}>
                  {[looking.creator, preview.total ? `${preview.total} songs` : '']
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </div>
            {preview.state === 'busy' ? (
              <p className={styles.hint}>Reading its songs…</p>
            ) : preview.songs.length ? (
              <ol className={styles.foundSongs}>
                {preview.songs.map((song, i) => (
                  <li key={`${i}-${song.title}`}>
                    {song.title}
                    {song.artist ? <span className={styles.tileDesc}> · {song.artist}</span> : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className={styles.hint}>
                Its songs couldn’t be read here. You’ll see them once it’s added.
              </p>
            )}
            {preview.total > preview.songs.length && preview.songs.length ? (
              <p className={styles.hint}>and {preview.total - preview.songs.length} more</p>
            ) : null}
            <div className={styles.foundActions}>
              <button type="button" className={styles.go} onClick={() => pick(looking)}>
                Add this playlist
              </button>
              <button type="button" className={styles.foundBack} onClick={() => setLooking(null)}>
                Back to results
              </button>
            </div>
          </div>
        ) : searching && found.length > 0 ? (
          <ul className={styles.found} aria-label="Public playlists">
            {found.map((p) => (
              <li key={`${p.source}:${p.id}`}>
                <button
                  type="button"
                  className={styles.foundRow}
                  aria-label={`Look at ${p.title}`}
                  onClick={() => setLooking(p)}
                >
                  {p.image_url ? (
                    <img className={styles.foundArt} src={p.image_url} alt="" loading="lazy" />
                  ) : (
                    <span className={styles.foundArt} aria-hidden="true" />
                  )}
                  <span className={styles.foundText}>
                    <span className={styles.tileName}>{p.title}</span>
                    <span className={styles.tileDesc}>
                      {[p.creator, p.track_count ? `${p.track_count} songs` : '']
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <span className={styles.foundSource} aria-hidden="true">
                    <SourceIcon source={p.source} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}

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
