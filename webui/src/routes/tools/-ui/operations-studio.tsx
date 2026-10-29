/**
 * Simple mode: the maintenance surface for people who want a tidy library
 * without managing thirty jobs. Same page on both sides; what differs (the
 * pillars, the playbooks, the album spotlight) comes from a per-side table.
 *
 * Oct 27 2026 rework. Simple mode used to open with its OWN health number,
 * "(library − open findings) / library", while Advanced opened with the
 * weighted score from -tools.groups. Same page, two numbers that disagreed
 * (0% here, 95 there on the same library). There is one score now, and it is
 * the weighted one.
 *
 * The three triage cards also said things that weren't true: "Quarantine" for
 * findings nothing had quarantined, "zero-risk" fixes listed as "high-res vinyl
 * covers". They are now sorted by what a fix actually does, they name the
 * finding types inside them, and every name is a link into those findings.
 *
 *   safe to fix      fixable, touches no file, starts no download
 *   needs your call  fixable, but moves/deletes/renames files or downloads
 *   review only      no automatic fix; something for you to look at
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
import { healthBandLabel, isSafeType, libraryHealth } from '../-tools.groups';
import { useToolsMedia, type ToolsMedia } from '../-tools.media';
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
    tagline: 'Whether the audio is what it claims to be, and good enough',
    description:
      'Finds files that fail to decode, FLAC made from a lossy source, tracks below your quality profile and 30-second preview clips saved as full tracks.',
    jobIds: [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'quality_upgrade_scanner',
      'short_preview_track',
    ],
    findingTypes: ['corrupt_audio', 'fake_lossless', 'quality_upgrade', 'short_preview_track'],
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
    tagline: 'What is on disk, where it belongs, and what should not be there',
    description:
      'Finds duplicate tracks, audio files the library has no row for, library rows whose file is gone, and empty folders.',
    jobIds: [
      'duplicate_detector',
      'orphan_file_detector',
      'dead_file_cleaner',
      'empty_folder_cleaner',
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
    subtitle: 'One pass over audio, tags, artwork, lyrics and duplicates',
    icon: '🛡️',
    jobIds: [
      'audio_corruption_detector',
      'album_tag_consistency',
      'missing_lyrics',
      'missing_cover_art',
      'duplicate_detector',
    ],
  },
  {
    id: 'audio_sweep',
    title: 'Audio Fidelity Sweep',
    subtitle: 'Broken files, fake FLAC and tracks below your quality profile',
    icon: '🎵',
    jobIds: ['audio_corruption_detector', 'fake_lossless_detector', 'quality_upgrade_scanner'],
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
    jobIds: ['metadata_gaps'],
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
  runs: RepairJobRun[];
  trackCount?: number | null;
  /** the library size in its own words (video: movies + episodes) */
  sizeLabel?: string | null;
  onChanged: () => void;
  onShowFindings: (jobId: string, options?: { severity?: string; findingType?: string }) => void;
  onSwitchToAdvanced: (category?: string) => void;
}

/** the chips under a triage card: the types inside it, each a way in. */
function TriageTypes({
  bucket,
  onOpen,
}: {
  bucket: TriageBucket;
  onOpen: (findingType: string) => void;
}) {
  if (!bucket.types.length) return null;
  const shown = bucket.types.slice(0, 4);
  return (
    <div className="operations-triage-types">
      {shown.map((entry) => (
        <button
          type="button"
          key={entry.type}
          className="operations-triage-type"
          onClick={() => onOpen(entry.type)}
          title={`Open the ${entry.count.toLocaleString()} ${entry.label.toLowerCase()} findings`}
        >
          <span className="operations-triage-type-label">{entry.label}</span>
          <span className="operations-triage-type-count">{entry.count.toLocaleString()}</span>
        </button>
      ))}
      {bucket.types.length > shown.length ? (
        <span className="operations-triage-type-more">
          +{bucket.types.length - shown.length} more
        </span>
      ) : null}
    </div>
  );
}

export function OperationsStudio({
  jobs,
  progress,
  runs: _runs,
  trackCount,
  sizeLabel,
  onChanged,
  onShowFindings,
  onSwitchToAdvanced,
}: OperationsStudioProps) {
  const media = useToolsMedia();
  const config = STUDIO_CONFIG[media];
  const [runningPlaybook, setRunningPlaybook] = useState<string | null>(null);
  const [runningPillar, setRunningPillar] = useState<string | null>(null);
  const [groups, setGroups] = useState<FindingGroup[]>([]);
  /** until the first groups land, every count here would read zero, and a
   *  zero health page says "nothing needs you". say we're checking instead. */
  const [loaded, setLoaded] = useState(false);
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
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData, jobs]);

  // a fix-all started elsewhere (advanced, another tab) shows its progress here too
  useEffect(() => {
    void fetchBulkFixStatus().then((status) => {
      if (status?.running) setBulkStatus(status);
    });
  }, []);

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
          toast(
            `Fixed ${status.fixed.toLocaleString()}${status.failed ? `, ${status.failed.toLocaleString()} failed` : ''}`,
            status.failed ? 'warning' : 'success',
          );
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

  const infoOf = useCallback((findingType: string) => catalog[findingType], [catalog]);
  const buckets = useMemo(() => triageBuckets(groups, infoOf), [groups, infoOf]);
  const safeCount = buckets.safe.count;

  // ONE health number, the same one advanced mode shows
  const health = useMemo(() => libraryHealth(groups, trackCount), [groups, trackCount]);
  const runningCount = (jobs || []).filter(
    (job) => job.is_running || progress[job.job_id]?.status === 'running',
  ).length;

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

  const handleApplyAllSafeFixes = useCallback(async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Fix all safe findings',
      message: `Apply the default fix to ${safeCount.toLocaleString()} findings? ${config.safeHint}`,
      confirmText: 'Fix all safe',
      destructive: false,
    });
    if (confirmed === false) return;
    try {
      const result = await startBulkFix({ safeOnly: true });
      if (result.started) {
        toast(
          `Fixing ${(result.total || safeCount).toLocaleString()} findings in the background…`,
          'info',
        );
        setBulkStatus({ running: true, done: 0, total: result.total || safeCount });
      } else if (result.already_running) {
        toast('A fix is already running, showing its progress', 'info');
        setBulkStatus({ running: true });
      } else {
        toast(result.error || 'Could not start the fixes', 'error');
      }
    } catch {
      toast('Could not start the fixes', 'error');
    }
  }, [config.safeHint, safeCount]);

  const handleStopBulkFix = useCallback(async () => {
    try {
      stopBulkFix();
      toast('Stopping after the current fix…', 'info');
      setBulkStatus((prev) => (prev ? { ...prev, running: false } : null));
      onChanged();
      void loadData();
    } catch {
      toast('Could not stop the fix', 'error');
    }
  }, [loadData, onChanged]);

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

  const pending = health.pending;
  const sizeLine = sizeLabel || (trackCount ? `${trackCount.toLocaleString()} tracks` : '');

  return (
    <div className="operations-studio" data-studio-media={media}>
      {/* ── Health ─────────────────────────────────────────────────────── */}
      <div className={`operations-executive-hero ${loaded ? health.band : ''}`}>
        <div className="operations-exec-metric">
          <div className={`operations-exec-score ${loaded ? health.band : ''}`}>
            <span className="operations-exec-num">{loaded ? health.score : '–'}</span>
            <span className="operations-exec-status">
              {loaded ? healthBandLabel(health.band) : 'checking'}
            </span>
          </div>
          <div className="operations-exec-detail">
            <div className="operations-exec-title">
              {!loaded
                ? 'Looking at your findings…'
                : pending > 0
                  ? `${pending.toLocaleString()} open finding${pending === 1 ? '' : 's'}`
                  : 'Nothing needs you'}
            </div>
            <div className="operations-exec-sub">
              {[
                sizeLine,
                safeCount > 0 ? `${safeCount.toLocaleString()} safe to fix in one click` : '',
                runningCount > 0
                  ? `${runningCount} job${runningCount === 1 ? '' : 's'} running`
                  : '',
              ]
                .filter(Boolean)
                .join(' · ')}
            </div>
          </div>
        </div>

        {safeCount > 0 && !bulkStatus?.running ? (
          <button
            type="button"
            className="operations-exec-cta"
            onClick={() => void handleApplyAllSafeFixes()}
            title={config.safeHint}
          >
            Fix {safeCount.toLocaleString()} safe
          </button>
        ) : null}
      </div>

      {/* ── Triage ─────────────────────────────────────────────────────── */}
      <div className="operations-triage-section">
        <div className="operations-triage-header">
          <div className="operations-triage-title-group">
            <h5 className="operations-triage-title">What needs you</h5>
          </div>
          <p className="operations-triage-sub">
            Every open finding, sorted by what fixing it would do. Click a type to see its findings.
          </p>
        </div>

        <div className="operations-triage-grid">
          <div className="operations-triage-card safe">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">⚡</span>
              <span className="operations-triage-count-pill safe">
                {safeCount > 0 ? `${safeCount.toLocaleString()} ready` : 'All clear'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Safe to fix</h6>
            <p className="operations-triage-desc">{config.safeHint}</p>
            <TriageTypes bucket={buckets.safe} onOpen={openType} />
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn safe"
                disabled={safeCount === 0 || Boolean(bulkStatus?.running)}
                onClick={() => void handleApplyAllSafeFixes()}
              >
                {bulkStatus?.running
                  ? `Fixing ${bulkStatus.done || 0} / ${bulkStatus.total || safeCount}…`
                  : safeCount > 0
                    ? `Fix all ${safeCount.toLocaleString()}`
                    : 'Nothing to fix'}
              </button>
            </div>
          </div>

          <div className="operations-triage-card quarantine">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">🛡️</span>
              <span className="operations-triage-count-pill quarantine">
                {buckets.call.count > 0
                  ? `${buckets.call.count.toLocaleString()} to decide`
                  : 'None'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Needs your call</h6>
            <p className="operations-triage-desc">{config.callHint}</p>
            <TriageTypes bucket={buckets.call} onOpen={openType} />
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn quarantine"
                disabled={buckets.call.count === 0}
                onClick={() => openType(buckets.call.types[0]?.type || '')}
              >
                Review {buckets.call.types[0]?.label || 'findings'} →
              </button>
            </div>
          </div>

          <div className="operations-triage-card suggestions">
            <div className="operations-triage-top">
              <span className="operations-triage-icon">💡</span>
              <span className="operations-triage-count-pill suggestions">
                {buckets.review.count > 0
                  ? `${buckets.review.count.toLocaleString()} to look at`
                  : 'None'}
              </span>
            </div>
            <h6 className="operations-triage-heading">Review only</h6>
            <p className="operations-triage-desc">{config.reviewHint}</p>
            <TriageTypes bucket={buckets.review} onOpen={openType} />
            <div className="operations-triage-action-row">
              <button
                type="button"
                className="operations-triage-btn suggestions"
                disabled={buckets.review.count === 0}
                onClick={() => openType(buckets.review.types[0]?.type || '')}
              >
                Review {buckets.review.types[0]?.label || 'findings'} →
              </button>
            </div>
          </div>
        </div>

        {bulkStatus?.running ? (
          <div className="operations-triage-bulk-banner" role="status">
            <div className="operations-triage-bulk-info">
              <span className="operations-triage-bulk-pulse" />
              <span className="operations-triage-bulk-text">
                Fixing in the background: {bulkStatus.done || 0} / {bulkStatus.total || safeCount}{' '}
                done
              </span>
            </div>
            <button
              type="button"
              className="operations-triage-bulk-stop"
              onClick={() => void handleStopBulkFix()}
            >
              Stop
            </button>
          </div>
        ) : null}
      </div>

      {/* ── Running now ────────────────────────────────────────────────── */}
      {activeRunningJob ? (
        <div className="operations-live-hud" role="status" aria-live="polite">
          <div className="operations-live-hud-head">
            <div className="operations-live-hud-radar">
              <span className="operations-live-hud-ping" />
              <span className="operations-live-hud-dot" />
            </div>
            <div className="operations-live-hud-title-wrap">
              <span className="operations-live-hud-badge">Running now</span>
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
                ⏹ Stop
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

      {/* ── Playbooks ──────────────────────────────────────────────────── */}
      <div className="operations-playbooks-section">
        <div className="operations-playbooks-header">
          <div className="operations-playbooks-title-group">
            <h5 className="operations-playbooks-title">Run a check</h5>
          </div>
          <p className="operations-playbooks-sub">
            Each one queues a handful of jobs together. Their findings land above.
          </p>
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
                <div className="operations-playbook-card-top">
                  <span className="operations-playbook-icon">{playbook.icon}</span>
                  <span className="operations-playbook-arrow">→</span>
                </div>
                <h6 className="operations-playbook-name">{playbook.title}</h6>
                <p className="operations-playbook-desc">{playbook.subtitle}</p>
                <div className="operations-playbook-action-row">
                  <span className="operations-playbook-cta">
                    {isRunning ? 'Queuing…' : '▶ Run'}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Areas ──────────────────────────────────────────────────────── */}
      <div className="operations-pillars-section">
        <div className="operations-pillars-header">
          <div className="operations-pillars-title-group">
            <h5 className="operations-pillars-title">Areas</h5>
          </div>
          <p className="operations-pillars-sub">
            The jobs grouped by what they look after. Put an area on its schedule, or check it now.
          </p>
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
            // the biggest open type in this area is where its badge goes
            const topType = groups
              .filter((g) => pillar.findingTypes.includes(g.finding_type) && (g.pending || 0) > 0)
              .sort((a, b) => (b.pending || 0) - (a.pending || 0))[0]?.finding_type;

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
                  ) : null}
                </div>

                <p className="operations-pillar-desc">{pillar.description}</p>

                <div className="operations-pillar-stats-row">
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">
                      {enabledCount}/{pillarJobs.length}
                    </span>
                    <span className="operations-pillar-stat-lbl">On schedule</span>
                  </div>
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">
                      {isRunning
                        ? 'Checking'
                        : allEnabled
                          ? 'Scheduled'
                          : enabledCount
                            ? 'Partly'
                            : 'Off'}
                    </span>
                    <span className="operations-pillar-stat-lbl">Status</span>
                  </div>
                  <div className="operations-pillar-stat">
                    <span className="operations-pillar-stat-val">
                      {openFindings > 0 ? `${openFindings.toLocaleString()} open` : 'Clean'}
                    </span>
                    <span className="operations-pillar-stat-lbl">Findings</span>
                  </div>
                </div>

                <div className="operations-pillar-actions">
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
                    {isRunning ? 'Checking…' : '▶ Check now'}
                  </button>
                </div>

                <div className="operations-pillar-footer">
                  <span className="operations-pillar-subjob-hint">
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
                    title="See each job's schedule and settings in Advanced"
                  >
                    Job settings →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Releases needing attention (music) ─────────────────────────── */}
      {config.spotlight ? (
        <div className="operations-spotlight-section">
          <div className="operations-spotlight-header">
            <div className="operations-spotlight-title-group">
              <h5 className="operations-spotlight-title">Albums with findings</h5>
            </div>
            <p className="operations-spotlight-sub">
              The album-level view of what the jobs found. Open one to work on it track by track.
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
      ) : null}

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
