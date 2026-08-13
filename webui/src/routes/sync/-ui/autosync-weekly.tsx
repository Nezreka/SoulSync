/**
 * The Auto-Sync weekly schedule board — auto-sync.js 861-1078, plus the drop
 * and editor handlers at 2145-2232.
 *
 * Seven day lanes, Mon–Sun. A multi-day schedule renders under EVERY matching
 * day, which is deliberate: the vanilla's comment at 866-871 says users think
 * of it as "this playlist runs on Mon AND Wed AND Fri", so the same card
 * appears three times rather than once with a list. The vanilla walks the
 * schedules dict ONCE and pushes into per-day buckets rather than scanning all
 * schedules per day (917-931); `autoSyncWeeklyCardsByDay` keeps that shape.
 *
 * A SECOND DECLARED DIVERGENCE — the vanilla's Save leaves the editor on
 * screen. `saveAutoSyncWeeklyFromEditor` (2218-2227) awaits the save, which
 * ends in `await refreshAutoSyncScheduleModal()` (2284) — a full re-render
 * taken while `_autoSyncWeeklyEditor` is STILL set, so the popover is rendered
 * back into the DOM — and only then nulls the flag, with no further render.
 * Nothing re-renders afterwards: the status poller (2346) runs only while a
 * pipeline is running, so in the ordinary case the popover just sits there
 * until the user dismisses it by hand. The sibling exit
 * `unscheduleAutoSyncWeeklyFromEditor` (2229-2234) nulls FIRST and is correct;
 * the two were written in opposite orders and only one of them works. This
 * port closes on save, matching the unschedule path's evident intent. It also
 * closes immediately rather than after the request settles, which the vanilla
 * cannot meaningfully be said to do either way.
 *
 * The editor is a controlled draft. In the vanilla the day toggle re-renders
 * the entire modal (2200) while the time and tz inputs deliberately do NOT
 * (2205-2215) — because a re-render would blow away the caret mid-typing. That
 * asymmetry exists only to work around innerHTML rendering; React re-renders
 * all three without losing focus, so the draft is plain state and the
 * distinction disappears.
 */

import { useState } from 'react';

import {
  AUTO_SYNC_WEEKDAYS,
  AUTO_SYNC_WEEKDAY_LABELS,
  autoSyncCanSchedulePlaylist,
  autoSyncGroupBySource,
  autoSyncIntervalLabel,
  autoSyncMatchesFilter,
  autoSyncNextRunLabel,
  autoSyncWeeklyLabel,
  detectBrowserTimezone,
  type AutoSyncHistoryEntry,
  type AutoSyncHourlyEntry,
  type AutoSyncWeeklyEntry,
  type MirroredRow,
} from '../-sync.autosync';
import {
  AutoSyncBoardIntro,
  AutoSyncLane,
  AutoSyncScheduledCard,
  AutoSyncSidebar,
  type AutoSyncCardActions,
} from './autosync-shared';

/** The vanilla's default when a drop or an editor open has nothing to inherit. */
export const AUTO_SYNC_DEFAULT_WEEKLY_TIME = '09:00';

export interface AutoSyncWeeklyDraft {
  playlistId: number;
  time: string;
  days: string[];
  tz: string;
}

/**
 * 917-931. Walk the schedules once and bucket by day, so a Mon+Wed+Fri
 * schedule lands in three lanes without a per-day rescan. A schedule whose
 * playlist is not in the (filtered) list is dropped entirely — that is how the
 * sidebar filter narrows the lanes too.
 */
export function autoSyncWeeklyCardsByDay(
  schedulable: MirroredRow[],
  weeklySchedules: Record<string, AutoSyncWeeklyEntry>,
): Record<string, { playlist: MirroredRow; schedule: AutoSyncWeeklyEntry }[]> {
  const byId = new Map(schedulable.map((p) => [parseInt(String(p.id), 10), p]));
  const cardsByDay: Record<string, { playlist: MirroredRow; schedule: AutoSyncWeeklyEntry }[]> = {};
  AUTO_SYNC_WEEKDAYS.forEach((d) => {
    cardsByDay[d] = [];
  });
  Object.entries(weeklySchedules || {}).forEach(([pid, sched]) => {
    const playlist = byId.get(parseInt(pid, 10));
    if (!playlist) return;
    (sched?.days || []).forEach((day) => {
      if (cardsByDay[day]) cardsByDay[day].push({ playlist, schedule: sched });
    });
  });
  return cardsByDay;
}

/**
 * 2160-2168. Dropping onto a day AUGMENTS an existing schedule rather than
 * replacing it — dropping the same playlist on Wednesday when it already runs
 * Monday makes it run both. Dropping on a day it already has is a no-op.
 */
export function autoSyncWeeklyDropDraft(
  playlistId: number,
  day: string,
  existing: AutoSyncWeeklyEntry | undefined,
): AutoSyncWeeklyDraft {
  const days = existing
    ? existing.days.includes(day)
      ? existing.days
      : [...existing.days, day]
    : [day];
  return {
    playlistId,
    days,
    time: existing?.time || AUTO_SYNC_DEFAULT_WEEKLY_TIME,
    tz: existing?.tz || detectBrowserTimezone(),
  };
}

/** 2173-2185. The draft the editor opens with. */
export function autoSyncWeeklyEditorDraft(
  playlistId: number,
  existing: AutoSyncWeeklyEntry | undefined,
): AutoSyncWeeklyDraft {
  return {
    playlistId,
    time: existing?.time || AUTO_SYNC_DEFAULT_WEEKLY_TIME,
    days: existing ? [...existing.days] : [],
    tz: existing?.tz || detectBrowserTimezone(),
  };
}

export interface AutoSyncWeeklyEditorProps {
  draft: AutoSyncWeeklyDraft;
  playlistName: string;
  /** Whether a schedule already exists — gates the Unschedule button (1068). */
  hasExisting: boolean;
  onChange: (draft: AutoSyncWeeklyDraft) => void;
  onSave: () => void;
  onUnschedule: () => void;
  onClose: () => void;
}

/** 1026-1077. */
export function AutoSyncWeeklyEditor({
  draft,
  playlistName,
  hasExisting,
  onChange,
  onSave,
  onUnschedule,
  onClose,
}: AutoSyncWeeklyEditorProps) {
  const toggleDay = (day: string) => {
    // 2192-2202. Guarded against a day outside the seven, same as the vanilla.
    if (!AUTO_SYNC_WEEKDAYS.includes(day)) return;
    const days = draft.days.includes(day)
      ? draft.days.filter((d) => d !== day)
      : [...draft.days, day];
    onChange({ ...draft, days });
  };
  return (
    <div className="auto-sync-weekly-editor-backdrop" onClick={onClose}>
      <div
        className="auto-sync-weekly-editor"
        onClick={(e) => {
          e.stopPropagation();
        }}
      >
        <div className="auto-sync-weekly-editor-head">
          <h4>Weekly schedule</h4>
          <button type="button" className="auto-sync-close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="auto-sync-weekly-editor-playlist">{playlistName}</div>
        <div className="auto-sync-weekly-editor-section">
          <label>Days</label>
          <div className="auto-sync-weekly-editor-days">
            {AUTO_SYNC_WEEKDAYS.map((day) => (
              <button
                key={day}
                type="button"
                className={`auto-sync-weekly-day-toggle ${draft.days.includes(day) ? 'active' : ''}`}
                onClick={() => {
                  toggleDay(day);
                }}
              >
                {AUTO_SYNC_WEEKDAY_LABELS[day]}
              </button>
            ))}
          </div>
        </div>
        <div className="auto-sync-weekly-editor-section">
          <label htmlFor="auto-sync-weekly-time">Time</label>
          <input
            type="time"
            id="auto-sync-weekly-time"
            value={draft.time}
            onChange={(e) => {
              // 2205-2208: an empty value falls back rather than being stored.
              onChange({ ...draft, time: e.target.value || AUTO_SYNC_DEFAULT_WEEKLY_TIME });
            }}
          />
        </div>
        <div className="auto-sync-weekly-editor-section">
          <label htmlFor="auto-sync-weekly-tz">Timezone (IANA)</label>
          <input
            type="text"
            id="auto-sync-weekly-tz"
            value={draft.tz}
            onChange={(e) => {
              onChange({ ...draft, tz: e.target.value || 'UTC' });
            }}
          />
          <small className="auto-sync-weekly-editor-hint">
            e.g. America/Los_Angeles, Europe/London, Asia/Tokyo
          </small>
        </div>
        <div className="auto-sync-weekly-editor-actions">
          {hasExisting ? (
            <button type="button" className="auto-sync-weekly-editor-delete" onClick={onUnschedule}>
              Unschedule
            </button>
          ) : null}
          <div className="auto-sync-weekly-editor-actions-right">
            <button type="button" className="auto-sync-weekly-editor-cancel" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="auto-sync-weekly-editor-save" onClick={onSave}>
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export interface AutoSyncWeeklyBoardActions extends AutoSyncCardActions {
  /** A drop or an editor Save — both end at saveAutoSyncWeeklySchedule (2237). */
  onSave: (draft: AutoSyncWeeklyDraft) => void;
  onRefresh: () => void;
}

export interface AutoSyncWeeklyBoardProps {
  playlists: MirroredRow[];
  weeklySchedules: Record<string, AutoSyncWeeklyEntry>;
  /** Read only to mark rows scheduled on the OTHER board (879-887). */
  playlistSchedules: Record<string, AutoSyncHourlyEntry>;
  runHistory: AutoSyncHistoryEntry[];
  now: number;
  actions: AutoSyncWeeklyBoardActions;
}

export function AutoSyncWeeklyBoard({
  playlists,
  weeklySchedules,
  playlistSchedules,
  runHistory,
  now,
  actions,
}: AutoSyncWeeklyBoardProps) {
  const [filter, setFilter] = useState('');
  const [expandedKinds, setExpandedKinds] = useState<Set<string>>(() => new Set());
  const [draft, setDraft] = useState<AutoSyncWeeklyDraft | null>(null);

  const matching = playlists.filter((p) => autoSyncMatchesFilter(p, filter));
  const schedulable = matching.filter((p) => autoSyncCanSchedulePlaylist(p));
  const unavailable = matching.filter((p) => !autoSyncCanSchedulePlaylist(p));
  const groups = autoSyncGroupBySource(schedulable);
  const cardsByDay = autoSyncWeeklyCardsByDay(schedulable, weeklySchedules);

  const toggleKind = (kind: string) => {
    setExpandedKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  /**
   * 878-884. Three states, not two: a playlist with an HOURLY schedule is
   * marked `scheduled-elsewhere` here so it is visibly spoken for without
   * looking like it runs weekly.
   */
  const badgeFor = (p: MirroredRow) => {
    const weekly = weeklySchedules[String(p.id)];
    const hourly = playlistSchedules[String(p.id)];
    if (weekly) {
      return { stateClass: 'scheduled', assigned: autoSyncWeeklyLabel(weekly), active: true };
    }
    if (hourly) {
      return {
        stateClass: 'scheduled-elsewhere',
        assigned: `Hourly (${autoSyncIntervalLabel(hourly.hours).toLowerCase()})`,
        active: true,
      };
    }
    return { stateClass: '', assigned: 'Unscheduled', active: false };
  };

  const draftPlaylist = draft
    ? playlists.find((p) => parseInt(String(p.id), 10) === draft.playlistId)
    : undefined;

  return (
    <>
      <AutoSyncBoardIntro
        heading="Drag playlists onto a day"
        blurb="Each placement creates a weekly-time schedule. Click a card to edit time, additional days, or timezone."
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
        />
        <main className="auto-sync-lanes auto-sync-weekly-lanes">
          {AUTO_SYNC_WEEKDAYS.map((day) => {
            const cards = cardsByDay[day];
            return (
              <AutoSyncLane
                key={day}
                title={AUTO_SYNC_WEEKDAY_LABELS[day]}
                subtitle="Weekly"
                count={cards.length || null}
                dataAttrs={{ 'data-day': day }}
                hint={`Drag a playlist here to sync every ${AUTO_SYNC_WEEKDAY_LABELS[day]}`}
                onDropPlaylist={(playlistId) => {
                  // 2153-2158. A source that cannot be refreshed is refused
                  // here rather than at the sidebar, because the card is
                  // draggable either way. The vanilla also re-checks that the
                  // day is one of the seven (2152); here `day` comes from
                  // mapping AUTO_SYNC_WEEKDAYS itself, so that check has
                  // nothing to guard against and is dropped.
                  const playlist = playlists.find((p) => parseInt(String(p.id), 10) === playlistId);
                  if (!playlist || !autoSyncCanSchedulePlaylist(playlist)) {
                    window.showToast?.(
                      'That playlist source cannot be refreshed by Auto-Sync.',
                      'info',
                    );
                    return;
                  }
                  actions.onSave(
                    autoSyncWeeklyDropDraft(playlistId, day, weeklySchedules[String(playlistId)]),
                  );
                }}
              >
                {cards.map(({ playlist, schedule }) => {
                  const nextLabel = schedule?.next_run
                    ? autoSyncNextRunLabel(schedule.next_run, now)
                    : '';
                  return (
                    <AutoSyncScheduledCard
                      key={`${day}-${String(playlist.id)}`}
                      playlist={playlist}
                      enabled={schedule?.enabled !== false}
                      history={runHistory}
                      actions={actions}
                      extraClass="auto-sync-weekly-card"
                      unscheduleTitle="Remove this weekly schedule"
                      onCardClick={() => {
                        setDraft(
                          autoSyncWeeklyEditorDraft(
                            parseInt(String(playlist.id), 10),
                            weeklySchedules[String(playlist.id)],
                          ),
                        );
                      }}
                      timing={
                        <>
                          <span>{autoSyncWeeklyLabel(schedule)}</span>
                          <small>{schedule?.tz || 'UTC'}</small>
                          {nextLabel ? <small>{nextLabel}</small> : null}
                        </>
                      }
                    />
                  );
                })}
              </AutoSyncLane>
            );
          })}
        </main>
      </div>
      {draft && draftPlaylist ? (
        <AutoSyncWeeklyEditor
          draft={draft}
          playlistName={draftPlaylist.name || ''}
          hasExisting={!!weeklySchedules[String(draft.playlistId)]}
          onChange={setDraft}
          onSave={() => {
            // 2218-2227. An empty day set is refused rather than saved.
            if (!draft.days.length) {
              window.showToast?.('Pick at least one day for the weekly schedule.', 'error');
              return;
            }
            actions.onSave(draft);
            setDraft(null);
          }}
          onUnschedule={() => {
            setDraft(null);
            actions.onUnschedule(draft.playlistId);
          }}
          onClose={() => {
            setDraft(null);
          }}
        />
      ) : null}
    </>
  );
}
