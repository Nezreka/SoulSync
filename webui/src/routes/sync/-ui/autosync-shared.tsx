/**
 * The chrome both Auto-Sync boards render — the source icon (auto-sync.js
 * 197-203), the sidebar and its kind-groups (436-457, 766-812 / 892-914), the
 * scheduled card (1951-1976 / 979-1024), the organize toggle (1920-1931) and
 * the lane shell (809-826 / 933-949).
 *
 * The vanilla duplicates all of this across the two panels and says so in a
 * comment at 980-987: "Mirror the hourly board's autoSyncScheduledCardHtml
 * shape so the two boards stay visually consistent", followed by an explicit
 * list of the only four differences (the timing line, the click, the drag
 * functions, the unschedule helper). Those four are the props below; nothing
 * else about the two cards differs, and keeping them one component means they
 * cannot drift apart the way two copies can.
 *
 * ONE DECLARED DIVERGENCE. The hourly board's dragleave guards on
 * `col.contains(event.relatedTarget)` (2030-2035); the weekly board's does not
 * (2138-2142). That asymmetry is a bug, not a design: without the guard,
 * moving the cursor from a lane onto a card INSIDE that lane fires dragleave
 * and the drop highlight flickers off mid-drag. The shared lane carries the
 * guard, so the weekly board gains the hourly board's behaviour.
 */

import { useEffect, useState } from 'react';

import {
  autoSyncGroupSidebarRows,
  autoSyncSourceLabel,
  autoSyncPlaylistHealth,
  type AutoSyncHistoryEntry,
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

/** Both cards write the dragged playlist id the same way (2013-2019 / 2115-2121). */
function startDrag(e: React.DragEvent, playlistId: number | string | undefined) {
  e.dataTransfer.setData('text/plain', String(playlistId));
  e.dataTransfer.effectAllowed = 'move';
}

/**
 * 1920-1931. The quality-profile select is a shared-helpers.js global the
 * vanilla renders through a `typeof === 'function'` guard; an absent global
 * yields nothing, which is what this reproduces.
 *
 * The markup is only half the seam. `playlistQualityProfileSelectHtml` emits
 * an EMPTY select; `hydratePlaylistQualityProfileSelects` fills it with the
 * real profile list afterwards. The vanilla calls the second one in a loop
 * over every playlist after each full re-render (735-744) and again after a
 * sidebar filter keystroke (1089-1096) — two call sites for the same job,
 * because an innerHTML re-render destroys the hydrated options.
 *
 * Here it hydrates PER CARD, in the card's own effect. That covers both of the
 * vanilla's call sites and every case they miss: a card that appears because a
 * filter was cleared, or because a tab was switched, hydrates itself. Missing
 * this entirely would not have thrown — the select would simply have rendered
 * empty forever, which is exactly the silent-seam failure vanilla-seams.test.ts
 * exists to catch.
 */
function AutoSyncOrganizeRow({
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

  const { source_playlist_id: sourcePlaylistId, source, quality_profile_id: profileId } = playlist;
  useEffect(() => {
    if (!profileHtml) return;
    if (typeof window.hydratePlaylistQualityProfileSelects !== 'function') return;
    void window.hydratePlaylistQualityProfileSelects(sourcePlaylistId, source, profileId);
  }, [profileHtml, sourcePlaylistId, source, profileId]);

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

export interface AutoSyncCardActions {
  onRun: (playlistId: number) => void;
  onUnschedule: (playlistId: number) => void;
  onOrganizeChange: (playlistId: number, enabled: boolean) => void;
}

/**
 * 1951-1976 (hourly) and 979-1024 (weekly). `timing` is the whole timing line
 * because that is one of the four things the two boards genuinely disagree
 * about; the rest is identical by the vanilla's own intent.
 */
export function AutoSyncScheduledCard({
  playlist,
  enabled,
  timing,
  history,
  actions,
  extraClass = '',
  unscheduleTitle,
  onCardClick,
}: {
  playlist: MirroredRow;
  enabled: boolean;
  timing: React.ReactNode;
  history: AutoSyncHistoryEntry[];
  actions: AutoSyncCardActions;
  extraClass?: string;
  unscheduleTitle: string;
  onCardClick?: () => void;
}) {
  const isRunning = playlist.pipeline_state?.status === 'running';
  const health = autoSyncPlaylistHealth(history, playlist.id as number);
  const healthClass =
    health.level === 'failing' ? 'failing' : health.level === 'warning' ? 'warning' : '';
  return (
    <div
      className={`auto-sync-scheduled-card ${extraClass} ${enabled ? '' : 'disabled'} ${healthClass}`}
      draggable
      data-playlist-id={playlist.id}
      onDragStart={(e) => {
        startDrag(e, playlist.id);
      }}
      onClick={onCardClick}
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
        <AutoSyncOrganizeRow playlist={playlist} onOrganizeChange={actions.onOrganizeChange} />
        <div className="auto-sync-scheduled-timing">{timing}</div>
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
          title={unscheduleTitle}
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

/**
 * What the sidebar says about one playlist. The hourly board reads only its
 * own schedules (755-757); the weekly board also marks a playlist scheduled on
 * the OTHER board with `scheduled-elsewhere` (876-887).
 */
export interface AutoSyncSidebarBadge {
  stateClass: string;
  assigned: string;
  /** Counts toward a kind-group's "N on" tally (443-444 / 890). */
  active: boolean;
}

/** 754-763 / 876-889. A card sitting in the SIDEBAR. */
function SidebarCard({
  playlist,
  displayName,
  badge,
}: {
  playlist: MirroredRow;
  displayName?: string;
  badge: AutoSyncSidebarBadge;
}) {
  return (
    <div
      className={`auto-sync-playlist ${badge.stateClass}`}
      draggable
      data-playlist-id={playlist.id}
      onDragStart={(e) => {
        startDrag(e, playlist.id);
      }}
    >
      <div className="auto-sync-playlist-name">{displayName || playlist.name}</div>
      <div className="auto-sync-playlist-meta">
        {playlist.track_count || 0} tracks &middot; {badge.assigned}
      </div>
    </div>
  );
}

/**
 * 436-457. Flat rows first, then the collapsible variant-kind groups. The
 * vanilla keeps the expanded set in a module global so a full re-render
 * survives; here it is board state, passed down.
 */
function SidebarGroupBody({
  rows,
  badgeFor,
  expandedKinds,
  onToggleKind,
}: {
  rows: MirroredRow[];
  badgeFor: (p: MirroredRow) => AutoSyncSidebarBadge;
  expandedKinds: Set<string>;
  onToggleKind: (kind: string) => void;
}) {
  const { flat, groups } = autoSyncGroupSidebarRows(rows);
  const card = (p: MirroredRow, displayName?: string) => (
    <SidebarCard key={String(p.id)} playlist={p} displayName={displayName} badge={badgeFor(p)} />
  );
  return (
    <>
      {flat.map((p) => card(p))}
      {groups.map((g) => {
        const expanded = expandedKinds.has(g.kind);
        const activeCount = g.rows.filter((p) => badgeFor(p).active).length;
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

/**
 * 764-812 / 892-914 plus the filter box at 838-845 / 962-969. Only the hourly
 * board offers the Bulk button — the weekly board's group head is otherwise
 * identical but has none (894-901).
 */
export function AutoSyncSidebar({
  groups,
  unavailable,
  badgeFor,
  filter,
  onFilterChange,
  expandedKinds,
  onToggleKind,
  onBulkMenu,
}: {
  groups: { source: string; rows: MirroredRow[] }[];
  unavailable: MirroredRow[];
  badgeFor: (p: MirroredRow) => AutoSyncSidebarBadge;
  filter: string;
  onFilterChange: (value: string) => void;
  expandedKinds: Set<string>;
  onToggleKind: (kind: string) => void;
  onBulkMenu?: (event: React.MouseEvent, source: string) => void;
}) {
  return (
    <aside className="auto-sync-sidebar">
      <div className="auto-sync-sidebar-title">Mirrored playlists</div>
      <div className="auto-sync-sidebar-filter">
        <input
          type="search"
          className="auto-sync-sidebar-search"
          placeholder="Filter playlists…"
          value={filter}
          onChange={(e) => {
            onFilterChange(e.target.value);
          }}
        />
        {filter ? (
          <button
            type="button"
            className="auto-sync-sidebar-filter-clear"
            aria-label="Clear filter"
            onClick={() => {
              onFilterChange('');
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
                {onBulkMenu ? (
                  <button
                    type="button"
                    className="auto-sync-source-bulk-btn"
                    title={`Schedule all ${autoSyncSourceLabel(g.source)} playlists at the same interval`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onBulkMenu(e, g.source);
                    }}
                  >
                    Bulk
                  </button>
                ) : null}
              </div>
              <SidebarGroupBody
                rows={g.rows}
                badgeFor={badgeFor}
                expandedKinds={expandedKinds}
                onToggleKind={onToggleKind}
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
  );
}

/**
 * 809-826 / 933-949. One lane row, with its own drag-over highlight. See the
 * declared divergence at the top of this file about the dragleave guard.
 */
export function AutoSyncLane({
  title,
  subtitle,
  count,
  extraClass = '',
  dataAttrs,
  hint,
  onDropPlaylist,
  children,
}: {
  title: string;
  subtitle: React.ReactNode;
  /** null when the lane is empty — an empty lane carries no count badge. */
  count: number | null;
  extraClass?: string;
  dataAttrs: Record<string, string | number>;
  hint: React.ReactNode;
  onDropPlaylist: (playlistId: number) => void;
  children: React.ReactNode;
}) {
  const [dragOver, setDragOver] = useState(false);
  const filled = count !== null;
  return (
    <div
      className={`auto-sync-lane ${filled ? 'filled' : 'empty'} ${extraClass} ${
        dragOver ? 'drag-over' : ''
      }`}
      {...dataAttrs}
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
        onDropPlaylist(playlistId);
      }}
    >
      <div className="auto-sync-lane-badge">
        <b>{title}</b>
        <span>{subtitle}</span>
        {filled ? <em className="auto-sync-lane-count">{count}</em> : null}
      </div>
      <div className="auto-sync-lane-track">
        {filled ? (
          children
        ) : (
          <div className="auto-sync-lane-hint">
            <span className="auto-sync-lane-hint-ic">+</span> {hint}
          </div>
        )}
      </div>
    </div>
  );
}

/** 826-834 / 950-960. The strip above both boards. */
export function AutoSyncBoardIntro({ heading, blurb }: { heading: string; blurb: string }) {
  return (
    <div className="auto-sync-board-intro">
      <div>
        <strong>{heading}</strong>
        <span>{blurb}</span>
      </div>
    </div>
  );
}
