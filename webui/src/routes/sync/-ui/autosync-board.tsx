/**
 * The Auto-Sync hourly schedule board — auto-sync.js 741-859. The chrome it
 * shares with the weekly board (sidebar, lanes, cards, icons) lives in
 * ./autosync-shared; this file is the hourly-specific wiring.
 *
 * Shape note: the vanilla re-renders the whole panel through innerHTML on
 * every filter keystroke and then re-focuses the input and restores the caret
 * (1080-1102). React keeps the input mounted, so that whole dance — and the
 * `_autoSyncSidebarFilter` module global it existed to survive — has no
 * counterpart here. The filter is state.
 */

import { useState } from 'react';

import {
  autoSyncBucketLabel,
  autoSyncBuildLanes,
  autoSyncCanSchedulePlaylist,
  autoSyncGroupBySource,
  autoSyncIntervalLabel,
  autoSyncLaneCadence,
  autoSyncMatchesFilter,
  autoSyncNextRunLabel,
  type AutoSyncHistoryRow,
  type AutoSyncHourlyEntry,
  type MirroredRow,
} from '../-sync.autosync';
import {
  AutoSyncBoardIntro,
  AutoSyncLane,
  AutoSyncScheduledCard,
  AutoSyncSidebar,
  type AutoSyncCardActions,
} from './autosync-shared';

export interface AutoSyncBoardActions extends AutoSyncCardActions {
  onDrop: (playlistId: number, hours: number) => void;
  onBulkMenu: (event: React.MouseEvent, source: string) => void;
  onRefresh: () => void;
}

export interface AutoSyncBoardProps {
  playlists: MirroredRow[];
  playlistSchedules: Record<string, AutoSyncHourlyEntry>;
  runHistory: AutoSyncHistoryRow[];
  /** Injected so the 'next in 3h' labels are assertable without faking time. */
  now: number;
  actions: AutoSyncBoardActions;
}

export function AutoSyncBoard({
  playlists,
  playlistSchedules,
  runHistory,
  now,
  actions,
}: AutoSyncBoardProps) {
  const [filter, setFilter] = useState('');
  const [expandedKinds, setExpandedKinds] = useState<Set<string>>(() => new Set());

  const matching = playlists.filter((p) => autoSyncMatchesFilter(p, filter));
  const schedulable = matching.filter((p) => autoSyncCanSchedulePlaylist(p));
  const unavailable = matching.filter((p) => !autoSyncCanSchedulePlaylist(p));
  const groups = autoSyncGroupBySource(schedulable);
  const lanes = autoSyncBuildLanes(schedulable, playlistSchedules);

  const toggleKind = (kind: string) => {
    setExpandedKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  /** 755-757. */
  const badgeFor = (p: MirroredRow) => {
    const schedule = playlistSchedules[String(p.id)];
    return {
      stateClass: schedule ? 'scheduled' : '',
      assigned: schedule ? autoSyncIntervalLabel(schedule.hours) : 'Unscheduled',
      active: !!schedule,
    };
  };

  return (
    <>
      <AutoSyncBoardIntro
        heading="Drag playlists into an interval"
        blurb="Each placement creates or updates an Auto-Sync-owned playlist-pipeline automation."
        onRefresh={actions.onRefresh}
      />
      <div className="auto-sync-body">
        <AutoSyncSidebar
          groups={groups}
          unavailable={unavailable}
          badgeFor={badgeFor}
          filter={filter}
          onFilterChange={setFilter}
          expandedKinds={expandedKinds}
          onToggleKind={toggleKind}
          onBulkMenu={actions.onBulkMenu}
        />
        <main className="auto-sync-lanes">
          {lanes.map((lane) => (
            <AutoSyncLane
              key={lane.hours}
              title={autoSyncBucketLabel(lane.hours)}
              subtitle={`${autoSyncLaneCadence(lane.hours)}${lane.isCustom ? ' · custom' : ''}`}
              count={lane.playlists.length || null}
              extraClass={lane.isCustom ? 'custom' : ''}
              dataAttrs={{ 'data-hours': lane.hours }}
              hint={`Drag a playlist here to sync ${autoSyncIntervalLabel(lane.hours).toLowerCase()}`}
              onDropPlaylist={(playlistId) => {
                actions.onDrop(playlistId, lane.hours);
              }}
            >
              {lane.playlists.map((p) => {
                const schedule = playlistSchedules[String(p.id)];
                const nextLabel = schedule?.next_run
                  ? autoSyncNextRunLabel(schedule.next_run, now)
                  : '';
                return (
                  <AutoSyncScheduledCard
                    key={String(p.id)}
                    playlist={p}
                    enabled={schedule?.enabled !== false}
                    history={runHistory}
                    actions={actions}
                    unscheduleTitle="Remove this Auto-Sync schedule"
                    timing={
                      <>
                        <span>{autoSyncIntervalLabel(schedule?.hours || 24)}</span>
                        {nextLabel ? <small>{nextLabel}</small> : null}
                      </>
                    }
                  />
                );
              })}
            </AutoSyncLane>
          ))}
        </main>
      </div>
    </>
  );
}
