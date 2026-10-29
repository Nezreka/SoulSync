/**
 * The four URL-import tabs (deezer-link 2706, spotify-public 6611,
 * itunes-link 7633, youtube 8806 — sync-services.js): paste a link and it's
 * imported. The tab loads it, saves it as a mirror (which starts identifying
 * it in the background) and lands you on it under Mirrored, where a playlist
 * is managed. The cards below are what you've pasted before: each opens its
 * playlist, or imports it if it never made it in. A Recent-URLs pill bar
 * re-pastes a link.
 *
 * The cards used to run their own identify→sync→download vertical, a second
 * home for the same playlist the Mirrored tab already managed (#1910's
 * follow-up). The notes below describe that older card flow where the shared
 * helpers still serve the Mirrored tab.
 *
 * Declared divergences (the P5a pattern): card clicks open the React
 * DiscoveryModal for every phase — the vanilla's downloading/download_complete
 * branches reopened the vanilla ENGINE modal via the script-scoped
 * activeDownloadProcesses registry, unreachable from here; page-load engine
 * modal rehydration rides the same registry and is likewise not reproduced.
 * The card progress line derives from vertical state on every render, so it
 * survives re-renders (the vanilla's innerHTML reset blanked it until the
 * next progress event). The vanilla's 'Playlist state not found - try
 * refreshing the page' guards became silent seeding — React cards render
 * from the playlists array, so the missing-state case is unreachable by
 * construction.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ImportSpec } from '../-sync.importer';
import type { SourceVerticalConfig } from '../-sync.sources';
import type { UrlTabPlaylist } from '../-sync.url-tabs';
import type { SourceVertical } from '../-sync.use-vertical';

import { urlTabCacheKey, useRememberedPlaylists } from '../-sync.account-cache';
import {
  fetchDeezerLinkPlaylist,
  fetchSourcePlaylistsStates,
  fetchYouTubePlaylists,
  parseITunesLinkUrl,
  parseSpotifyPublicUrl,
  parseYouTubeUrl,
} from '../-sync.api';
import {
  DEEZER_PLAYLIST_PROGRESS_EVENT,
  type DeezerPlaylistProgressFrame,
  deezerProgressLabel,
} from '../-sync.events';
import {
  asString,
  deezerAlreadyLoaded,
  deezerInputResult,
  deezerMirrorTracks,
  itunesLinkAlreadyLoaded,
  itunesLinkCardIcon,
  itunesLinkMirrorTracks,
  itunesLinkTypeBadge,
  itunesLinkUrlError,
  spotifyPublicAlreadyLoaded,
  spotifyPublicCardIcon,
  spotifyPublicMirrorTracks,
  spotifyPublicTypeBadge,
  spotifyPublicUrlError,
  youtubeAlreadyLoaded,
  youtubeMirrorTracks,
  youtubeUrlError,
} from '../-sync.url-tabs';
import { URL_HISTORY_SOURCES } from '../-sync.urls';
import { ImportCard, ImportGrid } from './import-card';
import { playlistArtUrl } from './playlist-art';
import { PlaylistSearch, noMatchText, playlistNameMatches } from './playlist-search';
import { UrlHistoryBar, useUrlHistory } from './url-history-bar';
import { useImporter, type Importer } from './use-importer';

/* ── Shared pieces ────────────────────────────────────────────────────────── */

/** The input + parse-button row (the youtube-input-section markup). */
function UrlInputSection({
  inputId,
  buttonId,
  placeholder,
  buttonLabel,
  busy,
  busyLabel,
  value,
  onChange,
  onSubmit,
}: {
  inputId: string;
  buttonId: string;
  placeholder: string;
  buttonLabel: string;
  busy: boolean;
  /** null = the button never changes (YouTube — the temp card is the feedback). */
  busyLabel: string | null;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="youtube-input-section">
      <input
        type="text"
        id={inputId}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onSubmit();
        }}
      />
      <button type="button" id={buttonId} disabled={busyLabel !== null && busy} onClick={onSubmit}>
        {busyLabel !== null && busy ? busyLabel : buttonLabel}
      </button>
    </div>
  );
}

/** Hydrate every backend state row whose playlist is loaded in a tab
 * (loadDeezerPlaylistStatesFromBackend 3195 and clones — rows for playlists
 * that are not loaded are skipped, exactly like applyXPlaylistState). Rows
 * hydrated mid-flight resume their pollers, the vanilla's
 * startXDiscoveryPolling/startXSyncPolling tail (3320-3326 and clones). */
export async function hydrateStatesForLoaded(
  config: SourceVerticalConfig,
  vertical: SourceVertical,
  findPlaylist: (playlistId: string) => UrlTabPlaylist | undefined,
  /** Optional staleness check — a tab that reloads mid-flight passes its
   * generation guard so an abandoned load can't hydrate or resume over the
   * new one (the vanilla had no such guard anywhere). */
  isCurrent?: () => boolean,
  /**
   * Which field of a states row identifies the playlist. Defaults to
   * playlist_id, which every vertical uses EXCEPT mirrored: its endpoint
   * sends playlist_id as a bare int (web_server.py 38509) while its state
   * key is the url_hash 'mirrored_<n>' (stats-automations.js 986).
   */
  rowKey: (row: Record<string, unknown>) => string = (row) => asString(row.playlist_id),
): Promise<void> {
  try {
    const data = await fetchSourcePlaylistsStates(config);
    if (isCurrent && !isCurrent()) return;
    const states = (data as { states?: Record<string, unknown>[] }).states ?? [];
    for (const row of states) {
      if (isCurrent && !isCurrent()) return;
      // Per-row isolation, like every applyXPlaylistState clone (867 +
      // 946-948): one malformed row must not drop the rest.
      try {
        const playlistId = rowKey(row);
        if (!playlistId) continue;
        const playlist = findPlaylist(playlistId);
        if (!playlist) continue;
        vertical.hydrate(playlistId, { ...row, playlist });
        resumeIfInFlight(vertical, playlistId, asString(row.phase));
      } catch {
        // Skip this row.
      }
    }
  } catch {
    // Best-effort, like the vanilla's swallowed console.error path.
  }
}

/** Restart the transport for a row restored in a running phase. */
export function resumeIfInFlight(vertical: SourceVertical, sourceId: string, phase: string): void {
  if (phase === 'discovering') vertical.resumeDiscovery(sourceId);
  else if (phase === 'syncing') vertical.resumeSync(sourceId);
}

/* ── The three link tabs (deezer-link / spotify-public / itunes-link) ─────── */

interface LinkTabChrome {
  historySource: string;
  inputId: string;
  buttonId: string;
  placeholder: string;
  buttonLabel: string;
  containerId: string;
  emptyPlaceholder: string;
  cardIdPrefix: string;
  cardClassName: string;
}

/**
 * Load a URL the Add-playlist sheet routed here.
 *
 * The sheet decides WHICH tab a link belongs to; the tab still does the
 * loading, so nothing about the four bespoke loaders changes. Firing on a
 * change of `pendingUrl` (not on mount) means re-opening a tab does not
 * re-parse whatever was added last time, and `onPendingConsumed` clears the
 * page's slot so the same link can be routed again later if the user wants.
 */
function usePendingUrl(
  pendingUrl: string | undefined,
  parse: (url: string) => void,
  onPendingConsumed?: () => void,
) {
  const parseRef = useRef(parse);
  parseRef.current = parse;
  const consumedRef = useRef(onPendingConsumed);
  consumedRef.current = onPendingConsumed;
  useEffect(() => {
    if (!pendingUrl) return;
    parseRef.current(pendingUrl);
    consumedRef.current?.();
  }, [pendingUrl]);
}

function LinkTabShell({
  chrome,
  playlists,
  specOf,
  importer,
  cardIcon,
  typeBadge,
  input,
  setInput,
  busy,
  busyLabel,
  parse,
  isAlreadyLoaded,
  historyOps,
}: {
  chrome: LinkTabChrome;
  playlists: UrlTabPlaylist[];
  /** How one of these imports (its mirror key, tracks and track shape). */
  specOf: (p: UrlTabPlaylist) => ImportSpec;
  importer: Importer;
  cardIcon: (p: UrlTabPlaylist) => string;
  typeBadge?: (p: UrlTabPlaylist) => { text: string; color: string };
  input: string;
  setInput: (value: string) => void;
  /** Overrides the plain "Loading..." while busy — used to narrate progress. */
  busyLabel?: string | null;
  busy: boolean;
  parse: (url: string) => void;
  /** The pasted playlist already on a card: opens it rather than loading it again. */
  isAlreadyLoaded: (url: string) => UrlTabPlaylist | undefined;
  historyOps: ReturnType<typeof useUrlHistory>;
}) {
  const [query, setQuery] = useState('');
  return (
    <div>
      <UrlInputSection
        inputId={chrome.inputId}
        buttonId={chrome.buttonId}
        placeholder={chrome.placeholder}
        buttonLabel={chrome.buttonLabel}
        busy={busy}
        busyLabel={busyLabel || 'Loading...'}
        value={input}
        onChange={setInput}
        onSubmit={() => parse(input.trim())}
      />
      <UrlHistoryBar
        containerId={`${chrome.historySource}-url-history`}
        icon={URL_HISTORY_SOURCES[chrome.historySource].icon}
        history={historyOps.history}
        onPick={(url) => {
          const loaded = isAlreadyLoaded(url);
          if (loaded) {
            void importer.importOrOpen(specOf(loaded));
            return;
          }
          setInput(url);
          parse(url);
        }}
        onRemove={historyOps.remove}
      />
      {playlists.length > 0 && (
        <div className="playlist-header">
          <PlaylistSearch value={query} onChange={setQuery} />
        </div>
      )}
      <div className="playlist-scroll-container" id={chrome.containerId}>
        {playlists.length === 0 ? (
          <div className="playlist-placeholder">{chrome.emptyPlaceholder}</div>
        ) : !playlists.some((p) => playlistNameMatches([p.name], query)) ? (
          <div className="playlist-placeholder">{noMatchText(query)}</div>
        ) : (
          <ImportGrid>
            {playlists.map((p) => {
              if (!playlistNameMatches([p.name], query)) return null;
              const spec = specOf(p);
              const trackCount =
                (p.track_count as number | undefined) ||
                (Array.isArray(p.tracks) ? (p.tracks as unknown[]).length : 0);
              return (
                <ImportCard
                  key={String(spec.sourceId)}
                  id={`${chrome.cardIdPrefix}-${String(spec.sourceId)}`}
                  name={asString(p.name)}
                  meta={`${trackCount} track${trackCount === 1 ? '' : 's'}`}
                  artUrl={playlistArtUrl(p)}
                  glyph={cardIcon(p)}
                  badge={typeBadge?.(p).text}
                  status={importer.statusOf(spec.source, spec.sourceId)}
                  onAction={() => void importer.importOrOpen(spec)}
                  onCopy={importer.copyAsMineFor(spec)}
                  copying={importer.copyingOf(spec.source, spec.sourceId)}
                />
              );
            })}
          </ImportGrid>
        )}
      </div>
    </div>
  );
}

/** What importing a pasted Deezer playlist takes. Exported for the tests. */
export function deezerLinkSpec(playlist: UrlTabPlaylist, url?: string): ImportSpec {
  return {
    source: 'deezer',
    sourceId: String(playlist.id),
    name: asString(playlist.name),
    owner: playlist.owner as string | undefined,
    imageUrl: playlist.image_url as string | undefined,
    description: url ?? (playlist.url as string | undefined),
    loadTracks: async () => ({
      tracks: Array.isArray(playlist.tracks) ? (playlist.tracks as unknown[]) : [],
    }),
    toMirrorTracks: deezerMirrorTracks,
  };
}

export function DeezerLinkTab({
  pendingUrl,
  onPendingConsumed,
}: {
  /** A link routed here by the Add-playlist sheet. */
  pendingUrl?: string;
  onPendingConsumed?: () => void;
} = {}) {
  const importer = useImporter();
  // Survives leaving /sync — see useRememberedPlaylists.
  const [playlists, setPlaylists] = useRememberedPlaylists(urlTabCacheKey('deezer'));
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  // Live label for the load button. Resolving a big playlist is ~1 rate-limited
  // request per unique album — minutes on a 1500-track list — and the only
  // feedback used to be the word "Loading...", which reads as hung.
  const [progress, setProgress] = useState<string | null>(null);
  const historyOps = useUrlHistory('deezer');
  const playlistsRef = useRef(playlists);
  playlistsRef.current = playlists;

  const parse = useCallback(
    async (rawUrl: string) => {
      const checked = deezerInputResult(rawUrl);
      if (!checked.ok) {
        window.showToast?.(checked.error, 'error');
        return;
      }
      if (deezerAlreadyLoaded(playlistsRef.current, checked.id)) {
        // pasted again: open it (or import it, if it never made it in)
        const loaded = playlistsRef.current.find((p) => String(p.id) === String(checked.id));
        setInput('');
        if (loaded) await importer.importOrOpen(deezerLinkSpec(loaded));
        return;
      }
      setBusy(true);
      setProgress(null);
      // Full-screen overlay, not just the button label: a few words changing
      // inside a small button is easy to miss, and this wait is minutes long.
      // Same overlay the Deezer ACCOUNT tab already uses for the identical
      // wait, so the two paths now look the same.
      const label = 'Loading Deezer playlist';
      window.showLoadingOverlay?.(`${label}...`);
      // Only ever rewrites TEXT, so if the frames never arrive (older server,
      // socket down) the load still completes normally and the overlay still
      // clears in the finally — the wait just goes back to being silent.
      const onProgress = (event: Event) => {
        const frame = (event as CustomEvent<DeezerPlaylistProgressFrame>).detail;
        const text = deezerProgressLabel(frame, checked.id);
        setProgress(text);
        if (text) window.showLoadingOverlay?.(`${label} — ${text}`);
      };
      window.addEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT, onProgress);
      try {
        const playlist = await fetchDeezerLinkPlaylist(checked.id);
        setPlaylists((prev) => [...prev, { ...playlist, url: rawUrl }]);
        historyOps.save(rawUrl, asString(playlist.name));
        setInput('');
        window.removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT, onProgress);
        window.hideLoadingOverlay?.();
        // loaded is imported: saved as a mirror, identifying in the
        // background, and you land on it
        await importer.importOrOpen(deezerLinkSpec(playlist, rawUrl));
      } catch (error) {
        window.showToast?.(
          `Error loading Deezer playlist: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      } finally {
        window.removeEventListener(DEEZER_PLAYLIST_PROGRESS_EVENT, onProgress);
        window.hideLoadingOverlay?.();
        setProgress(null);
        setBusy(false);
      }
    },
    [historyOps, importer],
  );

  usePendingUrl(pendingUrl, parse, onPendingConsumed);

  return (
    <LinkTabShell
      chrome={{
        historySource: 'deezer',
        inputId: 'deezer-url-input',
        buttonId: 'deezer-parse-btn',
        placeholder: 'Paste Deezer Playlist URL...',
        buttonLabel: 'Load Playlist',
        containerId: 'deezer-playlist-container',
        emptyPlaceholder: 'Paste a Deezer playlist URL above to get started.',
        cardIdPrefix: 'deezer-card',
        cardClassName: 'deezer-playlist-card',
      }}
      playlists={playlists}
      specOf={(p) => deezerLinkSpec(p)}
      importer={importer}
      cardIcon={() => '🎵'}
      input={input}
      setInput={setInput}
      busy={busy}
      busyLabel={progress}
      parse={(url) => void parse(url)}
      isAlreadyLoaded={(url) => {
        const checked = deezerInputResult(url);
        return checked.ok
          ? playlistsRef.current.find((p) => String(p.id) === String(checked.id))
          : undefined;
      }}
      historyOps={historyOps}
    />
  );
}

/** The spotify-public/itunes-link clone pair, parameterized by their drift. */
/** What importing a pasted Spotify / iTunes link takes. Exported for the tests. */
export function publicLinkSpec(
  source: string,
  result: UrlTabPlaylist,
  mirrorTracks: (tracks: unknown[]) => Record<string, unknown>[],
): ImportSpec {
  return {
    source,
    sourceId: String(result.url_hash),
    name: asString(result.name),
    owner: (result.subtitle as string | undefined) || '',
    imageUrl: '',
    description: (result.url as string | undefined) || '',
    loadTracks: async () => ({
      tracks: Array.isArray(result.tracks) ? (result.tracks as unknown[]) : [],
    }),
    toMirrorTracks: mirrorTracks,
  };
}

function PublicLinkTab({
  pendingUrl,
  onPendingConsumed,
  chrome,
  validateError,
  alreadyLoaded,
  callParse,
  mirrorSource,
  mirrorTracks,
  errorNoun,
  cardIcon,
  typeBadge,
}: {
  /** A link routed here by the Add-playlist sheet. */
  pendingUrl?: string;
  onPendingConsumed?: () => void;
  chrome: LinkTabChrome;
  validateError: (url: string) => string | null;
  alreadyLoaded: (playlists: UrlTabPlaylist[], url: string) => boolean;
  callParse: (url: string) => Promise<Record<string, unknown>>;
  mirrorSource: string;
  mirrorTracks: (tracks: unknown[]) => Record<string, unknown>[];
  /** 'Spotify' | 'iTunes' — the catch toast noun (6690/7715). */
  errorNoun: string;
  cardIcon: (p: UrlTabPlaylist) => string;
  typeBadge: (p: UrlTabPlaylist) => { text: string; color: string };
}) {
  const importer = useImporter();
  const [playlists, setPlaylists] = useRememberedPlaylists(urlTabCacheKey(chrome.historySource));
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const historyOps = useUrlHistory(chrome.historySource);
  const playlistsRef = useRef(playlists);
  playlistsRef.current = playlists;
  const specOf = useCallback(
    (p: UrlTabPlaylist) => publicLinkSpec(mirrorSource, p, mirrorTracks),
    [mirrorSource, mirrorTracks],
  );
  // a pasted link whose playlist is already on a card. compared the way
  // alreadyLoaded does, so both agree on what "the same link" means
  const loadedFor = useCallback(
    (url: string) =>
      alreadyLoaded(playlistsRef.current, url)
        ? playlistsRef.current.find((p) => alreadyLoaded([p], url))
        : undefined,
    [alreadyLoaded],
  );

  const parse = useCallback(
    async (url: string) => {
      const invalid = validateError(url);
      if (invalid) {
        window.showToast?.(invalid, 'error');
        return;
      }
      const loaded = loadedFor(url);
      if (loaded) {
        setInput('');
        await importer.importOrOpen(specOf(loaded));
        return;
      }
      setBusy(true);
      try {
        const result = await callParse(url);
        if (result.error) {
          window.showToast?.(`Error: ${asString(result.error)}`, 'error');
          return;
        }
        const same = playlistsRef.current.find(
          (p) => String(p.url_hash) === String(result.url_hash),
        );
        setInput('');
        if (same) {
          await importer.importOrOpen(specOf(same));
          return;
        }
        setPlaylists((prev) => [...prev, result]);
        historyOps.save(url, asString(result.name));
        await importer.importOrOpen(specOf(result));
      } catch (error) {
        window.showToast?.(
          `Error parsing ${errorNoun} URL: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      } finally {
        setBusy(false);
      }
    },
    [historyOps, validateError, loadedFor, callParse, errorNoun, importer, specOf],
  );

  usePendingUrl(pendingUrl, parse, onPendingConsumed);

  return (
    <LinkTabShell
      chrome={chrome}
      playlists={playlists}
      specOf={specOf}
      importer={importer}
      cardIcon={cardIcon}
      typeBadge={typeBadge}
      input={input}
      setInput={setInput}
      busy={busy}
      parse={(url) => void parse(url)}
      isAlreadyLoaded={loadedFor}
      historyOps={historyOps}
    />
  );
}

export function SpotifyPublicTab(
  props: {
    /** A link routed here by the Add-playlist sheet. */
    pendingUrl?: string;
    onPendingConsumed?: () => void;
  } = {},
) {
  return (
    <PublicLinkTab
      {...props}
      chrome={{
        historySource: 'spotify-public',
        inputId: 'spotify-public-url-input',
        buttonId: 'spotify-public-parse-btn',
        placeholder: 'Paste Spotify Playlist or Album URL...',
        buttonLabel: 'Load',
        containerId: 'spotify-public-playlist-container',
        emptyPlaceholder:
          'Paste a Spotify playlist or album URL above to load tracks without needing Spotify API credentials.',
        cardIdPrefix: 'spotify-public-card',
        cardClassName: 'spotify-public-card',
      }}
      validateError={spotifyPublicUrlError}
      alreadyLoaded={spotifyPublicAlreadyLoaded}
      callParse={parseSpotifyPublicUrl as (url: string) => Promise<Record<string, unknown>>}
      mirrorSource="spotify_public"
      mirrorTracks={spotifyPublicMirrorTracks}
      errorNoun="Spotify"
      cardIcon={spotifyPublicCardIcon}
      typeBadge={spotifyPublicTypeBadge}
    />
  );
}

export function ITunesLinkTab(
  props: {
    /** A link routed here by the Add-playlist sheet. */
    pendingUrl?: string;
    onPendingConsumed?: () => void;
  } = {},
) {
  return (
    <PublicLinkTab
      {...props}
      chrome={{
        historySource: 'itunes-link',
        inputId: 'itunes-link-url-input',
        buttonId: 'itunes-link-parse-btn',
        placeholder: 'Paste iTunes or Apple Music Album/Track URL...',
        buttonLabel: 'Load',
        containerId: 'itunes-link-playlist-container',
        emptyPlaceholder: 'Paste an iTunes or Apple Music album/track URL above to load tracks.',
        cardIdPrefix: 'itunes-link-card',
        cardClassName: 'itunes-link-card',
      }}
      validateError={itunesLinkUrlError}
      alreadyLoaded={itunesLinkAlreadyLoaded}
      callParse={parseITunesLinkUrl as (url: string) => Promise<Record<string, unknown>>}
      mirrorSource="itunes_link"
      mirrorTracks={itunesLinkMirrorTracks}
      errorNoun="iTunes"
      cardIcon={itunesLinkCardIcon}
      typeBadge={itunesLinkTypeBadge}
    />
  );
}

/* ── The YouTube tab ──────────────────────────────────────────────────────── */

/** What importing a pasted YouTube playlist takes. Exported for the tests. */
export function youtubeSpec(playlist: UrlTabPlaylist): ImportSpec {
  return {
    source: 'youtube',
    sourceId: String(playlist.url_hash),
    name: asString(playlist.name),
    description: (playlist.url as string | undefined) || '',
    loadTracks: async () => ({
      tracks: Array.isArray(playlist.tracks) ? (playlist.tracks as unknown[]) : [],
    }),
    toMirrorTracks: youtubeMirrorTracks,
  };
}

export function YouTubeTab({
  pendingUrl,
  onPendingConsumed,
}: {
  /** A link routed here by the Add-playlist sheet. */
  pendingUrl?: string;
  onPendingConsumed?: () => void;
} = {}) {
  const importer = useImporter();
  const [playlists, setPlaylists] = useRememberedPlaylists(urlTabCacheKey('youtube'));
  /** In-flight parses — each renders the vanilla's temp 'Parsing…' card (8879). */
  const [pendingUrls, setPendingUrls] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [input, setInput] = useState('');
  const historyOps = useUrlHistory('youtube');
  const playlistsRef = useRef(playlists);
  playlistsRef.current = playlists;
  const pendingRef = useRef(pendingUrls);
  pendingRef.current = pendingUrls;

  const loadedUrls = useCallback(
    () => [...playlistsRef.current.map((p) => asString(p.url)), ...pendingRef.current],
    [],
  );

  /** loadYouTubePlaylistsFromBackend (sync-spotify.js 695): the playlists
   * the server still holds from earlier pastes come back as cards. */
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await fetchYouTubePlaylists();
        for (const info of rows) {
          if (cancelled) return;
          const urlHash = asString(info.url_hash);
          const playlist = (info.playlist as UrlTabPlaylist | undefined) ?? {};
          if (!urlHash) continue;
          setPlaylists((prev) =>
            prev.some((p) => String(p.url_hash) === urlHash)
              ? prev
              : [...prev, { ...playlist, url_hash: urlHash }],
          );
        }
      } catch {
        // Best-effort, like the vanilla's console-only catch.
      }
    })();
    return () => {
      cancelled = true;
    };
    // Mount-only, like the vanilla's page-load hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const parse = useCallback(
    async (url: string) => {
      const invalid = youtubeUrlError(url);
      if (invalid) {
        window.showToast?.(invalid, 'error');
        return;
      }
      if (youtubeAlreadyLoaded(loadedUrls(), url)) {
        // pasted again: open the one already here (or import it, if it never
        // made it in). one still parsing just waits for its own import
        const loaded = playlistsRef.current.find((p) => asString(p.url) === url);
        setInput('');
        if (loaded) await importer.importOrOpen(youtubeSpec(loaded));
        return;
      }
      // The temp card appears immediately (createYouTubeCard 8832).
      setPendingUrls((prev) => [...prev, url]);
      try {
        const result = await parseYouTubeUrl(url);
        if (result.error) {
          window.showToast?.(`Error parsing YouTube playlist: ${result.error}`, 'error');
          return;
        }
        const raw = result as Record<string, unknown>;
        const urlHash = asString(raw.url_hash);
        historyOps.save(url, asString(raw.name));
        // URL variants (youtube.com vs music.youtube.com) canonicalize to the
        // SAME url_hash server-side — never append a duplicate card.
        setPlaylists((prev) =>
          prev.some((p) => String(p.url_hash) === urlHash) ? prev : [...prev, { ...raw, url }],
        );
        setInput('');
        await importer.importOrOpen(youtubeSpec({ ...raw, url }));
      } catch (error) {
        window.showToast?.(
          `Error parsing YouTube playlist: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      } finally {
        // Error or success, the temp card goes (removeYouTubeCard / re-key).
        setPendingUrls((prev) => prev.filter((u) => u !== url));
      }
    },
    [historyOps, loadedUrls, importer],
  );

  usePendingUrl(pendingUrl, parse, onPendingConsumed);

  return (
    <div>
      <UrlInputSection
        inputId="youtube-url-input"
        buttonId="youtube-parse-btn"
        placeholder="Paste YouTube Music Playlist URL..."
        buttonLabel="Parse Playlist"
        busy={pendingUrls.length > 0}
        busyLabel={null}
        value={input}
        onChange={setInput}
        onSubmit={() => void parse(input.trim())}
      />
      <UrlHistoryBar
        containerId="youtube-url-history"
        icon={URL_HISTORY_SOURCES.youtube.icon}
        history={historyOps.history}
        onPick={(url) => {
          if (youtubeAlreadyLoaded(loadedUrls(), url)) {
            window.showToast?.('This playlist is already loaded', 'info');
            return;
          }
          setInput(url);
          void parse(url);
        }}
        onRemove={historyOps.remove}
      />
      {playlists.length > 0 && (
        <div className="playlist-header">
          <PlaylistSearch value={query} onChange={setQuery} />
        </div>
      )}
      <div className="playlist-scroll-container" id="youtube-playlist-container">
        {playlists.length === 0 && pendingUrls.length === 0 ? (
          <div className="playlist-placeholder">Parsed YouTube playlists will appear here.</div>
        ) : (
          <>
            {pendingUrls.length === 0 &&
              !playlists.some((p) => playlistNameMatches([p.name], query)) && (
                <div className="playlist-placeholder">{noMatchText(query)}</div>
              )}
            <ImportGrid>
              {playlists.map((p) => {
                if (!playlistNameMatches([p.name], query)) return null;
                const urlHash = String(p.url_hash);
                const tracks = Array.isArray(p.tracks) ? (p.tracks as unknown[]) : [];
                return (
                  <ImportCard
                    key={urlHash}
                    id={`youtube-card-${urlHash}`}
                    name={asString(p.name)}
                    meta={`${tracks.length} track${tracks.length === 1 ? '' : 's'}`}
                    artUrl={playlistArtUrl(p)}
                    glyph="▶"
                    status={importer.statusOf('youtube', urlHash)}
                    onAction={() => void importer.importOrOpen(youtubeSpec(p))}
                    onCopy={importer.copyAsMineFor(youtubeSpec(p))}
                    copying={importer.copyingOf('youtube', urlHash)}
                  />
                );
              })}
            </ImportGrid>
            {pendingUrls.map((url) => (
              <div className="youtube-playlist-card" key={url} data-url={url}>
                <div className="playlist-card-icon youtube-icon">▶</div>
                <div className="playlist-card-content">
                  <div className="playlist-card-name">Parsing YouTube playlist...</div>
                  <div className="playlist-card-info">
                    <span className="playlist-card-track-count">-- tracks</span>
                    <span
                      className="playlist-card-phase-text"
                      style={{ color: 'var(--white-a60)' }}
                    >
                      Loading...
                    </span>
                  </div>
                </div>
                <div className="playlist-card-progress hidden">♪ 0 / ✓ 0 / ✗ 0 / 0%</div>
                <button type="button" className="playlist-card-action-btn" disabled>
                  Parsing...
                </button>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
