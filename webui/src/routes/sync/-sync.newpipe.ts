/**
 * The NewPipe import. NewPipe keeps its playlists on the phone; its backup
 * (Settings > Backup and restore > Export) is a zip with newpipe.db inside.
 * The backend reads it (core/playlists/newpipe_backup.py), the page shows the
 * playlists, and each picked one is mirrored as source 'newpipe'.
 */

import type { MirrorPayload, MirrorSourceTrack } from './-sync.import';

import { buildMirrorPayload } from './-sync.import';

/** the export zip, or the bare db pulled out of it. */
export const NEWPIPE_EXTENSIONS: readonly string[] = ['zip', 'db'];

export function isNewPipeFile(fileName: string): boolean {
  const dot = fileName.lastIndexOf('.');
  return dot >= 0 && NEWPIPE_EXTENSIONS.includes(fileName.slice(dot + 1).toLowerCase());
}

export interface NewPipePlaylist {
  /** newpipe_<uid>: the mirror key, stable across backups. */
  id: string;
  name: string;
  image_url?: string;
  track_count: number;
  tracks: MirrorSourceTrack[];
}

export interface NewPipeParseResponse {
  success?: boolean;
  playlists?: NewPipePlaylist[];
  /** bookmarked youtube playlists: only a link in the backup, not imported. */
  bookmarked?: number;
  error?: string;
}

export async function parseNewPipeBackup(file: File): Promise<NewPipeParseResponse> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch('/api/import/newpipe', { method: 'POST', body });
  try {
    return (await response.json()) as NewPipeParseResponse;
  } catch {
    return { success: false, error: `the server answered ${response.status}` };
  }
}

/** same key every import, so a newer backup overwrites the mirror it made. */
export function newpipeMirrorPayload(playlist: NewPipePlaylist): MirrorPayload {
  return buildMirrorPayload('newpipe', playlist.id, playlist.name, playlist.tracks, {
    description: 'Imported from a NewPipe backup',
    owner: 'NewPipe',
    image_url: playlist.image_url || '',
  });
}

/** an empty playlist has nothing to mirror, and can't overwrite a full one. */
export function newpipeImportable(playlist: NewPipePlaylist): boolean {
  return playlist.track_count > 0;
}
