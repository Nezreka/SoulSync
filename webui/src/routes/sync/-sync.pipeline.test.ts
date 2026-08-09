/**
 * The Auto-Sync pipeline core, run DIFFERENTIALLY against the real auto-sync.js
 * bodies.
 *
 * Both of the vanilla's two interesting functions are liftable: apply writes
 * into a `youtubePlaylistStates` object the harness supplies, and parse takes a
 * Response-shaped object. So neither side is a hand-copied expectation — the
 * comparison is against the code being replaced.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { MirroredPipelineState } from './-sync.pipeline';

import { extractFunction } from '../../test/vanilla-extract';
import {
  PIPELINE_POLL_MS,
  PIPELINE_ROUTES_MISSING,
  PIPELINE_RUN_FAILED,
  PIPELINE_STARTING_STATE,
  PIPELINE_STATUS_FAILED,
  SOURCE_REF_FAILED,
  SOURCE_REF_REQUIRED,
  applyPipelineState,
  pipelinePhaseForStatus,
  pipelineResponseError,
  pipelineStartedToast,
  pipelineStatusErrorToast,
  pipelineTickOutcome,
  sourceRefLabel,
  sourceRefPrompt,
  sourceRefUpdatedToast,
} from './-sync.pipeline';

const AUTOSYNC = readFileSync(resolve(process.cwd(), 'static/auto-sync.js'), 'utf8');

interface VanillaApply {
  applyMirroredPipelineState: (id: number, state: MirroredPipelineState) => void;
  youtubePlaylistStates: Record<string, Record<string, unknown>>;
  painted: [string, string | undefined][];
}

/** The vanilla apply, over a scratch state registry and a recording card writer. */
function loadApply(seed: Record<string, Record<string, unknown>> = {}): VanillaApply {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`
    const youtubePlaylistStates = ${JSON.stringify(seed)};
    const painted = [];
    const updateMirroredCardPhase = (hash, phase) => painted.push([hash, phase]);
    ${extractFunction('applyMirroredPipelineState', AUTOSYNC)}
    return { applyMirroredPipelineState, youtubePlaylistStates, painted };
  `)() as VanillaApply;
}

interface VanillaParse {
  parseMirroredPipelineResponse: (
    res: { ok: boolean; status: number; text: () => Promise<string> },
    fallback: string,
  ) => Promise<unknown>;
}

function loadParse(): VanillaParse {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`
    ${extractFunction('parseMirroredPipelineResponse', AUTOSYNC)}
    return { parseMirroredPipelineResponse };
  `)() as VanillaParse;
}

const fakeRes = (ok: boolean, status: number, text: string) => ({
  ok,
  status,
  text: async () => text,
});

describe('pipelinePhaseForStatus (2445-2449)', () => {
  it('maps the four known statuses and leaves everything else alone', () => {
    expect(pipelinePhaseForStatus('running')).toBe('pipeline_running');
    expect(pipelinePhaseForStatus('finished')).toBe('pipeline_complete');
    expect(pipelinePhaseForStatus('error')).toBe('pipeline_error');
    expect(pipelinePhaseForStatus('skipped')).toBe('pipeline_error');
    expect(pipelinePhaseForStatus('idle')).toBeUndefined();
    expect(pipelinePhaseForStatus('queued')).toBeUndefined();
    expect(pipelinePhaseForStatus(undefined)).toBeUndefined();
  });
});

describe('applyPipelineState — differential against applyMirroredPipelineState', () => {
  const STATES: MirroredPipelineState[] = [
    { status: 'running', progress: 40, phase: 'Discovering' },
    { status: 'finished', progress: 100, result: { queued: 3 } },
    { status: 'error', error: 'source unreachable' },
    { status: 'skipped', phase: 'Nothing to do' },
    { status: 'idle' },
    { status: 'queued', progress: 5 },
    {},
    { status: 'running', log: ['a', 'b'] },
    { status: 'running', progress: 0, phase: '', error: '', log: [], result: null },
  ];

  const EXISTING: Record<string, Record<string, unknown>>[] = [
    {},
    { mirrored_3: { phase: 'discovering', discoveryProgress: 40 } },
    { mirrored_3: { phase: 'sync_complete', convertedSpotifyPlaylistId: 'vp_1' } },
    { mirrored_3: {} },
  ];

  it('writes the same entry the vanilla writes, for every state × prior state', () => {
    for (const seed of EXISTING) {
      for (const state of STATES) {
        const V = loadApply(structuredClone(seed));
        V.applyMirroredPipelineState(3, state);
        const theirs = V.youtubePlaylistStates.mirrored_3;

        const existing = seed.mirrored_3 ?? {};
        const ours = {
          ...existing,
          ...applyPipelineState(existing.phase as string | undefined, state),
        };
        expect(ours).toEqual(theirs);
      }
    }
  });

  it('an idle pipeline does NOT clobber a card mid-discovery', () => {
    const V = loadApply({ mirrored_3: { phase: 'discovering' } });
    V.applyMirroredPipelineState(3, { status: 'idle' });
    expect(V.youtubePlaylistStates.mirrored_3.phase).toBe('discovering');
    expect(applyPipelineState('discovering', { status: 'idle' }).phase).toBe('discovering');
    // ...and with no prior phase there is simply none.
    expect(applyPipelineState(undefined, { status: 'idle' }).phase).toBeUndefined();
  });

  it('the phase write is what the vanilla hands the card renderer', () => {
    const V = loadApply({ mirrored_3: { phase: 'discovering' } });
    V.applyMirroredPipelineState(3, { status: 'running', progress: 12 });
    expect(V.painted).toEqual([['mirrored_3', 'pipeline_running']]);
  });

  it('every field defaults the way the vanilla defaults it', () => {
    expect(applyPipelineState(undefined, {})).toEqual({
      phase: undefined,
      pipeline_status: 'idle',
      pipeline_progress: 0,
      pipeline_phase: '',
      pipeline_error: '',
      pipeline_log: [],
      pipeline_result: null,
    });
  });
});

describe('pipelineTickOutcome (2500-2519)', () => {
  it('finished stops with a success toast and a list reload', () => {
    expect(pipelineTickOutcome({ status: 'finished' }, 'Road Trip')).toEqual({
      terminal: true,
      toast: { message: 'Auto-Sync complete for Road Trip', type: 'success' },
      reload: true,
    });
  });

  it('error and skipped both stop with the backend message, or a generic one', () => {
    expect(pipelineTickOutcome({ status: 'error', error: 'no source ref' }, 'RT')).toEqual({
      terminal: true,
      toast: { message: 'no source ref', type: 'error' },
      reload: true,
    });
    expect(pipelineTickOutcome({ status: 'skipped' }, 'RT').toast).toEqual({
      message: 'Pipeline stopped for RT',
      type: 'error',
    });
    expect(pipelineTickOutcome({ status: 'error' }, 'RT').toast?.message).toBe(
      'Pipeline stopped for RT',
    );
  });

  it('idle stops SILENTLY — no toast, no reload', () => {
    expect(pipelineTickOutcome({ status: 'idle' }, 'RT')).toEqual({
      terminal: true,
      reload: false,
    });
  });

  it('running and anything unrecognised keep polling', () => {
    expect(pipelineTickOutcome({ status: 'running' }, 'RT')).toEqual({
      terminal: false,
      reload: false,
    });
    expect(pipelineTickOutcome({}, 'RT').terminal).toBe(false);
  });
});

describe('pipelineResponseError — differential against parseMirroredPipelineResponse', () => {
  const CASES: [boolean, number, string, string][] = [
    [true, 200, '', PIPELINE_RUN_FAILED],
    [true, 200, '{"status":"running"}', PIPELINE_RUN_FAILED],
    [true, 200, '{"error":"busy"}', PIPELINE_RUN_FAILED],
    [false, 500, '{"error":"boom"}', PIPELINE_RUN_FAILED],
    [false, 500, '{}', PIPELINE_STATUS_FAILED],
    [false, 500, '', PIPELINE_STATUS_FAILED],
    [false, 404, '<!doctype html>', PIPELINE_RUN_FAILED],
    [true, 404, 'not json', PIPELINE_STATUS_FAILED],
    [false, 502, '<html>bad gateway</html>', PIPELINE_STATUS_FAILED],
  ];

  it('throws exactly what the vanilla throws, and passes exactly when it passes', async () => {
    const V = loadParse();
    for (const [ok, status, text, fallback] of CASES) {
      const ours = pipelineResponseError(ok, status, text, fallback);
      let theirs: string | null = null;
      try {
        await V.parseMirroredPipelineResponse(fakeRes(ok, status, text), fallback);
      } catch (err) {
        theirs = (err as Error).message;
      }
      expect(ours).toBe(theirs);
    }
  });

  it('an EMPTY body is not an error — it parses to {} and passes both guards', () => {
    expect(pipelineResponseError(true, 200, '', PIPELINE_RUN_FAILED)).toBeNull();
  });

  it('a 404 with unparseable body blames a stale server, not the parse', () => {
    expect(pipelineResponseError(false, 404, 'nope', PIPELINE_RUN_FAILED)).toBe(
      PIPELINE_ROUTES_MISSING,
    );
    expect(AUTOSYNC).toContain(
      'Auto-Sync endpoint not found. Restart the SoulSync server so the new backend routes load.',
    );
  });
});

describe('the source-ref edit (2410-2441)', () => {
  it('link-sourced mirrors are asked for a URL; everyone else for either', () => {
    expect(sourceRefLabel('spotify_public')).toBe('original playlist URL');
    expect(sourceRefLabel('youtube')).toBe('original playlist URL');
    expect(sourceRefLabel('tidal')).toBe('original playlist ID or URL');
    expect(sourceRefLabel('spotify')).toBe('original playlist ID or URL');
    expect(sourceRefLabel(undefined)).toBe('original playlist ID or URL');
    expect(AUTOSYNC).toContain("'original playlist URL'");
    expect(AUTOSYNC).toContain("'original playlist ID or URL'");
  });

  it('the question quotes the playlist name', () => {
    expect(sourceRefPrompt('youtube', 'Road Trip')).toBe(
      'Update original playlist URL for "Road Trip"',
    );
    expect(sourceRefPrompt('tidal', 'Road Trip')).toBe(
      'Update original playlist ID or URL for "Road Trip"',
    );
  });

  it('carries the vanilla messages', () => {
    expect(SOURCE_REF_REQUIRED).toBe('Source link or ID is required');
    expect(SOURCE_REF_FAILED).toBe('Failed to update source reference');
    expect(sourceRefUpdatedToast('Road Trip')).toBe('Updated source for Road Trip');
    expect(AUTOSYNC).toContain('Source link or ID is required');
    expect(AUTOSYNC).toContain('Failed to update source reference');
    expect(AUTOSYNC).toContain('Updated source for ');
  });
});

describe('constants and toasts', () => {
  it('the optimistic start state is the vanilla literal', () => {
    expect(PIPELINE_STARTING_STATE).toEqual({
      status: 'running',
      progress: 0,
      phase: 'Starting pipeline...',
    });
    expect(AUTOSYNC).toContain("{ status: 'running', progress: 0, phase: 'Starting pipeline...' }");
  });

  it('polls every 2.5s', () => {
    expect(PIPELINE_POLL_MS).toBe(2500);
    expect(AUTOSYNC).toContain('setInterval(tick, 2500)');
  });

  it('names the playlist in both toasts', () => {
    expect(pipelineStartedToast('Road Trip')).toBe('Auto-Sync started for Road Trip');
    expect(pipelineStatusErrorToast('boom')).toBe('Pipeline status error: boom');
    expect(AUTOSYNC).toContain('Auto-Sync started for ');
    expect(AUTOSYNC).toContain('Pipeline status error: ');
  });

  it('the two fallback messages are the vanilla call-site strings', () => {
    expect(PIPELINE_RUN_FAILED).toBe('Failed to start Auto-Sync');
    expect(PIPELINE_STATUS_FAILED).toBe('Failed to read Auto-Sync status');
    expect(AUTOSYNC).toContain("'Failed to start Auto-Sync'");
    expect(AUTOSYNC).toContain("'Failed to read Auto-Sync status'");
  });
});
