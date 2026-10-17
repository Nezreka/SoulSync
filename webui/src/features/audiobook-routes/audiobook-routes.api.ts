import { apiClient, readJson } from '@/app/api-client';

/**
 * Artists whose music downloads are filed in the audiobook library
 * (core/audiobook_routing.py). The server owns the name matching; the page
 * only ever asks "which of these names are routed" and compares what comes
 * back, so a spelling the page would normalise differently can't drift.
 */

export const AUDIOBOOK_ROUTES_QUERY_KEY = ['audiobook-routes'] as const;

export type MoveItem = { src: string; dst: string; action: string; error?: string };
export type MoveResult = {
  applied: boolean;
  items: MoveItem[];
  moved: number;
  skipped: number;
  failed: number;
};

type Success = { success: boolean; error?: string };

function assertSuccess<T extends Success>(payload: T, fallback: string): T {
  if (!payload.success) throw new Error(payload.error || fallback);
  return payload;
}

/** The subset of `names` that is routed, as passed in. */
export async function fetchRoutedNames(names: string[]): Promise<string[]> {
  if (names.length === 0) return [];
  const payload = await readJson<Success & { routed: string[] }>(
    apiClient.post('audiobook-routes/check', { json: { names } }),
  );
  return assertSuccess(payload, 'Could not check audiobook routing.').routed;
}

/** False until the user has set an audiobook folder; the server refuses routes before that. */
export async function fetchFolderConfigured(): Promise<boolean> {
  const payload = await readJson<Success & { audiobook_folder_configured: boolean }>(
    apiClient.get('audiobook-routes'),
  );
  return assertSuccess(payload, 'Could not read the audiobook folder setting.')
    .audiobook_folder_configured;
}

export async function setAudiobookRoute(name: string, routed: boolean): Promise<void> {
  const payload = routed
    ? await readJson<Success>(apiClient.post('audiobook-routes', { json: { name } }))
    : await readJson<Success>(apiClient.delete(`audiobook-routes/${encodeURIComponent(name)}`));
  assertSuccess(payload, 'Could not change audiobook routing.');
}

/** Preview (default) or perform moving the artist's albums out of the music library. */
export async function moveExistingAlbums(name: string, apply: boolean): Promise<MoveResult> {
  const payload = await readJson<Success & MoveResult>(
    apiClient.post(`audiobook-routes/${encodeURIComponent(name)}/move`, { json: { apply } }),
  );
  return assertSuccess(payload, 'Could not move the albums.');
}

/** (Re)write metadata.opf (series, episode number) for albums already filed as audiobooks. */
export async function refreshAudiobookMetadata(
  name: string,
): Promise<{ written: number; unchanged: number }> {
  const payload = await readJson<Success & { written: number; unchanged: number }>(
    apiClient.post(`audiobook-routes/${encodeURIComponent(name)}/refresh`),
  );
  return assertSuccess(payload, 'Could not write the audiobook metadata.');
}

/** How many albums a move would actually relocate / leave alone. */
export function summariseMove(result: MoveResult): { toMove: number; clashes: number } {
  return {
    toMove: result.items.filter((item) => item.action === 'move').length,
    clashes: result.items.filter((item) => item.action === 'exists').length,
  };
}
