import { useCallback, useRef, useState } from 'react';

import type { Station, StationSnapshot, StationTrack } from './-discover.stations';

import { fetchStationSnapshot, stationSelectionOf } from './-discover.stations';

/**
 * The station preview controller: open, snapshot, select, refresh, close.
 *
 * Two properties are load-bearing:
 *
 *   - **Nothing here touches playback.** Opening a preview asks the backend for
 *     a list. It does not start audio, pause audio, or modify the queue, so a
 *     user can look at a station while something else is playing.
 *
 *   - **The open selection cannot move.** A snapshot is stored server-side and
 *     comes back identical until Refresh asks for a new revision, and a
 *     response for a station the user has already navigated away from is
 *     dropped rather than swapped in under an open dialog.
 */

export interface StationController {
  /** The station whose preview is open, or null. */
  station: Station | null;
  snapshot: StationSnapshot | null;
  loading: boolean;
  error: string | null;
  selected: number[];
  /** The card whose preview is still resolving — for that card's spinner. */
  pendingId: string | null;
  /** Per-card failures, keyed by artist id, shown next to the control. */
  cardErrors: Record<string, string>;
  open: (station: Station) => void;
  close: () => void;
  refresh: () => void;
  toggleTrack: (index: number) => void;
  selectAll: (indices: number[]) => void;
  clearSelection: () => void;
  selection: () => StationTrack[];
  /** Drop everything on a profile change — the old profile's preview is not
   *  this profile's, and a late response must never fill it in. */
  reset: () => void;
}

export function useStationPreview(): StationController {
  const [station, setStation] = useState<Station | null>(null);
  const [snapshot, setSnapshot] = useState<StationSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
  // A stale request resolving after close, a second station, or a profile
  // switch must not reopen or repopulate anything.
  const generation = useRef(0);

  const load = useCallback((target: Station, refresh: boolean) => {
    generation.current += 1;
    const gen = generation.current;
    const id = String(target.artist_id);
    setStation(target);
    setLoading(true);
    setError(null);
    setPendingId(id);
    setCardErrors((prev) => {
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    if (!refresh) {
      setSnapshot(null);
      setSelected([]);
    }
    void fetchStationSnapshot(target.artist_id, refresh)
      .then((snap) => {
        if (generation.current !== gen) return;
        setSnapshot(snap);
        // A refresh is a NEW revision, so the old selection no longer refers
        // to the same rows. Starting empty is the honest reset.
        setSelected([]);
        setLoading(false);
        setPendingId(null);
      })
      .catch((err: unknown) => {
        if (generation.current !== gen) return;
        const message = err instanceof Error ? err.message : 'Could not open that station';
        setLoading(false);
        setPendingId(null);
        setError(message);
        setCardErrors((prev) => ({ ...prev, [id]: message }));
      });
  }, []);

  const open = useCallback(
    (target: Station) => {
      // A second click on the SAME station while it resolves is a no-op; a
      // different station replaces the request rather than racing it.
      if (pendingId === String(target.artist_id)) return;
      load(target, false);
    },
    [load, pendingId],
  );

  const refresh = useCallback(() => {
    if (station) load(station, true);
  }, [load, station]);

  const close = useCallback(() => {
    generation.current += 1;
    setStation(null);
    setSnapshot(null);
    setSelected([]);
    setError(null);
    setLoading(false);
    setPendingId(null);
  }, []);

  const reset = useCallback(() => {
    generation.current += 1;
    setStation(null);
    setSnapshot(null);
    setSelected([]);
    setError(null);
    setLoading(false);
    setPendingId(null);
    setCardErrors({});
  }, []);

  const toggleTrack = useCallback((index: number) => {
    setSelected((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    );
  }, []);

  const selection = useCallback(() => stationSelectionOf(snapshot, selected), [snapshot, selected]);

  return {
    station,
    snapshot,
    loading,
    error,
    selected,
    pendingId,
    cardErrors,
    open,
    close,
    refresh,
    toggleTrack,
    selectAll: useCallback((indices: number[]) => setSelected(indices), []),
    clearSelection: useCallback(() => setSelected([]), []),
    selection,
    reset,
  };
}
