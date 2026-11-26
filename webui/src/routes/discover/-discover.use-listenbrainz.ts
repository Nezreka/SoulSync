import { useCallback, useEffect, useRef, useState } from 'react';

import type { LbTabId } from './-discover.listenbrainz';
import type { DiscoverMix } from './-discover.mixes';

import { refreshListenBrainz } from './-discover.api';
import {
  groupLbPlaylists,
  LB_DEFAULT_TAB,
  LB_TABS,
  lbActiveSubTab,
  lbFirstActiveTab,
  lbIdentifier,
  lbPickUsername,
  lbPlaylistData,
  lbPlaylistMix,
  lbUsesSubTabs,
} from './-discover.listenbrainz';

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
  loading: boolean;
  sourceErrors: Partial<Record<LbTabId, string>>;
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
  refreshError: string | null;
  selectTab: (tab: LbTabId) => void;
  selectGroup: (group: string) => void;
  retry: () => Promise<void>;
  refresh: () => Promise<void>;
}

type Playlist = Record<string, unknown>;

interface SourceData {
  playlists: Playlist[];
  username?: string;
}

type SourceOutcome =
  | { kind: 'confirmed'; data: SourceData }
  | { kind: 'disconnected' }
  | { kind: 'failed'; message: string };

interface ShelfState {
  activeTab: LbTabId | null;
  selectedGroup: string | null;
  confirmed: Partial<Record<LbTabId, SourceData>>;
  sourceErrors: Partial<Record<LbTabId, string>>;
  showsConnect: boolean;
  loaded: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isPlaylistAnnotation(value: unknown): boolean {
  // The cache preserves the source's optional JSPF annotation text.
  if (value == null || typeof value === 'string') return true;
  return isRecord(value) && (value.track_count === undefined || isCount(value.track_count));
}

function isPlaylist(value: unknown): value is Playlist {
  if (!isRecord(value) || ('playlist' in value && !isRecord(value.playlist))) return false;
  const data = lbPlaylistData(value);
  return (
    Boolean(lbIdentifier(value).trim()) &&
    (data.title == null || typeof data.title === 'string') &&
    (data.creator == null || typeof data.creator === 'string') &&
    isPlaylistAnnotation(data.annotation) &&
    (data.track === undefined || (Array.isArray(data.track) && data.track.every(isRecord)))
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}

async function readSource(url: string, signal: AbortSignal): Promise<SourceOutcome> {
  try {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Request failed (HTTP ${response.status})`);
    const body: unknown = await response.json();
    if (
      isRecord(body) &&
      body.success === false &&
      body.error === 'Not authenticated' &&
      body.username === null &&
      body.count === 0 &&
      Array.isArray(body.playlists) &&
      body.playlists.length === 0
    ) {
      return { kind: 'disconnected' };
    }
    if (isRecord(body) && body.success === false && typeof body.error === 'string') {
      return { kind: 'failed', message: body.error };
    }
    if (
      !isRecord(body) ||
      body.success !== true ||
      !Array.isArray(body.playlists) ||
      !body.playlists.every(isPlaylist) ||
      (body.username != null && typeof body.username !== 'string')
    ) {
      throw new Error('Invalid playlist response');
    }
    return {
      kind: 'confirmed',
      data: { playlists: body.playlists, username: body.username ?? undefined },
    };
  } catch (error) {
    return { kind: 'failed', message: errorMessage(error) };
  }
}

function hasPlaylists(confirmed: ShelfState['confirmed']): Record<LbTabId, boolean> {
  return {
    recommendations: Boolean(confirmed.recommendations?.playlists.length),
    user: Boolean(confirmed.user?.playlists.length),
    collaborative: Boolean(confirmed.collaborative?.playlists.length),
  };
}

function applyRead(
  previous: ShelfState,
  outcomes: { id: LbTabId; outcome: SourceOutcome }[],
): ShelfState {
  const confirmed = { ...previous.confirmed };
  const sourceErrors: ShelfState['sourceErrors'] = {};
  let disconnected = 0;
  for (const { id, outcome } of outcomes) {
    switch (outcome.kind) {
      case 'confirmed':
        confirmed[id] = outcome.data;
        break;
      case 'disconnected':
        delete confirmed[id];
        disconnected++;
        break;
      case 'failed':
        sourceErrors[id] = outcome.message;
        break;
    }
  }

  const { activeTab } = previous;
  const available = hasPlaylists(confirmed);
  const needsFallback = !activeTab || (!available[activeTab] && !(activeTab in sourceErrors));
  return {
    activeTab: needsFallback ? (lbFirstActiveTab(available) ?? activeTab) : activeTab,
    selectedGroup: lbActiveSubTab(
      previous.selectedGroup,
      groupLbPlaylists(confirmed.recommendations?.playlists ?? []).groupOrder,
    ),
    confirmed,
    sourceErrors,
    showsConnect: disconnected === LB_TABS.length,
    loaded: true,
  };
}

function isRefreshSummary(summary: unknown): summary is Parameters<typeof lbRefreshMessage>[0] {
  const sources = ['created_for', 'user', 'collaborative'];
  const counters = ['new', 'updated', 'skipped'];
  return (
    isRecord(summary) &&
    Object.keys(summary).length === sources.length &&
    sources.every((source) => {
      const counts = summary[source];
      return (
        isRecord(counts) &&
        Object.keys(counts).length === counters.length &&
        counters.every((counter) => isCount(counts[counter]))
      );
    })
  );
}

export function useListenBrainz(onToast: (toast: LbToast) => void): ListenBrainzController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;
  const mounted = useRef(false);
  const latestRead = useRef<AbortController | null>(null);
  const refreshOwner = useRef<object | null>(null);

  const [shelf, setShelf] = useState<ShelfState>({
    activeTab: null,
    selectedGroup: null,
    confirmed: {},
    sourceErrors: {},
    showsConnect: false,
    loaded: false,
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const readShelf = useCallback(async () => {
    if (!mounted.current) return false;
    latestRead.current?.abort();
    const controller = new AbortController();
    latestRead.current = controller;
    setLoading(true);

    const outcomes = await Promise.all(
      LB_TABS.map(async ({ id, url }) => ({
        id,
        outcome: await readSource(url, controller.signal),
      })),
    );
    if (latestRead.current !== controller) return false;
    latestRead.current = null;
    setShelf((previous) => applyRead(previous, outcomes));
    setLoading(false);
    return outcomes.every(({ outcome }) => outcome.kind === 'confirmed');
  }, []);

  useEffect(() => {
    mounted.current = true;
    void readShelf();
    return () => {
      mounted.current = false;
      latestRead.current?.abort();
      latestRead.current = null;
      refreshOwner.current = null;
    };
  }, [readShelf]);

  const { confirmed } = shelf;
  const activeTab = shelf.activeTab ?? LB_DEFAULT_TAB;
  const username = LB_TABS.reduce<string | null>(
    (name, { id }) => lbPickUsername(name, confirmed[id]?.username),
    null,
  );
  const playlists = confirmed[activeTab]?.playlists ?? [];
  const grouping = groupLbPlaylists(playlists);
  const usesSubTabs = lbUsesSubTabs(activeTab, playlists.length, grouping.groupOrder.length);
  const activeGroup = usesSubTabs ? lbActiveSubTab(shelf.selectedGroup, grouping.groupOrder) : null;
  const visible = usesSubTabs && activeGroup ? (grouping.groups[activeGroup] ?? []) : playlists;

  return {
    loaded: shelf.loaded,
    error: Object.keys(shelf.sourceErrors).length > 0,
    loading,
    sourceErrors: shelf.sourceErrors,
    username,
    hasData: hasPlaylists(confirmed),
    activeTab,
    mixes: visible.map((p) => lbPlaylistMix(p, activeTab)),
    groups: usesSubTabs
      ? grouping.groupOrder.map((name) => ({
          name,
          count: (grouping.groups[name] ?? []).length,
        }))
      : null,
    activeGroup,
    showsConnect: shelf.showsConnect,
    refreshing,
    refreshError,
    selectTab: (tab) => setShelf((previous) => ({ ...previous, activeTab: tab })),
    selectGroup: (group) => setShelf((previous) => ({ ...previous, selectedGroup: group })),
    retry: async () => {
      if (!refreshOwner.current) await readShelf();
    },
    refresh: async () => {
      if (!mounted.current || refreshOwner.current) return;
      const owner = {};
      refreshOwner.current = owner;
      setRefreshing(true);
      setRefreshError(null);
      try {
        const data = await refreshListenBrainz();
        if (refreshOwner.current !== owner) return;
        if (data?.success !== true || !isRefreshSummary(data.summary)) {
          throw new Error(
            typeof data?.error === 'string' ? data.error : 'Invalid refresh response',
          );
        }
        const message = lbRefreshMessage(data.summary);
        const reloaded = await readShelf();
        if (refreshOwner.current !== owner) return;
        if (reloaded) {
          toastRef.current({ message, level: 'success' });
        }
      } catch (error) {
        if (refreshOwner.current !== owner) return;
        const message = `Failed to refresh: ${errorMessage(error)}`;
        setRefreshError(message);
        toastRef.current({ message, level: 'error' });
      } finally {
        if (refreshOwner.current === owner) {
          refreshOwner.current = null;
          setRefreshing(false);
        }
      }
    },
  };
}
