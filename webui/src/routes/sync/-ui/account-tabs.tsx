/**
 * The Spotify and Deezer-ARL tabs — the two ENGINE-DRIVEN account verticals
 * (sync-spotify.js 1598-1630 + 1832-1876, sync-services.js 2437-2570).
 *
 * They share this file because Deezer-ARL is a clone of the Spotify archetype;
 * every difference between them is named at its use site with the vanilla line.
 * Neither has anything to do with -sync.sources.ts or useSourceVertical — those
 * drive the DISCOVERY verticals, a different machine entirely.
 *
 * WHAT THIS DOES NOT OWN (Option A, the settled port shape): the download
 * engine. Opening a download is window.openDownloadMissingModal; an in-flight
 * one is re-shown through the process registry; progress, status and the two
 * action buttons are painted by the engine into the ids the card renders.
 *
 * SEEDING: openDownloadMissingModal looks the playlist up in
 * `spotifyPlaylists` and bails with 'Could not find playlist data.' when it is
 * absent (2235-2240). That array is a top-level `let` at core.js:33 which no
 * module can push to, and the vanilla loader that fills it (1612) stops running
 * once these tabs own their containers. Both tabs therefore seed through
 * window.registerSyncAccountPlaylist — the bridge added beside
 * startDiscoverVirtualSync, which the discover port added for this same trap.
 *
 * Spotify seeds the whole loaded list, because the vanilla ASSIGNS the array
 * wholesale at 1612. ARL seeds per playlist at hand-off, reproducing the shim
 * the vanilla builds at 2646-2654 — including its track count, which comes from
 * the FETCHED tracks and not the row.
 *
 * SELECTION is deliberately a PROP, not local state. It spans tab + shell +
 * engine — `selectedPlaylists` is script-scoped in core.js:34 and
 * startSequentialSync reads it directly — so the shell wave owns the store and
 * decides the bridge. Holding it here would render a `.selected` class that
 * nothing consumes, which is exactly what the reverted P5h did.
 */

import { useCallback, useEffect, useState } from 'react';

import type { AccountPlaylistRow } from '../-sync.accounts';
import type { AccountPlaylistTracks } from '../-sync.api';

import {
  arlShimRow,
  deezerArlId,
  deezerArlStatusClass,
  deezerArlStatusLabel,
  spotifyStatusClass,
} from '../-sync.accounts';
import {
  fetchAccountSyncStatus,
  fetchDeezerArlPlaylistTracks,
  fetchDeezerArlPlaylists,
  fetchSpotifyPlaylistTracks,
  fetchSpotifyPlaylists,
} from '../-sync.api';
import { AccountDetailsModal } from './account-details-modal';
import { AccountPlaylistCard } from './account-playlist-card';

/** The open details modal: which row, and the tracks fetched for it. */
interface OpenDetail {
  row: AccountPlaylistRow;
  detail: AccountPlaylistTracks;
}

export interface AccountTabProps {
  /** The shell's selection store — Spotify only (1794-1809). */
  selectedIds?: ReadonlySet<string>;
  onToggleSelect?: (playlistId: string) => void;
}

/* ── The Spotify tab (1598-1630, 1832-1876) ───────────────────────────────── */

export function SpotifyTab({ selectedIds, onToggleSelect }: AccountTabProps) {
  const [rows, setRows] = useState<AccountPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading playlists...');
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState<OpenDetail | null>(null);

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading playlists...');
    setRows(null);
    setRefreshing(true);
    try {
      const list = (await fetchSpotifyPlaylists()) as AccountPlaylistRow[];
      setRows(list);
      // 1612 assigns the whole array; the engine reads it on every download.
      for (const row of list) {
        window.registerSyncAccountPlaylist?.({ ...row, id: String(row.id) });
      }
      if (list.length === 0) setPlaceholder('No Spotify playlists found.');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      // Both the container AND a toast, as the vanilla does (1624-1625).
      setPlaceholder(`❌ Error: ${message}`);
      window.showToast?.(`Error loading playlists: ${message}`, 'error');
    } finally {
      // The vanilla restores the button in a FINALLY, so an error still
      // re-enables it (1626-1629).
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** openPlaylistDetailsModal (1832-1876). */
  const openDetails = useCallback(async (row: AccountPlaylistRow) => {
    window.showLoadingOverlay?.(`Loading playlist: ${row.name}...`);
    try {
      const detail = await fetchSpotifyPlaylistTracks(String(row.id));
      if (detail.error) throw new Error(detail.error);
      setOpen({ row, detail });
    } catch (error) {
      window.showToast?.(
        `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
        'error',
      );
    } finally {
      window.hideLoadingOverlay?.();
    }
  }, []);

  return (
    <div>
      <div className="playlist-header">
        <h3>Your Spotify Playlists</h3>
        <button
          type="button"
          className="refresh-button"
          id="spotify-refresh-btn"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container" id="spotify-playlist-container">
        {rows === null || rows.length === 0 ? (
          <div className="playlist-placeholder">{placeholder}</div>
        ) : (
          rows.map((row) => {
            const cardId = String(row.id);
            return (
              <AccountPlaylistCard
                key={cardId}
                cardId={cardId}
                row={row}
                statusClass={spotifyStatusClass(row.sync_status)}
                statusLabel={row.sync_status ?? ''}
                selectable
                selected={selectedIds?.has(cardId) ?? false}
                onToggleSelect={() => onToggleSelect?.(cardId)}
                onOpenDetails={() => void openDetails(row)}
                // handleViewProgressClick (1668-1677) — the engine owns the
                // modal; re-showing it is all there is to do.
                onViewProgress={() => window.reopenActiveDownloadModal?.(cardId)}
              />
            );
          })
        )}
      </div>
      {open && (
        <AccountDetailsModal
          modalId="playlist-details-modal"
          playlistId={String(open.row.id)}
          row={open.row}
          detail={open.detail}
          // 1901 prints the count raw — a zero stays a zero.
          trackCount={open.detail.track_count ?? open.row.track_count ?? 0}
          onClose={() => setOpen(null)}
          closeBeforeDownload={false}
          onDownloadMissing={() => void window.openDownloadMissingModal?.(String(open.row.id))}
        />
      )}
    </div>
  );
}

/* ── The Deezer-ARL tab (2437-2570) ───────────────────────────────────────── */

export function DeezerArlTab() {
  const [rows, setRows] = useState<AccountPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading playlists...');
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState<OpenDetail | null>(null);

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading playlists...');
    setRows(null);
    setRefreshing(true);
    try {
      const list = (await fetchDeezerArlPlaylists()) as AccountPlaylistRow[];
      setRows(list);
      if (list.length === 0) {
        setPlaceholder('No Deezer playlists found.');
        return;
      }
      // 2462-2479: ARL rehydrates its OWN in-flight syncs, one awaited request
      // per playlist. A row the backend reports 'syncing' gets shimmed into
      // spotifyPlaylists and handed back to the engine's card painter+poller.
      for (const row of list) {
        const arlId = deezerArlId(row.id);
        try {
          const status = await fetchAccountSyncStatus(arlId);
          if (status.status === 'syncing') {
            window.updateCardToSyncing?.(arlId, status.progress?.progress ?? 0, status.progress);
            window.startSyncPolling?.(arlId);
          }
        } catch {
          // 2478: no active sync is the normal case, not an error.
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      setPlaceholder(`❌ Error: ${message}`);
      window.showToast?.(`Error loading Deezer playlists: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** openDeezerArlPlaylistDetailsModal (2534-2570). */
  const openDetails = useCallback(async (row: AccountPlaylistRow) => {
    window.showLoadingOverlay?.(`Loading playlist: ${row.name}...`);
    try {
      // The PATH takes the raw deezer id; the cache and the engine use the
      // prefixed one (2557 vs 2540).
      const detail = await fetchDeezerArlPlaylistTracks(String(row.id));
      if (detail.error) throw new Error(detail.error);
      setOpen({ row, detail });
    } catch (error) {
      window.showToast?.(
        `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
        'error',
      );
    } finally {
      window.hideLoadingOverlay?.();
    }
  }, []);

  return (
    <div>
      <div className="playlist-header">
        <h3>Your Deezer Playlists</h3>
        <button
          type="button"
          className="refresh-button"
          id="deezer-arl-refresh-btn"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container" id="deezer-arl-playlist-container">
        {rows === null || rows.length === 0 ? (
          <div className="playlist-placeholder">{placeholder}</div>
        ) : (
          rows.map((row) => {
            const cardId = deezerArlId(row.id);
            return (
              <AccountPlaylistCard
                key={cardId}
                cardId={cardId}
                row={row}
                statusClass={deezerArlStatusClass(row.sync_status)}
                statusLabel={deezerArlStatusLabel(row.sync_status)}
                extraClassName="deezer-arl-playlist-card"
                // 2503: no selection handler on this card at all.
                selectable={false}
                selected={false}
                onOpenDetails={() => void openDetails(row)}
                onViewProgress={() => window.reopenActiveDownloadModal?.(cardId)}
              />
            );
          })
        )}
      </div>
      {open && (
        <AccountDetailsModal
          modalId="deezer-arl-playlist-details-modal"
          playlistId={deezerArlId(open.row.id)}
          row={open.row}
          detail={open.detail}
          // 2592: `||`, not `??` — a zero count falls through to the fetched
          // track list, where Spotify would print the zero.
          trackCount={
            (open.detail.track_count ?? open.row.track_count) || (open.detail.tracks?.length ?? 0)
          }
          onClose={() => setOpen(null)}
          // 2637-2639: ARL closes before handing off.
          closeBeforeDownload
          onDownloadMissing={() => {
            // The shim at 2646-2654, seeded through the bridge: prefixed id,
            // and the count taken from the FETCHED tracks.
            window.registerSyncAccountPlaylist?.(arlShimRow(open.row, 'modal', open.detail.tracks));
            void window.openDownloadMissingModal?.(deezerArlId(open.row.id));
          }}
        />
      )}
    </div>
  );
}
