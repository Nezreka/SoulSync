/**
 * The ListenBrainz tab (sync-listenbrainz.js, whole file) and the import list
 * it shares with its Last.fm Radio sibling. Three JSPF categories behind
 * sub-tabs, each playlist ready to import into SoulSync.
 *
 * ListenBrainz and Last.fm playlists carry names (and recording MBIDs), not a
 * service's track ids, so importing saves those tracks as a mirror and the
 * mirror identifies them in the background, the way a file import does. A
 * rotating series (Weekly Jams, Weekly Exploration, a yearly round-up) files
 * under ONE series key, so next week's rolls onto the same mirror instead of
 * piling up; that's why a card's key has to be resolved before it can say
 * whether it's in.
 *
 * These used to run their own identify in an LB-only window and mirror only
 * the matched tracks when it finished. The import flow replaces that.
 */

import { useCallback, useEffect, useState } from 'react';

import type { ImportSpec } from '../-sync.importer';
import type { LbCardData, LbMirrorTarget, LbSubTabType, LbTrack } from '../-sync.lb-tabs';

import { detectLbSeries } from '../-sync.api';
import { mapWithConcurrency } from '../-sync.core';
import {
  LB_EMPTY_MESSAGES,
  LB_SUB_TABS,
  fetchLbCategories,
  fetchLbPlaylistTracks,
  postLbCacheRefresh,
  resolveLbMirrorTarget,
  unwrapJspfPlaylist,
} from '../-sync.lb-tabs';
import { ImportCard, ImportGrid, ImportIntro } from './import-card';
import { useImporter } from './use-importer';

/** an LB track as a mirror row: names to identify by, the recording mbid kept */
export function lbMirrorTrack(t: LbTrack): Record<string, unknown> {
  return {
    track_name: t.track_name,
    artist_name: t.artist_name,
    album_name: t.album_name,
    duration_ms: t.duration_ms,
    image_url: t.album_cover_url || null,
    source_track_id: t.mbid || '',
    extra_data: null,
  };
}

/** What importing one LB / Last.fm playlist takes. Exported for the tests. */
export function lbImportSpec(card: LbCardData, target: LbMirrorTarget): ImportSpec {
  return {
    source: target.source,
    sourceId: target.sourcePlaylistId,
    name: target.name,
    owner: target.owner,
    description: `${card.count} tracks from ${card.title}`,
    loadTracks: async () => ({ tracks: await fetchLbPlaylistTracks(card.mbid) }),
    toMirrorTracks: (tracks) => (tracks as LbTrack[]).map(lbMirrorTrack),
  };
}

/** where each card's mirror is filed, resolved once per playlist */
const targetCache = new Map<string, LbMirrorTarget>();

async function resolveTarget(card: LbCardData): Promise<LbMirrorTarget> {
  const cached = targetCache.get(card.mbid);
  if (cached) return cached;
  let series = null;
  try {
    series = await detectLbSeries(card.title);
  } catch {
    // no series answer files it under its own mbid, like the old mirror did
  }
  const target = resolveLbMirrorTarget(card.mbid, card.title, card.creator, series);
  targetCache.set(card.mbid, target);
  return target;
}

/** forget resolved keys (tests) */
export function forgetLbTargets(): void {
  targetCache.clear();
}

function useLbTargets(cards: LbCardData[]): ReadonlyMap<string, LbMirrorTarget> {
  const [targets, setTargets] = useState<ReadonlyMap<string, LbMirrorTarget>>(
    () =>
      new Map(
        cards.flatMap((c) => (targetCache.has(c.mbid) ? [[c.mbid, targetCache.get(c.mbid)!]] : [])),
      ),
  );
  const ids = cards.map((c) => c.mbid).join('|');
  useEffect(() => {
    let alive = true;
    void mapWithConcurrency(cards, 6, async (card) => {
      const target = await resolveTarget(card);
      if (alive) setTargets((prev) => new Map(prev).set(card.mbid, target));
    });
    return () => {
      alive = false;
    };
    // the card list is rebuilt every render; its ids are what matter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids]);
  return targets;
}

/** the import cards both LB-style tabs render */
export function LbImportList({
  cards,
  glyph,
  idPrefix,
}: {
  cards: LbCardData[];
  glyph: string;
  idPrefix: string;
}) {
  const importer = useImporter();
  const targets = useLbTargets(cards);
  const statusOf = (card: LbCardData) => {
    const target = targets.get(card.mbid);
    return target ? importer.statusOf(target.source, target.sourcePlaylistId) : 'checking';
  };
  // a series' weeks share one mirror, so they count once toward Import all
  const remaining = [
    ...new Map(
      cards
        .filter((card) => statusOf(card) === 'new')
        .map((card) => {
          const target = targets.get(card.mbid)!;
          return [
            `${target.source}:${target.sourcePlaylistId}`,
            lbImportSpec(card, target),
          ] as const;
        }),
    ).values(),
  ];

  return (
    <>
      <ImportIntro
        canCopy
        remaining={remaining.length}
        onImportAll={() => void importer.importAll(remaining)}
      />
      <ImportGrid>
        {cards.map((card) => {
          const target = targets.get(card.mbid);
          return (
            <ImportCard
              key={card.mbid}
              id={`${idPrefix}-${card.mbid}`}
              name={card.title}
              meta={`${card.count} track${card.count === 1 ? '' : 's'} · by ${card.creator}`}
              glyph={glyph}
              status={statusOf(card)}
              onAction={() => {
                if (target) void importer.importOrOpen(lbImportSpec(card, target));
              }}
              onCopy={target ? importer.copyAsMineFor(lbImportSpec(card, target)) : undefined}
              copying={target ? importer.copyingOf(target.source, target.sourcePlaylistId) : false}
            />
          );
        })}
      </ImportGrid>
    </>
  );
}

export function ListenBrainzSyncTab() {
  const [subTab, setSubTab] = useState<LbSubTabType>('created_for_user');
  const [byType, setByType] = useState<Record<LbSubTabType, unknown[]> | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading ListenBrainz playlists...');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading ListenBrainz playlists...');
    setByType(null);
    setRefreshing(true);
    try {
      const categories = await fetchLbCategories();
      if (categories.unauthenticated) {
        setPlaceholder(
          'ListenBrainz not connected. Add your token in Settings → Connections to see your playlists here.',
        );
        return;
      }
      setByType(categories.playlists);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      setPlaceholder(`❌ Error loading ListenBrainz playlists: ${message}`);
      window.showToast?.(`Error loading ListenBrainz playlists: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cards = (byType?.[subTab] ?? []).map((entry) =>
    unwrapJspfPlaylist(entry, { title: 'ListenBrainz Playlist', creator: 'ListenBrainz' }),
  );

  return (
    <div>
      {/* The sub-tabs live INSIDE the header row (index.html 3202-3210) —
          .listenbrainz-sub-tabs is inline-flex with margin-left:16px
          (style.css 14864), so as a sibling they'd drop to their own line. */}
      <div className="playlist-header">
        <h3>Your ListenBrainz Playlists</h3>
        <div className="listenbrainz-sub-tabs">
          {LB_SUB_TABS.map((tab) => (
            <button
              key={tab.type}
              type="button"
              className={`listenbrainz-sub-tab-btn ${subTab === tab.type ? 'active' : ''}`}
              data-lb-type={tab.type}
              onClick={() => setSubTab(tab.type)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="refresh-button listenbrainz"
          id="listenbrainz-sync-refresh-btn"
          disabled={refreshing}
          onClick={() => {
            // Backend refetch first (best-effort), then reload the caches (348-356).
            void postLbCacheRefresh().then(load);
          }}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container">
        {byType === null || cards.length === 0 ? (
          <div className="playlist-placeholder">
            {byType === null ? placeholder : LB_EMPTY_MESSAGES[subTab]}
          </div>
        ) : (
          <LbImportList cards={cards} glyph="🎧" idPrefix="listenbrainz-sync-card" />
        )}
      </div>
    </div>
  );
}
