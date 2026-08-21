/**
 * The Auto-Sync live pipeline monitor (auto-sync.js 1131-1183) and the
 * read-only Automations panel (1185-1198, 1883-1918).
 *
 * The monitor is the modal's only genuinely live surface — the vanilla drives
 * it by re-rendering the WHOLE modal on a 3s interval, but only while
 * something is running (2338-2350), and it skips a tick mid-drag so a
 * re-render cannot yank a card out from under the cursor. That poller is
 * slice E's problem; this file just renders what it is given.
 *
 * The Automations panel is read-only by design: these pipelines are owned by
 * the Automations page, and the vanilla says so both in its intro copy and
 * with a "Read only" corner tag.
 */

import {
  autoSyncAutomationCardFields,
  autoSyncMonitorSummary,
  autoSyncPipelineLatestLog,
  autoSyncPipelineProgress,
  autoSyncPipelineStatusClass,
  autoSyncPipelineStatusLabel,
  type AutomationRow,
  type MirroredRow,
  type PipelineState,
} from '../-sync.autosync';

/** 1162-1182. */
export function AutoSyncMonitorCard({
  playlist,
  state,
  onDetails,
}: {
  playlist: MirroredRow;
  state: PipelineState;
  onDetails: (playlistId: number) => void;
}) {
  const status = state.status || 'idle';
  const progress = autoSyncPipelineProgress(state.progress);
  const latest = autoSyncPipelineLatestLog(state);
  // 1166: the backend's own phase wins; the status label is only the fallback.
  const phase = state.phase || autoSyncPipelineStatusLabel(status);
  return (
    <article className={`auto-sync-monitor-card ${autoSyncPipelineStatusClass(status)}`}>
      <div className="auto-sync-monitor-card-main">
        <div className="auto-sync-monitor-title-row">
          <strong>{playlist.name || `Playlist #${playlist.id}`}</strong>
          <span>{autoSyncPipelineStatusLabel(status)}</span>
        </div>
        <div className="auto-sync-monitor-phase">{phase}</div>
        <div className="auto-sync-monitor-progress" aria-label={`${progress}% complete`}>
          <div style={{ width: `${progress}%` }} />
        </div>
        {latest ? <small>{latest}</small> : null}
      </div>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDetails(Number(playlist.id));
        }}
      >
        Details
      </button>
    </article>
  );
}

/** 1131-1160. */
export function AutoSyncMonitorPanel({
  playlists,
  onDetails,
}: {
  playlists: MirroredRow[];
  onDetails: (playlistId: number) => void;
}) {
  const { visible, title, detail } = autoSyncMonitorSummary(playlists);
  return (
    <section className="auto-sync-monitor">
      <div className="auto-sync-monitor-head">
        <div>
          <span className="auto-sync-monitor-kicker">Live pipeline monitor</span>
          <strong>{title}</strong>
          <small>{detail}</small>
        </div>
      </div>
      {visible.length ? (
        <div className="auto-sync-monitor-list">
          {visible.map(({ playlist, state }) => (
            <AutoSyncMonitorCard
              key={String(playlist.id)}
              playlist={playlist}
              state={state}
              onDetails={onDetails}
            />
          ))}
        </div>
      ) : (
        <div className="auto-sync-monitor-empty">
          <span>Ready</span>
          <small>Scheduled playlists appear here while the all-in-one pipeline runs.</small>
        </div>
      )}
    </section>
  );
}

/** 1894-1917. */
function AutoSyncAutomationCard({
  auto,
  playlists,
  now,
}: {
  auto: AutomationRow;
  playlists: MirroredRow[];
  now: number;
}) {
  const f = autoSyncAutomationCardFields(auto, playlists, now);
  return (
    <div className="auto-sync-automation-card">
      <span className={`auto-sync-card-status-dot ${f.enabled ? 'enabled' : 'disabled'}`} />
      <div className="auto-sync-automation-main">
        <div className="auto-sync-automation-title-row">
          <strong>{f.name}</strong>
        </div>
        <div className="auto-sync-card-flow">
          <span className="flow-trigger">{f.trigger}</span>
          <span className="flow-arrow">&rarr;</span>
          <span className="flow-action">Playlist pipeline</span>
          <span className="flow-arrow">&rarr;</span>
          <span className="flow-notify">Refresh + sync</span>
        </div>
        <div className="auto-sync-automation-meta">
          <span className={`auto-sync-status ${f.enabled ? 'enabled' : 'disabled'}`}>
            {f.enabled ? 'Enabled' : 'Disabled'}
          </span>
          <span>{f.sourceLabel}</span>
          <span>{f.target}</span>
          <span>{f.next}</span>
        </div>
      </div>
      <div className="auto-sync-automation-lock">Read only</div>
    </div>
  );
}

/** 1185-1198. */
export function AutoSyncAutomationPanel({
  automationPipelines,
  playlists,
  now,
}: {
  automationPipelines: AutomationRow[];
  playlists: MirroredRow[];
  now: number;
}) {
  if (!automationPipelines.length) {
    return (
      <div className="auto-sync-automation-empty">
        No Automations-page playlist pipelines found.
      </div>
    );
  }
  return (
    <>
      <div className="auto-sync-automation-intro">
        <strong>Read-only Automations-page pipelines</strong>
        <span>
          These use the playlist pipeline but are managed from the Automations page, so this modal
          only displays them.
        </span>
      </div>
      <div className="auto-sync-automation-list">
        {automationPipelines.map((auto, i) => (
          <AutoSyncAutomationCard
            key={String(auto.id ?? i)}
            auto={auto}
            playlists={playlists}
            now={now}
          />
        ))}
      </div>
    </>
  );
}
