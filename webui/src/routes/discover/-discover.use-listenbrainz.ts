import { useCallback, useEffect, useRef, useState } from 'react';

import type { LbTabId } from './-discover.listenbrainz';
import type { DiscoverMix } from './-discover.mixes';

import { refreshListenBrainz } from './-discover.api';
import {
  groupLbPlaylists,
  LB_TABS,
  lbActiveSubTab,
  lbFirstActiveTab,
  lbPickUsername,
  lbPlaylistMix,
  lbShowsConnectPrompt,
  lbUsesSubTabs,
} from './-discover.listenbrainz';

/**
 * The ListenBrainz section's controller.
 *
 * Transcribed from `initializeListenBrainzTabs` (3397-3518),
 * `switchListenBrainzTab` + `loadListenBrainzTabContent` (3519-3760) and
 * `refreshListenBrainzPlaylists` (4193-4258), over the module.
 *
 * The init's failure semantics are SPLIT, and the split matters: a tab whose
 * endpoint answers non-OK is simply a tab WITHOUT DATA (3424 `if (res.ok)` —
 * skipped, never fatal), while a NETWORK failure rejects the Promise.all and
 * errors the whole section (3512). That is why this uses raw fetch: the api
 * module's readJson throws on non-OK, which would turn one dead tab into a
 * dead section.
 *
 * Username comes from the FIRST endpoint that offers one, in created-for →
 * user → collaborative order (3428-3448). Zero tabs with data → the connect
 * prompt. The recommendations tab groups into sub-tabs only when it has more
 * than one playlist AND more than one group (3685-3690), keeping the current
 * group across reloads when it still exists (3697-3699).
 */

export type LbToast = { message: string; level: 'success' | 'error' | 'info' };

export const LB_REFRESHED = 'ListenBrainz playlists refreshed!';
export const LB_UP_TO_DATE = 'All playlists are up to date';

/** The refresh summary line (4218-4231): "… Updated: 3 created_for, 1 user". */
export function lbRefreshMessage(
  summary: Record<string, { new?: number; updated?: number }>,
): string {
  const updates: string[] = [];
  for (const [type, stats] of Object.entries(summary)) {
    const total = (stats.new || 0) + (stats.updated || 0);
    if (total > 0) updates.push(`${total} ${type}`);
  }
  return updates.length > 0 ? `${LB_REFRESHED} Updated: ${updates.join(', ')}` : LB_UP_TO_DATE;
}

export interface ListenBrainzController {
  loaded: boolean;
  error: boolean;
  username: string | null;
  hasData: Record<LbTabId, boolean>;
  activeTab: LbTabId;
  /** The active tab's cards — the active GROUP's cards when sub-tabs apply. */
  mixes: DiscoverMix[];
  /** Sub-tab groups with counts, or null when the tab has no sub-tabs. */
  groups: { name: string; count: number }[] | null;
  activeGroup: string | null;
  showsConnect: boolean;
  refreshing: boolean;
  selectTab: (tab: LbTabId) => void;
  selectGroup: (group: string) => void;
  refresh: () => Promise<void>;
}

export function useListenBrainz(onToast: (toast: LbToast) => void): ListenBrainzController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, Record<string, unknown>[]>>({});
  const [activeTab, setActiveTab] = useState<LbTabId>('recommendations');
  const [activeGroupState, setActiveGroup] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const init = useCallback(async () => {
    setError(false);
    try {
      const responses = await Promise.all(LB_TABS.map((tab) => fetch(tab.url)));
      const nextCache: Record<string, Record<string, unknown>[]> = {};
      const bodies: (Record<string, unknown> | null)[] = [];
      for (const [i, res] of responses.entries()) {
        if (!res.ok) {
          // A dead tab is a tab WITHOUT DATA, never a dead section (3424).
          bodies.push(null);
          continue;
        }
        const data = (await res.json()) as {
          success?: boolean;
          username?: string;
          playlists?: Record<string, unknown>[];
        };
        bodies.push(data);
        if (data.success && data.playlists && data.playlists.length > 0) {
          nextCache[LB_TABS[i].id] = data.playlists;
        }
      }
      // FIRST provider wins, in created-for → user → collaborative order
      // (3428-3448) — lbPickUsername folds left.
      let name: string | null = null;
      for (const b of bodies) name = lbPickUsername(name, b?.username as string | undefined);
      setUsername(name);
      setCache(nextCache);
      const first = lbFirstActiveTab(
        Object.fromEntries(LB_TABS.map((t) => [t.id, Boolean(nextCache[t.id])])),
      );
      if (first) setActiveTab(first);
    } catch {
      // A NETWORK failure rejects the Promise.all → the whole section errors
      // (3512-3517).
      setError(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void init();
  }, [init]);

  const hasData = Object.fromEntries(LB_TABS.map((t) => [t.id, Boolean(cache[t.id])])) as Record<
    LbTabId,
    boolean
  >;

  const playlists = cache[activeTab] ?? [];
  const grouping = groupLbPlaylists(playlists);
  const usesSubTabs = lbUsesSubTabs(activeTab, playlists.length, grouping.groupOrder.length);
  const activeGroup = usesSubTabs ? lbActiveSubTab(activeGroupState, grouping.groupOrder) : null;
  const visible = usesSubTabs && activeGroup ? (grouping.groups[activeGroup] ?? []) : playlists;

  return {
    loaded,
    error,
    username,
    hasData,
    activeTab,
    mixes: visible.map((p) => lbPlaylistMix(p, activeTab)),
    groups: usesSubTabs
      ? grouping.groupOrder.map((name) => ({
          name,
          count: (grouping.groups[name] ?? []).length,
        }))
      : null,
    activeGroup,
    showsConnect:
      loaded && !error && lbShowsConnectPrompt(Object.values(hasData).filter(Boolean).length),
    refreshing,
    selectTab: (tab) => setActiveTab(tab),
    selectGroup: (group) => setActiveGroup(group),
    refresh: async () => {
      setRefreshing(true);
      try {
        const data = (await refreshListenBrainz()) as {
          success?: boolean;
          error?: string;
          summary?: Record<string, { new?: number; updated?: number }>;
        };
        if (!data.success) throw new Error(data.error || 'Unknown error');
        toastRef.current({ message: lbRefreshMessage(data.summary ?? {}), level: 'success' });
        await init();
      } catch (e) {
        toastRef.current({
          message: `Failed to refresh: ${(e as Error).message}`,
          level: 'error',
        });
      } finally {
        setRefreshing(false);
      }
    },
  };
}
