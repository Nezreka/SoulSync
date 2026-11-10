/**
 * Library maintenance: the three panels behind the Findings, Jobs and History
 * pills, the job list they share, and the master switch.
 *
 * It used to be one surface with a Simple and an Advanced mode. The findings
 * list only existed in Advanced, so "review these" in Simple flipped the mode
 * under you and the page you were reading was gone. Each thing now has one
 * home: findings and their health on Findings, checks, areas and every job on
 * Jobs, the run log on History. A link to a job's findings changes the pill;
 * nothing changes shape.
 *
 * This component owns the job list because two panels need it: Jobs renders
 * it, and the findings filter is filled from it.
 *
 * Two contracts that outlive the redesigns:
 *
 * 1. `.repair-job-card[data-job-id]` is the stable per-job hook the tests (and
 *    a /tools?job=<id> link) key on. The vanilla socket handler only
 *    dispatches ss:repair-progress now; React owns the nodes.
 *
 * 2. The class on the card is NOT the class on the status dot. An idle enabled
 *    job gets dot 'enabled' and card class '' (see repairJobCardClass).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type { RepairJob, RepairJobProgress, RepairJobRun } from '../-tools.types';
import type { ToolsTab } from './tools-page';

import {
  fetchDatabaseStats,
  fetchRepairHistory,
  fetchRepairJobs,
  fetchRepairProgress,
  fetchRepairStatus,
  toggleRepairMaster,
} from '../-tools.api';
import {
  isRepairJobDryRun,
  linkedJobId,
  prettifyRepairSettingKey,
  repairSettingText,
} from '../-tools.core';
import { useRepairProgressEvent, useRepairStatusEvent } from '../-tools.events';
import { takeFindingsFocus } from '../-tools.findings-focus';
import { useToolsMedia } from '../-tools.media';
import { fetchVideoLibraryStats } from '../-video-tools.api';
import { FindingsSurface } from './findings-surface';
import { Operations } from './operations';
import { OperationsStudio } from './operations-studio';
import { RunHistory } from './run-history';

/** The vanilla hides a finished job's progress panel 30s after it lands. */
const PROGRESS_HIDE_MS = 30000;

/** the video worker has no status push, so its badge and toggle poll. */
const VIDEO_STATUS_POLL_MS = 15000;

/** Optional-called: jsdom has no scrollIntoView, and a nav button is not
 *  worth failing a render over. */
function jumpToSection(anchor: string) {
  document.getElementById(anchor)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
}

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
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
              style={{ background: 'rgba(var(--success-rgb), 0.12)', color: 'var(--success)' }}
            >
              Enabled
            </span>
          ) : (
            <span
              className="repair-flow-badge"
              style={{ background: 'var(--white-a06)', color: 'var(--white-a40)' }}
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
                  {typeof value === 'boolean' ? (value ? 'Yes' : 'No') : repairSettingText(value)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export interface MaintenanceHeroProps {
  /** which pill is showing. the page owns it: the Tools pill is not ours. */
  tab: ToolsTab;
  onTab: (tab: ToolsTab) => void;
  /** the header's right side, where the master switch renders */
  headerSlot: HTMLElement | null;
  /** open findings, for the count on the Findings pill */
  onPendingChanged?: (pending: number) => void;
}

export function MaintenanceHero({
  tab,
  onTab,
  headerSlot,
  onPendingChanged,
}: MaintenanceHeroProps) {
  const media = useToolsMedia();
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
  /** what the library size is OF, for the health meta line. */
  const [sizeLabel, setSizeLabel] = useState<string | null>(null);
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
      // enough runs for the history page's two-week view to mean something
      setHistory(await fetchRepairHistory(200));
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
    if (media === 'video') {
      // a video library is movies AND episodes. both are things a finding can
      // be about, so both count toward the per-1,000 health normalisation.
      void fetchVideoLibraryStats().then((stats) => {
        if (!stats) return;
        setTrackCount(stats.movies + stats.episodes);
        setSizeLabel(
          `${stats.movies.toLocaleString()} movies · ${stats.episodes.toLocaleString()} episodes`,
        );
      });
    } else {
      void fetchDatabaseStats().then((stats) => {
        if (stats) setTrackCount(stats.tracks || 0);
      });
    }
    void fetchRepairProgress().then((frames) => {
      if (Object.keys(frames).length) setProgress(frames);
    });
  }, [loadHistory, loadJobs, media]);

  useEffect(() => {
    if (media !== 'video') return;
    const timer = setInterval(refreshStatus, VIDEO_STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [media, refreshStatus]);

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

  useEffect(() => {
    onPendingChanged?.(findingsPending);
  }, [findingsPending, onPendingChanged]);

  /**
   * "See this job's findings", from a job row, a run or an area. The token
   * makes a second click on the same job re-fire: without it, clicking the
   * same row twice after wandering off would change nothing.
   */
  const [jobFocus, setJobFocus] = useState<{
    jobId: string;
    token: number;
    query?: string;
    severity?: string;
    findingType?: string;
  } | null>(null);
  const showJobFindings = useCallback(
    (jobId: string, optionsOrLabel?: string | { severity?: string; findingType?: string }) => {
      const options = typeof optionsOrLabel === 'object' ? optionsOrLabel : undefined;
      setJobFocus((previous) => ({
        jobId,
        severity: options?.severity,
        findingType: options?.findingType,
        token: (previous?.token || 0) + 1,
      }));
      onTab('findings');
      setTimeout(() => jumpToSection('repair-section-findings'), 150);
    },
    [onTab],
  );

  // sent here from an issue ("find duplicates"): that job's findings, searched
  useEffect(() => {
    const asked = takeFindingsFocus();
    if (!asked) return;
    setJobFocus((previous) => ({ ...asked, token: (previous?.token || 0) + 1 }));
    onTab('findings');
    setTimeout(() => jumpToSection('repair-section-findings'), 300);
  }, [onTab]);

  /** an area's "Job settings": its family in the list below, on this panel */
  const showFamily = useCallback((category?: string) => {
    if (!category) {
      jumpToSection('repair-section-operations');
      return;
    }
    const target = [...document.querySelectorAll<HTMLElement>('.repair-family')].find(
      (node) => node.dataset.category === category,
    );
    if (target) target.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    else jumpToSection('repair-section-operations');
  }, []);

  // /tools?job=<id>: the job's row is on the Jobs pill. open it there, scroll
  // to the row and light it up.
  const linkedJobHandled = useRef(false);
  useEffect(() => {
    if (linkedJobHandled.current || !jobs) return;
    const jobId = linkedJobId(
      window.location.search,
      jobs.map((job) => job.job_id),
    );
    if (!jobId) return;
    linkedJobHandled.current = true;
    onTab('jobs');
    setTimeout(() => {
      const card = document.querySelector<HTMLElement>(`.repair-job-card[data-job-id="${jobId}"]`);
      card?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      card?.classList.add('repair-job-card--linked');
      setTimeout(() => card?.classList.remove('repair-job-card--linked'), 2600);
    }, 150);
  }, [jobs, onTab]);

  const enabledJobs = (jobs || []).filter((job) => job.enabled).length;
  const jobNames = useMemo(
    () => Object.fromEntries((jobs || []).map((job) => [job.job_id, job.display_name])),
    [jobs],
  );

  return (
    <div className="tools-maintenance-hero">
      {headerSlot
        ? createPortal(
            <label
              className="repair-master-toggle"
              title="Lets enabled jobs run on their schedules. Off, a job only runs when you start it."
            >
              <span className="repair-master-name">Scheduled jobs</span>
              <span className="repair-toggle-label" id="repair-master-label">
                {enabled ? 'Enabled' : 'Disabled'}
              </span>
              <input
                type="checkbox"
                id="repair-master-toggle"
                checked={enabled}
                onChange={() => void onMasterToggle()}
              />
              <span className="repair-toggle-slider" />
            </label>,
            headerSlot,
          )
        : null}

      <div
        role="tabpanel"
        id="tools-panel-findings"
        aria-labelledby="tools-tab-findings"
        hidden={tab !== 'findings'}
      >
        <FindingsSurface
          jobs={jobs || []}
          runs={history || []}
          trackCount={trackCount}
          sizeLabel={sizeLabel}
          focusJob={jobFocus}
          onStatusChanged={refreshStatus}
        />
      </div>

      <div
        role="tabpanel"
        id="tools-panel-jobs"
        aria-labelledby="tools-tab-jobs"
        hidden={tab !== 'jobs'}
      >
        <OperationsStudio
          jobs={jobs}
          progress={progress}
          onChanged={loadJobs}
          onShowFindings={showJobFindings}
          onShowFamily={showFamily}
        />

        <section className="repair-section" id="repair-section-operations">
          <div className="tools-section-head">
            <h3 className="tools-section-title">All jobs</h3>
            {jobs && jobs.length ? (
              <span className="tools-section-blurb">
                {jobs.length} jobs, {enabledJobs} on a schedule.
                {enabled ? '' : ' Scheduled jobs are off, so none of them runs by itself.'}
              </span>
            ) : null}
          </div>
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
      </div>

      <div
        role="tabpanel"
        id="tools-panel-history"
        aria-labelledby="tools-tab-history"
        hidden={tab !== 'history'}
      >
        <section className="repair-section" id="repair-section-history">
          <div className="tools-section-head">
            <h3 className="tools-section-title">Recent runs</h3>
            <span className="tools-section-blurb">
              Every time a job ran, what it checked and what it found.
            </span>
          </div>
          <div className="repair-runs-card" id="repair-history-list">
            <RunHistory
              runs={history}
              error={historyError}
              onShowFindings={showJobFindings}
              onRefresh={() => void loadHistory()}
              jobNames={jobNames}
            />
          </div>
        </section>
      </div>

      {helpJob ? <JobHelpOverlay job={helpJob} onClose={() => setHelpJob(null)} /> : null}
    </div>
  );
}
