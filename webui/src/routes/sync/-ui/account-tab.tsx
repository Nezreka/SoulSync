/**
 * The Tidal, Qobuz and YouTube Music account tabs: your playlists on that
 * service, each one ready to import into SoulSync.
 *
 * They used to be a second home for every playlist. Refresh mirrored the
 * whole account in the background (a full track fetch per playlist, which is
 * what made Tidal's refresh crawl for minutes), and each card ran its own
 * identify, sync and download alongside the same playlist on the Mirrored
 * tab. Now a card has one action: import it (or open it, once it's in).
 * Importing fetches that one playlist's tracks, saves it as a mirror, which
 * starts identifying it, and lands you on it under Mirrored.
 */

import { useCallback, useState } from 'react';

import type { SourceSkipped } from '../-sync.state';
import type { UrlTabPlaylist } from '../-sync.url-tabs';

import { recallAccountPlaylists, rememberAccountPlaylists } from '../-sync.account-cache';
import { fetchAccountPlaylist, fetchSourcePlaylists } from '../-sync.api';
import { type ImportSpec } from '../-sync.importer';
import { asString, deezerMirrorTracks } from '../-sync.url-tabs';
import { ImportCard, ImportGrid, ImportIntro } from './import-card';
import { playlistArtUrl } from './playlist-art';
import { PlaylistSearch, noMatchText, playlistNameMatches } from './playlist-search';
import { useImporter } from './use-importer';

interface AccountTabChrome {
  base: 'tidal' | 'qobuz' | 'ytmusic';
  title: string;
  refreshBtnId: string;
  refreshBtnClass: string;
  containerId: string;
  initialPlaceholder: string;
  loadingPlaceholder: string;
  /** 'Tidal' | 'Qobuz' | 'YouTube Music' — the toast/placeholder noun. */
  noun: string;
  cardIdPrefix: string;
}

/** What importing one of these playlists takes. Exported for the tests. */
export function accountImportSpec(base: AccountTabChrome['base'], p: UrlTabPlaylist): ImportSpec {
  return {
    // the mirror's source is the vertical's id, which is the base here
    source: base,
    sourceId: String(p.id),
    name: asString(p.name),
    owner: p.owner as string | undefined,
    imageUrl: p.image_url as string | undefined,
    description: p.description as string | undefined,
    loadTracks: async () => {
      const cached = Array.isArray(p.tracks) ? (p.tracks as unknown[]) : [];
      if (cached.length > 0) {
        return { tracks: cached, sourceSkipped: p.source_skipped as SourceSkipped | null };
      }
      const full = await fetchAccountPlaylist(base, String(p.id));
      if (full.error) throw new Error(asString(full.error));
      return {
        tracks: Array.isArray(full.tracks) ? (full.tracks as unknown[]) : [],
        // tidal says what it couldn't load; the rest never set it (#1613)
        sourceSkipped:
          'source_skipped' in full ? (full.source_skipped as SourceSkipped) : undefined,
      };
    },
    toMirrorTracks: deezerMirrorTracks,
  };
}

function AccountImportTab({ chrome }: { chrome: AccountTabChrome }) {
  /** null = never loaded (the click-Refresh placeholder). */
  const [playlists, setPlaylists] = useState<UrlTabPlaylist[] | null>(() =>
    recallAccountPlaylists(chrome.base),
  );
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const importer = useImporter();

  const load = useCallback(async () => {
    setLoading(true);
    setErrorText(null);
    try {
      const rows = (await fetchSourcePlaylists(chrome.base)) as UrlTabPlaylist[];
      setPlaylists(rows);
      rememberAccountPlaylists(chrome.base, rows);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      setErrorText(`❌ Error: ${message}`);
      window.showToast?.(`Error loading ${chrome.noun} playlists: ${message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [chrome]);

  const shown = (playlists ?? []).filter((p) => playlistNameMatches([p.name], query));
  const remaining = (playlists ?? []).filter(
    (p) => importer.statusOf(chrome.base, String(p.id)) === 'new',
  );

  return (
    <div>
      <div className="playlist-header">
        <h3>{chrome.title}</h3>
        {playlists && playlists.length > 0 && <PlaylistSearch value={query} onChange={setQuery} />}
        <button
          type="button"
          className={`refresh-button ${chrome.refreshBtnClass}`}
          id={chrome.refreshBtnId}
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container" id={chrome.containerId}>
        {errorText ? (
          <div className="playlist-placeholder">{errorText}</div>
        ) : playlists === null ? (
          <div className="playlist-placeholder">
            {loading ? chrome.loadingPlaceholder : chrome.initialPlaceholder}
          </div>
        ) : playlists.length === 0 ? (
          <div className="playlist-placeholder">{`No ${chrome.noun} playlists found.`}</div>
        ) : shown.length === 0 ? (
          <div className="playlist-placeholder">{noMatchText(query)}</div>
        ) : (
          <>
            <ImportIntro
              canCopy
              remaining={remaining.length}
              onImportAll={() =>
                void importer.importAll(remaining.map((p) => accountImportSpec(chrome.base, p)))
              }
            />
            <ImportGrid>
              {shown.map((p) => {
                const sourceId = String(p.id);
                const count = (p.track_count as number | undefined) ?? 0;
                const owner = asString(p.owner);
                return (
                  <ImportCard
                    key={sourceId}
                    id={`${chrome.cardIdPrefix}-${sourceId}`}
                    name={asString(p.name)}
                    meta={`${count} track${count === 1 ? '' : 's'}${owner ? ` · ${owner}` : ''}`}
                    artUrl={playlistArtUrl(p)}
                    status={importer.statusOf(chrome.base, sourceId)}
                    onAction={() => void importer.importOrOpen(accountImportSpec(chrome.base, p))}
                    onCopy={importer.copyAsMineFor(accountImportSpec(chrome.base, p))}
                    copying={importer.copyingOf(chrome.base, sourceId)}
                  />
                );
              })}
            </ImportGrid>
          </>
        )}
      </div>
    </div>
  );
}

export function TidalTab() {
  return (
    <AccountImportTab
      chrome={{
        base: 'tidal',
        title: 'Your Tidal Playlists',
        refreshBtnId: 'tidal-refresh-btn',
        refreshBtnClass: 'tidal',
        containerId: 'tidal-playlist-container',
        initialPlaceholder: "Click 'Refresh' to load your Tidal playlists.",
        loadingPlaceholder: '🔄 Loading Tidal playlists...',
        noun: 'Tidal',
        cardIdPrefix: 'tidal-card',
      }}
    />
  );
}

export function QobuzTab() {
  return (
    <AccountImportTab
      chrome={{
        base: 'qobuz',
        title: 'Your Qobuz Playlists',
        refreshBtnId: 'qobuz-refresh-btn',
        refreshBtnClass: 'qobuz',
        containerId: 'qobuz-playlist-container',
        initialPlaceholder: "Click 'Refresh' to load your Qobuz playlists.",
        loadingPlaceholder: '🔄 Loading Qobuz playlists...',
        noun: 'Qobuz',
        cardIdPrefix: 'qobuz-card',
      }}
    />
  );
}

/**
 * The YouTube Music ACCOUNT tab: the signed-in account's own library
 * playlists (+ Liked Music), the auth-based counterpart to the `youtube` tab's
 * paste-a-link flow.
 */
export function YTMusicTab() {
  return (
    <AccountImportTab
      chrome={{
        base: 'ytmusic',
        title: 'Your YouTube Music Playlists',
        refreshBtnId: 'ytmusic-refresh-btn',
        refreshBtnClass: 'ytmusic',
        containerId: 'ytmusic-playlist-container',
        initialPlaceholder: "Click 'Refresh' to load your YouTube Music playlists.",
        loadingPlaceholder: '🔄 Loading YouTube Music playlists...',
        noun: 'YouTube Music',
        cardIdPrefix: 'ytmusic-card',
      }}
    />
  );
}
