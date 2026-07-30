import { apiClient, readJson } from '@/app/api-client';

import type { BasicResult, BasicSource, BasicSourcesResponse } from './-basic.types';

/**
 * The download sources the chip row offers.
 *
 * Best-effort by design (downloads.js:4280): the picker is a convenience, and
 * searching still works without it — an unreachable endpoint must leave the row
 * empty rather than block the page.
 */
export async function fetchBasicSources(): Promise<BasicSourcesResponse> {
  try {
    const data = await readJson<BasicSourcesResponse>(apiClient.get('search/sources'));
    return { mode: data?.mode ?? '', sources: data?.sources ?? [] };
  } catch {
    return { mode: '', sources: [] };
  }
}

/**
 * True when the chip row is a label rather than a picker.
 *
 * One source, or a non-hybrid mode, means there is nothing to choose between:
 * the vanilla still rendered the chip so the user could see WHICH source they
 * were searching, but disabled it.
 */
export function isSingleSourceMode(response: BasicSourcesResponse): boolean {
  return response.mode !== 'hybrid' || response.sources.length < 2;
}

/**
 * Run the file search.
 *
 * No timeout — a Soulseek search takes as long as the network takes, and the
 * vanilla's fetch had none. Cancellation is the caller's, through `signal`;
 * an aborted request rejects with an AbortError the controller distinguishes
 * from a real failure.
 *
 * `source` is omitted when there is none to send: the server then falls
 * through to the orchestrator's own selection (single-source mode, or the
 * first source in the hybrid chain).
 */
export async function performBasicSearch(
  query: string,
  source: string | null,
  signal?: AbortSignal,
): Promise<BasicResult[]> {
  const json: { query: string; source?: string } = { query };
  if (source) json.source = source;

  const data = await readJson<{ results?: BasicResult[]; error?: string }>(
    apiClient.post('search', { json, timeout: false, signal }),
  );

  // The endpoint answers 200 with an `error` key for user-facing config
  // problems (a SoundCloud link with the source disabled, say). Surfacing it
  // as a thrown error is what puts the message in the status bar.
  if (data?.error) throw new Error(data.error);
  return data?.results ?? [];
}

export interface DownloadResponse {
  success?: boolean;
  message?: string;
  error?: string;
}

/**
 * Queue a download.
 *
 * The WHOLE result object is the body — the server reads `result_type` and
 * then picks fields off it (`username`, `filename`, `size`, and for an album
 * each entry of `tracks`). It uses `.get()` throughout rather than
 * reconstructing a dataclass, so passing the result through untouched is both
 * the contract and safe against new fields.
 */
export function postDownload(
  payload: BasicResult | (Record<string, unknown> & { result_type: string }),
): Promise<DownloadResponse> {
  return readJson<DownloadResponse>(apiClient.post('download', { json: payload }));
}

/** The chip label for a source, falling back to its raw name. */
export function sourceLabel(source: BasicSource): string {
  return source.display_name || source.name;
}
