/**
 * The four URL-import tabs (deezer-link 2706, spotify-public 6611,
 * itunes-link 7633, youtube 8806 — sync-services.js): paste a link, parse it
 * into a card, auto-mirror, keep a Recent-URLs pill bar, and run the shared
 * discover→sync→download vertical from the card.
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

import type { SourceVerticalConfig } from '../-sync.sources';
import type { UrlTabPlaylist } from '../-sync.url-tabs';
import type { SourceVertical } from '../-sync.use-vertical';

import {
  fetchDeezerLinkPlaylist,
  fetchSourcePlaylistsStates,
  fetchYouTubePlaylists,
  parseITunesLinkUrl,
  parseSpotifyPublicUrl,
  parseYouTubeUrl,
  postMirrorPlaylist,
} from '../-sync.api';
import {
  DEEZER_PLAYLIST_PROGRESS_EVENT,
  type DeezerPlaylistProgressFrame,
  deezerProgressLabel,
} from '../-sync.events';
import { buildMirrorPayload } from '../-sync.import';
import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
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
  ytActionButtonText,
  ytProgressVisible,
} from '../-sync.url-tabs';
import { URL_HISTORY_SOURCES } from '../-sync.urls';
import { fetchAndHydrateState } from '../-sync.use-vertical';
import { cardProgressLine } from './card-progress';
import { SourceCard } from './source-card';
import { UrlHistoryBar, useUrlHistory } from './url-history-bar';

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

/**
 * The card-open flow every URL tab shares (handleDeezerCardClick 2835-2901
 * and its clones): ensure a seeded state, refetch the full backend state when
 * a 'discovered' card lost its results, then open the shared modal.
 */
export function useUrlCardOpen(
  config: SourceVerticalConfig,
  vertical: SourceVertical,
  setOpenId: (id: string) => void,
): (sourceId: string, playlist: UrlTabPlaylist) => Promise<void> {
  return useCallback(
    async (sourceId, playlist) => {
      const existing = vertical.states[sourceId];
      if (!existing) vertical.seed(sourceId, playlist);
      if (existing && existing.phase === 'discovered' && existing.rawResults.length === 0) {
        // The vanilla refetches /api/<src>/state/<id> here (2862-2878); the
        // backend row carries no playlist object, so it is re-attached.
        await fetchAndHydrateState(config, sourceId, (sid, backend) =>
          vertical.hydrate(sid, { ...backend, playlist: backend.playlist ?? playlist }),
        );
      }
      setOpenId(sourceId);
    },
    [config, vertical, setOpenId],
  );
}

/**
 * YouTube's drift (handleYouTubeCardClick 9047-9054): a FRESH card click
 * starts discovery immediately AND opens the modal — the other verticals wait
 * for the modal's Start Discovery button.
 */
export function useYouTubeCardOpen(
  vertical: SourceVertical,
  setOpenId: (id: string) => void,
): (sourceId: string, playlist: UrlTabPlaylist) => Promise<void> {
  const openShared = useUrlCardOpen(SYNC_SOURCES.youtube, vertical, setOpenId);
  return useCallback(
    async (sourceId, playlist) => {
      const existing = vertical.states[sourceId];
      if (!existing || existing.phase === 'fresh') {
        if (!existing) vertical.seed(sourceId, playlist);
        void vertical.startDiscovery(sourceId);
        setOpenId(sourceId);
        return;
      }
      await openShared(sourceId, playlist);
    },
    [vertical, setOpenId, openShared],
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

function LinkTabShell({
  chrome,
  config,
  vertical,
  playlists,
  idOf,
  cardIcon,
  typeBadge,
  input,
  setInput,
  busy,
  busyLabel,
  parse,
  isAlreadyLoaded,
  onOpen,
  historyOps,
}: {
  chrome: LinkTabChrome;
  config: SourceVerticalConfig;
  vertical: SourceVertical;
  playlists: UrlTabPlaylist[];
  idOf: (p: UrlTabPlaylist) => string;
  cardIcon: (p: UrlTabPlaylist) => string;
  typeBadge?: (p: UrlTabPlaylist) => { text: string; color: string };
  input: string;
  setInput: (value: string) => void;
  /** Overrides the plain "Loading..." while busy — used to narrate progress. */
  busyLabel?: string | null;
  busy: boolean;
  parse: (url: string) => void;
  isAlreadyLoaded: (url: string) => boolean;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
  historyOps: ReturnType<typeof useUrlHistory>;
}) {
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
          if (isAlreadyLoaded(url)) {
            window.showToast?.('This playlist is already loaded', 'info');
            return;
          }
          setInput(url);
          parse(url);
        }}
        onRemove={historyOps.remove}
      />
      <div className="playlist-scroll-container" id={chrome.containerId}>
        {playlists.length === 0 ? (
          <div className="playlist-placeholder">{chrome.emptyPlaceholder}</div>
        ) : (
          playlists.map((p) => {
            const id = idOf(p);
            const state = vertical.states[id] ?? freshSourceState(config, id);
            const trackCount =
              (p.track_count as number | undefined) ||
              (Array.isArray(p.tracks) ? (p.tracks as unknown[]).length : 0);
            return (
              <SourceCard
                key={id}
                id={`${chrome.cardIdPrefix}-${id}`}
                cardClassName={chrome.cardClassName}
                icon={cardIcon(p)}
                name={asString(p.name)}
                countText={`${trackCount} tracks`}
                phase={state.phase}
                typeBadge={typeBadge?.(p)}
                progressLine={cardProgressLine(state, config)}
                onClick={() => onOpen(id, p)}
              />
            );
          })
        )}
      </div>
    </div>
  );
}

export function DeezerLinkTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
}) {
  const config = SYNC_SOURCES.deezer;
  const [playlists, setPlaylists] = useState<UrlTabPlaylist[]>([]);
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
        window.showToast?.('This playlist is already loaded', 'info');
        setInput('');
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
        setPlaylists((prev) => [...prev, playlist]);
        const tracks = Array.isArray(playlist.tracks) ? (playlist.tracks as unknown[]) : [];
        if (tracks.length > 0) {
          // Fire-and-forget, like the vanilla's mirrorPlaylist call (2756).
          void postMirrorPlaylist(
            buildMirrorPayload(
              'deezer',
              playlist.id as string | number,
              asString(playlist.name),
              deezerMirrorTracks(tracks),
              {
                owner: playlist.owner as string | undefined,
                image_url: playlist.image_url as string | undefined,
                description: rawUrl,
              },
            ),
          ).catch(() => undefined);
        }
        historyOps.save(rawUrl, asString(playlist.name));
        vertical.seed(String(playlist.id), playlist);
        await hydrateStatesForLoaded(config, vertical, (pid) =>
          [...playlistsRef.current, playlist].find((p) => String(p.id) === pid),
        );
        setInput('');
        window.showToast?.(
          `Deezer playlist loaded: ${asString(playlist.name)} (${(playlist.track_count as number) || tracks.length} tracks)`,
          'success',
        );
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
    [config, vertical, historyOps],
  );

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
      config={config}
      vertical={vertical}
      playlists={playlists}
      idOf={(p) => String(p.id)}
      cardIcon={() => '🎵'}
      input={input}
      setInput={setInput}
      busy={busy}
      busyLabel={progress}
      parse={(url) => void parse(url)}
      isAlreadyLoaded={(url) => {
        const checked = deezerInputResult(url);
        return checked.ok ? deezerAlreadyLoaded(playlistsRef.current, checked.id) : false;
      }}
      onOpen={onOpen}
      historyOps={historyOps}
    />
  );
}

/** The spotify-public/itunes-link clone pair, parameterized by their drift. */
function PublicLinkTab({
  vertical,
  onOpen,
  config,
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
  vertical: SourceVertical;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
  config: SourceVerticalConfig;
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
  const [playlists, setPlaylists] = useState<UrlTabPlaylist[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const historyOps = useUrlHistory(chrome.historySource);
  const playlistsRef = useRef(playlists);
  playlistsRef.current = playlists;

  const parse = useCallback(
    async (url: string) => {
      const invalid = validateError(url);
      if (invalid) {
        window.showToast?.(invalid, 'error');
        return;
      }
      if (alreadyLoaded(playlistsRef.current, url)) {
        window.showToast?.('This playlist is already loaded', 'info');
        setInput('');
        return;
      }
      setBusy(true);
      try {
        const result = await callParse(url);
        if (result.error) {
          window.showToast?.(`Error: ${asString(result.error)}`, 'error');
          return;
        }
        if (playlistsRef.current.find((p) => String(p.url_hash) === String(result.url_hash))) {
          window.showToast?.('This playlist is already loaded', 'info');
          setInput('');
          return;
        }
        setPlaylists((prev) => [...prev, result]);
        const tracks = Array.isArray(result.tracks) ? (result.tracks as unknown[]) : [];
        if (tracks.length > 0) {
          void postMirrorPlaylist(
            buildMirrorPayload(
              mirrorSource,
              String(result.url_hash),
              asString(result.name),
              mirrorTracks(tracks),
              {
                owner: (result.subtitle as string | undefined) || '',
                image_url: '',
                description: (result.url as string | undefined) || '',
              },
            ),
          ).catch(() => undefined);
        }
        historyOps.save(url, asString(result.name));
        vertical.seed(String(result.url_hash), result);
        await hydrateStatesForLoaded(config, vertical, (pid) =>
          [...playlistsRef.current, result].find((p) => String(p.url_hash) === pid),
        );
        setInput('');
        window.showToast?.(
          `Loaded: ${asString(result.name)} (${String(result.track_count as number | undefined)} tracks)`,
          'success',
        );
      } catch (error) {
        window.showToast?.(
          `Error parsing ${errorNoun} URL: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      } finally {
        setBusy(false);
      }
    },
    [
      config,
      vertical,
      historyOps,
      validateError,
      alreadyLoaded,
      callParse,
      mirrorSource,
      mirrorTracks,
      errorNoun,
    ],
  );

  return (
    <LinkTabShell
      chrome={chrome}
      config={config}
      vertical={vertical}
      playlists={playlists}
      idOf={(p) => String(p.url_hash)}
      cardIcon={cardIcon}
      typeBadge={typeBadge}
      input={input}
      setInput={setInput}
      busy={busy}
      parse={(url) => void parse(url)}
      isAlreadyLoaded={(url) => alreadyLoaded(playlistsRef.current, url)}
      onOpen={onOpen}
      historyOps={historyOps}
    />
  );
}

export function SpotifyPublicTab(props: {
  vertical: SourceVertical;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
}) {
  return (
    <PublicLinkTab
      {...props}
      config={SYNC_SOURCES.spotify_public}
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

export function ITunesLinkTab(props: {
  vertical: SourceVertical;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
}) {
  return (
    <PublicLinkTab
      {...props}
      config={SYNC_SOURCES.itunes_link}
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

export function YouTubeTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (sourceId: string, playlist: UrlTabPlaylist) => void;
}) {
  const config = SYNC_SOURCES.youtube;
  const [playlists, setPlaylists] = useState<UrlTabPlaylist[]>([]);
  /** In-flight parses — each renders the vanilla's temp 'Parsing…' card (8879). */
  const [pendingUrls, setPendingUrls] = useState<string[]>([]);
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

  /** loadYouTubePlaylistsFromBackend (sync-spotify.js 695) — restore stored
   * playlists + their states on mount; non-fresh/discovering rows also pull
   * the full per-hash state for their discovery results. */
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
          vertical.hydrate(urlHash, { ...info, playlist });
          const phase = asString(info.phase, 'fresh') || 'fresh';
          if (phase !== 'fresh' && phase !== 'discovering') {
            await fetchAndHydrateState(config, urlHash, (sid, backend) =>
              vertical.hydrate(sid, { ...info, ...backend, playlist }),
            );
          }
          // The vanilla resumed in-flight YT work via checkForActiveProcesses
          // → rehydrateYouTubePlaylist (sync-spotify.js 1507-1513); here the
          // vertical's own pollers are the guaranteed path.
          resumeIfInFlight(vertical, urlHash, phase);
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
        window.showToast?.('This playlist is already loaded', 'info');
        setInput('');
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
        const tracks = Array.isArray(raw.tracks) ? (raw.tracks as unknown[]) : [];
        historyOps.save(url, asString(raw.name));
        // URL variants (youtube.com vs music.youtube.com) canonicalize to the
        // SAME url_hash server-side — never append a duplicate card.
        setPlaylists((prev) =>
          prev.some((p) => String(p.url_hash) === urlHash) ? prev : [...prev, { ...raw, url }],
        );
        vertical.seed(urlHash, { ...raw, url });
        // Unconditional auto-mirror — the one parse head with no length guard
        // (8861); album_name is always '' for YouTube tracks.
        void postMirrorPlaylist(
          buildMirrorPayload('youtube', urlHash, asString(raw.name), youtubeMirrorTracks(tracks), {
            description: url,
          }),
        ).catch(() => undefined);
        setInput('');
        window.showToast?.(
          `YouTube playlist parsed: ${asString(raw.name)} (${tracks.length} tracks)`,
          'success',
        );
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
    [config, vertical, historyOps, loadedUrls],
  );

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
      <div className="playlist-scroll-container" id="youtube-playlist-container">
        {playlists.length === 0 && pendingUrls.length === 0 ? (
          <div className="playlist-placeholder">Parsed YouTube playlists will appear here.</div>
        ) : (
          <>
            {playlists.map((p) => {
              const urlHash = String(p.url_hash);
              const state = vertical.states[urlHash] ?? freshSourceState(config, urlHash);
              const tracks = Array.isArray(p.tracks) ? (p.tracks as unknown[]) : [];
              return (
                <SourceCard
                  key={urlHash}
                  id={`youtube-card-${urlHash}`}
                  cardClassName=""
                  icon="▶"
                  iconClassName="youtube-icon"
                  name={asString(p.name)}
                  countText={`${tracks.length} tracks`}
                  phase={state.phase}
                  buttonText={ytActionButtonText(state.phase)}
                  // YouTube hides the element outside the running phases
                  // (8988-9036) — inside them the shared line applies.
                  progressLine={
                    ytProgressVisible(state.phase) ? cardProgressLine(state, config) : null
                  }
                  onClick={() => onOpen(urlHash, p)}
                />
              );
            })}
            {pendingUrls.map((url) => (
              <div className="youtube-playlist-card" key={url} data-url={url}>
                <div className="playlist-card-icon youtube-icon">▶</div>
                <div className="playlist-card-content">
                  <div className="playlist-card-name">Parsing YouTube playlist...</div>
                  <div className="playlist-card-info">
                    <span className="playlist-card-track-count">-- tracks</span>
                    <span className="playlist-card-phase-text" style={{ color: '#999' }}>
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
