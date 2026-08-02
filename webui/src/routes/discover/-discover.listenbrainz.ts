/**
 * ListenBrainz playlists — tabs, sub-tab grouping, and the playlist cards.
 *
 * Transcribed from `initializeListenBrainzTabs` (3397), `switchListenBrainzTab`
 * (3519), `groupListenBrainzPlaylists` (3537),
 * `buildListenBrainzPlaylistsHtml` (3577), `loadTracksForPlaylists` (3633),
 * `switchListenBrainzSubTab` (3642), `loadListenBrainzTabContent` (3674) and
 * `displayListenBrainzTracks` (3816) — read end to end.
 *
 * The cache contract these share with the Sync tab lives in
 * `-discover.listenbrainz-cache.ts`.
 */

import { cleanArtistName } from './-discover.helpers';
import { MIX_ACTION_DOWNLOAD, MIX_ACTION_SYNC, type DiscoverMix } from './-discover.mixes';

export const LB_CREATED_FOR_URL = '/api/discover/listenbrainz/created-for';
export const LB_USER_PLAYLISTS_URL = '/api/discover/listenbrainz/user-playlists';
export const LB_COLLABORATIVE_URL = '/api/discover/listenbrainz/collaborative';

export type LbTabId = 'recommendations' | 'user' | 'collaborative';

/** The three tabs, in order, with their labels verbatim (3415-3417). */
export const LB_TABS: { id: LbTabId; label: string; url: string }[] = [
  { id: 'recommendations', label: '🎁 Recommendations', url: LB_CREATED_FOR_URL },
  { id: 'user', label: '📚 Your Playlists', url: LB_USER_PLAYLISTS_URL },
  { id: 'collaborative', label: '🤝 Collaborative', url: LB_COLLABORATIVE_URL },
];

export const LB_DEFAULT_TAB: LbTabId = 'recommendations';

export const LB_EMPTY_CATEGORY = 'No playlists in this category';
export const LB_LOAD_FAILED = 'Failed to load playlists';
export const LB_CONNECT_TITLE = 'Connect ListenBrainz';

/** `Playlists for ${username}` / the anonymous fallback (3497). */
export function lbSubtitle(username: string | null | undefined): string {
  return username ? `Playlists for ${username}` : 'Playlists from ListenBrainz';
}

/**
 * A tab counts as having data only with a non-empty playlist array (3428).
 *
 * The username is read even from a tab with NO playlists (3427) — it is the
 * first source that supplies one that wins, so a user with only collaborative
 * playlists still gets their name in the subtitle.
 */
export function lbTabHasData(
  data: { success?: boolean; playlists?: unknown[] } | null | undefined,
): boolean {
  return Boolean(data?.success && Array.isArray(data.playlists) && data.playlists.length > 0);
}

/** `if (data.username && !lbUsername)` — FIRST non-empty wins (3438, 3449). */
export function lbPickUsername(
  current: string | null,
  incoming: string | undefined,
): string | null {
  return current || incoming || null;
}

/** No tab has data → the whole strip becomes a connect prompt (3477). */
export function lbShowsConnectPrompt(tabsWithData: number): boolean {
  return tabsWithData === 0;
}

/** The first tab WITH DATA becomes active, not simply the first tab (3501). */
export function lbFirstActiveTab(hasData: Record<string, boolean>): LbTabId | null {
  return LB_TABS.find((t) => hasData[t.id])?.id ?? null;
}

// ── Sub-tab grouping (3537) ────────────────────────────────────────────────

/**
 * ListenBrainz names its generated playlists predictably, so the
 * recommendations tab is split by matching the TITLE, lowercased.
 *
 * Order of these checks matters only in that they are mutually exclusive in
 * practice; the group ORDER is first-seen, except that "Other" is always moved
 * to the end (3568) so the recognised groups lead.
 */
export const LB_GROUP_MATCHERS: { needle: string; group: string }[] = [
  { needle: 'weekly jams', group: 'Weekly Jams' },
  { needle: 'weekly exploration', group: 'Weekly Exploration' },
  { needle: 'top discoveries', group: 'Top Discoveries' },
  { needle: 'top missed recordings', group: 'Top Missed Recordings' },
  { needle: 'daily jams', group: 'Daily Jams' },
];

export const LB_OTHER_GROUP = 'Other';

/** LB wraps each entry as `{ playlist: {...} }`; some paths hand it bare (3542). */
export function lbPlaylistData(playlist: Record<string, unknown>): Record<string, unknown> {
  return (playlist.playlist as Record<string, unknown>) || playlist;
}

export function lbGroupFor(playlist: Record<string, unknown>): string {
  // The cast is a no-op at runtime and keeps String(...) over exactly what the
  // vanilla stringifies (3543); it only tells the linter the field is a string.
  const title = String((lbPlaylistData(playlist).title || '') as string).toLowerCase();
  return LB_GROUP_MATCHERS.find((m) => title.includes(m.needle))?.group ?? LB_OTHER_GROUP;
}

export interface LbGrouping {
  groups: Record<string, Record<string, unknown>[]>;
  groupOrder: string[];
}

export function groupLbPlaylists(playlists: Record<string, unknown>[]): LbGrouping {
  const groups: Record<string, Record<string, unknown>[]> = {};
  const groupOrder: string[] = [];
  for (const playlist of playlists) {
    const name = lbGroupFor(playlist);
    if (!groups[name]) {
      groups[name] = [];
      groupOrder.push(name);
    }
    groups[name].push(playlist);
  }
  const otherIdx = groupOrder.indexOf(LB_OTHER_GROUP);
  if (otherIdx !== -1 && otherIdx !== groupOrder.length - 1) {
    groupOrder.splice(otherIdx, 1);
    groupOrder.push(LB_OTHER_GROUP);
  }
  return { groups, groupOrder };
}

/**
 * Sub-tabs appear only for the recommendations tab, with MORE THAN ONE playlist
 * AND more than one group (3684, 3689).
 *
 * A single group renders flat — a sub-tab bar with one tab is pure chrome.
 */
export function lbUsesSubTabs(tabId: string, playlistCount: number, groupCount: number): boolean {
  return tabId === 'recommendations' && playlistCount > 1 && groupCount > 1;
}

/**
 * The active sub-tab survives a re-render if it still exists (3697).
 *
 * Otherwise it falls back to the first group — so switching away and back does
 * not silently reset the user's choice.
 */
export function lbActiveSubTab(current: string | null, groupOrder: string[]): string | null {
  if (current && groupOrder.includes(current)) return current;
  return groupOrder[0] ?? null;
}

// ── The playlist card (3577) ───────────────────────────────────────────────

/** The MBID is the LAST path segment of the identifier URL (3582). */
export function lbIdentifier(playlist: Record<string, unknown>): string {
  const raw = lbPlaylistData(playlist).identifier;
  return typeof raw === 'string' ? (raw.split('/').pop() ?? '') : '';
}

/**
 * `trackCount` defaults to 50 (3585) — a GUESS, not a count.
 *
 * The card shows it before any tracks are fetched. A real count replaces it
 * from `annotation.track_count` when positive, else from the embedded `track`
 * array when non-empty. Both guards are `> 0`, so a zero-length playlist keeps
 * the optimistic 50 rather than showing "0 tracks".
 */
export const LB_DEFAULT_TRACK_COUNT = 50;

export function lbTrackCount(playlist: Record<string, unknown>): number {
  const d = lbPlaylistData(playlist);
  const annotation = d.annotation as { track_count?: number } | undefined;
  if (annotation?.track_count && annotation.track_count > 0) return annotation.track_count;
  const track = d.track;
  if (Array.isArray(track) && track.length > 0) return track.length;
  return LB_DEFAULT_TRACK_COUNT;
}

/**
 * The status element base (3594).
 *
 * ListenBrainz uses `-sync-total` / `-sync-matched`, where the generic discover
 * sync uses `-sync-completed` / `-sync-pending`. Different spans, which is why
 * the card supplies its own `statusHtml` instead of reusing the shared block.
 */
export function lbStatusBase(identifier: string): string {
  return `discover-lb-playlist-${identifier}`;
}

export const lbSyncTotalId = (id: string) => `${lbStatusBase(id)}-sync-total`;
export const lbSyncMatchedId = (id: string) => `${lbStatusBase(id)}-sync-matched`;
export const lbSyncFailedId = (id: string) => `${lbStatusBase(id)}-sync-failed`;
export const lbSyncPercentageId = (id: string) => `${lbStatusBase(id)}-sync-percentage`;

/**
 * The mix key includes the TAB (3610).
 *
 * `lb-${tabId}-${identifier}` — the same playlist can legitimately appear under
 * two tabs, and a key without the tab would make the second registration
 * overwrite the first in the shared registry.
 */
export function lbMixKey(tabId: string, identifier: string): string {
  return `lb-${tabId}-${identifier}`;
}

export function lbPlaylistMix(playlist: Record<string, unknown>, tabId: string): DiscoverMix {
  const d = lbPlaylistData(playlist);
  const identifier = lbIdentifier(playlist);
  const title = (d.title as string) || 'Untitled Playlist';
  const creator = (d.creator as string) || 'ListenBrainz';
  return {
    key: lbMixKey(tabId, identifier),
    title,
    subtitle: `by ${creator}`,
    trackCount: lbTrackCount(playlist),
    statusBase: lbStatusBase(identifier),
    actions: [
      { label: MIX_ACTION_DOWNLOAD, closeFirst: true, onclick: `lb-download:${identifier}` },
      { label: MIX_ACTION_SYNC, primary: true, isSync: true, onclick: `lb-sync:${identifier}` },
    ],
  };
}

/**
 * Covers hydrate on the NEXT TICK (3628).
 *
 * The caller injects this HTML synchronously with `innerHTML`, so the cards do
 * not exist yet when the builder returns. A same-tick hydrate would find no
 * elements and silently leave every card on its placeholder mosaic.
 */
export const LB_HYDRATE_DEFERS_A_TICK = true;

// ── The track table (3816) ─────────────────────────────────────────────────

/** Inline SVG data URI, so a missing cover needs no network round trip (3836). */
export const LB_PLACEHOLDER_IMAGE =
  'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIHZpZXdCb3g9IjAgMCA0MCA0MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIGZpbGw9IiMyYTJhMmEiLz48cGF0aCBkPSJNMjQgMTJ2MTIuNUEzLjUgMy41IDAgMSAxIDIwLjUgMjFWMTZsLTUgMXY5YTMuNSAzLjUgMCAxIDEtMy41LTMuNVYxM2wxMi0zeiIgZmlsbD0iIzU1NSIvPjwvc3ZnPg==';

export interface LbTrackRow {
  position: number;
  name: string;
  artist: string;
  album: string;
  cover: string;
  duration: string;
}

/**
 * One track row (3839-3860).
 *
 * The artist goes through `cleanArtistName` — the only track renderer on the
 * page that does — because ListenBrainz credits often carry "feat." strings the
 * other sources have already stripped.
 */
export function lbTrackRows(tracks: Record<string, unknown>[]): LbTrackRow[] {
  return tracks.map((track, index) => {
    const ms = (track.duration_ms as number) || 0;
    const min = Math.floor(ms / 60000);
    const sec = Math.floor((ms % 60000) / 1000);
    return {
      position: index + 1,
      name: (track.track_name as string) || 'Unknown Track',
      artist: cleanArtistName(track.artist_name as string) || 'Unknown Artist',
      album: (track.album_name as string) || '',
      cover: (track.album_cover_url as string) || LB_PLACEHOLDER_IMAGE,
      duration: ms > 0 ? `${min}:${sec.toString().padStart(2, '0')}` : '',
    };
  });
}

/** `by ${creator} • ${n} track${n !== 1 ? 's' : ''}` (3832). */
export function lbTrackCountLabel(creator: string, count: number): string {
  return `by ${creator} • ${count} track${count !== 1 ? 's' : ''}`;
}

/**
 * The vanilla recovers the creator by REGEX-ing it back out of the rendered
 * text: `currentText.match(/by (.+?) •/)` (3830), defaulting to 'ListenBrainz'.
 *
 * That is a read-back from the DOM because the function is not given the
 * creator. The React port passes it as a prop instead, so this exists only to
 * document why the vanilla looks the way it does.
 */
export const LB_CREATOR_FALLBACK = 'ListenBrainz';
