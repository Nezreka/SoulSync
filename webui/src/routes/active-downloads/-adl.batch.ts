/**
 * Pure batch-panel computation: the aggregate strip, the segmented bar, phase
 * copy and stat chips. Kept out of the component so each rule is testable on
 * its own — several are arithmetic that is easy to get subtly wrong.
 */

import type { RateSample } from './-adl.helpers';
import type { AdlBatch, AdlDownload } from './-adl.types';

import {
  bundleProgressPercent,
  bundleProgressText,
  formatDuration,
  sampleRate,
} from './-adl.helpers';

/** Phases with nothing left to do. */
export const TERMINAL_PHASES = ['complete', 'cancelled', 'error'];

export function isTerminalPhase(phase: string): boolean {
  return TERMINAL_PHASES.includes(phase);
}

/** A batch is "live" when work is actually moving, which drives the glow. */
export function isBatchActive(batch: AdlBatch): boolean {
  return (batch.phase === 'downloading' && batch.active > 0) || batch.phase === 'album_downloading';
}

export interface BatchSummary {
  /** `3 batches · 2 downloading · 5 queued · 1.2 MB/s` */
  main: string;
  /** `~4m left`, or '' when there is nothing to estimate from. */
  eta: string;
}

/**
 * The aggregate strip above the batch cards.
 *
 * Rates are SUMMED across batches — three batches each finishing a track a
 * second finish three a second between them, so the combined ETA has to divide
 * the combined remainder by the combined rate, not average anything.
 *
 * Only the FIRST release-download speed is shown: they are pre-formatted
 * strings from different downloaders and cannot be added together.
 */
export function batchSummary(
  activeBatches: AdlBatch[],
  samplesFor: (batchId: string) => RateSample[],
  now: number,
): BatchSummary | null {
  if (!activeBatches.length) return null;

  let downloading = 0;
  let queued = 0;
  let remaining = 0;
  let rate = 0;
  let bundleSpeed = '';

  for (const batch of activeBatches) {
    downloading += batch.active || 0;
    queued += batch.queued || 0;
    if (batch.phase === 'downloading') {
      const done = (batch.completed || 0) + (batch.failed || 0);
      remaining += Math.max(0, (batch.total || 0) - done);
      rate += sampleRate(samplesFor(batch.batch_id), done, now);
    }
    if (batch.phase === 'album_downloading' && batch.album_bundle?.speed && !bundleSpeed) {
      bundleSpeed = batch.album_bundle.speed;
    }
  }

  const parts = [`${activeBatches.length} batch${activeBatches.length === 1 ? '' : 'es'}`];
  if (downloading) parts.push(`${downloading} downloading`);
  if (queued) parts.push(`${queued} queued`);
  if (bundleSpeed) parts.push(bundleSpeed);

  return {
    main: parts.join(' · '),
    eta: rate > 0 && remaining > 0 ? `~${formatDuration(remaining / rate)} left` : '',
  };
}

export interface PhaseDisplay {
  text: string;
  /** 'spinner' | 'check' | 'hourglass' | null — the component renders the node. */
  icon: 'spinner' | 'check' | 'hourglass' | null;
}

/**
 * The line under a batch's name.
 *
 * `queued` is its own phase and says "Queued", NOT "Analyzing...". The executor
 * runs 3 workers by default, so a wishlist run with more sub-batches parks the
 * rest here — labelling those as analysing made 26 parked batches look like 26
 * running ones.
 */
export function phaseDisplay(batch: AdlBatch): PhaseDisplay {
  const total = batch.total || 1;
  switch (batch.phase) {
    case 'queued':
      return { text: 'Queued', icon: 'hourglass' };
    case 'analysis':
      return { text: 'Analyzing...', icon: 'spinner' };
    case 'album_downloading':
      return { text: bundleProgressText(batch.album_bundle), icon: 'spinner' };
    case 'downloading':
      return {
        text: `${batch.completed}/${total} tracks`,
        // No spinner when nothing is actually in flight.
        icon: batch.active > 0 ? 'spinner' : null,
      };
    case 'complete':
      return { text: `Done — ${batch.completed} tracks`, icon: 'check' };
    case 'cancelled':
      return { text: 'Cancelled', icon: null };
    case 'error':
      return { text: 'Error', icon: null };
    default:
      return { text: batch.phase, icon: null };
  }
}

export interface ProgressSegments {
  done: number;
  failed: number;
  active: number;
}

/**
 * Widths for the three-segment progress bar, as percentages.
 *
 * Each segment is clamped against what the ones before it already used, so
 * they can never total more than 100% and overflow the bar — which is
 * reachable, because a batch's counters can briefly disagree with its total
 * while tasks move between states.
 *
 * A release download has one file in flight and no per-track counts, so the
 * whole bar is its bundle progress.
 */
export function progressSegments(batch: AdlBatch): ProgressSegments {
  if (batch.phase === 'album_downloading') {
    return { done: 0, failed: 0, active: bundleProgressPercent(batch.album_bundle) };
  }
  const total = batch.total || 1;
  const done = Math.max(0, Math.min(100, ((batch.completed || 0) / total) * 100));
  const failed = Math.max(0, Math.min(100 - done, ((batch.failed || 0) / total) * 100));
  const active = Math.max(0, Math.min(100 - done - failed, ((batch.active || 0) / total) * 100));
  return { done, failed, active };
}

export interface StatChip {
  className: string;
  text: string;
}

/** Only non-zero counts get a chip, so an idle batch shows none. */
export function statChips(batch: AdlBatch): StatChip[] {
  const chips: StatChip[] = [];
  if (batch.completed)
    chips.push({ className: 'adl-chip adl-chip-done', text: `✓ ${batch.completed}` });
  if (batch.failed) chips.push({ className: 'adl-chip adl-chip-fail', text: `✗ ${batch.failed}` });
  if (batch.active)
    chips.push({ className: 'adl-chip adl-chip-active', text: `↓ ${batch.active}` });
  if (batch.queued)
    chips.push({ className: 'adl-chip adl-chip-queued', text: `${batch.queued} queued` });
  return chips;
}

/**
 * The track a batch is working on right now.
 *
 * Prefers a downloading track over a searching one — both are "active", but
 * the downloading one is the more informative thing to name.
 */
export function nowTrack(batch: AdlBatch, batchTracks: AdlDownload[]): AdlDownload | null {
  if (!isBatchActive(batch)) return null;
  return (
    batchTracks.find((t) => t.status === 'downloading') ??
    batchTracks.find((t) => t.status === 'searching') ??
    null
  );
}

export interface TrackRowState {
  /** '42%' | 'proc' | '✓' | '✗' | '·' — or null when a spinner belongs there. */
  text: string | null;
  spinner: boolean;
  title?: string;
}

/** The right-hand state marker on an expanded batch's track row. */
export function trackRowState(track: AdlDownload, statusClassOf: string): TrackRowState {
  const progress = track.progress || 0;
  if (track.status === 'downloading' && progress > 0) {
    return { text: `${Math.round(progress)}%`, spinner: false };
  }
  if (track.status === 'searching') return { text: null, spinner: true };
  if (track.status === 'post_processing')
    return { text: 'proc', spinner: false, title: 'Processing' };
  if (statusClassOf === 'completed') return { text: '✓', spinner: false };
  if (statusClassOf === 'failed') {
    return { text: '✗', spinner: false, title: track.error || 'Failed' };
  }
  return { text: '·', spinner: false };
}

/** A mini progress bar shows only while there is real progress to show. */
export function showTrackProgressBar(track: AdlDownload): boolean {
  return track.status === 'downloading' && (track.progress || 0) > 0;
}

/** `3d ago` / `5h ago` / `just now`; '' when there is no timestamp. */
export function historyAgo(completedAt: string | undefined, now: number): string {
  if (!completedAt) return '';
  const then = new Date(completedAt).getTime();
  if (!Number.isFinite(then)) return '';
  const hours = Math.floor((now - then) / 3_600_000);
  if (hours < 1) return 'just now';
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** Source → dot colour on a history row; unknown sources get plain white. */
export function historyDotColor(sourcePage: string | undefined): string {
  const colors: Record<string, string> = {
    wishlist: '168, 85, 247',
    sync: '59, 130, 246',
    album: '16, 185, 129',
  };
  return colors[String(sourcePage ?? '')] || '255, 255, 255';
}
