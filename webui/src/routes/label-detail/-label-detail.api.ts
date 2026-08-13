import { apiClient, readJson } from '@/app/api-client';

import type { LabelCatalogResponse, LabelRelease } from './-label-detail.types';

import { releaseKey } from './-label-detail.helpers';

export const LABEL_DETAIL_QUERY_KEY = ['label-detail'] as const;

/** The vanilla's PAGE_SIZE. Pinned against the source in the helpers test. */
export const LABEL_PAGE_SIZE = 60;

/**
 * One page of a label's catalog.
 *
 * `name` rides along because the browse call that links here does not return
 * the label's display name — the endpoint uses it to label the response, and
 * falls back to the watchlist row when the label is followed. The vanilla sent
 * it only when non-empty, and so does this: an empty `name=` would overwrite a
 * good watchlist name with nothing.
 *
 * No timeout: this walks MusicBrainz release-groups server-side and a big label
 * is genuinely slow. The vanilla fetch had none, and ky's default 10s would
 * turn a working page into a spurious error.
 */
export function fetchLabelCatalogPage(
  labelId: string,
  labelName: string,
  page: number,
  signal?: AbortSignal,
): Promise<LabelCatalogResponse> {
  const searchParams: Record<string, string> = {
    page: String(page),
    page_size: String(LABEL_PAGE_SIZE),
  };
  if (labelName) searchParams.name = labelName;

  return readJson<LabelCatalogResponse>(
    apiClient.get(`labels/${encodeURIComponent(labelId)}/catalog`, {
      searchParams,
      timeout: false,
      signal,
    }),
  );
}

/**
 * Ownership for a batch of releases, as a set of keys.
 *
 * The endpoint answers POSITIONALLY — `albums[i]` corresponds to the i-th album
 * sent — so the request order is the contract. Returning a Set of keys rather
 * than the raw array is what stops a caller re-deriving that correspondence
 * later against a differently-ordered list.
 *
 * Ownership is a nicety: the vanilla swallowed every failure here and left the
 * cards unchecked rather than showing them all as missing, and so does this.
 */
export async function fetchOwnedKeys(
  releases: LabelRelease[],
  signal?: AbortSignal,
): Promise<Set<string>> {
  const owned = new Set<string>();
  if (!releases.length) return owned;

  try {
    const response = await readJson<{ albums?: unknown[] }>(
      apiClient.post('enhanced-search/library-check', {
        json: {
          albums: releases.map((r) => ({ name: r.album, artist: r.artist })),
          tracks: [],
        },
        signal,
      }),
    );
    const flags = response?.albums ?? [];
    releases.forEach((release, index) => {
      if (flags[index]) owned.add(releaseKey(release));
    });
  } catch {
    // Left unchecked on purpose — see above.
  }
  return owned;
}

/** Follow / unfollow. Returns whether the server accepted it. */
export async function setLabelWatched(
  labelId: string,
  labelName: string,
  watching: boolean,
): Promise<boolean> {
  const path = watching ? 'labels/watchlist/add' : 'labels/watchlist/remove';
  // The remove call takes only the id; add carries the name so the watchlist
  // row has something to display before its first scan.
  const json = watching
    ? { musicbrainz_label_id: labelId, label_name: labelName }
    : { musicbrainz_label_id: labelId };
  try {
    const data = await readJson<{ success?: boolean }>(apiClient.post(path, { json }));
    return Boolean(data?.success);
  } catch {
    return false;
  }
}

/** Backlog on/off for a followed label. Returns whether the server accepted it. */
export async function setLabelBacklog(labelId: string, backlog: boolean): Promise<boolean> {
  try {
    const data = await readJson<{ success?: boolean }>(
      apiClient.post('labels/watchlist/backlog', {
        json: { musicbrainz_label_id: labelId, backlog },
      }),
    );
    return Boolean(data?.success);
  } catch {
    return false;
  }
}
