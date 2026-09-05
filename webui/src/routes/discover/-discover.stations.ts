/**
 * Recommended Stations — the card contract and the finite preview.
 *
 * The row used to offer exactly one thing: start endless radio. There was
 * nothing to inspect, download or sync, because the queue only existed inside
 * the player and kept refilling itself — so a "station" could not be handed to
 * any of the acquisition machinery the rest of the page uses.
 *
 * A station now has two separate actions, and they are not the same operation:
 *
 *   - **Play radio** keeps the existing non-stop behaviour, unchanged.
 *   - **View station** asks the backend for a finite snapshot (up to forty
 *     library tracks), which is stored server-side and comes back identical
 *     until something explicitly refreshes it. That snapshot is what Download
 *     and Sync act on, so a checkbox cannot move under an open dialog while
 *     radio refills in the background.
 *
 * Pure module: fetch wrappers and shaping only, so the identity rules are
 * testable without a DOM.
 */

export interface Station {
  artist_id: string | number;
  name: string;
  image_url: string;
  /** Companions the library can actually play. */
  with: string[];
  /** Named by the similarity graph, but NOT guaranteed by any playback path. */
  related?: string[];
  playable_tracks?: number;
}

export interface StationTrack {
  id?: string;
  library_track_id?: number | string;
  track_id?: string;
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  album_cover_url?: string | null;
  duration_ms?: number;
  has_file_path?: boolean;
  available?: boolean;
  owned?: boolean;
  source?: string;
}

export interface StationSnapshot {
  schema?: number;
  snapshot_id: string;
  revision: number;
  profile_id?: number;
  station: { artist_id: string | number; name: string; image_url: string };
  generated_at?: string;
  requested?: number;
  tracks: StationTrack[];
  counts?: { returned: number; available: number; unavailable: number };
  actions?: string[];
  status?: string;
  reason?: string | null;
  message?: string | null;
}

export const STATIONS_URL = '/api/discover/stations';

/**
 * The row's fetch.
 *
 * THROWS on failure rather than returning an empty list. The first version
 * collapsed every error to `[]`, which rendered exactly like "you have no
 * stations" — the caller could not tell a down backend from an empty library.
 */
export async function fetchStations(): Promise<Station[]> {
  const response = await fetch(STATIONS_URL);
  if (!response.ok) throw new Error(`stations request failed (${response.status})`);
  const data = (await response.json()) as {
    success?: boolean;
    stations?: Station[];
    error?: string;
  };
  if (!data?.success) throw new Error(data?.error || 'stations request failed');
  return Array.isArray(data.stations) ? data.stations : [];
}

export function stationSnapshotUrl(artistId: string | number, refresh = false): string {
  const base = `/api/discover/stations/${encodeURIComponent(String(artistId))}/snapshot`;
  return refresh ? `${base}?refresh=1` : base;
}

export async function fetchStationSnapshot(
  artistId: string | number,
  refresh = false,
): Promise<StationSnapshot> {
  const response = await fetch(stationSnapshotUrl(artistId, refresh), { method: 'POST' });
  const data = (await response.json()) as {
    success?: boolean;
    snapshot?: StationSnapshot;
    error?: string;
  };
  if (!response.ok || !data?.success || !data.snapshot) {
    throw new Error(data?.error || `station preview failed (${response.status})`);
  }
  return data.snapshot;
}

/**
 * The subtitle.
 *
 * "With X and Y" is a claim about what you will hear, so it is only made for
 * artists the library can actually play. Everything else is offered under a
 * weaker label, and a station with neither says only what it truthfully is.
 */
export function stationSubtitle(station: Station): string {
  if (station.with?.length) {
    return `With ${station.with.join(', ')} and more`;
  }
  if (station.related?.length) {
    return `Related artists: ${station.related.join(', ')}`;
  }
  return 'Artist radio from your library';
}

/**
 * The operation identity for a station's download and sync.
 *
 * Profile scoping comes from the server session; the station and the snapshot
 * REVISION are here, so a refreshed preview is a different operation and a
 * retry of the same one is idempotent. It deliberately shares no key space
 * with the daily mixes.
 */
export function stationSyncKey(snapshot: StationSnapshot): string {
  return `station_${snapshot.station.artist_id}_r${snapshot.revision}`;
}

export function stationVirtualId(snapshot: StationSnapshot): string {
  return `discover_${stationSyncKey(snapshot)}`;
}

export function stationTitle(snapshot: StationSnapshot): string {
  return `${snapshot.station.name} Station`;
}

/** "40 tracks from your library" — the finite scope, said out loud. */
export function stationScopeCopy(snapshot: StationSnapshot): string {
  const count = snapshot.tracks?.length ?? 0;
  return `${count} track${count === 1 ? '' : 's'} from your library`;
}

export function stationSyncCopy(snapshot: StationSnapshot): string {
  const count = snapshot.tracks?.length ?? 0;
  return `Sync these ${count}`;
}

/**
 * What a Download of this selection can honestly promise.
 *
 * Station tracks are library rows, so "everything selected is already here" is
 * a legitimate and common answer. Queueing redundant downloads to make the
 * button look busy would be worse than saying nothing to do.
 */
export function stationAcquisitionNote(tracks: StationTrack[]): string {
  if (!tracks.length) return '';
  const missing = tracks.filter((t) => !t.available);
  if (missing.length === 0) return 'Everything selected is already in your library.';
  return `${missing.length} of ${tracks.length} selected are missing from disk.`;
}

export function stationSelectionOf(
  snapshot: StationSnapshot | null,
  selected: number[],
): StationTrack[] {
  const all = snapshot?.tracks ?? [];
  return selected.map((i) => all[i]).filter((t): t is StationTrack => Boolean(t));
}

export const STATION_NO_BRIDGE =
  'Playback is not available on this page yet — reload and try again.';
export const STATION_NOTHING_SELECTED = 'Select at least one track first';
