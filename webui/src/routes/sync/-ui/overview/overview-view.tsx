/**
 * The Playlists Overview: mission control, not a management console.
 *
 * Two columns on one screen — the 48h sync timeline on the left, Now Syncing
 * and the Needs Attention queue on the right — with a quiet activity strip
 * along the bottom. Read-only except for scheduling, which goes through the
 * same `useCardSchedules` controller the cards use.
 */

import { useState } from 'react';

import type { MirroredPlaylistRow } from '../../-sync.mirrored';

import { detectBrowserTimezone } from '../../-sync.autosync';
import { useCardSchedules } from '../../-sync.card-schedule';
import {
  libraryCoveragePct,
  libraryDiscovered,
  libraryMissingCount,
  libraryTotal,
} from '../../-sync.library';
import { AutoSyncWeeklyEditor, type AutoSyncWeeklyDraft } from '../autosync-weekly';
import { PlaylistArt, playlistArtUrl } from '../playlist-art';
import { ScheduleMenu } from '../schedule-menu';
import { runSyncHeaderAction } from '../sync-shell';
import { OverviewTimeline, overviewDisplayName } from './overview-timeline';
import { useOverviewModel } from './use-overview-model';
import './overview.css';

export interface OverviewViewProps {
  onActivity: () => void;
  onOpenAutoSync: () => void;
  onRunPlaylist: (playlistId: number, name: string) => void;
  onOpenPlaylist: (playlistId: number) => void;
}

interface ScheduleMenuState {
  row: MirroredPlaylistRow;
  top: number;
  left: number;
}

function attentionReason(row: MirroredPlaylistRow): string {
  if (row.pipeline_state?.status === 'error' || row.pipeline_state?.error) {
    return 'Sync failed — needs a look';
  }
  const missing = libraryMissingCount(row);
  if (missing > 0) return `${missing} track${missing === 1 ? '' : 's'} to download`;
  return 'Needs review';
}

export function OverviewView({
  onActivity,
  onOpenAutoSync,
  onRunPlaylist,
  onOpenPlaylist,
}: OverviewViewProps) {
  const model = useOverviewModel(true);
  const cardSchedules = useCardSchedules();
  const [schedMenu, setSchedMenu] = useState<ScheduleMenuState | null>(null);
  const [weeklyDraft, setWeeklyDraft] = useState<{
    draft: AutoSyncWeeklyDraft;
    row: MirroredPlaylistRow;
  } | null>(null);

  const closeSchedMenu = () => setSchedMenu(null);

  if (model.loading) {
    return (
      <div className="ov" aria-label="Playlists overview">
        <div className="ov-main">
          <div className="ov-left">
            <div className="ov-skel ov-skel--h" />
            <div className="ov-skel ov-skel--timeline" />
          </div>
          <div className="ov-right">
            <div className="ov-skel ov-skel--card" />
            <div className="ov-skel ov-skel--rows" />
          </div>
        </div>
      </div>
    );
  }

  if (model.error) {
    return (
      <div className="ov" aria-label="Playlists overview">
        <p className="ov-error">
          Couldn&apos;t load the overview: {model.error}{' '}
          <button type="button" className="ov-link" onClick={() => model.refresh()}>
            Retry
          </button>
        </p>
      </div>
    );
  }

  const running = model.running;
  const schedEntry = schedMenu ? cardSchedules.schedules[String(schedMenu.row.id)] : undefined;

  return (
    <div className="ov" aria-label="Playlists overview">
      <div className="ov-main">
        <div className="ov-left">
          <OverviewTimeline
            scheduled={model.scheduled}
            scheduledLater={model.scheduledLater}
            unscheduled={model.unscheduled}
            now={model.now}
            onOpenPlaylist={onOpenPlaylist}
            onScheduleDrop={(row, anchor) =>
              setSchedMenu({ row, top: anchor.top, left: anchor.left })
            }
          />
        </div>

        <aside className="ov-right">
          <section className="ov-section" aria-label="Now syncing">
            <h2 className="ov-h">Now Syncing</h2>
            {running.length > 0 ? (
              <div className="ov-syncing-list">
                {running.map((row) => (
                  <div key={row.id} className="ov-syncing">
                    <span className="ov-syncing-art">
                      <PlaylistArt
                        url={playlistArtUrl(row)}
                        glyph={overviewDisplayName(row).charAt(0)}
                      />
                    </span>
                    <div className="ov-syncing-body">
                      <div className="ov-syncing-name">{overviewDisplayName(row)}</div>
                      <div
                        className="ov-syncing-bar"
                        role="progressbar"
                        aria-valuenow={libraryCoveragePct(row)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`${overviewDisplayName(row)} sync progress`}
                      >
                        <span style={{ width: `${libraryCoveragePct(row)}%` }} />
                      </div>
                      <div className="ov-syncing-sub">
                        {libraryDiscovered(row)} of {libraryTotal(row)} tracks
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="ov-quiet">Nothing syncing right now.</p>
            )}
          </section>

          <section className="ov-section" aria-label="Needs attention">
            <h2 className="ov-h">
              Needs Attention
              {model.attention.length > 0 && (
                <span className="ov-count">{model.attention.length}</span>
              )}
            </h2>
            {model.attention.length === 0 ? (
              <p className="ov-quiet">All clear.</p>
            ) : (
              <ul className="ov-attn-list">
                {model.attention.map((row) => {
                  const name = overviewDisplayName(row);
                  return (
                    <li key={row.id}>
                      <div className="ov-attn-row">
                        <span className="ov-attn-dot" aria-hidden="true" />
                        <button
                          type="button"
                          className="ov-attn-text"
                          onClick={() => onOpenPlaylist(row.id)}
                          title={`Open ${name}`}
                        >
                          <span className="ov-attn-name">{name}</span>
                          <span className="ov-attn-reason">{attentionReason(row)}</span>
                        </button>
                        <button
                          type="button"
                          className="ov-attn-sync"
                          onClick={() => onRunPlaylist(row.id, name)}
                        >
                          Sync
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </aside>
      </div>

      <footer className="ov-strip">
        <span className="ov-strip-summary">{model.summary}</span>
        <span className="ov-strip-actions">
          <button type="button" className="ov-strip-btn" onClick={onActivity}>
            Activity
          </button>
          <button
            type="button"
            className="ov-strip-btn"
            onClick={() => runSyncHeaderAction('download-origins', onOpenAutoSync, onActivity)}
          >
            Download Origins
          </button>
        </span>
      </footer>

      {schedMenu && (
        <ScheduleMenu
          row={schedMenu.row}
          hours={schedEntry?.hours ?? null}
          weekly={Boolean(schedEntry?.weekly)}
          anchor={{ top: schedMenu.top, left: schedMenu.left }}
          onClose={closeSchedMenu}
          onPickHours={(hours) => {
            void (async () => {
              await cardSchedules.set(schedMenu.row, hours);
              model.refresh();
              closeSchedMenu();
            })();
          }}
          onPickWeekly={(days) => {
            void (async () => {
              await cardSchedules.setWeekly(schedMenu.row, { days });
              model.refresh();
              closeSchedMenu();
            })();
          }}
          onCustomWeekly={() => {
            const current = schedEntry?.weeklyConfig;
            setWeeklyDraft({
              row: schedMenu.row,
              draft: {
                playlistId: schedMenu.row.id,
                days: current?.days ?? ['mon'],
                time: current?.time ?? '09:00',
                tz: current?.tz ?? detectBrowserTimezone(),
              },
            });
            closeSchedMenu();
          }}
        />
      )}

      {weeklyDraft && (
        <AutoSyncWeeklyEditor
          draft={weeklyDraft.draft}
          playlistName={overviewDisplayName(weeklyDraft.row)}
          hasExisting={Boolean(
            cardSchedules.schedules[String(weeklyDraft.draft.playlistId)]?.weekly,
          )}
          onChange={(draft) => setWeeklyDraft({ row: weeklyDraft.row, draft })}
          onSave={() => {
            void cardSchedules
              .setWeekly(weeklyDraft.row, {
                days: weeklyDraft.draft.days,
                time: weeklyDraft.draft.time,
                tz: weeklyDraft.draft.tz,
              })
              .then(() => model.refresh());
            setWeeklyDraft(null);
          }}
          onUnschedule={() => {
            void cardSchedules.set(weeklyDraft.row, null).then(() => model.refresh());
            setWeeklyDraft(null);
          }}
          onClose={() => setWeeklyDraft(null)}
        />
      )}
    </div>
  );
}
