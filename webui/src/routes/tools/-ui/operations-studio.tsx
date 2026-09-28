/**
 * Operations Studio — The Premier Outcome-Driven Operations Workstation.
 *
 * Designed for everyday collectors and audiophiles who want immaculate libraries
 * without micromanaging 30 individual backend cron daemons.
 *
 * Organizes maintenance into:
 * 1. One-Click Playbooks (Full Tune-Up, Audio Integrity Sweep, Metadata Polish, Media Enrichment)
 * 2. The 4 Strategic Pillars (Audio Fidelity, Metadata Perfection, Media Enrichment, Storage Hygiene)
 * 3. Seamless cross-links to Findings and the Album Inspection Tray.
 */

import { useCallback, useMemo, useState } from 'react';

import type { RepairJob, RepairJobProgress, RepairJobRun } from '../-tools.types';

import { runRepairJob, setRepairJobEnabled } from '../-tools.api';
import { repairJobBadge } from '../-tools.core';

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
      'quality_upgrade',
      'short_preview_track',
      'lossy_converter_scan',
    ],
  },
  {
    id: 'metadata',
    category: 'Tags & metadata',
    title: 'Metadata & Tag Perfection',
    icon: '🏷️',
    glow: '168, 85, 247',
    tagline: 'Standardize artist names, split collaborations, and align discographies',
    description:
      'Cleans up featured artists, commas, Romanized non-Latin titles, and inconsistent album release metadata.',
    jobIds: [
      'album_tag_consistency',
      'comma_artist_splitter',
      'genre_cleanup',
      'nonlatin_matching',
      'suspect_album_tag',
      'acoustid_audio_fingerprint',
    ],
  },
  {
    id: 'enrichment',
    category: 'Artwork & lyrics',
    title: 'Media & Artwork Enrichment',
    icon: '🎨',
    glow: '245, 158, 11',
    tagline: 'Fetch synchronized lyrics, maximum-resolution covers, and loudness tags',
    description:
      'Locates synchronized time-coded lyrics, downloads high-resolution album art sleeves, and applies ReplayGain normalization.',
    jobIds: ['lyrics_fetcher', 'artwork_fetcher', 'replaygain_filler'],
  },
  {
    id: 'storage',
    category: 'Files & storage',
    title: 'Storage & Library Hygiene',
    icon: '📦',
    glow: '56, 189, 248',
    tagline: 'Relocate tracks, clean duplicates, and quarantine unlinked files',
    description:
      'Detects duplicate recordings, cleans up unlinked orphan tracks, and moves files into organized directory structures.',
    jobIds: ['orphan_file_detector', 'relocate', 'duplicate_cleaner', 'cache_evictor'],
  },
] as const;

export interface PlaybookPreset {
  id: string;
  title: string;
  icon: string;
  tagline: string;
  jobIds: readonly string[];
}

export const PLAYBOOK_PRESETS: readonly PlaybookPreset[] = [
  {
    id: 'full_tuneup',
    title: 'Full Library Tune-Up',
    icon: '🛡️',
    tagline: 'Complete system-wide health sweep across audio fidelity, metadata tags, and storage',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'album_tag_consistency',
      'comma_artist_splitter',
      'orphan_file_detector',
    ],
  },
  {
    id: 'audio_sweep',
    title: 'Audio Fidelity Sweep',
    icon: '🎵',
    tagline: 'Audit FLAC integrity, detect fake transcodes, and check low bitrates',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'quality_upgrade',
      'short_preview_track',
    ],
  },
  {
    id: 'tag_polish',
    title: 'Metadata Polish',
    icon: '🏷️',
    tagline: 'Clean up collaboration tags, commas, genres, and track numbering',
    jobIds: [
      'album_tag_consistency',
      'comma_artist_splitter',
      'genre_cleanup',
      'nonlatin_matching',
    ],
  },
  {
    id: 'media_enrichment',
    title: 'Media Enrichment',
    icon: '🎨',
    tagline: 'Download missing synced lyrics, high-res vinyl covers, and loudness tags',
    jobIds: ['lyrics_fetcher', 'artwork_fetcher', 'replaygain_filler'],
  },
] as const;

export interface OperationsStudioProps {
  jobs: RepairJob[] | null;
  progress: Record<string, RepairJobProgress>;
  runs: RepairJobRun[];
  onChanged: () => void;
  onShowFindings: (jobId: string) => void;
  onSwitchToAdvanced: (category?: string) => void;
}

export function OperationsStudio({
  jobs,
  progress,
  runs: _runs,
  onChanged,
  onShowFindings,
  onSwitchToAdvanced,
}: OperationsStudioProps) {
  const [runningPlaybook, setRunningPlaybook] = useState<string | null>(null);
  const [runningPillar, setRunningPillar] = useState<string | null>(null);

  const jobMap = useMemo(() => {
    const map = new Map<string, RepairJob>();
    for (const job of jobs || []) {
      map.set(job.job_id, job);
    }
    return map;
  }, [jobs]);

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
        toast(
          `${pillar.title} autopilot ${enable ? 'activated' : 'paused'}`,
          'success',
        );
        onChanged();
      } catch {
        toast('Error toggling autopilot for pillar', 'error');
      }
    },
    [jobMap, onChanged],
  );

  return (
    <div className="operations-studio">
      {/* ── 1-Click Playbooks Bar ────────────────────────────────────────── */}
      <div className="operations-playbooks-section">
        <div className="operations-playbooks-header">
          <div className="operations-playbooks-title-group">
            <span className="operations-playbooks-badge">1-Click Automation</span>
            <h5 className="operations-playbooks-title">Curated Library Playbooks</h5>
          </div>
          <p className="operations-playbooks-sub">
            Run automated composite health audits across your music library without tweaking individual scripts.
          </p>
        </div>

        <div className="operations-playbooks-grid">
          {PLAYBOOK_PRESETS.map((pb) => {
            const isRunning =
              runningPlaybook === pb.id ||
              pb.jobIds.some((id) => jobMap.get(id)?.is_running);

            return (
              <button
                type="button"
                className={`operations-playbook-card ${isRunning ? 'active' : ''}`}
                key={pb.id}
                onClick={() => void handlePlaybookRun(pb)}
                title={`Launch ${pb.title}`}
              >
                <div className="operations-playbook-top">
                  <span className="operations-playbook-icon">{pb.icon}</span>
                  <span className={`operations-playbook-status-dot ${isRunning ? 'running' : 'idle'}`} />
                </div>
                <div className="operations-playbook-title">{pb.title}</div>
                <div className="operations-playbook-desc">{pb.tagline}</div>
                <div className="operations-playbook-action">
                  <span>{isRunning ? 'Auditing Library…' : '▶ Run Playbook'}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── The 4 Strategic Pillars ──────────────────────────────────────── */}
      <div className="operations-pillars-section">
        <div className="operations-pillars-header">
          <h5 className="operations-pillars-title">Operational Pillars</h5>
          <span className="operations-pillars-count">4 Strategic Domains</span>
        </div>

        <div className="operations-pillars-grid">
          {STRATEGIC_PILLARS.map((pillar) => {
            const pillarJobs = pillar.jobIds
              .map((id) => jobMap.get(id))
              .filter((j): j is RepairJob => Boolean(j));

            const isRunning =
              runningPillar === pillar.id ||
              pillarJobs.some((j) => j.is_running || progress[j.job_id]?.status === 'running');

            // Aggregate open findings across this pillar
            let openFindings = 0;
            for (const j of pillarJobs) {
              const b = repairJobBadge(j);
              if (b.kind === 'pending') openFindings += b.count;
            }

            const allEnabled =
              pillarJobs.length > 0 && pillarJobs.every((j) => j.enabled);

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
                          onShowFindings(firstWithFindings.job_id);
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
                    <span className="operations-pillar-stat-val">
                      {pillarJobs.length}
                    </span>
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
                    title={allEnabled ? 'Pause automated background scans for this pillar' : 'Enable automated background scans for this pillar'}
                  >
                    <input
                      type="checkbox"
                      checked={allEnabled}
                      onChange={(e) =>
                        void handlePillarAutopilotToggle(pillar, e.target.checked)
                      }
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
                    Includes {pillarJobs.map((j) => j.display_name).slice(0, 3).join(', ')}
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
    </div>
  );
}
