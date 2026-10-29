/**
 * The Spotify and Deezer account tabs: your playlists on that service, each
 * one ready to import into SoulSync.
 *
 * These used to drive the old download engine straight from the card: a
 * details modal, Download Missing, Sync, and for Spotify a select-several
 * sequential sync. All of that is what an imported playlist does under
 * Mirrored (Sync & download), so a card now has one action: import it, or
 * open it once it's in. Importing fetches its tracks, saves it as a mirror
 * (which starts identifying it) and lands you on it.
 *
 * The mirror keys match the ones the old engine wrote ('spotify' + the
 * playlist id, 'deezer' + the raw deezer id), so a playlist imported the old
 * way reads as already in.
 */

import { useCallback, useEffect, useState } from 'react';

import type { AccountPlaylistRow } from '../-sync.accounts';
import type { AccountPlaylistTracks } from '../-sync.api';
import type { ImportSpec } from '../-sync.importer';

import {
  fetchDeezerArlPlaylistTracks,
  fetchDeezerArlPlaylists,
  fetchSpotifyPlaylistTracks,
  fetchSpotifyPlaylists,
} from '../-sync.api';
import { DEEZER_PLAYLIST_PROGRESS_EVENT } from '../-sync.events';
import { ImportCard, ImportGrid, ImportIntro } from './import-card';
import { PlaylistSearch, noMatchText, playlistNameMatches } from './playlist-search';
import { useImporter } from './use-importer';

// Lives in -sync.events now that both deezer tabs listen for the same frames.
// Re-exported here so existing importers of this module keep working.
export { DEEZER_PLAYLIST_PROGRESS_EVENT };

/** One frame of it: how far through resolving a playlist's albums we are. */
export interface DeezerPlaylistProgress {
  playlist_id: string;
  done: number;
  total: number;
  /** 'release dates' or 'track numbers' — the two passes over the albums. */
  phase: string;
}

/** What importing one account playlist takes. Exported for the tests. */
export function accountRowImportSpec(
  source: 'spotify' | 'deezer',
  row: AccountPlaylistRow,
  fetchTracks: (id: string) => Promise<AccountPlaylistTracks>,
): ImportSpec {
  return {
    source,
    sourceId: String(row.id),
    name: row.name ?? 'Playlist',
    owner: row.owner,
    imageUrl: row.image_url,
    description: row.description,
    loadTracks: async () => {
      const detail = await fetchTracks(String(row.id));
      if (detail.error) throw new Error(detail.error);
      return { tracks: detail.tracks ?? [] };
    },
    // spotify-shaped tracks (artists and album as objects), which the mirror
    // payload builder reads as they are
    toMirrorTracks: (tracks) => tracks as Record<string, unknown>[],
  };
}

function AccountRowsTab({
  source,
  title,
  refreshBtnId,
  containerId,
  cardIdPrefix,
  noun,
  fetchRows,
  fetchTracks,
}: {
  source: 'spotify' | 'deezer';
  title: string;
  refreshBtnId: string;
  containerId: string;
  cardIdPrefix: string;
  noun: string;
  fetchRows: () => Promise<unknown>;
  fetchTracks: (id: string) => Promise<AccountPlaylistTracks>;
}) {
  const [rows, setRows] = useState<AccountPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading playlists...');
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState('');
  const importer = useImporter();

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading playlists...');
    setRows(null);
    setRefreshing(true);
    try {
      const list = (await fetchRows()) as AccountPlaylistRow[];
      setRows(list);
      if (list.length === 0) setPlaceholder(`No ${noun} playlists found.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      setPlaceholder(`❌ Error: ${message}`);
      window.showToast?.(`Error loading ${noun} playlists: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, [fetchRows, noun]);

  useEffect(() => {
    void load();
  }, [load]);

  const spec = (row: AccountPlaylistRow) => accountRowImportSpec(source, row, fetchTracks);
  const shown = (rows ?? []).filter((row) => playlistNameMatches([row.name], query));
  const remaining = (rows ?? []).filter(
    (row) => importer.statusOf(source, String(row.id)) === 'new',
  );

  return (
    <div>
      <div className="playlist-header">
        <h3>{title}</h3>
        {rows && rows.length > 0 && <PlaylistSearch value={query} onChange={setQuery} />}
        <button
          type="button"
          className="refresh-button"
          id={refreshBtnId}
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container" id={containerId}>
        {rows === null || rows.length === 0 ? (
          <div className="playlist-placeholder">{placeholder}</div>
        ) : shown.length === 0 ? (
          <div className="playlist-placeholder">{noMatchText(query)}</div>
        ) : (
          <>
            <ImportIntro
              canCopy
              remaining={remaining.length}
              onImportAll={() => void importer.importAll(remaining.map(spec))}
            />
            <ImportGrid>
              {shown.map((row) => {
                const id = String(row.id);
                const count = row.track_count ?? 0;
                return (
                  <ImportCard
                    key={id}
                    id={`${cardIdPrefix}-${id}`}
                    name={row.name ?? 'Playlist'}
                    meta={`${count} track${count === 1 ? '' : 's'}${row.owner ? ` · ${row.owner}` : ''}`}
                    artUrl={row.image_url}
                    status={importer.statusOf(source, id)}
                    onAction={() => void importer.importOrOpen(spec(row))}
                    onCopy={importer.copyAsMineFor(spec(row))}
                    copying={importer.copyingOf(source, id)}
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

export function SpotifyTab() {
  return (
    <AccountRowsTab
      source="spotify"
      title="Your Spotify Playlists"
      refreshBtnId="spotify-refresh-btn"
      containerId="spotify-playlist-container"
      cardIdPrefix="spotify-card"
      noun="Spotify"
      fetchRows={fetchSpotifyPlaylists}
      fetchTracks={fetchSpotifyPlaylistTracks}
    />
  );
}

export function DeezerArlTab() {
  return (
    <AccountRowsTab
      source="deezer"
      title="Your Deezer Playlists"
      refreshBtnId="deezer-arl-refresh-btn"
      containerId="deezer-arl-playlist-container"
      cardIdPrefix="deezer-arl-card"
      noun="Deezer"
      fetchRows={fetchDeezerArlPlaylists}
      // the path takes the RAW deezer id; deezer_arl_ was the old engine's
      // client-side id space
      fetchTracks={fetchDeezerArlPlaylistTracks}
    />
  );
}
