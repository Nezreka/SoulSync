/**
 * The SoulSync Discovery sync tab (sync-soulsync-discovery.js, whole file).
 * Different shape from the MB tabs: personalized tracks already carry
 * provider ids, so there is no discovery hop — click = Refresh & Mirror
 * (regenerate the kind, project into the mirror contract with inline
 * matched_data, POST the mirror, open the mirrored detail modal via the
 * stats-automations seam, window-reachable).
 *
 * Declared improvement over the vanilla: after a successful mirror the CARD
 * re-renders with the new count + 'Ready' (the vanilla updated its array but
 * never re-rendered the DOM, leaving the card stale until a tab reload).
 */

import { useCallback, useEffect, useState } from 'react';

import type { SsdRecord } from '../-sync.lb-tabs';

import { postMirrorPlaylist } from '../-sync.api';
import {
  buildSsdMirrorPayload,
  fetchSsdRecords,
  postSsdRefresh,
  soulsyncSyntheticId,
  ssdStaleness,
} from '../-sync.lb-tabs';
import { SourceCard } from './source-card';

declare global {
  interface Window {
    openMirroredPlaylistModal?: (playlistId: number) => Promise<void> | void;
  }
}

export function SoulsyncDiscoveryTab() {
  const [records, setRecords] = useState<SsdRecord[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading SoulSync Discovery playlists...');
  const [refreshing, setRefreshing] = useState(false);
  // Per-card busy + result lines — cards refresh independently (the vanilla
  // disabled each card's own DOM, 136-142).
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [resultLines, setResultLines] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading SoulSync Discovery playlists...');
    setRecords(null);
    setRefreshing(true);
    try {
      const { records: rows, error } = await fetchSsdRecords();
      if (error) {
        setPlaceholder(`❌ ${error}`);
        return;
      }
      setRecords(rows);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown error';
      setPlaceholder(`❌ Error: ${message}`);
      window.showToast?.(`Error loading SoulSync Discovery: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Refresh & Mirror (handleSoulsyncDiscoverySyncCardClick 131-281). */
  const refreshAndMirror = useCallback(async (record: SsdRecord) => {
    const kind = record.kind;
    const variant = record.variant || '';
    if (!kind) {
      window.showToast?.('Missing kind', 'error');
      return;
    }
    const syntheticId = soulsyncSyntheticId(kind, variant || undefined);
    setBusyIds((current) => new Set(current).add(syntheticId));
    // The vanilla unhides the (empty) progress element while refreshing (142).
    setResultLines((current) => ({ ...current, [syntheticId]: '' }));
    try {
      const data = await postSsdRefresh(kind, variant);
      if (!data.success) throw new Error(data.error || 'Generator failed');

      const generated = data.playlist ?? {};
      const tracks = data.tracks ?? [];
      const finalName =
        (generated as { name?: string }).name || record.name || `${kind} ${variant}`.trim();

      if (tracks.length === 0) {
        window.showToast?.(
          `'${finalName}' generated 0 tracks. Try widening the playlist's config in Discover.`,
          'warning',
        );
      }

      // POST inline (not fire-and-forget) — the returned row id opens the
      // detail modal afterward (204-234).
      const mirrorData = await postMirrorPlaylist(
        buildSsdMirrorPayload(kind, variant, finalName, tracks),
      );
      if (!mirrorData.success) throw new Error(mirrorData.error || 'Mirror creation failed');

      setRecords((current) =>
        (current ?? []).map((row) =>
          row.kind === kind && (row.variant || '') === variant
            ? {
                ...row,
                ...(generated as SsdRecord),
                track_count: tracks.length,
                is_stale: false,
                _never_generated: false,
              }
            : row,
        ),
      );
      // The vanilla writes the mirrored line into the progress element (236-238).
      setResultLines((current) => ({
        ...current,
        [syntheticId]: `♪ ${tracks.length} / ✓ ${tracks.length} / mirrored`,
      }));
      window.showToast?.(`Mirrored '${finalName}' with ${tracks.length} tracks`, 'success');

      if (mirrorData.playlist_id) {
        try {
          await window.openMirroredPlaylistModal?.(mirrorData.playlist_id);
        } catch {
          // Non-fatal — the mirror exists; the detail modal is a convenience.
        }
      }
    } catch (err) {
      window.showToast?.(
        `Refresh failed: ${err instanceof Error ? err.message : 'unknown error'}`,
        'error',
      );
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(syntheticId);
        return next;
      });
    }
  }, []);

  return (
    <div>
      <div className="playlist-header">
        <h3>SoulSync Discovery</h3>
        <button
          type="button"
          className="refresh-button"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container">
        {records === null || records.length === 0 ? (
          <div className="playlist-placeholder">
            {records === null
              ? placeholder
              : 'No SoulSync Discovery playlists yet. Open the Discover page and generate a few personalized playlists first.'}
          </div>
        ) : (
          records.map((record) => {
            const syntheticId = soulsyncSyntheticId(record.kind, record.variant || undefined);
            const staleness = ssdStaleness(record);
            const busy = busyIds.has(syntheticId);
            return (
              <SourceCard
                key={syntheticId}
                id={`soulsync-discovery-sync-card-${syntheticId}`}
                cardClassName="soulsync-discovery-playlist-card"
                icon="✨"
                name={record.name || `${record.kind} ${record.variant || ''}`.trim()}
                countText={`${record.track_count || 0} tracks`}
                owner={record.variant ? `${record.kind} · ${record.variant}` : record.kind}
                phase="fresh"
                statusText={staleness.text}
                statusColor={staleness.color}
                buttonText={busy ? 'Refreshing…' : 'Refresh & Mirror'}
                buttonDisabled={busy}
                progressLine={resultLines[syntheticId] ?? null}
                onClick={() => {
                  if (!busy) void refreshAndMirror(record);
                }}
              />
            );
          })
        )}
      </div>
    </div>
  );
}
