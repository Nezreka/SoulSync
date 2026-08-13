/**
 * The two mirrored-card actions that live in auto-sync.js: the Auto-Sync
 * pipeline button (2358-2525) and the 🔗 source-ref edit (2410-2441).
 *
 * "Auto-Sync" here is the ONE-SHOT run of a playlist's pipeline: refresh from
 * the source, discover, sync, queue what is missing. The scheduled version of
 * the same thing lives on the Auto-Sync board, which is its own wave — the
 * three board calls the vanilla makes after a successful start
 * (_autoSyncScheduleState bookkeeping, renderAutoSyncScheduleModal,
 * manageAutoSyncStatusPolling — 2478-2483) have nothing to update until that
 * board exists, and are noted at the call site rather than silently dropped.
 *
 * The pipeline phases layer OVER the seven-phase machine: writing one leaves
 * every other field of the per-hash state alone, and an 'idle' status leaves
 * even the phase alone (2445-2449).
 */

/** The pipeline_state the run/status endpoints return. */
export interface MirroredPipelineState {
  status?: string;
  progress?: number;
  phase?: string;
  error?: string;
  log?: string[];
  result?: unknown;
}

/** What the run POST assumes when the backend answered without a state (2475). */
export const PIPELINE_STARTING_STATE: MirroredPipelineState = {
  status: 'running',
  progress: 0,
  phase: 'Starting pipeline...',
};

/** The status poll's cadence (2524). */
export const PIPELINE_POLL_MS = 2500;

/**
 * A pipeline status → the card phase it forces.
 *
 * undefined means KEEP whatever phase the state already had: 'idle' and any
 * unrecognised status fall through the vanilla's if/else chain without
 * assigning, so a card mid-discovery is not reset by an idle pipeline.
 */
export function pipelinePhaseForStatus(status: string | undefined): string | undefined {
  if (status === 'running') return 'pipeline_running';
  if (status === 'finished') return 'pipeline_complete';
  if (status === 'error' || status === 'skipped') return 'pipeline_error';
  return undefined;
}

/** The six pipeline_* fields applyMirroredPipelineState writes (2451-2460). */
export interface PipelineStatePatch {
  phase?: string;
  pipeline_status: string;
  pipeline_progress: number;
  pipeline_phase: string;
  pipeline_error: string;
  pipeline_log: string[];
  pipeline_result: unknown;
}

/**
 * The state write. `existingPhase` is carried through untouched when the
 * status maps to no phase — the vanilla seeds `let phase = existing.phase`
 * and only reassigns on a recognised status.
 */
export function applyPipelineState(
  existingPhase: string | undefined,
  state: MirroredPipelineState,
): PipelineStatePatch {
  const status = state.status || 'idle';
  return {
    phase: pipelinePhaseForStatus(status) ?? existingPhase,
    pipeline_status: status,
    pipeline_progress: state.progress || 0,
    pipeline_phase: state.phase || '',
    pipeline_error: state.error || '',
    pipeline_log: state.log || [],
    pipeline_result: state.result || null,
  };
}

export interface PipelineTickOutcome {
  /** false → the interval keeps running. */
  terminal: boolean;
  toast?: { message: string; type: 'success' | 'error' };
  /** Whether the list is refetched — 'idle' stops silently, without one. */
  reload: boolean;
}

/**
 * What one status tick does after the state is applied (2500-2519).
 *
 * Three ways to stop: finished (success toast), error/skipped (error toast),
 * and idle — which stops with NO toast and NO reload. Idle means the backend
 * has no record of a run, so there is nothing to report or refresh.
 *
 * VANILLA QUIRK, preserved: `state.error` is unreachable here in practice.
 * parseMirroredPipelineResponse rejects ANY body carrying an `error` key
 * (2372), so a `{status: 'error', error: '...'}` status never reaches this
 * function — it throws, and the poller's catch reports "Pipeline status
 * error: ..." and stops WITHOUT reloading. The arm below therefore fires only
 * for `skipped`, or for an `error` status with no message, and its `||`
 * fallback is what the user actually sees.
 */
export function pipelineTickOutcome(
  state: MirroredPipelineState,
  name: string,
): PipelineTickOutcome {
  if (state.status === 'finished') {
    return {
      terminal: true,
      toast: { message: `Auto-Sync complete for ${name}`, type: 'success' },
      reload: true,
    };
  }
  if (state.status === 'error' || state.status === 'skipped') {
    return {
      terminal: true,
      toast: { message: state.error || `Pipeline stopped for ${name}`, type: 'error' },
      reload: true,
    };
  }
  if (state.status === 'idle') {
    return { terminal: true, reload: false };
  }
  return { terminal: false, reload: false };
}

/** The toast a thrown status tick raises, then the poller stops (2521-2523). */
export function pipelineStatusErrorToast(message: string): string {
  return `Pipeline status error: ${message}`;
}

/** The toast a successful start raises (2477). */
export function pipelineStartedToast(name: string): string {
  return `Auto-Sync started for ${name}`;
}

/* ── Response parsing (parseMirroredPipelineResponse, 2358-2375) ──────────── */

export const PIPELINE_RUN_FAILED = 'Failed to start Auto-Sync';
export const PIPELINE_STATUS_FAILED = 'Failed to read Auto-Sync status';

/**
 * A 404 whose body is not JSON means the server predates these routes — the
 * vanilla says so explicitly rather than reporting a parse failure, because
 * SoulSync runs from a working tree and a stale process is the likely cause.
 */
export const PIPELINE_ROUTES_MISSING =
  'Auto-Sync endpoint not found. Restart the SoulSync server so the new backend routes load.';

/**
 * The error a pipeline response should throw, or null when it is good.
 *
 * The vanilla reads the body as TEXT first: an EMPTY body is not an error (it
 * parses to `{}` and passes both guards), but a non-empty body that is not
 * JSON is — and which message that gets depends on the status code.
 */
export function pipelineResponseError(
  ok: boolean,
  status: number,
  text: string,
  fallback: string,
): string | null {
  let data: { error?: string } = {};
  if (text) {
    try {
      data = JSON.parse(text) as { error?: string };
    } catch {
      return status === 404 ? PIPELINE_ROUTES_MISSING : fallback;
    }
  }
  if (!ok || data.error) return data.error || fallback;
  return null;
}

/* ── The 🔗 source-ref edit (editMirroredSourceRef, 2410-2441) ────────────── */

/**
 * What the field is asking for. spotify_public and youtube mirrors are created
 * FROM a link and have no meaningful bare id, so they are asked for a URL;
 * every other source accepts either (2411-2413).
 */
export function sourceRefLabel(source: string | null | undefined): string {
  return source === 'spotify_public' || source === 'youtube'
    ? 'original playlist URL'
    : 'original playlist ID or URL';
}

/** The prompt's question (2414). */
export function sourceRefPrompt(source: string | null | undefined, name: string): string {
  return `Update ${sourceRefLabel(source)} for "${name}"`;
}

/** Submitting an empty value is rejected client-side (2418). */
export const SOURCE_REF_REQUIRED = 'Source link or ID is required';

export const SOURCE_REF_FAILED = 'Failed to update source reference';

export function sourceRefUpdatedToast(name: string): string {
  return `Updated source for ${name}`;
}
