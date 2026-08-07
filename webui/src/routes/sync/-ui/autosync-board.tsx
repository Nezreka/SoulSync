/**
 * The Auto-Sync hourly schedule board — auto-sync.js 741-859, plus the shared
 * pieces it pulls in: the source icon (197-203), the sidebar kind-groups
 * (436-457), the scheduled card (1951-1976), the organize toggle (1920-1931)
 * and the five drag handlers (2013-2049).
 *
 * Shape note: the vanilla re-renders the whole panel through innerHTML on
 * every filter keystroke and then re-focuses the input and restores the caret
 * (1080-1102). React keeps the input mounted, so that whole dance — and the
 * `_autoSyncSidebarFilter` module global it existed to survive — has no
 * counterpart here. The filter is state.
 *
 * Drag-over highlighting is likewise state rather than a classList poke. The
 * vanilla's dragleave guard (`col.contains(event.relatedTarget)`, 2030-2035)
 * is kept verbatim, because without it moving the cursor over a card INSIDE a
 * lane fires dragleave on the lane and the highlight flickers off.
 */

import { useState } from 'react';

import {
  autoSyncBucketLabel,
  autoSyncBuildLanes,
  autoSyncCanSchedulePlaylist,
  autoSyncGroupBySource,
  autoSyncGroupSidebarRows,
  autoSyncIntervalLabel,
  autoSyncLaneCadence,
  autoSyncMatchesFilter,
  autoSyncNextRunLabel,
  autoSyncPlaylistHealth,
  autoSyncSourceLabel,
  type AutoSyncHistoryRow,
  type AutoSyncHourlyEntry,
  type MirroredRow,
} from '../-sync.autosync';

/** 184-195. */
const SOURCE_LOGOS: Record<string, string> = {
  spotify: '/static/img/brands/spotify.png',
  spotify_public: '/static/img/brands/spotify.png',
  tidal: '/static/img/brands/tidal.svg',
  youtube: '/static/img/brands/youtube.svg',
  deezer: '/static/img/brands/deezer.png',
  qobuz: '/static/img/brands/qobuz.svg',
  itunes_link: '/static/img/brands/itunes.png',
  lastfm: '/static/img/brands/lastfm.png',
  listenbrainz: '/static/img/brands/listenbrainz.png',
  soulsync_discovery: '/static/favicon.png',
};

/** 197-203. Absent source → nothing at all, not a broken image. */
export function AutoSyncSourceIcon({ source }: { source: string }) {
  const src = SOURCE_LOGOS[source];
  if (!src) return null;
  return (
    <img
      className="auto-sync-source-icon"
      data-svc={source}
      src={src}
      alt=""
      aria-hidden="true"
      onError={(e) => {
        e.currentTarget.style.display = 'none';
      }}
    />
  );
}

export interface AutoSyncBoardActions {
  onDrop: (playlistId: number, hours: number) => void;
  onRun: (playlistId: number) => void;
  onUnschedule: (playlistId: number) => void;
  onOrganizeChange: (playlistId: number, enabled: boolean) => void;
  onBulkMenu: (event: React.MouseEvent, source: string) => void;
  onRefresh: () => void;
}

/**
 * 1920-1931. The quality-profile select is a shared-helpers.js global the
 * vanilla renders through a `typeof === 'function'` guard; an absent global
 * yields nothing, which is what this reproduces.
 */
function OrganizeRow({
  playlist,
  onOrganizeChange,
}: {
  playlist: MirroredRow;
  onOrganizeChange: (playlistId: number, enabled: boolean) => void;
}) {
  const profileHtml =
    typeof window.playlistQualityProfileSelectHtml === 'function'
      ? window.playlistQualityProfileSelectHtml(playlist.source_playlist_id, playlist.source, true)
      : '';
  return (
    <>
      <label
        className="auto-sync-organize-toggle"
        onClick={(e) => {
          e.stopPropagation();
        }}
        title="Download missing tracks into a playlist-named folder (artist - track)"
      >
        <input
          type="checkbox"
          checked={!!playlist.organize_by_playlist}
          onChange={(e) => {
            onOrganizeChange(Number(playlist.id), e.target.checked);
          }}
        />
        <span>Organize by playlist</span>
      </label>
      {profileHtml ? <span dangerouslySetInnerHTML={{ __html: profileHtml }} /> : null}
    </>
  );
}

/** 1951-1976. A card sitting IN a lane. */
export function AutoSyncScheduledCard({
  playlist,
  schedule,
  history,
  now,
  actions,
}: {
  playlist: MirroredRow;
  schedule: AutoSyncHourlyEntry | undefined;
  history: AutoSyncHistoryRow[];
  now: number;
  actions: AutoSyncBoardActions;
}) {
  const enabled = schedule?.enabled !== false;
  const nextLabel = schedule?.next_run ? autoSyncNextRunLabel(schedule.next_run, now) : '';
  const isRunning = playlist.pipeline_state?.status === 'running';
  const health = autoSyncPlaylistHealth(history, playlist.id as number);
  const healthClass =
    health.level === 'failing' ? 'failing' : health.level === 'warning' ? 'warning' : '';
  return (
    <div
      className={`auto-sync-scheduled-card ${enabled ? '' : 'disabled'} ${healthClass}`}
      draggable
      data-playlist-id={playlist.id}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', String(playlist.id));
        e.dataTransfer.effectAllowed = 'move';
      }}
    >
      <div className="auto-sync-scheduled-main">
        <div className="auto-sync-scheduled-name">
          {health.level !== 'ok' ? (
            <span className={`auto-sync-scheduled-health ${healthClass}`} title={health.tooltip}>
              {health.level === 'failing' ? '!' : '⚠'}
            </span>
          ) : null}
          {playlist.name}
        </div>
        <div className="auto-sync-scheduled-meta">
          {autoSyncSourceLabel(playlist.source)} &middot; {playlist.track_count || 0} tracks
        </div>
        <OrganizeRow playlist={playlist} onOrganizeChange={actions.onOrganizeChange} />
        <div className="auto-sync-scheduled-timing">
          <span>{autoSyncIntervalLabel(schedule?.hours || 24)}</span>
          {nextLabel ? <small>{nextLabel}</small> : null}
        </div>
      </div>
      <div className="auto-sync-scheduled-actions">
        <button
          className="run"
          type="button"
          title="Run the playlist pipeline now"
          disabled={isRunning}
          onClick={(e) => {
            e.stopPropagation();
            actions.onRun(Number(playlist.id));
          }}
        >
          {isRunning ? 'Running' : 'Run now'}
        </button>
        <button
          type="button"
          title="Remove this Auto-Sync schedule"
          onClick={(e) => {
            e.stopPropagation();
            actions.onUnschedule(Number(playlist.id));
          }}
        >
          &times;
        </button>
      </div>
    </div>
  );
}

/** 754-763. A card sitting in the SIDEBAR. */
function SidebarCard({
  playlist,
  displayName,
  scheduled,
  assigned,
}: {
  playlist: MirroredRow;
  displayName?: string;
  scheduled: boolean;
  assigned: string;
}) {
  return (
    <div
      className={`auto-sync-playlist ${scheduled ? 'scheduled' : ''}`}
      draggable
      data-playlist-id={playlist.id}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', String(playlist.id));
        e.dataTransfer.effectAllowed = 'move';
      }}
    >
      <div className="auto-sync-playlist-name">{displayName || playlist.name}</div>
      <div className="auto-sync-playlist-meta">
        {playlist.track_count || 0} tracks &middot; {assigned}
      </div>
    </div>
  );
}

/**
 * 436-457. Flat rows first, then the collapsible variant-kind groups. The
 * vanilla keeps the expanded set in a module global so a full re-render
 * survives; here it is the board's own state, passed down.
 */
function SidebarGroupBody({
  rows,
  schedules,
  expandedKinds,
  onToggleKind,
}: {
  rows: MirroredRow[];
  schedules: Record<string, AutoSyncHourlyEntry>;
  expandedKinds: Set<string>;
  onToggleKind: (kind: string) => void;
}) {
  const { flat, groups } = autoSyncGroupSidebarRows(rows);
  const card = (p: MirroredRow, displayName?: string) => {
    const schedule = schedules[String(p.id)];
    return (
      <SidebarCard
        key={String(p.id)}
        playlist={p}
        displayName={displayName}
        scheduled={!!schedule}
        assigned={schedule ? autoSyncIntervalLabel(schedule.hours) : 'Unscheduled'}
      />
    );
  };
  return (
    <>
      {flat.map((p) => card(p))}
      {groups.map((g) => {
        const expanded = expandedKinds.has(g.kind);
        const activeCount = g.rows.filter((p) => !!schedules[String(p.id)]).length;
        return (
          <div
            key={g.kind}
            className={`auto-sync-kind-group ${expanded ? 'expanded' : ''} ${activeCount ? 'has-active' : ''}`}
            data-kind={g.kind}
          >
            <div
              className="auto-sync-kind-group-head"
              onClick={() => {
                onToggleKind(g.kind);
              }}
            >
              <span className="auto-sync-kind-group-chevron">&#9654;</span>
              <span className="auto-sync-kind-group-label">{g.label}</span>
              {activeCount ? (
                <span className="auto-sync-kind-group-active">{activeCount} on</span>
              ) : null}
              <span className="auto-sync-kind-group-count">{g.rows.length}</span>
            </div>
            <div className="auto-sync-kind-group-body">{g.rows.map((p) => card(p, p.variant))}</div>
          </div>
        );
      })}
    </>
  );
}

/** 809-826. One interval row, with its own drag-over highlight. */
function Lane({
  hours,
  isCustom,
  playlists,
  schedules,
  history,
  now,
  actions,
}: {
  hours: number;
  isCustom: boolean;
  playlists: MirroredRow[];
  schedules: Record<string, AutoSyncHourlyEntry>;
  history: AutoSyncHistoryRow[];
  now: number;
  actions: AutoSyncBoardActions;
}) {
  const [dragOver, setDragOver] = useState(false);
  const filled = playlists.length > 0;
  return (
    <div
      className={`auto-sync-lane ${filled ? 'filled' : 'empty'} ${isCustom ? 'custom' : ''} ${
        dragOver ? 'drag-over' : ''
      }`}
      data-hours={hours}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        // 2030-2035: a dragleave fired because the cursor moved onto a child
        // is not a real leave.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const playlistId = parseInt(e.dataTransfer.getData('text/plain'), 10);
        if (!playlistId) return;
        actions.onDrop(playlistId, hours);
      }}
    >
      <div className="auto-sync-lane-badge">
        <b>{autoSyncBucketLabel(hours)}</b>
        <span>
          {autoSyncLaneCadence(hours)}
          {isCustom ? ' · custom' : ''}
        </span>
        {filled ? <em className="auto-sync-lane-count">{playlists.length}</em> : null}
      </div>
      <div className="auto-sync-lane-track">
        {filled ? (
          playlists.map((p) => (
            <AutoSyncScheduledCard
              key={String(p.id)}
              playlist={p}
              schedule={schedules[String(p.id)]}
              history={history}
              now={now}
              actions={actions}
            />
          ))
        ) : (
          <div className="auto-sync-lane-hint">
            <span className="auto-sync-lane-hint-ic">+</span> Drag a playlist here to sync{' '}
            {autoSyncIntervalLabel(hours).toLowerCase()}
          </div>
        )}
      </div>
    </div>
  );
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

  return (
    <>
      <div className="auto-sync-board-intro">
        <div>
          <strong>Drag playlists into an interval</strong>
          <span>
            Each placement creates or updates an Auto-Sync-owned playlist-pipeline automation.
          </span>
        </div>
        <button type="button" onClick={actions.onRefresh}>
          Refresh
        </button>
      </div>
      <div className="auto-sync-body">
        <aside className="auto-sync-sidebar">
          <div className="auto-sync-sidebar-title">Mirrored playlists</div>
          <div className="auto-sync-sidebar-filter">
            <input
              type="search"
              className="auto-sync-sidebar-search"
              placeholder="Filter playlists…"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
              }}
            />
            {filter ? (
              <button
                type="button"
                className="auto-sync-sidebar-filter-clear"
                aria-label="Clear filter"
                onClick={() => {
                  setFilter('');
                }}
              >
                &times;
              </button>
            ) : null}
          </div>
          <div className="auto-sync-source-list">
            {groups.length ? (
              groups.map((g) => (
                <div key={g.source} className="auto-sync-source-group">
                  <div className="auto-sync-source-group-head">
                    <span className="auto-sync-source-title">
                      <AutoSyncSourceIcon source={g.source} />
                      <span className="auto-sync-source-title-label">
                        {autoSyncSourceLabel(g.source)}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="auto-sync-source-bulk-btn"
                      title={`Schedule all ${autoSyncSourceLabel(g.source)} playlists at the same interval`}
                      onClick={(e) => {
                        e.stopPropagation();
                        actions.onBulkMenu(e, g.source);
                      }}
                    >
                      Bulk
                    </button>
                  </div>
                  <SidebarGroupBody
                    rows={g.rows}
                    schedules={playlistSchedules}
                    expandedKinds={expandedKinds}
                    onToggleKind={toggleKind}
                  />
                </div>
              ))
            ) : (
              <div className="auto-sync-empty">No refreshable mirrored playlists yet.</div>
            )}
            {unavailable.length ? (
              <div className="auto-sync-source-group auto-sync-source-group-disabled">
                <div className="auto-sync-source-title">Not schedulable</div>
                {unavailable.map((p) => (
                  <div key={String(p.id)} className="auto-sync-playlist unavailable">
                    <div className="auto-sync-playlist-name">{p.name}</div>
                    <div className="auto-sync-playlist-meta">
                      {autoSyncSourceLabel(p.source)} &middot; refresh not supported
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </aside>
        <main className="auto-sync-lanes">
          {lanes.map((lane) => (
            <Lane
              key={lane.hours}
              hours={lane.hours}
              isCustom={lane.isCustom}
              playlists={lane.playlists}
              schedules={playlistSchedules}
              history={runHistory}
              now={now}
              actions={actions}
            />
          ))}
        </main>
      </div>
    </>
  );
}
