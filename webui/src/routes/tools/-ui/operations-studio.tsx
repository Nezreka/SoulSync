/**
 * Operations Studio — The Premier Outcome-Driven Operations Workstation.
 *
 * Designed for everyday collectors and audiophiles who want immaculate libraries
 * without micromanaging 30 individual backend cron daemons.
 *
 * Features:
 * 1. Executive Library Health & Telemetry Hero
 * 2. Smart Action Triage Center (Safe 1-Click Auto-Fixes vs. Quarantine Staging)
 * 3. Live Scanning Mission Control HUD with real-time telemetry
 * 4. 1-Click Playbooks (Full Tune-Up, Audio Integrity Sweep, Metadata Polish, Media Enrichment)
 * 5. The 4 Strategic Pillars with direct resolution actions
 * 6. Visual Album Spotlight Grid with Luxury Vinyl Record disc sleeves & Album Inspection Tray.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { FindingAlbumGroup } from '../-tools.api';
import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type {
  BulkFixStatus,
  RepairFinding,
  RepairJob,
  RepairJobProgress,
  RepairJobRun,
} from '../-tools.types';

import {
  dismissFinding,
  fetchBulkFixStatus,
  fetchFindingGroups,
  fetchFindingTypes,
  fixFinding,
  reopenFinding,
  runRepairJob,
  setRepairJobEnabled,
  startBulkFix,
  stopBulkFix,
  stopRepairJob,
} from '../-tools.api';
import { repairJobBadge } from '../-tools.core';
import { RedownloadModal } from '../../artist-detail/-ui/redownload-modal';
import { AlbumInspectionTray } from './album-inspection-tray';
import { FindingsAlbumGrid } from './findings-album-grid';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

export interface StrategicPillar {
  id: string;
  category: string;
  title: string;
  icon: string;
  glow: string;
  tagline: string;
  description: string;
  jobIds: readonly string[];
  findingTypes: readonly string[];
}

export const STRATEGIC_PILLARS: readonly StrategicPillar[] = [
  {
    id: 'audio',
    category: 'Audio quality',
    title: 'Audio Fidelity & Integrity',
    icon: '🎵',
    glow: '244, 114, 182',
    tagline: 'Lossless verification, corrupt file detection, and bitrate inspection',
    description:
      'Analyzes spectrums for fake 320kbps upconversions, verifies FLAC frame integrity, and flags truncated preview tracks.',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'quality_upgrade_detector',
      'short_preview_detector',
    ],
    findingTypes: ['corrupt_audio', 'fake_lossless', 'quality_upgrade', 'short_preview_track'],
  },
  {
    id: 'metadata',
    category: 'Tags & metadata',
    title: 'Metadata & Tag Perfection',
    icon: '🏷️',
    glow: '129, 140, 248',
    tagline: 'Standardize artist names, split collaborations, and align discographies',
    description:
      'Cleans up featured artists, commas, Romanized non-Latin titles, and inconsistent album release metadata.',
    jobIds: [
      'album_tag_consistency',
      'album_release_year_repair',
      'comma_artist_splitter',
      'genre_tag_cleaner',
      'suspect_album_tag_detector',
      'track_number_fixer',
      'mbid_resolver',
    ],
    findingTypes: [
      'album_tag_inconsistency',
      'album_release_year_mismatch',
      'comma_artist_split',
      'genre_cleanup',
      'suspect_album_tag',
      'track_number_mismatch',
    ],
  },
  {
    id: 'media',
    category: 'Artwork & lyrics',
    title: 'Media & Artwork Enrichment',
    icon: '🎨',
    glow: '251, 191, 36',
    tagline: 'Fetch synchronized lyrics, maximum-resolution covers, and loudness tags',
    description:
      'Locates synchronized time-coded lyrics, downloads high-resolution album art sleeves, and applies ReplayGain normalization.',
    jobIds: ['lyrics_fetcher', 'artwork_fetcher', 'replaygain_filler'],
    findingTypes: ['missing_lyrics', 'missing_cover_art', 'missing_replaygain', 'replaygain_retag'],
  },
  {
    id: 'hygiene',
    category: 'Files & storage',
    title: 'Storage & Library Hygiene',
    icon: '📦',
    glow: '56, 189, 248',
    tagline: 'Relocate tracks, clean duplicates, and quarantine unlinked files',
    description:
      'Detects duplicate recordings, cleans up unlinked orphan tracks, and moves files into organized directory structures.',
    jobIds: [
      'duplicate_finder',
      'orphan_file_detector',
      'dead_file_cleaner',
      'empty_folder_remover',
    ],
    findingTypes: ['duplicate_tracks', 'orphan_file', 'dead_file', 'empty_folder'],
  },
] as const;

export interface PlaybookPreset {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
  jobIds: readonly string[];
}

export const PLAYBOOK_PRESETS: readonly PlaybookPreset[] = [
  {
    id: 'tune_up',
    title: 'Full Library Tune-Up',
    subtitle: 'Complete system-wide health sweep across audio fidelity, metadata tags, and storage',
    icon: '🛡️',
    jobIds: [
      'audio_corruption_detector',
      'album_tag_consistency',
      'lyrics_fetcher',
      'artwork_fetcher',
      'duplicate_finder',
    ],
  },
  {
    id: 'audio_sweep',
    title: 'Audio Fidelity Sweep',
    subtitle: 'Audit FLAC integrity, detect fake transcodes, and check low bitrates',
    icon: '🎵',
    jobIds: ['audio_corruption_detector', 'fake_lossless_detector', 'quality_upgrade_detector'],
  },
  {
    id: 'metadata_polish',
    title: 'Metadata Polish',
    subtitle: 'Clean up collaboration tags, commas, genres, and track numbering',
    icon: '🏷️',
    jobIds: [
      'album_tag_consistency',
      'comma_artist_splitter',
      'genre_tag_cleaner',
      'track_number_fixer',
    ],
  },
  {
    id: 'enrichment_pass',
    title: 'Media Enrichment',
    subtitle: 'Download missing synced lyrics, high-res vinyl covers, and loudness tags',
    icon: '🎨',
    jobIds: ['lyrics_fetcher', 'artwork_fetcher', 'replaygain_filler'],
  },
] as const;

export interface OperationsStudioProps {
  jobs: RepairJob[] | null;
  progress: Record<string, RepairJobProgress>;
  runs: RepairJobRun[];
  trackCount?: number | null;
  onChanged: () => void;
  onShowFindings: (jobId: string, options?: { severity?: string; findingType?: string }) => void;
  onSwitchToAdvanced: (category?: string) => void;
}

export function OperationsStudio({
  jobs,
  progress,
  runs: _runs,
  trackCount,
  onChanged,
  onShowFindings,
  onSwitchToAdvanced,
}: OperationsStudioProps) {
  const [runningPlaybook, setRunningPlaybook] = useState<string | null>(null);
  const [runningPillar, setRunningPillar] = useState<string | null>(null);
  const [groups, setGroups] = useState<FindingGroup[]>([]);
  const [catalog, setCatalog] = useState<Record<string, FindingTypeInfo>>({});
  const [bulkStatus, setBulkStatus] = useState<BulkFixStatus | null>(null);
  const [selectedAlbum, setSelectedAlbum] = useState<FindingAlbumGroup | null>(null);
  const [redownloadFinding, setRedownloadFinding] = useState<RepairFinding | null>(null);
  const bulkTimerRef = useRef<NodeJS.Timeout | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [groupsData, typesData] = await Promise.all([
        fetchFindingGroups(),
        fetchFindingTypes(),
      ]);
      setGroups(groupsData || []);
      const catMap: Record<string, FindingTypeInfo> = {};
      for (const t of typesData || []) {
        catMap[t.type] = t;
      }
      setCatalog(catMap);
    } catch {
      // Ignore load error
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData, jobs]);

  // Poll bulk fix progress if active
  useEffect(() => {
    const poll = async () => {
      const status = await fetchBulkFixStatus();
      if (!status) return;
      setBulkStatus(status);
      if (!status.running) {
        if (bulkTimerRef.current) {
          clearInterval(bulkTimerRef.current);
          bulkTimerRef.current = null;
        }
        if (status.fixed && status.fixed > 0) {
          toast(`Successfully applied ${status.fixed} fixes!`, 'success');
        }
        onChanged();
        void loadData();
      }
    };

    if (bulkStatus?.running && !bulkTimerRef.current) {
      bulkTimerRef.current = setInterval(() => void poll(), 1000);
      void poll();
    }

    return () => {
      if (bulkTimerRef.current) {
        clearInterval(bulkTimerRef.current);
        bulkTimerRef.current = null;
      }
    };
  }, [bulkStatus?.running, onChanged, loadData]);

  const jobMap = useMemo(() => {
    const map = new Map<string, RepairJob>();
    for (const job of jobs || []) {
      map.set(job.job_id, job);
    }
    return map;
  }, [jobs]);

  // Compute Authority Buckets (Uses both groups & catalog)
  const { safeCount, suggestionCount, quarantineCount } = useMemo(() => {
    let safe = 0;
    let suggestions = 0;
    let quarantine = 0;

    for (const g of groups) {
      const count = g.pending ?? (g as any).count ?? 0;
      if (count <= 0) continue;

      const info = catalog[g.finding_type];
      const isDestructive = info ? info.destructive : (g as any).destructive;
      const isFixable = info ? info.fixable : (g as any).fixable;

      if (isDestructive) {
        quarantine += count;
      } else if (isFixable) {
        safe += count;
      } else {
        suggestions += count;
      }
    }

    return { safeCount: safe, suggestionCount: suggestions, quarantineCount: quarantine };
  }, [groups, catalog]);

  // Executive Health Score
  const healthScore = useMemo(() => {
    const total = trackCount || 1000;
    const totalPending = groups.reduce((acc, g) => acc + (g.pending ?? (g as any).count ?? 0), 0);
    if (totalPending === 0) return 100;
    return Math.max(0, Math.min(100, Math.round(((total - totalPending) / total) * 100)));
  }, [groups, trackCount]);

  // Active running job for Live Mission Control HUD
  const activeRunningJob = useMemo(() => {
    for (const job of jobs || []) {
      if (job.is_running || progress[job.job_id]?.status === 'running') {
        return {
          job,
          prog: progress[job.job_id],
        };
      }
    }
    return null;
  }, [jobs, progress]);

  const handleApplyAllSafeFixes = useCallback(async () => {
    try {
      const result = await startBulkFix({ safeOnly: true });
      if (result.started) {
        toast(`Applying ${result.total || safeCount} safe fixes in background…`, 'info');
        setBulkStatus({ running: true, done: 0, total: result.total || safeCount });
      } else if (result.already_running) {
        toast('A maintenance fix task is already in progress', 'info');
        setBulkStatus({ running: true });
      } else {
        toast(result.error || 'Could not start safe fixes', 'error');
      }
    } catch {
      toast('Error launching safe fixes', 'error');
    }
  }, [safeCount]);

  const handleStopBulkFix = useCallback(async () => {
    try {
      await stopBulkFix();
      toast('Stopping bulk fix task…', 'info');
      setBulkStatus((prev) => (prev ? { ...prev, running: false } : null));
      onChanged();
      void loadData();
    } catch {
      toast('Failed to stop bulk fix task', 'error');
    }
  }, [loadData, onChanged]);

  const handleStopSingleJob = useCallback(
    async (jobId: string) => {
      try {
        await stopRepairJob(jobId);
        toast('Stop signal sent to operation', 'info');
        setTimeout(onChanged, 800);
      } catch {
        toast('Failed to stop job', 'error');
      }
    },
    [onChanged],
  );

  const runJobSequence = useCallback(
    async (jobIds: readonly string[], label: string) => {
      const activeTargets = jobIds
        .map((id) => jobMap.get(id))
        .filter((j): j is RepairJob => Boolean(j));

      if (activeTargets.length === 0) {
        toast(`No jobs found for ${label}`, 'info');
        return;
      }

      toast(`Triggering ${label} (${activeTargets.length} operations)...`, 'info');

      for (const target of activeTargets) {
        try {
          await runRepairJob(target.job_id);
        } catch {
          // Continue with next job
        }
      }

      toast(`${label} successfully launched in background`, 'success');
      setTimeout(onChanged, 800);
    },
    [jobMap, onChanged],
  );

  const handlePlaybookRun = useCallback(
    async (playbook: PlaybookPreset) => {
      setRunningPlaybook(playbook.id);
      try {
        await runJobSequence(playbook.jobIds, playbook.title);
      } finally {
        setTimeout(() => setRunningPlaybook(null), 1500);
      }
    },
    [runJobSequence],
  );

  const handlePillarScan = useCallback(
    async (pillar: StrategicPillar) => {
      setRunningPillar(pillar.id);
      try {
        await runJobSequence(pillar.jobIds, pillar.title);
      } finally {
        setTimeout(() => setRunningPillar(null), 1500);
      }
    },
    [runJobSequence],
  );

  const handlePillarAutopilotToggle = useCallback(
    async (pillar: StrategicPillar, enable: boolean) => {
      const targets = pillar.jobIds
        .map((id) => jobMap.get(id))
        .filter((j): j is RepairJob => Boolean(j));

      try {
        for (const target of targets) {
          await setRepairJobEnabled(target.job_id, enable);
        }
        toast(`${pillar.title} autopilot ${enable ? 'activated' : 'paused'}`, 'success');
        onChanged();
      } catch {
        toast('Error toggling autopilot for pillar', 'error');
      }
    },
    [jobMap, onChanged],
  );

  return (
    <div className="operations-studio">
      {/* ── Executive Library Health Hero ─────────────────────────────────── */}
      <div className="operations-executive-hero">
        <div className="operations-exec-metric">
          <div className="operations-exec-score">
            <span className="operations-exec-num">{healthScore}%</span>
            <span className="operations-exec-status">
              {safeCount === 0 && quarantineCount === 0 ? 'Pristine' : 'Tune-Up Ready'}
            </span>
          </div>
          <div className="operations-exec-detail">
            <div className="operations-exec-title">
              {trackCount ? `${trackCount.toLocaleString()} Tracks Verified` : 'Library Telemetry'}
            </div>
            <div className="operations-exec-sub">
              {safeCount > 0
                ? `${safeCount} one-click enrichments ready · `
                : 'All tags & lyrics aligned · '}
              {quarantineCount > 0
                ? `${quarantineCount} files in quarantine review`
                : 'Zero audio defects'}
            </div>
          </div>
        </div>

        {safeCount > 0 && !bulkStatus?.running ? (
          <button
            type="button"
            className="operations-exec-cta"
            onClick={() => void handleApplyAllSafeFixes()}
          >
            ⚡ Apply All {safeCount} Safe Fixes
          </button>
        ) : null}
      </div>

      {/* ── Autonomous Authority & Triage Center ────────────────────────── */}
      <div className="operations-triage-section">
        <div className="operations-triage-header">
          <div className="operations-triage-title-group">
            <span className="operations-triage-badge">Autonomous Curation</span>
            <h5 className="operations-triage-title">Smart Action Triage</h5>
          </div>
          <p className="operations-triage-sub">
            Prioritized by authority and data safety. Apply zero-risk enrichments instantly or
            review quarantined file changes.
          </p>
        </div>

        <div className="operations-triage-grid">
          {/* Card 1: Safe 1-Click Auto-Fixes */}
          <div className="operations-triage-card safe">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">⚡</span>
              <span className="operations-triage-count-pill safe">
                {safeCount > 0 ? `${safeCount} Ready` : 'All Clean'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Zero-Risk Auto-Fixes</h6>
            <p className="operations-triage-desc">
              Missing synced lyrics, high-res vinyl covers, ReplayGain loudness normalization, and
              tag alignment.
            </p>
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn safe"
                disabled={safeCount === 0 || Boolean(bulkStatus?.running)}
                onClick={() => void handleApplyAllSafeFixes()}
              >
                {bulkStatus?.running
                  ? `Applying Fixes (${bulkStatus.done || 0}/${bulkStatus.total || safeCount})…`
                  : `⚡ Apply All ${safeCount} Safe Fixes`}
              </button>
            </div>
          </div>

          {/* Card 2: Curator Suggestions */}
          <div className="operations-triage-card suggestions">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">💡</span>
              <span className="operations-triage-count-pill suggestions">
                {suggestionCount > 0 ? `${suggestionCount} Suggestions` : 'Optimal'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Curator Recommendations</h6>
            <p className="operations-triage-desc">
              Collaboration artist splits, canonical release alignment, and discography gaps.
            </p>
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn suggestions"
                disabled={suggestionCount === 0}
                onClick={() => onShowFindings('', { severity: 'info' })}
              >
                Review Suggestions ➔
              </button>
            </div>
          </div>

          {/* Card 3: Quarantine & High-Risk Decisions */}
          <div className="operations-triage-card quarantine">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">🛡️</span>
              <span className="operations-triage-count-pill quarantine">
                {quarantineCount > 0 ? `⚠️ ${quarantineCount} In Quarantine` : '0 Critical'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Quarantine &amp; Review</h6>
            <p className="operations-triage-desc">
              Corrupt audio files, duplicate recordings, orphan audio files, and short preview
              clips.
            </p>
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn quarantine"
                disabled={quarantineCount === 0}
                onClick={() => onShowFindings('', { severity: 'error' })}
              >
                🛡️ Inspect Quarantine ➔
              </button>
            </div>
          </div>
        </div>

        {/* Live Bulk Fix Progress Bar if running */}
        {bulkStatus?.running ? (
          <div className="operations-triage-bulk-banner" role="status">
            <div className="operations-triage-bulk-info">
              <span className="operations-triage-bulk-pulse" />
              <span className="operations-triage-bulk-text">
                Applying non-destructive fixes in background: {bulkStatus.done || 0} /{' '}
                {bulkStatus.total || safeCount} completed
              </span>
            </div>
            <button
              type="button"
              className="operations-triage-bulk-stop"
              onClick={() => void handleStopBulkFix()}
            >
              ⏹ Stop Fix
            </button>
          </div>
        ) : null}
      </div>

      {/* ── Live Mission Control Telemetry HUD ───────────────────────────── */}
      {activeRunningJob ? (
        <div className="operations-live-hud" role="status" aria-live="polite">
          <div className="operations-live-hud-head">
            <div className="operations-live-hud-radar">
              <span className="operations-live-hud-ping" />
              <span className="operations-live-hud-dot" />
            </div>
            <div className="operations-live-hud-title-wrap">
              <span className="operations-live-hud-badge">Active Operation Telemetry</span>
              <h6 className="operations-live-hud-name">{activeRunningJob.job.display_name}</h6>
              {activeRunningJob.prog?.phase ? (
                <span className="operations-live-hud-phase">{activeRunningJob.prog.phase}</span>
              ) : null}
            </div>
            <div className="operations-live-hud-actions">
              <button
                type="button"
                className="operations-live-hud-stop-btn"
                onClick={() => void handleStopSingleJob(activeRunningJob.job.job_id)}
                title={`Stop ${activeRunningJob.job.display_name}`}
              >
                ⏹ Stop Operation
              </button>
            </div>
          </div>
          {typeof activeRunningJob.prog?.progress === 'number' &&
          activeRunningJob.prog.progress > 0 ? (
            <div className="operations-live-hud-bar-track">
              <div
                className="operations-live-hud-bar-fill"
                style={{ width: `${Math.min(100, Math.max(3, activeRunningJob.prog.progress))}%` }}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ── 1-Click Curated Playbooks ─────────────────────────────────────── */}
      <div className="operations-playbooks-section">
        <div className="operations-playbooks-header">
          <div className="operations-playbooks-title-group">
            <span className="operations-playbooks-badge">1-Click Automation</span>
            <h5 className="operations-playbooks-title">Curated Library Playbooks</h5>
          </div>
          <p className="operations-playbooks-sub">
            Run automated composite health audits across your music library without tweaking
            individual scripts.
          </p>
        </div>

        <div className="operations-playbooks-grid">
          {PLAYBOOK_PRESETS.map((playbook) => {
            const isRunning = runningPlaybook === playbook.id;
            return (
              <button
                type="button"
                key={playbook.id}
                className={`operations-playbook-card ${isRunning ? 'running' : ''}`}
                disabled={isRunning}
                onClick={() => void handlePlaybookRun(playbook)}
                title={`Launch ${playbook.title}`}
              >
                <div className="operations-playbook-card-top">
                  <span className="operations-playbook-icon">{playbook.icon}</span>
                  <span className="operations-playbook-arrow">➔</span>
                </div>
                <h6 className="operations-playbook-name">{playbook.title}</h6>
                <p className="operations-playbook-desc">{playbook.subtitle}</p>
                <div className="operations-playbook-action-row">
                  <span className="operations-playbook-cta">
                    {isRunning ? 'Launching…' : '▶ Run Playbook'}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── The 4 Strategic Pillars ───────────────────────────────────────── */}
      <div className="operations-pillars-section">
        <div className="operations-pillars-header">
          <div className="operations-pillars-title-group">
            <span className="operations-pillars-badge">Operational Pillars</span>
            <h5 className="operations-pillars-title">4 Strategic Domains</h5>
          </div>
          <p className="operations-pillars-sub">
            Autonomous background curation grouped by collection priorities. Toggle continuous
            Autopilot or run manual domain sweeps.
          </p>
        </div>

        <div className="operations-pillars-grid">
          {STRATEGIC_PILLARS.map((pillar) => {
            const pillarJobs = pillar.jobIds
              .map((id) => jobMap.get(id))
              .filter((j): j is RepairJob => Boolean(j));

            const isRunning =
              runningPillar === pillar.id ||
              pillarJobs.some((j) => j.is_running || progress[j.job_id]?.status === 'running');

            const openFindings = groups
              .filter((g) => pillar.findingTypes.includes(g.finding_type))
              .reduce((sum, g) => sum + (g.pending ?? (g as any).count ?? 0), 0);

            const allEnabled = pillarJobs.length > 0 && pillarJobs.every((j) => j.enabled);

            return (
              <div
                className={`operations-pillar-card ${isRunning ? 'running' : ''}`}
                style={{ ['--pillar-glow' as string]: pillar.glow }}
                key={pillar.id}
              >
                <div className="operations-pillar-head">
                  <div className="operations-pillar-icon-box">
                    <span>{pillar.icon}</span>
                  </div>
                  <div className="operations-pillar-head-info">
                    <h6 className="operations-pillar-name">{pillar.title}</h6>
                    <span className="operations-pillar-tagline">{pillar.tagline}</span>
                  </div>
                  {openFindings > 0 ? (
                    <button
                      type="button"
                      className="operations-pillar-findings-badge"
                      title="View these findings in the inspection tray"
                      onClick={() => {
                        const firstWithFindings = pillarJobs.find(
                          (j) => repairJobBadge(j).kind === 'pending',
                        );
                        if (firstWithFindings) {
                          onShowFindings(firstWithFindings.job_id, {
                            severity: pillar.id === 'audio' ? 'error' : undefined,
                          });
                        } else {
                          onShowFindings('', {
                            severity: pillar.id === 'audio' ? 'error' : undefined,
                          });
                        }
                      }}
                    >
                      {openFindings.toLocaleString()} Issues
                    </button>
                  ) : null}
                </div>

                <p className="operations-pillar-desc">{pillar.description}</p>

                <div className="operations-pillar-stats-row">
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">{pillarJobs.length}</span>
                    <span className="operations-pillar-stat-lbl">Active Jobs</span>
                  </div>
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">
                      {isRunning ? 'Auditing' : allEnabled ? 'Scheduled' : 'Paused'}
                    </span>
                    <span className="operations-pillar-stat-lbl">Status</span>
                  </div>
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">
                      {openFindings > 0 ? `${openFindings} open` : 'Clean'}
                    </span>
                    <span className="operations-pillar-stat-lbl">Findings</span>
                  </div>
                </div>

                <div className="operations-pillar-actions">
                  <label
                    className="operations-pillar-autopilot"
                    title={
                      allEnabled
                        ? 'Pause automated background scans for this pillar'
                        : 'Enable automated background scans for this pillar'
                    }
                  >
                    <input
                      type="checkbox"
                      checked={allEnabled}
                      onChange={(e) => void handlePillarAutopilotToggle(pillar, e.target.checked)}
                    />
                    <span className="repair-toggle-slider small" />
                    <span className="operations-pillar-autopilot-label">
                      Autopilot {allEnabled ? 'On' : 'Off'}
                    </span>
                  </label>

                  <button
                    type="button"
                    className="operations-pillar-scan-btn"
                    disabled={isRunning}
                    onClick={() => void handlePillarScan(pillar)}
                    title={`Scan all ${pillar.title} now`}
                  >
                    {isRunning ? 'Scanning…' : '▶ Run Scan'}
                  </button>
                </div>

                <div className="operations-pillar-footer">
                  <span className="operations-pillar-subjob-hint">
                    Includes{' '}
                    {pillarJobs
                      .map((j) => j.display_name)
                      .slice(0, 3)
                      .join(', ')}
                    {pillarJobs.length > 3 ? ` +${pillarJobs.length - 3} more` : ''}
                  </span>
                  <button
                    type="button"
                    className="operations-pillar-inspect-link"
                    onClick={() => onSwitchToAdvanced(pillar.category)}
                    title="Inspect individual script cadences and settings in Advanced Mode"
                  >
                    Inspect in Advanced ➔
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Releases Needing Attention (Album Spotlight Grid) ─────────────── */}
      <div className="operations-spotlight-section">
        <div className="operations-spotlight-header">
          <div className="operations-spotlight-title-group">
            <span className="operations-spotlight-badge">Album Spotlight</span>
            <h5 className="operations-spotlight-title">Releases Needing Attention</h5>
          </div>
          <p className="operations-spotlight-sub">
            Curated visual overview of albums flagged for audio quality defects, missing artwork, or
            tag alignment.
          </p>
        </div>

        {selectedAlbum ? (
          <AlbumInspectionTray
            group={selectedAlbum}
            status="pending"
            onClose={() => setSelectedAlbum(null)}
            onFixFinding={async (f) => {
              await fixFinding(f.id);
              onChanged();
              void loadData();
            }}
            onDismissFinding={async (id) => {
              await dismissFinding(id);
              onChanged();
              void loadData();
            }}
            onInspectRedownload={(f) => {
              setRedownloadFinding(f);
            }}
            onRefresh={() => {
              onChanged();
              void loadData();
            }}
            onReopenFinding={async (id) => {
              await reopenFinding(id);
              onChanged();
              void loadData();
            }}
          />
        ) : null}

        <FindingsAlbumGrid
          groupBy="album"
          status="pending"
          selectedGroupKey={selectedAlbum?.key}
          onOpen={(group) => setSelectedAlbum(group)}
        />
      </div>

      {/* ── Redownload Modal ──────────────────────────────────────────────── */}
      {/* a finding with no track behind it (a fake-lossless FILE finding) has no
          id to search for; its finding id is not a track id */}
      {redownloadFinding?.entity_id ? (
        <RedownloadModal
          track={{
            id: String(redownloadFinding.entity_id),
            track_id: String(redownloadFinding.entity_id),
            title: String(
              (redownloadFinding.details as Record<string, any>)?.track_title ||
                redownloadFinding.title ||
                '',
            ),
            file_path:
              (redownloadFinding.details as Record<string, any>)?.file_path ||
              redownloadFinding.file_path ||
              '',
            format: (redownloadFinding.details as Record<string, any>)?.format || '',
            bitrate: (redownloadFinding.details as Record<string, any>)?.bitrate || 0,
          }}
          album={{
            id: (redownloadFinding.details as Record<string, any>)?.album_id || '',
            name:
              (redownloadFinding.details as Record<string, any>)?.album_title ||
              (redownloadFinding.details as Record<string, any>)?.album ||
              '',
            title:
              (redownloadFinding.details as Record<string, any>)?.album_title ||
              (redownloadFinding.details as Record<string, any>)?.album ||
              '',
            tracks: [
              {
                id: String(redownloadFinding.entity_id),
                track_id: String(redownloadFinding.entity_id),
                title: String(
                  (redownloadFinding.details as Record<string, any>)?.track_title ||
                    redownloadFinding.title ||
                    '',
                ),
                file_path:
                  (redownloadFinding.details as Record<string, any>)?.file_path ||
                  redownloadFinding.file_path ||
                  '',
              },
            ],
          }}
          artistName={String(
            (redownloadFinding.details as Record<string, any>)?.artist_name ||
              (redownloadFinding.details as Record<string, any>)?.artist ||
              '',
          )}
          upgrade={
            redownloadFinding.finding_type === 'quality_upgrade' ||
            redownloadFinding.finding_type === 'fake_lossless'
          }
          onReload={() => {
            onChanged();
            void loadData();
          }}
          onClose={() => {
            setRedownloadFinding(null);
            onChanged();
            void loadData();
          }}
        />
      ) : null}
    </div>
  );
}
