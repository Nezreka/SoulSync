/**
 * Importing a playlist into SoulSync.
 *
 * The source tabs used to be a second home for every playlist: each card ran
 * its own identify, sync and download with its own live status, while the
 * same playlist sat on the Mirrored tab doing all of that too. Now a source
 * tab only finds playlists. Importing saves it as a mirror (which starts
 * identifying it in the background) and lands you on it under Mirrored, the
 * one place a playlist is managed.
 *
 * A mirror is keyed by its source and that source's playlist id, the same key
 * the backend dedupes on, so importing twice opens the first one.
 */

import type { SourceSkipped } from './-sync.state';

import { postMirrorPlaylist } from './-sync.api';
import { buildMirrorPayload, type MirrorSourceTrack } from './-sync.import';

export function mirrorKey(source: string, sourceId: string | number): string {
  return `${source}:${String(sourceId)}`;
}

/** source:id → mirror id, for everything this profile has imported. */
export type MirrorIndex = ReadonlyMap<string, number>;

export async function fetchMirrorIndex(): Promise<Map<string, number>> {
  const response = await fetch('/api/mirrored-playlists');
  const rows = (await response.json()) as unknown;
  const index = new Map<string, number>();
  if (!Array.isArray(rows)) return index;
  for (const row of rows as { id?: number; source?: string; source_playlist_id?: string }[]) {
    if (typeof row.id !== 'number' || !row.source || row.source_playlist_id == null) continue;
    index.set(mirrorKey(row.source, row.source_playlist_id), row.id);
  }
  return index;
}

/** Everything an import needs; the tracks may still have to be fetched. */
export interface ImportSpec {
  source: string;
  sourceId: string | number;
  name: string;
  owner?: string;
  imageUrl?: string;
  /** Kept on the mirror as its description; the link it came from, usually. */
  description?: string;
  /** The tracks, fetched only when they're needed (an import, not a browse). */
  loadTracks?: () => Promise<{ tracks: unknown[]; sourceSkipped?: SourceSkipped | null }>;
  /** The source's track shape into the mirror's (each tab already has one). */
  toMirrorTracks?: (tracks: unknown[]) => MirrorSourceTrack[] | Record<string, unknown>[];
  /**
   * A source that saves its own mirror server-side (Beatport, which keys it by
   * the list's canonical ref) does it here and answers the mirror's id; the
   * tracks above are then not used.
   */
  save?: () => Promise<number>;
}

/** Save the playlist as a mirror. Resolves to the mirror's id. */
export async function importPlaylist(spec: ImportSpec): Promise<number> {
  if (spec.save) return spec.save();
  if (!spec.loadTracks || !spec.toMirrorTracks) {
    throw new Error(`Couldn't import ${spec.name}`);
  }
  const { tracks, sourceSkipped } = await spec.loadTracks();
  if (!tracks.length) throw new Error(`${spec.name} has no tracks to import`);
  const res = await postMirrorPlaylist(
    buildMirrorPayload(
      spec.source,
      spec.sourceId,
      spec.name,
      spec.toMirrorTracks(tracks) as MirrorSourceTrack[],
      {
        owner: spec.owner,
        image_url: spec.imageUrl,
        description: spec.description,
        ...(sourceSkipped !== undefined ? { source_skipped: sourceSkipped } : {}),
      },
    ),
  );
  if (!res.success || typeof res.playlist_id !== 'number') {
    throw new Error(res.error || `Couldn't import ${spec.name}`);
  }
  return res.playlist_id;
}
