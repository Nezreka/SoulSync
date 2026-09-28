import { useMemo } from 'react';

import type { Automation } from '../-automations.types';

import {
  automationMeta,
  formatAction,
  formatTrigger,
  timeAgo,
  timeUntil,
} from '../-automations.format';
import {
  attentionQueue,
  automationHealth,
  recentRuns,
  upcomingRuns,
} from '../-automations.helpers';
import { automationIcon } from '../-automations.icons';
import { useSecondTick } from '../-automations.progress';

interface Props {
  automations: Automation[];
  paused: boolean;
  blockLabel: (type: string) => string | undefined;
  onRun: (a: Automation) => void;
  onEdit: (a: Automation) => void;
  /** Jump to the Needs attention collection. */
  onReviewAttention: () => void;
  /** Jump to the library filtered to one automation's name. */
  onFind: (name: string) => void;
  /** Resume the side from the paused hero. */
  onResume: () => void;
}

/**
 * The overview: what the page opens on, and the answer to the question people
 * actually bring here — "is my automation working, and what's happening next?"
 *
 * Four panels, glanceable in seconds: a status hero with the verdict, an "Up
 * next" timeline of upcoming scheduled runs, the attention queue (failing or
 * never-run, with the fix one click away), and recently-run activity. The old
 * page buried all of this inside a scroll of sections; here it is the landing.
 */
export function AutomationsOverview({
  automations,
  paused,
  blockLabel,
  onRun,
  onEdit,
  onReviewAttention,
  onFind,
  onResume,
}: Props) {
  // One shared tick for every countdown on the overview.
  useSecondTick();
  const now = Date.now();

  const health = useMemo(() => automationHealth(automations, paused), [automations, paused]);
  const upcoming = useMemo(
    () => upcomingRuns(automations, paused).slice(0, 6),
    [automations, paused],
  );
  const attention = useMemo(() => attentionQueue(automations).slice(0, 6), [automations]);
  const recent = useMemo(() => recentRuns(automations).slice(0, 5), [automations]);

  const attentionCount = attentionQueue(automations).length;

  const nextIn =
    upcoming.length > 0 && upcoming[0].next_run ? timeUntil(upcoming[0].next_run, now) : null;

  return (
    <div className="automx-overview">
      {/* ── Status hero ─────────────────────────────────────────── */}
      <div
        className={`automx-hero${health.ok && !paused ? ' ok' : ''}${
          attentionCount > 0 ? ' warn' : ''
        }${paused ? ' paused' : ''}`}
      >
        <div className="automx-hero-glow" aria-hidden="true" />
        <div className="automx-hero-mark" aria-hidden="true">
          {paused ? '⏸' : attentionCount > 0 ? '⚠' : '✓'}
        </div>
        <div className="automx-hero-text">
          <div className="automx-hero-title">
            {paused
              ? 'Automations paused'
              : attentionCount > 0
                ? `${attentionCount} need${attentionCount === 1 ? 's' : ''} attention`
                : 'All systems go'}
          </div>
          <div className="automx-hero-sub">
            {paused
              ? 'Nothing is running on a schedule or event. Individual switches kept their state.'
              : attentionCount > 0
                ? `${health.armed} armed${nextIn ? ` · next run ${nextIn}` : ''} · ${attentionCount} want${attentionCount === 1 ? 's' : ''} a look`
                : `${health.armed} armed${nextIn ? ` · next run ${nextIn}` : ' · nothing scheduled'}`}
          </div>
        </div>
        <div className="automx-hero-actions">
          {paused ? (
            <button type="button" className="automx-hero-btn" onClick={onResume}>
              Resume automations
            </button>
          ) : attentionCount > 0 ? (
            <button type="button" className="automx-hero-btn" onClick={onReviewAttention}>
              Review now
            </button>
          ) : null}
        </div>
      </div>

      <div className="automx-overview-grid">
        {/* ── Up next ───────────────────────────────────────────── */}
        <section className="automx-panel automx-upnext" aria-label="Up next">
          <div className="automx-panel-head">
            <span className="automx-panel-title">Up next</span>
            <span className="automx-panel-sub">Scheduled runs, soonest first</span>
          </div>
          {upcoming.length === 0 ? (
            <div className="automx-panel-empty">
              {paused ? 'Resume automations to see what is coming up.' : 'Nothing scheduled.'}
            </div>
          ) : (
            <ol className="automx-timeline">
              {upcoming.map((a) => (
                <li key={a.id} className="automx-timeline-row">
                  <span className="automx-timeline-dot" aria-hidden="true" />
                  <span className="automx-time-chip">
                    {a.next_run ? timeUntil(a.next_run, now) : '—'}
                  </span>
                  <button
                    type="button"
                    className="automx-row-main"
                    title="Find in library"
                    onClick={() => onFind(a.name)}
                  >
                    <span className="automx-row-icon" aria-hidden="true">
                      {automationIcon(a.trigger_type)}
                    </span>
                    <span className="automx-row-name">{a.name}</span>
                    <span className="automx-row-sub">
                      {formatTrigger(a.trigger_type, a.trigger_config, blockLabel)}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        {/* ── Needs attention ─────────────────────────────────────── */}
        <section className="automx-panel automx-attention" aria-label="Needs attention">
          <div className="automx-panel-head">
            <span className="automx-panel-title">Needs attention</span>
            {attentionCount > 6 ? (
              <button type="button" className="automx-panel-link" onClick={onReviewAttention}>
                View all {attentionCount}
              </button>
            ) : null}
          </div>
          {attention.length === 0 ? (
            <div className="automx-panel-empty automx-panel-empty-good">
              Nothing here — everything is running as configured.
            </div>
          ) : (
            <ul className="automx-attention-list">
              {attention.map((a) => {
                const failed = Boolean(a.last_error);
                return (
                  <li key={a.id} className={`automx-attention-row${failed ? ' failed' : ''}`}>
                    <button
                      type="button"
                      className="automx-row-main"
                      title="Find in library"
                      onClick={() => onFind(a.name)}
                    >
                      <span className="automx-row-icon" aria-hidden="true">
                        {failed ? '⚠' : '💤'}
                      </span>
                      <span className="automx-row-name">{a.name}</span>
                      <span className="automx-row-sub">
                        {failed ? `Failed: ${a.last_error}` : 'Enabled but never ran'}
                      </span>
                    </button>
                    <span className="automx-row-actions">
                      <button
                        type="button"
                        className="automx-mini-btn"
                        title="Run now"
                        onClick={() => onRun(a)}
                      >
                        ▶
                      </button>
                      <button
                        type="button"
                        className="automx-mini-btn"
                        title="Edit"
                        onClick={() => onEdit(a)}
                      >
                        ⚙
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* ── Recently ran ──────────────────────────────────────────── */}
      <section className="automx-panel automx-recent" aria-label="Recently ran">
        <div className="automx-panel-head">
          <span className="automx-panel-title">Recently ran</span>
          <span className="automx-panel-sub">Latest activity across all automations</span>
        </div>
        {recent.length === 0 ? (
          <div className="automx-panel-empty">No runs yet.</div>
        ) : (
          <ul className="automx-recent-list">
            {recent.map((a) => {
              const meta = automationMeta(a, now, paused);
              return (
                <li key={a.id} className="automx-recent-row">
                  <button
                    type="button"
                    className="automx-row-main"
                    title="Find in library"
                    onClick={() => onFind(a.name)}
                  >
                    <span className="automx-row-icon" aria-hidden="true">
                      {automationIcon(a.action_type)}
                    </span>
                    <span className="automx-row-name">{a.name}</span>
                    <span className="automx-row-sub">
                      {formatAction(a.action_type, blockLabel)}
                    </span>
                  </button>
                  <span className="automx-recent-meta">
                    {meta.result ? (
                      <span
                        className={`automx-recent-result${meta.result.kind === 'skipped' ? ' skipped' : ''}`}
                        title={`Last run: ${meta.result.text}`}
                      >
                        {meta.result.text}
                      </span>
                    ) : null}
                    <span className="automx-recent-when">
                      {a.last_run ? timeAgo(a.last_run, now) : ''}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
