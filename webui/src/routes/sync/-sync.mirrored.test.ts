/**
 * The mirrored card's pure core, pinned against stats-automations.js.
 * timeAgo runs differentially against the REAL vanilla body.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { MirroredPlaylistRow } from './-sync.mirrored';

import { extractFunction } from '../../test/vanilla-extract';
import {
  MIRRORED_SOURCE_ICONS,
  mirroredHash,
  mirroredPhaseLine,
  mirroredRatio,
  mirroredSourceIcon,
  pipelinePhaseFor,
  timeAgo,
} from './-sync.mirrored';

const STATS = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

describe('mirroredSourceIcon (571-572)', () => {
  it('maps the five known sources and falls back to the clipboard', () => {
    expect(mirroredSourceIcon('spotify')).toBe('🎵');
    expect(mirroredSourceIcon('tidal')).toBe('🌊');
    expect(mirroredSourceIcon('youtube')).toBe('▶');
    expect(mirroredSourceIcon('beatport')).toBe('🎛');
    expect(mirroredSourceIcon('file')).toBe('📄');
    expect(mirroredSourceIcon('listenbrainz')).toBe('📋');
    expect(mirroredSourceIcon(undefined)).toBe('📋');
  });

  it('the icon table matches the vanilla literal', () => {
    expect(STATS).toContain(
      "{ spotify: '🎵', tidal: '🌊', youtube: '▶', beatport: '🎛', file: '📄' }",
    );
    expect(Object.keys(MIRRORED_SOURCE_ICONS)).toEqual([
      'spotify',
      'tidal',
      'youtube',
      'beatport',
      'file',
    ]);
  });
});

describe('pipelinePhaseFor (530-533)', () => {
  const row = (status?: string): MirroredPlaylistRow =>
    ({ id: 1, pipeline_state: status ? { status } : null }) as MirroredPlaylistRow;

  it('running/finished map to their phases; error AND skipped both mean error', () => {
    expect(pipelinePhaseFor(row('running'))).toBe('pipeline_running');
    expect(pipelinePhaseFor(row('finished'))).toBe('pipeline_complete');
    expect(pipelinePhaseFor(row('error'))).toBe('pipeline_error');
    expect(pipelinePhaseFor(row('skipped'))).toBe('pipeline_error');
  });

  it('no pipeline_state, or an unknown status, yields null', () => {
    expect(pipelinePhaseFor(row())).toBeNull();
    expect(pipelinePhaseFor(row('queued'))).toBeNull();
    expect(pipelinePhaseFor({ id: 1 } as MirroredPlaylistRow)).toBeNull();
  });
});

describe('mirroredPhaseLine — the unified renderer', () => {
  const row = { id: 1, track_count: 25 } as MirroredPlaylistRow;

  it('paints every phase with the vanilla text and inline colour', () => {
    expect(mirroredPhaseLine('pipeline_running', { pipeline_progress: 40 }, row)).toEqual({
      text: 'Pipeline running 40%',
      color: '#38bdf8',
    });
    expect(
      mirroredPhaseLine(
        'pipeline_running',
        { pipeline_phase: 'Discovering', pipeline_progress: 5 },
        row,
      ),
    ).toEqual({ text: 'Discovering 5%', color: '#38bdf8' });
    expect(mirroredPhaseLine('pipeline_complete', null, row)).toEqual({
      text: 'Pipeline complete',
      color: '#22c55e',
    });
    expect(mirroredPhaseLine('pipeline_error', null, row)).toEqual({
      text: 'Pipeline error',
      color: '#ef4444',
    });
    expect(mirroredPhaseLine('syncing', null, row)).toEqual({
      text: 'Syncing...',
      color: '#3b82f6',
    });
    expect(mirroredPhaseLine('sync_complete', null, row)).toEqual({
      text: 'Synced',
      color: '#3b82f6',
    });
    expect(mirroredPhaseLine('downloading', null, row)).toEqual({
      text: 'Downloading...',
      color: '#f59e0b',
    });
    expect(mirroredPhaseLine('download_complete', null, row)).toEqual({
      text: 'Downloaded',
      color: '#22c55e',
    });
  });

  it('the DECLARED unification: discovering keeps its percent (the live writer drops it)', () => {
    // renderMirroredCard 557-558 shows the percent; updateMirroredCardPhase
    // 853 shows a bare 'Discovering...'. We take the former.
    expect(mirroredPhaseLine('discovering', { discoveryProgress: 40 }, row)).toEqual({
      text: 'Discovering 40%',
      color: '#a78bfa',
    });
    expect(STATS).toContain('Discovering ${pct}%');
    expect(STATS).toContain('>Discovering...<');
  });

  it('the DECLARED unification: discovered falls back to track_count (the live writer does not)', () => {
    // renderMirroredCard 561: spotify_total || p.track_count.
    // updateMirroredCardPhase 857: spotify_total || 0.
    expect(mirroredPhaseLine('discovered', { spotifyMatches: 3 }, row)).toEqual({
      text: 'Discovered 3/25',
      color: '#22c55e',
    });
    expect(mirroredPhaseLine('discovered', { spotifyMatches: 3, spotifyTotal: 10 }, row)).toEqual({
      text: 'Discovered 3/10',
      color: '#22c55e',
    });
  });

  it('no phase, or an unknown one, renders nothing', () => {
    expect(mirroredPhaseLine(null, null, row)).toBeNull();
    expect(mirroredPhaseLine('fresh', null, row)).toBeNull();
  });
});

describe('mirroredRatio (575-582)', () => {
  it('hidden until something is discovered', () => {
    expect(mirroredRatio({ id: 1, discovered_count: 0, track_count: 9 }, 'Spotify')).toBeNull();
    expect(mirroredRatio({ id: 1, track_count: 9 }, 'Spotify')).toBeNull();
  });

  it('total prefers total_count, and complete flips at disc >= tot', () => {
    expect(
      mirroredRatio({ id: 1, discovered_count: 4, total_count: 9, track_count: 3 }, 'Spotify'),
    ).toEqual({ text: '4/9 discovered on Spotify', complete: false });
    expect(mirroredRatio({ id: 1, discovered_count: 9, total_count: 9 }, 'Spotify')).toEqual({
      text: '9/9 discovered on Spotify',
      complete: true,
    });
    // track_count is the fallback when total_count is absent.
    expect(mirroredRatio({ id: 1, discovered_count: 2, track_count: 5 }, 'Plex')).toEqual({
      text: '2/5 discovered on Plex',
      complete: false,
    });
  });
});

describe('timeAgo (1045-1061) — differential against the vanilla body', () => {
  const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);
  // The real vanilla body with its one clock call substituted, so both sides
  // measure from the same instant. Everything else runs verbatim.
  const vanillaSource = extractFunction('timeAgo', STATS).replace('Date.now()', 'NOW');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const vanillaTimeAgo = new Function('NOW', `${vanillaSource}; return timeAgo;`)(NOW) as (
    d: string,
  ) => string;

  it('agrees with the vanilla across every threshold', () => {
    {
      const cases = [
        '',
        '2026-01-15T11:59:58Z',
        '2026-01-15T11:59:30Z',
        '2026-01-15T11:30:00Z',
        '2026-01-15T06:00:00Z',
        '2026-01-10T12:00:00Z',
        // the d→mo boundary: 29d, exactly 30d, 31d, 45d
        '2025-12-17T12:00:00Z',
        '2025-12-16T12:00:00Z',
        '2025-12-15T12:00:00Z',
        '2025-12-01T12:00:00Z',
        // inputs that land BETWEEN thresholds, so each bound discriminates
        '2026-01-15T11:59:56Z',
        '2026-01-15T11:05:00Z',
        '2026-01-14T14:00:00Z',
        '2025-11-01T12:00:00Z',
        // the s→m, m→h and h→d edges
        '2026-01-15T11:59:00Z',
        '2026-01-15T11:00:00Z',
        '2026-01-14T12:00:00Z',
        '2025-10-15T12:00:00Z',
        '2026-01-15T11:30:00',
        '2026-01-15T11:30:00+00:00',
      ];
      for (const c of cases) {
        expect(timeAgo(c, NOW), `input ${c || '(empty)'}`).toBe(vanillaTimeAgo(c));
      }
    }
  });

  it('pins the boundary wording', () => {
    expect(timeAgo('2026-01-15T11:59:58Z', NOW)).toBe('just now');
    expect(timeAgo('2026-01-15T11:59:30Z', NOW)).toBe('30s ago');
    expect(timeAgo('2026-01-15T11:30:00Z', NOW)).toBe('30m ago');
    expect(timeAgo('2026-01-15T06:00:00Z', NOW)).toBe('6h ago');
    expect(timeAgo('2026-01-10T12:00:00Z', NOW)).toBe('5d ago');
    expect(timeAgo('2025-10-15T12:00:00Z', NOW)).toBe('3mo ago');
    // 29 days is still days; 30 flips to months (days/30 floors to 1).
    expect(timeAgo('2025-12-17T12:00:00Z', NOW)).toBe('29d ago');
    expect(timeAgo('2025-12-16T12:00:00Z', NOW)).toBe('1mo ago');
    // and the other three edges
    expect(timeAgo('2026-01-15T11:59:00Z', NOW)).toBe('1m ago');
    expect(timeAgo('2026-01-15T11:00:00Z', NOW)).toBe('1h ago');
    expect(timeAgo('2026-01-14T12:00:00Z', NOW)).toBe('1d ago');
    // between-threshold values: each one moves if its bound moves.
    expect(timeAgo('2026-01-15T11:59:56Z', NOW)).toBe('just now'); // 4s, bound is 5
    expect(timeAgo('2026-01-15T11:05:00Z', NOW)).toBe('55m ago'); // 55m, bound is 60
    expect(timeAgo('2026-01-14T14:00:00Z', NOW)).toBe('22h ago'); // 22h, bound is 24
    expect(timeAgo('2025-11-01T12:00:00Z', NOW)).toBe('2mo ago'); // 75d, divisor is 30
    expect(timeAgo(null, NOW)).toBe('');
  });

  it('a bare ISO string is read as UTC, not local', () => {
    // The 'Z' is appended only when there is no Z, no '+', and no '-' at or
    // after index 10 — the date's own hyphens must not count.
    expect(timeAgo('2026-01-15T11:30:00', NOW)).toBe('30m ago');
    expect(timeAgo('2026-01-15T11:30:00+00:00', NOW)).toBe('30m ago');
  });
});

describe('mirroredHash', () => {
  it('carries the marker INSIDE the id — it is never a prepended prefix', () => {
    expect(mirroredHash(3)).toBe('mirrored_3');
    expect(STATS).toContain('`mirrored_${p.id}`');
  });
});
