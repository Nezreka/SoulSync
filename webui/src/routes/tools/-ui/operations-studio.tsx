/**
 * The top of the Jobs panel: what is running, the checks that queue a few
 * jobs together, and the areas the jobs are grouped into. Same on both
 * sides; what differs (the areas, the checks) comes from a per-side table.
 *
 * This used to be "Simple mode", a second page with its own health number,
 * its own triage cards and an album grid. Those now live once, on the
 * Findings pill. What stayed here is the part about RUNNING things.
 *
 * The triage rule is still defined here because the areas and the findings
 * inbox both sort by it:
 *
 *   safe to fix      fixable, touches no file, starts no download
 *   needs your call  fixable, but moves/deletes/renames files or downloads
 *   review only      no automatic fix; something for you to look at
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { SHELL_LIBRARY_SCOPE_CHANGED_EVENT } from '@/platform/shell/bridge';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type { RepairJob, RepairJobProgress } from '../-tools.types';

import {
  fetchFindingGroups,
  runRepairJob,
  setRepairJobEnabled,
  stopRepairJob,
} from '../-tools.api';
import { repairJobBadge } from '../-tools.core';
import { isSafeType } from '../-tools.groups';
import { useToolsMedia, type ToolsMedia } from '../-tools.media';

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
    tagline: 'Whether the audio is what it claims to be, and good enough',
    description:
      'Finds files that fail to decode, FLAC made from a lossy source, 30-second preview clips saved as full tracks, AAC that shows 0:00, and files below their quality profile.',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'short_preview_track',
      'streamed_aac_repair',
      'quality_profile_audit',
    ],
    findingTypes: [
      'corrupt_audio',
      'fake_lossless',
      'quality_upgrade',
      'quality_upgrade_review',
      'quality_format_not_targeted',
      'quality_unknown',
      'short_preview_track',
      'zero_length_mp4',
    ],
  },
  {
    id: 'metadata',
    category: 'Tags & metadata',
    title: 'Metadata & Tag Perfection',
    icon: '🏷️',
    glow: '129, 140, 248',
    tagline: 'What is written on and about your tracks',
    description:
      'Splits combined artists, cleans genres, lines up track numbers and release years, and flags tracks filed under the wrong album.',
    jobIds: [
      'album_tag_consistency',
      'album_release_year_repair',
      'comma_artist_splitter',
      'genre_cleanup',
      'suspect_album_tag_detector',
      'track_number_repair',
      'mbid_mismatch_detector',
      'untagged_downloads',
    ],
    findingTypes: [
      'album_tag_inconsistency',
      'album_release_year_mismatch',
      'comma_artist_split',
      'genre_cleanup',
      'suspect_album_tag',
      'track_number_mismatch',
      'untagged_download',
      // the two biggest tag types had no area, so this one read "Clean" over
      // tens of thousands of open findings. counted here; neither job is in
      // jobIds, so the area's switch and Check now still leave them alone.
      'library_retag',
      'canonical_version',
    ],
  },
  {
    id: 'media',
    category: 'Artwork & lyrics',
    title: 'Media & Artwork Enrichment',
    icon: '🎨',
    glow: '251, 191, 36',
    tagline: 'The extras that make a library feel finished',
    description: 'Fills in synced lyrics, album and artist artwork, and ReplayGain loudness tags.',
    jobIds: ['missing_lyrics', 'missing_cover_art', 'replaygain_filler'],
    findingTypes: ['missing_lyrics', 'missing_cover_art', 'missing_replaygain', 'replaygain_retag'],
  },
  {
    id: 'hygiene',
    category: 'Files & storage',
    title: 'Storage & Library Hygiene',
    icon: '📦',
    glow: '56, 189, 248',
    tagline: 'Find unlinked files, missing paths, and empty folders',
    description:
      'Finds duplicate copies of a song, orphan audio files, stale library paths, and empty folders.',
    jobIds: [
      'native_duplicate_detector',
      'orphan_file_detector',
      'dead_file_cleaner',
      'empty_folder_cleaner',
    ],
    findingTypes: [
      'native_duplicate_tracks',
      'native_duplicate_releases',
      'duplicate_tracks',
      'orphan_file',
      'dead_file',
      'empty_folder',
    ],
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
    subtitle: 'One pass over audio, tags, artwork, lyrics and duplicates',
    icon: '🛡️',
    jobIds: [
      'audio_corruption_detector',
      'album_tag_consistency',
      'missing_lyrics',
      'missing_cover_art',
      // the duplicates the subtitle promises (3.6.1's duplicate_detector)
      'native_duplicate_detector',
      'empty_folder_cleaner',
    ],
  },
  {
    id: 'audio_sweep',
    title: 'Audio Fidelity Sweep',
    subtitle: 'Broken files, fake FLAC, preview clips and tracks below your quality profile',
    icon: '🎵',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'short_preview_track',
      // 3.6.1's quality check (quality_upgrade_scanner) lives on as this
      'quality_profile_audit',
    ],
  },
  {
    id: 'metadata_polish',
    title: 'Metadata Polish',
    subtitle: 'Album tags, combined artists, genres and track numbers',
    icon: '🏷️',
    jobIds: [
      'album_tag_consistency',
      'comma_artist_splitter',
      'genre_cleanup',
      'track_number_repair',
    ],
  },
  {
    id: 'enrichment_pass',
    title: 'Media Enrichment',
    subtitle: 'Lyrics, cover art and loudness tags',
    icon: '🎨',
    jobIds: ['missing_lyrics', 'missing_cover_art', 'replaygain_filler'],
  },
] as const;

export const VIDEO_PILLARS: readonly StrategicPillar[] = [
  {
    id: 'files',
    category: 'Files & storage',
    title: 'Files & Storage',
    icon: '📦',
    glow: '56, 189, 248',
    tagline: 'Broken files, extra copies, names and disk you could reclaim',
    description:
      'Finds truncated or stub files, titles you own twice, file names that drift from your templates, watched movies taking up disk, and YouTube downloads whose file is gone.',
    jobIds: [
      'broken_files',
      'duplicate_movies',
      'naming_conformance',
      'watched_cleanup',
      'youtube_ghosts',
    ],
    findingTypes: [
      'broken_file',
      'duplicate_movie',
      'duplicate_episode',
      'naming_mismatch',
      'watched_cleanup',
      'youtube_ghost',
      'youtube_mass_missing',
    ],
  },
  {
    id: 'quality',
    category: 'Quality',
    title: 'Picture & Subtitles',
    icon: '🎞️',
    glow: '244, 114, 182',
    tagline: 'Upgrades to your quality cutoff, and subtitles in your languages',
    description:
      'Finds movies and episodes whose best file sits below the cutoff in your quality profile, and videos with no subtitles in the languages you watch in.',
    jobIds: ['quality_upgrade', 'missing_subtitles'],
    findingTypes: ['quality_upgrade', 'missing_subtitles'],
  },
  {
    id: 'gaps',
    category: 'Collection gaps',
    title: 'Collection Gaps',
    icon: '🧩',
    glow: '34, 197, 94',
    tagline: 'Missing episodes, unfinished franchises, a tidy wishlist',
    description:
      'Finds aired episodes of your shows with no file, franchises you started but have not finished, and wishlist rows for things you already own.',
    jobIds: ['missing_episodes', 'movie_collections', 'wishlist_audit'],
    findingTypes: ['missing_episodes', 'incomplete_collection', 'stale_wishlist'],
  },
  {
    id: 'metadata',
    category: 'Metadata',
    title: 'Metadata',
    icon: '🏷️',
    glow: '168, 85, 247',
    tagline: 'Summaries, genres, artwork and TMDB matches',
    description:
      'Finds movies missing a summary, genres, a poster or backdrop, or a TMDB match, and fills them in from the metadata sources.',
    jobIds: ['metadata_gaps', 'media_info'],
    findingTypes: ['metadata_gap'],
  },
] as const;

export const VIDEO_PLAYBOOKS: readonly PlaybookPreset[] = [
  {
    id: 'checkup',
    title: 'Full Library Check-Up',
    subtitle: 'Broken files, quality, missing episodes, metadata and duplicates',
    icon: '🛡️',
    jobIds: [
      'broken_files',
      'quality_upgrade',
      'missing_episodes',
      'metadata_gaps',
      'duplicate_movies',
    ],
  },
  {
    id: 'gaps',
    title: 'Fill The Gaps',
    subtitle: 'Missing episodes, unfinished collections and subtitles',
    icon: '🧩',
    jobIds: ['missing_episodes', 'movie_collections', 'missing_subtitles'],
  },
  {
    id: 'files',
    title: 'File Health',
    subtitle: 'Broken files, duplicate copies and names off your templates',
    icon: '🩺',
    jobIds: ['broken_files', 'duplicate_movies', 'naming_conformance'],
  },
  {
    id: 'tidy',
    title: 'Tidy Up',
    subtitle: 'Stale wishlist rows, YouTube ghosts and watched movies',
    icon: '🧹',
    jobIds: ['wishlist_audit', 'youtube_ghosts', 'watched_cleanup'],
  },
] as const;

/** what differs between the two sides' simple mode. */
export interface StudioConfig {
  pillars: readonly StrategicPillar[];
  playbooks: readonly PlaybookPreset[];
  /** the album spotlight (music only: video findings aren't about albums) */
  spotlight: boolean;
  /** what lives in each triage bucket, in this side's words */
  safeHint: string;
  callHint: string;
  reviewHint: string;
}

export const STUDIO_CONFIG: Record<ToolsMedia, StudioConfig> = {
  music: {
    pillars: STRATEGIC_PILLARS,
    playbooks: PLAYBOOK_PRESETS,
    spotlight: true,
    safeHint: 'These only write tags and data. No file is moved or deleted.',
    callHint: 'Fixing these moves, deletes or replaces files, so each one asks first.',
    reviewHint: 'Reported for you to look at. There is no automatic fix.',
  },
  video: {
    pillars: VIDEO_PILLARS,
    playbooks: VIDEO_PLAYBOOKS,
    spotlight: false,
    safeHint: "These only change SoulSync's own data. Nothing on disk, nothing downloaded.",
    callHint: 'Fixing these renames or recycles files, or starts downloads, so look first.',
    reviewHint: 'Reported for you to look at. There is no automatic fix.',
  },
};

/** one bucket of the triage: the types in it, biggest first. */
export interface TriageBucket {
  count: number;
  types: Array<{ type: string; label: string; count: number }>;
}

/**
 * sort every pending type into the bucket its fix belongs to. pure, so the
 * buckets the cards show are pinned by tests.
 */
export function triageBuckets(
  groups: readonly FindingGroup[],
  infoOf: (findingType: string) => FindingTypeInfo | undefined,
): { safe: TriageBucket; call: TriageBucket; review: TriageBucket } {
  const empty = (): TriageBucket => ({ count: 0, types: [] });
  const out = { safe: empty(), call: empty(), review: empty() };
  for (const group of groups) {
    const count = Math.max(0, group.pending || 0);
    if (count <= 0) continue;
    const info = infoOf(group.finding_type);
    const bucket = isSafeType(info) ? out.safe : info?.fixable ? out.call : out.review;
    bucket.count += count;
    bucket.types.push({
      type: group.finding_type,
      label: info?.label || group.finding_type.replace(/_/g, ' '),
      count,
    });
  }
  for (const bucket of Object.values(out)) bucket.types.sort((a, b) => b.count - a.count);
  return out;
}

export interface OperationsStudioProps {
  jobs: RepairJob[] | null;
  progress: Record<string, RepairJobProgress>;
  onChanged: () => void;
  onShowFindings: (jobId: string, options?: { severity?: string; findingType?: string }) => void;
  /** an area's "Job settings": scroll to that family in the job list */
  onShowFamily: (category?: string) => void;
}

function PlayIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M7 4.5v15l13-7.5z" />
    </svg>
  );
}

export function OperationsStudio({
  jobs,
  progress,
  onChanged,
  onShowFindings,
  onShowFamily,
}: OperationsStudioProps) {
  const media = useToolsMedia();
  const config = STUDIO_CONFIG[media];
  const [runningPlaybook, setRunningPlaybook] = useState<string | null>(null);
  const [runningPillar, setRunningPillar] = useState<string | null>(null);
  const [groups, setGroups] = useState<FindingGroup[]>([]);

  const loadData = useCallback(async () => {
    try {
      setGroups((await fetchFindingGroups()) || []);
    } catch {
      // the areas show no counts; everything else still works
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData, jobs]);

  // G16-4: another library picked in the header; its findings, not these
  useEffect(() => {
    const switched = () => void loadData();
    window.addEventListener(SHELL_LIBRARY_SCOPE_CHANGED_EVENT, switched);
    return () => window.removeEventListener(SHELL_LIBRARY_SCOPE_CHANGED_EVENT, switched);
  }, [loadData]);

  const jobMap = useMemo(() => {
    const map = new Map<string, RepairJob>();
    for (const job of jobs || []) {
      map.set(job.job_id, job);
    }
    return map;
  }, [jobs]);

  // Active running job for the "running now" strip
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

  const openType = useCallback(
    (findingType: string) => onShowFindings('', { findingType }),
    [onShowFindings],
  );

  const handleStopSingleJob = useCallback(
    async (jobId: string) => {
      try {
        await stopRepairJob(jobId);
        toast('Stopping the job', 'info');
        setTimeout(onChanged, 800);
      } catch {
        toast('Could not stop the job', 'error');
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

      for (const target of activeTargets) {
        try {
          await runRepairJob(target.job_id);
        } catch {
          // Continue with next job
        }
      }

      toast(
        `${label}: ${activeTargets.length} job${activeTargets.length === 1 ? '' : 's'} queued`,
        'success',
      );
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
          `${pillar.title} ${enable ? 'now runs on its schedule' : 'is off its schedule'}`,
          'success',
        );
        onChanged();
      } catch {
        toast('Could not change the schedule', 'error');
      }
    },
    [jobMap, onChanged],
  );

  return (
    <div className="operations-studio" data-studio-media={media}>
      {/* ── Running now ────────────────────────────────────────────────── */}
      {activeRunningJob ? (
        <div className="operations-live-hud" role="status" aria-live="polite">
          <div className="operations-live-hud-head">
            <span className="operations-live-hud-dot" aria-hidden="true" />
            <div className="operations-live-hud-title-wrap">
              <h6 className="operations-live-hud-name">{activeRunningJob.job.display_name}</h6>
              <span className="operations-live-hud-phase">
                <span className="operations-live-hud-badge">Running now</span>
                {activeRunningJob.prog?.phase ? ` · ${activeRunningJob.prog.phase}` : ''}
              </span>
            </div>
            <button
              type="button"
              className="operations-live-hud-stop-btn"
              onClick={() => void handleStopSingleJob(activeRunningJob.job.job_id)}
              title={`Stop ${activeRunningJob.job.display_name}`}
            >
              Stop
            </button>
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

      {/* ── Checks ─────────────────────────────────────────────────────── */}
      <section className="operations-playbooks-section">
        <div className="tools-section-head">
          <h3 className="tools-section-title">Run a check</h3>
          <span className="tools-section-blurb">
            Each one queues a handful of jobs together. What they find lands on Findings.
          </span>
        </div>

        <div className="operations-playbooks-grid">
          {config.playbooks.map((playbook) => {
            const isRunning = runningPlaybook === playbook.id;
            return (
              <button
                type="button"
                key={playbook.id}
                className={`operations-playbook-card ${isRunning ? 'running' : ''}`}
                disabled={isRunning}
                onClick={() => void handlePlaybookRun(playbook)}
                title={`Run ${playbook.title}`}
              >
                <span className="operations-playbook-name">{playbook.title}</span>
                <span className="operations-playbook-desc">{playbook.subtitle}</span>
                <span className="operations-playbook-cta">
                  {isRunning ? (
                    'Queuing…'
                  ) : (
                    <>
                      <PlayIcon /> Run
                    </>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* ── Areas ──────────────────────────────────────────────────────── */}
      <section className="operations-pillars-section">
        <div className="tools-section-head">
          <h3 className="tools-section-title">Areas</h3>
          <span className="tools-section-blurb">
            The jobs grouped by what they look after. Put an area on its schedule, or check it now.
          </span>
        </div>

        <div className="operations-pillars-grid">
          {config.pillars.map((pillar) => {
            const pillarJobs = pillar.jobIds
              .map((id) => jobMap.get(id))
              .filter((j): j is RepairJob => Boolean(j));

            const isRunning =
              runningPillar === pillar.id ||
              pillarJobs.some((j) => j.is_running || progress[j.job_id]?.status === 'running');

            const openFindings = groups
              .filter((g) => pillar.findingTypes.includes(g.finding_type))
              .reduce((sum, g) => sum + Math.max(0, g.pending || 0), 0);

            const allEnabled = pillarJobs.length > 0 && pillarJobs.every((j) => j.enabled);
            const enabledCount = pillarJobs.filter((j) => j.enabled).length;
            // the biggest open type in this area is where its count goes
            const topType = groups
              .filter((g) => pillar.findingTypes.includes(g.finding_type) && (g.pending || 0) > 0)
              .sort((a, b) => (b.pending || 0) - (a.pending || 0))[0]?.finding_type;
            const jobNames =
              pillarJobs
                .map((j) => j.display_name)
                .slice(0, 3)
                .join(', ') + (pillarJobs.length > 3 ? ` +${pillarJobs.length - 3} more` : '');

            return (
              <div
                className={`operations-pillar-card ${isRunning ? 'running' : ''}`}
                key={pillar.id}
              >
                <div className="operations-pillar-head-info">
                  <h6 className="operations-pillar-name">{pillar.title}</h6>
                  <span className="operations-pillar-tagline" title={pillar.description}>
                    {pillar.tagline}
                  </span>
                  <span className="operations-pillar-subjob-hint">
                    {jobNames}
                    {jobNames ? ' · ' : ''}
                    <button
                      type="button"
                      className="operations-pillar-inspect-link"
                      onClick={() => onShowFamily(pillar.category)}
                      title="See each job's schedule and settings below"
                    >
                      Job settings
                    </button>
                  </span>
                </div>

                <div className="operations-pillar-stat">
                  <span className="operations-pillar-stat-val">
                    {isRunning ? 'Checking' : `${enabledCount} of ${pillarJobs.length} on schedule`}
                  </span>
                </div>

                <div className="operations-pillar-stat">
                  {openFindings > 0 ? (
                    <button
                      type="button"
                      className="operations-pillar-findings-badge"
                      title="See these findings"
                      onClick={() => {
                        if (topType) {
                          openType(topType);
                          return;
                        }
                        const firstWithFindings = pillarJobs.find(
                          (j) => repairJobBadge(j).kind === 'pending',
                        );
                        onShowFindings(firstWithFindings?.job_id || '');
                      }}
                    >
                      {openFindings.toLocaleString()} open
                    </button>
                  ) : (
                    <span className="operations-pillar-stat-val">Clean</span>
                  )}
                </div>

                <label
                  className="operations-pillar-autopilot"
                  title={
                    allEnabled
                      ? 'Take every job in this area off its schedule'
                      : 'Put every job in this area on its schedule'
                  }
                >
                  <input
                    type="checkbox"
                    checked={allEnabled}
                    onChange={(e) => void handlePillarAutopilotToggle(pillar, e.target.checked)}
                  />
                  <span className="repair-toggle-slider small" />
                  <span className="operations-pillar-autopilot-label">
                    {allEnabled ? 'On schedule' : 'Off schedule'}
                  </span>
                </label>

                <button
                  type="button"
                  className="operations-pillar-scan-btn"
                  disabled={isRunning}
                  onClick={() => void handlePillarScan(pillar)}
                  title={`Check ${pillar.title} now`}
                >
                  {isRunning ? 'Checking…' : 'Check now'}
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
