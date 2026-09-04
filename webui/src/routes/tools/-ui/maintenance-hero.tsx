/**
 * The Library Maintenance hero — master toggle, the four sections (health,
 * findings, operations, history) and the job list they share.
 *
 * The three tabs are gone. Tabs made the page a set of rooms you had to know
 * to walk into: the findings you needed to act on were behind a tab that
 * looked identical to the two you didn't want, and nothing on screen told you
 * whether your library was actually alright. It is one scroll surface now,
 * ordered by what you came here to learn: how healthy am I → what is wrong →
 * what is running → what happened. The nav jumps; it doesn't hide.
 *
 * The hero owns the job list because two sections need it: operations renders
 * it, and the findings filter is populated from it — which is exactly why the
 * vanilla filled that `<select>` from inside `loadRepairJobs`.
 *
 * Two contracts from the P0 that outlive this file:
 *
 * 1. `.repair-job-card[data-job-id]` — the vanilla socket handler used to find
 *    cards by this selector and write progress into them. My P6-era claim that
 *    the markup deletion would make that a no-op was WRONG: this component
 *    re-renders the very selector it queries, so the vanilla body was stomping
 *    React-managed nodes. The post-flip hardening reduced that handler to its
 *    ss:repair-progress dispatch; the attribute stays as the stable per-job
 *    hook the tests (and any future e2e) key on.
 *
 * 2. The class on the card is NOT the class on the status dot. An idle enabled
 *    job gets dot 'enabled' and card class '' — see repairJobCardClass.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { RepairJob, RepairJobProgress, RepairJobRun, RepairSection } from '../-tools.types';

import {
  fetchDatabaseStats,
  fetchRepairHistory,
  fetchRepairJobs,
  fetchRepairProgress,
  fetchRepairStatus,
  toggleRepairMaster,
} from '../-tools.api';
import { isRepairJobDryRun, prettifyRepairSettingKey } from '../-tools.core';
import { useRepairProgressEvent, useRepairStatusEvent } from '../-tools.events';
import { FindingsSurface } from './findings-surface';
import { Operations } from './operations';
import { RunHistory } from './run-history';

/** The vanilla hides a finished job's progress panel 30s after it lands. */
const PROGRESS_HIDE_MS = 30000;

/** Optional-called: jsdom has no scrollIntoView, and a nav button is not
 *  worth failing a render over. */
function jumpToSection(anchor: string) {
  document.getElementById(anchor)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
}

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

/**
 * A setting value as input text. Settings are primitives in practice, but the
 * payload is typed `unknown` because the backend is free to add a shape — and
 * `String({})` would silently render "[object Object]" into an input the user
 * could then save back.
 */
function settingText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="repair-empty-state">
      <div className="repair-empty-icon">{icon}</div>
      <div className="repair-empty-title">{title}</div>
      <div className="repair-empty-text">{text}</div>
    </div>
  );
}

// ── Job help overlay ─────────────────────────────────────────────────────────

/**
 * `showRepairJobHelp`. Built from the job payload the list already holds, which
 * is why it needs no fetch.
 *
 * The help text has a small format of its own: paragraphs are split on a blank
 * line, and a paragraph starting with "Settings:" becomes a bulleted list with
 * the leading "- " stripped from each line.
 */
function JobHelpOverlay({ job, onClose }: { job: RepairJob; onClose: () => void }) {
  const dryRun = isRepairJobDryRun(job);
  const settingRows = Object.entries(job.settings || {}).filter(
    ([key]) => !key.startsWith('_section_'),
  );

  const paragraphs = (job.help_text || job.description || '').split('\n\n');

  return (
    <div
      className="repair-help-overlay"
      id="repair-help-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="repair-help-modal">
        <div className="repair-help-header">
          <h3>{job.display_name}</h3>
          <button className="repair-help-close" type="button" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="repair-help-badges">
          {job.auto_fix ? (
            <span className={`repair-flow-badge ${dryRun ? 'dryrun' : 'autofix'}`}>
              {dryRun ? 'Dry Run' : 'Auto-fix'}
            </span>
          ) : (
            <span className="repair-flow-badge scan">Scan Only</span>
          )}
          <span className="repair-flow-badge scan">Every {job.interval_hours}h</span>
          {job.enabled ? (
            <span
              className="repair-flow-badge"
              style={{ background: 'rgba(74,222,128,0.12)', color: '#4ade80' }}
            >
              Enabled
            </span>
          ) : (
            <span
              className="repair-flow-badge"
              style={{ background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.4)' }}
            >
              Disabled
            </span>
          )}
        </div>
        <div className="repair-help-body">
          {paragraphs.map((paragraph, index) =>
            paragraph.startsWith('Settings:\n') ? (
              <div className="repair-help-setting-list" key={index}>
                {paragraph
                  .split('\n')
                  .slice(1)
                  .map((line, lineIndex) => (
                    <div className="repair-help-setting-item" key={lineIndex}>
                      {line.replace(/^- /, '')}
                    </div>
                  ))}
              </div>
            ) : (
              <p key={index}>
                {paragraph.split('\n').map((line, lineIndex, lines) => (
                  <span key={lineIndex}>
                    {line}
                    {lineIndex < lines.length - 1 ? <br /> : null}
                  </span>
                ))}
              </p>
            ),
          )}
        </div>
        {settingRows.length ? (
          <div className="repair-help-settings-section">
            <div className="repair-help-section-title">Current Settings</div>
            {settingRows.map(([key, value]) => (
              <div className="repair-help-setting" key={key}>
                <span className="repair-help-setting-key">{prettifyRepairSettingKey(key)}</span>
                <span className="repair-help-setting-val">
                  {typeof value === 'boolean' ? (value ? 'Yes' : 'No') : settingText(value)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── The hero ─────────────────────────────────────────────────────────────────

/** The nav. Jumping, not hiding — every section is on the page already. */
const SECTIONS: Array<{ id: RepairSection; label: string; anchor: string }> = [
  { id: 'health', label: 'Health', anchor: 'repair-section-health' },
  { id: 'findings', label: 'Findings', anchor: 'repair-section-findings' },
  // 'Jobs', not 'Operations': the page tab above is called Operations and two
  // controls with one name is a guessing game. This matches the section's own
  // heading, 'Maintenance jobs'.
  { id: 'operations', label: 'Jobs', anchor: 'repair-section-operations' },
  { id: 'history', label: 'History', anchor: 'repair-section-history' },
];

export function MaintenanceHero() {
  const [enabled, setEnabled] = useState(false);
  // Driven by the same /api/repair/status payload as the master toggle. Hidden
  // at zero rather than showing a "0" pill, matching updateRepairStatusFromData.
  const [findingsPending, setFindingsPending] = useState(0);
  const [jobs, setJobs] = useState<RepairJob[] | null>(null);
  const [jobsError, setJobsError] = useState(false);
  const [history, setHistory] = useState<RepairJobRun[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  /** Library size — the health score is per 1,000 tracks, so 200 orphans in a
   *  2,000-track library and in a 200,000-track one don't score the same. */
  const [trackCount, setTrackCount] = useState<number | null>(null);
  const [progress, setProgress] = useState<Record<string, RepairJobProgress>>({});
  const hideTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await fetchRepairJobs());
      setJobsError(false);
    } catch {
      setJobsError(true);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await fetchRepairHistory(50));
      setHistoryError(false);
    } catch {
      setHistoryError(true);
    }
  }, []);

  /** `updateRepairStatus` — the findings tab calls this after every mutation so
   *  the pending badge tracks what it just changed. */
  const refreshStatus = useCallback(() => {
    void fetchRepairStatus().then((status) => {
      if (!status) return;
      setEnabled(Boolean(status.enabled));
      setFindingsPending(status.findings_pending || 0);
    });
  }, []);

  // Live push. The vanilla's `updateRepairStatusFromData` writes the orb and its
  // tooltip (dashboard markup it owns); the same frame arrives here for the two
  // nodes this component owns. Without it the badge and the toggle only moved
  // when something in this page happened to refetch.
  useRepairStatusEvent(
    useCallback((frame) => {
      setEnabled(Boolean(frame.enabled));
      setFindingsPending(frame.findings_pending || 0);
    }, []),
  );

  // Job frames are PARTIAL — the vanilla iterates Object.entries(data) and
  // touches only the jobs named in it, leaving the rest alone. Merge, never
  // replace, or a frame about one job would blank every other job's panel.
  useRepairProgressEvent(
    useCallback((frames) => {
      if (Object.keys(frames).length) setProgress((previous) => ({ ...previous, ...frames }));
    }, []),
  );

  // `openRepairModal` hydrated the master state, the job list and any in-flight
  // progress on open; the tab switch drove the rest.
  useEffect(() => {
    void fetchRepairStatus().then((status) => {
      if (!status) return;
      setEnabled(Boolean(status.enabled));
      setFindingsPending(status.findings_pending || 0);
    });
    void loadJobs();
    void loadHistory();
    void fetchDatabaseStats().then((stats) => {
      if (stats) setTrackCount(stats.tracks || 0);
    });
    void fetchRepairProgress().then((frames) => {
      if (Object.keys(frames).length) setProgress(frames);
    });
  }, [loadHistory, loadJobs]);

  // A finished panel hides itself after 30s and the list reloads for fresh
  // stats — same contract as the vanilla's _repairProgressHideTimers.
  useEffect(() => {
    const timers = hideTimers.current;
    for (const [jobId, frame] of Object.entries(progress)) {
      const done = frame.status === 'finished' || frame.status === 'error';
      if (done && !timers[jobId]) {
        // Recent Runs used to sit stale until you reloaded the page or hit its
        // refresh button — you ran a job, watched it finish, and the history
        // below still showed the run before it (#1144). The worker writes the
        // run row (_record_job_finish) BEFORE it announces completion, so the
        // row is already there to fetch. This branch is the single-shot edge:
        // the timer is set in the same tick, so a completion refreshes once.
        void loadHistory();
        timers[jobId] = setTimeout(() => {
          delete timers[jobId];
          setProgress((previous) => {
            const next = { ...previous };
            delete next[jobId];
            return next;
          });
          void loadJobs();
        }, PROGRESS_HIDE_MS);
      } else if (!done && timers[jobId]) {
        clearTimeout(timers[jobId]);
        delete timers[jobId];
      }
    }
  }, [loadHistory, loadJobs, progress]);

  useEffect(() => {
    const timers = hideTimers.current;
    return () => {
      for (const timer of Object.values(timers)) clearTimeout(timer);
    };
  }, []);

  const onMasterToggle = useCallback(async () => {
    try {
      const result = await toggleRepairMaster();
      setEnabled(result.enabled);
    } catch {
      toast('Error toggling maintenance worker', 'error');
    }
  }, []);

  const [helpJob, setHelpJob] = useState<RepairJob | null>(null);

  /**
   * A run row's "see this job's findings" jump. The token makes a second
   * click on the same job re-fire — without it, clicking the same row twice
   * after wandering off would change nothing.
   */
  const [jobFocus, setJobFocus] = useState<{ jobId: string; token: number } | null>(null);
  const showJobFindings = useCallback((jobId: string) => {
    setJobFocus((previous) => ({ jobId, token: (previous?.token || 0) + 1 }));
    jumpToSection('repair-section-findings');
  }, []);

  return (
    <div className="tools-maintenance-hero">
      <div className="tools-maintenance-header">
        <div className="tools-maintenance-header-left">
          <img src="/static/whisoul.png" alt="" className="tools-maintenance-logo" />
          <div>
            <h3 className="tools-maintenance-title">Library Maintenance</h3>
            <p className="tools-maintenance-subtitle">
              Automated scanning, detection, and repair of library issues
            </p>
          </div>
        </div>
        <label className="repair-master-toggle">
          <input
            type="checkbox"
            id="repair-master-toggle"
            checked={enabled}
            onChange={() => void onMasterToggle()}
          />
          <span className="repair-toggle-slider" />
          <span className="repair-toggle-label" id="repair-master-label">
            {enabled ? 'Enabled' : 'Disabled'}
          </span>
        </label>
      </div>

      <nav className="repair-section-nav" aria-label="Maintenance sections">
        {SECTIONS.map((section) => (
          <button
            className="repair-section-link"
            type="button"
            data-section={section.id}
            key={section.id}
            onClick={() => jumpToSection(section.anchor)}
          >
            {section.label}
            {section.id === 'findings' ? (
              <span
                className="repair-tab-badge"
                id="repair-findings-tab-badge"
                style={{ display: findingsPending > 0 ? '' : 'none' }}
              >
                {findingsPending}
              </span>
            ) : null}
          </button>
        ))}
      </nav>

      <FindingsSurface
        jobs={jobs || []}
        runs={history || []}
        trackCount={trackCount}
        focusJob={jobFocus}
        onStatusChanged={refreshStatus}
      />

      <section className="repair-section" id="repair-section-operations">
        <h4 className="repair-section-title">Maintenance jobs</h4>
        <div id="repair-jobs-list">
          <Operations
            jobs={jobs}
            error={jobsError}
            progress={progress}
            runs={history || []}
            onChanged={loadJobs}
            onHelp={setHelpJob}
            onShowFindings={showJobFindings}
          />
        </div>
      </section>

      <section className="repair-section" id="repair-section-history">
        <h4 className="repair-section-title">Recent runs</h4>
        <div className="repair-runs-card" id="repair-history-list">
          <RunHistory
            runs={history}
            error={historyError}
            onShowFindings={showJobFindings}
            onRefresh={() => void loadHistory()}
          />
        </div>
      </section>

      {helpJob ? <JobHelpOverlay job={helpJob} onClose={() => setHelpJob(null)} /> : null}
    </div>
  );
}
