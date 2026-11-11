import { useMemo, useState } from 'react';

import type { Automation, AutomationActivityRow } from '../-automations.types';

import {
  automationMeta,
  displayName,
  formatTrigger,
  timeAgo,
  timeUntil,
} from '../-automations.format';
import {
  type LaneDot,
  attentionItems,
  recentRuns,
  scheduleLanes,
  upcomingRuns,
} from '../-automations.helpers';
import { useSecondTick } from '../-automations.progress';
import { AutomationArt, Glyph, playlistCover } from './automation-art';

interface Props {
  automations: Automation[];
  paused: boolean;
  blockLabel: (type: string) => string | undefined;
  /** the last 24 hours by automation; empty until it loads */
  recent: AutomationActivityRow[];
  onRun: (a: Automation) => void;
  onEdit: (a: Automation) => void;
  /** Jump to the Needs attention lens. */
  onReviewAttention: () => void;
  /** Jump to the library filtered to one automation's name. */
  onFind: (name: string) => void;
  /** Resume the side from the paused state. */
  onResume: () => void;
}

const LANE_HOURS = 24;
const LANE_TICKS = [0, 6, 12, 18, 24];

function laneMinute(minute: number): string {
  if (minute < 1) return 'now';
  if (minute < 60) return `in ${Math.ceil(minute)}m`;
  return `in ${Math.round(minute / 60)}h`;
}

/**
 * The overview: what the page opens on, and the answer to what people bring
 * here: is it working, and what happens next.
 *
 * It leads with the next 24 hours as lanes of runs, because a list of six
 * countdowns cannot show that eleven playlists all sync at the same hour.
 * Then what is next, what wants a look, the playlists that keep themselves in
 * sync (with their covers, since that is how a person knows a playlist), and
 * what ran last, in words.
 */
export function AutomationsOverview({
  automations,
  paused,
  blockLabel,
  recent,
  onRun,
  onEdit,
  onReviewAttention,
  onFind,
  onResume,
}: Props) {
  // One shared tick for every countdown on the overview.
  useSecondTick();
  const now = Date.now();
  const [tip, setTip] = useState<LaneDot | null>(null);

  const lanes = useMemo(
    () => scheduleLanes(automations, paused, now, LANE_HOURS),
    // minute precision is plenty for a 24 hour lane
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [automations, paused, Math.floor(now / 60000)],
  );
  const upcoming = useMemo(
    () => upcomingRuns(automations, paused).slice(0, 6),
    [automations, paused],
  );
  const attention = useMemo(() => attentionItems(automations, recent), [automations, recent]);
  const ran = useMemo(() => recentRuns(automations).slice(0, 6), [automations]);
  const playlists = useMemo(
    () =>
      upcomingRuns(automations, false).filter(
        (a) => Boolean(a.playlist) || a.owned_by === 'auto_sync',
      ),
    [automations],
  );
  const withCover = playlists.filter((a) => playlistCover(a.playlist)).length;

  return (
    <div className="automx-overview">
      {paused ? (
        <div className="automx-paused">
          <div>
            <b>Automations are paused</b>
            <span>
              Nothing runs on a schedule or an event. Each switch kept its state, and Run now still
              works.
            </span>
          </div>
          <button type="button" className="automx-cap automx-cap-primary" onClick={onResume}>
            Resume automations
          </button>
        </div>
      ) : null}

      <section className="automx-card" aria-label="The next 24 hours">
        <div className="automx-card-head">
          <h2>The next 24 hours</h2>
          <span>Each dot is a run. The dashed line is what runs every hour or so.</span>
        </div>
        {lanes.length === 0 ? (
          <div className="automx-none">
            {paused ? 'Resume automations to see what is coming up.' : 'Nothing is scheduled.'}
          </div>
        ) : (
          <div className="automx-lanes-scroll">
            <div className="automx-lanes">
              <div className="automx-axis" aria-hidden="true">
                {LANE_TICKS.map((h) => (
                  <span
                    key={h}
                    className="automx-axis-tick"
                    style={{ left: `${(h / LANE_HOURS) * 100}%` }}
                  >
                    <i />
                    <b>{h === 0 ? 'now' : `+${h}h`}</b>
                  </span>
                ))}
                <span className="automx-nowline" />
              </div>
              {lanes.map((lane) => (
                <div className="automx-lane" key={lane.key}>
                  <div className="automx-lane-name">
                    {lane.label}
                    <small>{lane.count} on a timer</small>
                  </div>
                  <div className="automx-lane-track">
                    {lane.frequent ? (
                      <span
                        className="automx-lane-band"
                        title={`${lane.frequent} run every hour or so`}
                      />
                    ) : null}
                    {lane.dots.map((dot, i) => (
                      <button
                        key={`${dot.automation.id}-${i}`}
                        type="button"
                        className={`automx-dot${dot.minute < 120 ? ' soon' : ''}`}
                        style={
                          {
                            left: `${(dot.minute / (LANE_HOURS * 60)) * 100}%`,
                            '--automx-step': dot.step,
                          } as React.CSSProperties
                        }
                        aria-label={`${displayName(dot.automation)}, ${laneMinute(dot.minute)}`}
                        onMouseEnter={() => setTip(dot)}
                        onMouseLeave={() => setTip(null)}
                        onFocus={() => setTip(dot)}
                        onBlur={() => setTip(null)}
                        onClick={() => onFind(dot.automation.name)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="automx-lane-tip" aria-live="polite">
          {tip ? (
            <>
              <b>{displayName(tip.automation)}</b>
              {' · '}
              {formatTrigger(
                tip.automation.trigger_type,
                tip.automation.trigger_config,
                blockLabel,
              )}
              {' · '}
              {laneMinute(tip.minute)}
            </>
          ) : (
            ' '
          )}
        </div>
      </section>

      <div className="automx-two">
        <section className="automx-card" aria-label="Up next">
          <div className="automx-card-head">
            <h2>Up next</h2>
            <span>Soonest first</span>
          </div>
          {upcoming.length === 0 ? (
            <div className="automx-none">
              {paused ? 'Resume automations to see what is coming up.' : 'Nothing scheduled.'}
            </div>
          ) : (
            <ol className="automx-list">
              {upcoming.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    className="automx-line"
                    title={`${a.name}: find in the list`}
                    onClick={() => onFind(a.name)}
                  >
                    <span className="automx-tile">
                      <AutomationArt automation={a} />
                    </span>
                    <span className="automx-line-text">
                      <span className="automx-line-name">{displayName(a)}</span>
                      <span className="automx-line-sub">
                        {formatTrigger(a.trigger_type, a.trigger_config, blockLabel)}
                      </span>
                    </span>
                    <span className="automx-line-when">
                      {a.next_run ? timeUntil(a.next_run, now) : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="automx-card" aria-label="Needs attention">
          <div className="automx-card-head">
            <h2>Needs attention</h2>
            {attention.length > 4 ? (
              <button type="button" className="automx-cap" onClick={onReviewAttention}>
                View all {attention.length}
              </button>
            ) : null}
          </div>
          {attention.length === 0 ? (
            <div className="automx-none automx-none-good">
              <b>Nothing is stuck</b>
              Every automation that is on ran as configured, and none failed in the last day.
            </div>
          ) : (
            <ul className="automx-list">
              {attention.slice(0, 4).map(({ automation: a, kind, text }) => (
                <li key={a.id} className={`automx-attn ${kind}`}>
                  <button
                    type="button"
                    className="automx-line"
                    title={`${a.name}: find in the list`}
                    onClick={() => onFind(a.name)}
                  >
                    <span className="automx-tile automx-tile-alert">
                      <Glyph name={kind === 'idle' ? 'clock' : 'bolt'} />
                    </span>
                    <span className="automx-line-text">
                      <span className="automx-line-name">{displayName(a)}</span>
                      <span className="automx-line-sub automx-wrap">{text}</span>
                    </span>
                  </button>
                  <span className="automx-attn-acts">
                    <button type="button" className="automx-cap" onClick={() => onRun(a)}>
                      Run now
                    </button>
                    <button
                      type="button"
                      className="automx-cap automx-cap-quiet"
                      onClick={() => onEdit(a)}
                    >
                      Edit
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {playlists.length > 0 ? (
        <section className="automx-card" aria-label="Playlists on a schedule">
          <div className="automx-card-head">
            <h2>Playlists on a schedule</h2>
            <span>
              {playlists.length} {playlists.length === 1 ? 'playlist syncs' : 'playlists sync'} on a
              timer. The tag is the next sync.
            </span>
          </div>
          <div className={`automx-shelf${withCover === 0 ? ' plain' : ''}`}>
            {playlists.map((a) => (
              <button
                key={a.id}
                type="button"
                className="automx-pl"
                title={`${a.name}: find in the list`}
                onClick={() => onFind(a.name)}
              >
                <span
                  className="automx-pl-art"
                  style={{ '--automx-hue': (a.id * 47) % 360 } as React.CSSProperties}
                >
                  <AutomationArt automation={a} named={displayName(a)} />
                  <em>{paused ? 'paused' : a.next_run ? timeUntil(a.next_run, now) : ''}</em>
                </span>
                <span className="automx-line-text">
                  <span className="automx-line-name">{a.playlist?.name || displayName(a)}</span>
                  <span className="automx-line-sub">
                    {formatTrigger(a.trigger_type, a.trigger_config, blockLabel)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <section className="automx-card" aria-label="Recently ran">
        <div className="automx-card-head">
          <h2>Recently ran</h2>
          <span>What each one did</span>
        </div>
        {ran.length === 0 ? (
          <div className="automx-none">No runs yet.</div>
        ) : (
          <ul className="automx-list">
            {ran.map((a) => {
              const meta = automationMeta(a, now, paused);
              const said = meta.error
                ? `Failed: ${meta.error}`
                : (meta.result?.text ?? 'Completed');
              return (
                <li key={a.id}>
                  <button
                    type="button"
                    className="automx-line"
                    title={`${a.name}: find in the list`}
                    onClick={() => onFind(a.name)}
                  >
                    <span className="automx-tile">
                      <AutomationArt automation={a} />
                    </span>
                    <span className="automx-line-text">
                      <span className="automx-line-name">{displayName(a)}</span>
                      <span className={`automx-line-sub${meta.error ? ' bad' : ''}`}>{said}</span>
                    </span>
                    <span className="automx-line-when">
                      {a.last_run ? timeAgo(a.last_run, now) : ''}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
