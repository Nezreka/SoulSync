/**
 * The per-source modal host — the wiring layer every tab shares: renders the
 * shared DiscoveryModal for the open playlist, mounts the React-native
 * FixModal (key'd by row index — its prefill and auto-search read the row
 * once), runs unmatch with the vanilla's toasts (wishlist-tools.js 701-703),
 * and hands downloads to the vanilla engine
 * (openDownloadMissingModalForYouTube with the config's vpid + the two-format
 * track builder, startYouTubeDownloadMissing 10755-10775).
 */

import { useCallback, useState } from 'react';

import type { FixTrack } from '../-sync.fix';
import type { SourceVerticalConfig } from '../-sync.sources';
import type { SourcePlaylistState } from '../-sync.state';
import type { DiscoveryRow } from '../-sync.transform';
import type { SourceVertical } from '../-sync.use-vertical';

import { postUnmatch } from '../-sync.fix';
import { buildDownloadTracks } from '../-sync.modal-core';
import { applyFixedMatch, applyUnmatched } from '../-sync.state';
import { DiscoveryModal } from './discovery-modal';
import { FixModal } from './fix-modal';

function statePlaylistTracks(state: SourcePlaylistState): unknown[] {
  return Array.isArray(state.playlist?.tracks) ? (state.playlist.tracks as unknown[]) : [];
}

// openDownloadMissingModalForYouTube is declared by the shell globals
// (globals.d.ts) with the engine's full signature.

export interface SourceModalsProps {
  config: SourceVerticalConfig;
  vertical: SourceVertical;
  /** The open playlist's source id; null = no modal. */
  openId: string | null;
  onClose: () => void;
  standalone: boolean;
  mirroredSource?: string;
  /** Optional per-source discovery-start body builder (LB sends {playlist}). */
  discoveryStartBody?: (state: SourcePlaylistState) => unknown;
}

export function SourceModals({
  config,
  vertical,
  openId,
  onClose,
  standalone,
  mirroredSource,
  discoveryStartBody,
}: SourceModalsProps) {
  const [fixRow, setFixRow] = useState<DiscoveryRow | null>(null);

  const state = openId !== null ? vertical.states[openId] : undefined;

  const close = useCallback(() => {
    if (openId !== null) void vertical.closeModalReset(openId);
    setFixRow(null);
    onClose();
  }, [openId, vertical, onClose]);

  const onDownloadMissing = useCallback(() => {
    if (!state || openId === null) return;
    // The vanilla's two distinct guards (10715-10716, 10755-10757).
    if (state.rawResults.length === 0) {
      window.showToast?.('No discovery results available for download', 'error');
      return;
    }
    const tracks = buildDownloadTracks(state.rawResults);
    if (tracks.length === 0) {
      window.showToast?.('No Spotify matches found for download', 'error');
      return;
    }
    const vpid = `${config.ids.vpidPrefix}${openId}`;
    const name = typeof state.playlist?.name === 'string' ? state.playlist.name : 'Playlist';
    // The vanilla stores the hand-off on the STATE (10765) — the converted id
    // keeps the Download button available and lets rehydration find the
    // engine modal later.
    vertical.patchState(openId, (s) => ({ ...s, convertedSpotifyPlaylistId: vpid }));
    // The vanilla hides the discovery modal BEFORE opening the engine's
    // (10768-10771) — the engine modal is z-index 9000 vs the overlay's
    // 10000, so leaving ours open would bury it.
    close();
    try {
      void window.openDownloadMissingModalForYouTube?.(vpid, name, tracks);
    } catch (error) {
      window.showToast?.(
        `Error starting downloads: ${error instanceof Error ? error.message : 'unknown error'}`,
        'error',
      );
    }
  }, [config, vertical, state, openId, close]);

  const onUnmatchTrack = useCallback(
    async (row: DiscoveryRow) => {
      if (openId === null) return;
      try {
        const data = await postUnmatch(config, openId, row.index);
        if (data.success) {
          vertical.patchState(openId, (s) => applyUnmatched(s, config, row.index));
          window.showToast?.('Match removed', 'success');
        } else {
          window.showToast?.(data.error || 'Failed to remove match', 'error');
        }
      } catch {
        window.showToast?.('Failed to remove match', 'error');
      }
    },
    [config, vertical, openId],
  );

  const onFixed = useCallback(
    (trackIndex: number, track: FixTrack) => {
      if (openId === null) return;
      vertical.patchState(openId, (s) => applyFixedMatch(s, config, trackIndex, track));
    },
    [config, vertical, openId],
  );

  if (!state || openId === null) return null;

  return (
    <>
      <DiscoveryModal
        config={config}
        state={state}
        standalone={standalone}
        mirroredSource={mirroredSource}
        onClose={close}
        onStartDiscovery={() => {
          // The LB endpoint REQUIRES {playlist} (web_server 34966-34970) —
          // derive it from the config so a wiring omission cannot break the
          // start invisibly; the prop stays as an override.
          const body =
            discoveryStartBody?.(state) ??
            (config.discovery.startBody === 'playlist'
              ? { playlist: { name: state.playlist?.name, tracks: statePlaylistTracks(state) } }
              : undefined);
          void vertical.startDiscovery(openId, body);
        }}
        onStartSync={() => void vertical.startSync(openId)}
        onCancelSync={() => void vertical.cancelSync(openId)}
        onDownloadMissing={onDownloadMissing}
        onFixTrack={(row) => setFixRow(row)}
        onUnmatchTrack={(row) => void onUnmatchTrack(row)}
      />
      {fixRow && (
        <FixModal
          key={fixRow.index}
          config={config}
          sourceId={openId}
          row={fixRow}
          onClose={() => setFixRow(null)}
          onFixed={onFixed}
        />
      )}
    </>
  );
}
