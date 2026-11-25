import { Dialog } from '@base-ui/react/dialog';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { useEffect, useRef, useState } from 'react';

import { apiClient } from '@/app/api-client';
import { DialogFrame } from '@/components/dialog';
import { appURL } from '@/platform/url-base';

import styles from './note-sonar.module.css';

/**
 * note sonar: the button in the search field that names a song playing nearby.
 *
 * a clip is recorded here (or a file is picked or dropped on the sheet) and
 * posted to /api/listen/identify, which asks Shazam. the answer opens the
 * song's own analysis box, the same one a search result's download button
 * opens, and when that box can't open the search runs for "artist title".
 *
 * every capture or upload gets an operation number. close, a new capture and
 * a file all bump it, so a permission prompt, a recorder stop event or a fetch
 * that lands after the user moved on can neither upload nor paint the sheet.
 * a file picked during a capture takes over first: the recorder is torn down
 * with its handlers detached, and only the file is sent.
 *
 * the microphone needs a secure context (https). over plain http the sheet
 * says so and offers the upload instead. Shazam is named as the third party
 * that gets the clip before any capture starts or any file is read.
 */

export const DISCLOSURE =
  'Your clip is sent to Shazam, a third-party service from Apple, to be identified. ' +
  'SoulSync does not keep the clip.';

const CLIP_MS = 9000; // Shazam needs a few seconds; 9 covers a noisy room
const SILENCE_RMS = 0.004; // quieter than this and the clip is called out as silent
const READY_TIMEOUT_MS = 8000;
const METER_BARS = 15;
const MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/ogg',
  'audio/mp4;codecs=mp4a.40.2',
  'audio/mp4',
];

export type Identification = {
  title?: string;
  artist?: string;
  album?: string;
  cover_url?: string;
  shazam_id?: string;
  isrc?: string;
  duration_s?: number;
  released?: string;
};

export type MatchRow = {
  id?: string | number;
  name?: string;
  artist?: string;
  artists?: unknown[];
  album?: string;
  album_id?: string | null;
  image_url?: string;
  images?: Array<{ url?: string } | string>;
  source?: string | null;
  duration_ms?: number | null;
  release_date?: string | null;
  popularity?: number;
};

export type IdentifyPayload = {
  success?: boolean;
  identification?: Identification | null;
  match?: MatchRow | null;
  in_library?: boolean | null;
  query?: string;
  message?: string;
  error?: string;
};

type Readiness = { ok: boolean; reason: string };

type Phase =
  | 'closed'
  | 'nomic' // no secure context: upload only
  | 'preflight' // asking the server before the first capture
  | 'unavailable'
  | 'ready'
  | 'starting'
  | 'listening'
  | 'identifying'
  | 'failed';

type Note = { kind: 'http' } | { kind: 'plain'; text: string } | null;

export type SonarView = {
  open: boolean;
  phase: Phase;
  title: string;
  status: string;
  error: boolean;
  note: Note;
};

const CLOSED: SonarView = {
  open: false,
  phase: 'closed',
  title: 'Identify a song',
  status: 'Ready when you are.',
  error: false,
  note: null,
};

function isAbort(error: unknown): boolean {
  const name = (error as { name?: string } | null)?.name;
  return name === 'AbortError' || (error as { code?: number } | null)?.code === 20;
}

function stopTracks(stream: MediaStream | null) {
  if (!stream || typeof stream.getTracks !== 'function') return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      // already gone
    }
  }
}

export function micUsable(): boolean {
  return Boolean(
    window.isSecureContext &&
    typeof navigator.mediaDevices?.getUserMedia === 'function' &&
    typeof window.MediaRecorder === 'function',
  );
}

function pickMimeType(): string {
  const Recorder = window.MediaRecorder as typeof MediaRecorder | undefined;
  if (!Recorder || !('isTypeSupported' in Recorder)) return '';
  return MIME_TYPES.find((type) => Recorder.isTypeSupported(type)) ?? '';
}

function extensionFor(type: string): string {
  if (type.includes('mp4')) return 'm4a';
  if (type.includes('ogg')) return 'ogg';
  return 'webm';
}

/** Is the recogniser there? Never throws: a failure is a reason. */
export async function fetchListenStatus(signal?: AbortSignal): Promise<Readiness> {
  try {
    const response = await apiClient.get('listen/status', {
      throwHttpErrors: false,
      timeout: READY_TIMEOUT_MS,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal,
    });
    const body = (await response.json().catch(() => ({}))) as {
      available?: boolean;
      reason?: string;
      message?: string;
    };
    if (!response.ok) {
      return { ok: false, reason: body.message || `The recogniser answered ${response.status}.` };
    }
    return {
      ok: body.available === true,
      reason: body.reason || 'Song identification is unavailable on this server.',
    };
  } catch (error) {
    const stopped = isAbort(error) || (error as { name?: string })?.name === 'TimeoutError';
    return {
      ok: false,
      reason: stopped
        ? 'The readiness check was cancelled.'
        : 'The recogniser status could not be checked.',
    };
  }
}

/**
 * The clip goes up as multipart with its size declared (the server refuses a
 * chunked upload), which is what fetch does with a FormData body. The server's
 * own words are what the sheet shows when it says no.
 */
async function postClip(form: FormData, signal: AbortSignal): Promise<IdentifyPayload> {
  const response = await fetch(appURL('/api/listen/identify'), {
    method: 'POST',
    body: form,
    signal,
    credentials: 'same-origin',
  });
  const body = (await response.json().catch(() => ({}))) as IdentifyPayload;
  if (!response.ok || body.success === false) {
    throw new Error(body.message || `The server answered ${response.status}.`);
  }
  return body;
}

/**
 * The arguments for window.openDownloadMissingModalForArtistAlbum, in the same
 * shape the search page's own track download builds (enrichSingleTrack), with
 * the same virtual id, so both paths share one process instead of two boxes.
 * Null when there is no name to show.
 */
type ItemBoxArgs = [string, string, unknown[], unknown, unknown, boolean];

export function itemBoxArgs(
  ident: Identification,
  match: MatchRow | null | undefined,
): ItemBoxArgs | null {
  const row: MatchRow = match || {};
  const name = row.name || ident.title;
  const artist = row.artist || ident.artist || '';
  if (!name) return null;
  const albumName = row.album || ident.album || name;
  const first = row.images?.[0];
  const image =
    row.image_url ||
    (first ? (typeof first === 'string' ? first : first.url) || '' : '') ||
    ident.cover_url ||
    '';
  const images = image ? [{ url: image }] : [];
  const trackId = String(row.id || `shazam_${ident.shazam_id || ident.isrc || name}`);

  let durationMs = row.duration_ms || null;
  if (!durationMs && typeof ident.duration_s === 'number') durationMs = ident.duration_s * 1000;

  const releaseDate = row.release_date || ident.released || null;
  const track = {
    id: trackId,
    name,
    source: row.source || null,
    artists: row.artists && row.artists.length ? row.artists : artist ? [artist] : [],
    album: {
      name: albumName,
      id: row.album_id || null,
      album_type: 'single',
      images,
      release_date: releaseDate,
      total_tracks: 1,
    },
    duration_ms: durationMs,
    popularity: row.popularity || 0,
    preview_url: null,
    external_urls: null,
    image_url: image,
  };
  const album = {
    name: albumName,
    id: null,
    album_type: 'single',
    images,
    release_date: releaseDate,
    total_tracks: 1,
    artists: [{ name: artist }],
  };
  return [
    `enhanced_search_track_${trackId}`,
    artist ? `${artist} - ${name}` : name,
    [track],
    album,
    { id: null, name: artist },
    false,
  ];
}

/** Opening the box writes nothing: the process stays idle until a button in it is pressed. */
function openItemBox(ident: Identification, match: MatchRow | null | undefined): boolean {
  const open = window.openDownloadMissingModalForArtistAlbum;
  if (typeof open !== 'function') return false;
  const args = itemBoxArgs(ident, match);
  if (!args) return false;
  try {
    void Promise.resolve(open(...args)).catch((error: unknown) => {
      console.debug('[listen] analysis box failed:', error);
    });
    return true;
  } catch (error) {
    console.debug('[listen] analysis box unavailable:', error);
    return false;
  }
}

type SonarHooks = {
  render: (view: SonarView) => void;
  bars: () => HTMLElement[];
  identified: (ident: Identification, payload: IdentifyPayload, query: string) => void;
};

/**
 * The capture and upload state machine. Plain code, not React state: the
 * recorder, the stream and the timers belong to one operation at a time, and
 * the callbacks that come back late have to check that synchronously.
 */
function createSonar(hooks: SonarHooks) {
  const s = {
    stream: null as MediaStream | null,
    recorder: null as MediaRecorder | null,
    audioCtx: null as AudioContext | null,
    analyser: null as AnalyserNode | null,
    raf: 0,
    timer: 0,
    countdown: 0,
    chunks: [] as Blob[],
    peak: 0,
    busy: false,
    open: false,
    op: 0,
    abort: null as AbortController | null,
    readyAbort: null as AbortController | null,
  };
  let view = CLOSED;

  const emit = (patch: Partial<SonarView>) => {
    view = { ...view, ...patch };
    hooks.render(view);
  };
  const isCurrent = (op: number) => s.open && op === s.op;

  function resetMeter() {
    for (const bar of hooks.bars()) {
      bar.style.height = '3px';
      bar.style.opacity = '0.55';
    }
  }

  /** a fresh sheet in the given phase */
  function reset(phase: Phase, status = 'Ready when you are.') {
    s.peak = 0;
    s.chunks = [];
    resetMeter();
    emit({ open: true, phase, status, error: false, title: 'Identify a song', note: null });
  }

  function abortRequest() {
    s.abort?.abort();
    s.abort = null;
  }

  function abortReadiness() {
    s.readyAbort?.abort();
    s.readyAbort = null;
  }

  async function checkReadiness(): Promise<Readiness> {
    abortReadiness();
    const ctrl = new AbortController();
    s.readyAbort = ctrl;
    const ready = await fetchListenStatus(ctrl.signal);
    if (s.readyAbort === ctrl) s.readyAbort = null;
    return ready;
  }

  function teardown() {
    window.clearTimeout(s.timer);
    window.clearTimeout(s.countdown);
    s.timer = 0;
    s.countdown = 0;
    if (s.raf) {
      window.cancelAnimationFrame(s.raf);
      s.raf = 0;
    }
    const recorder = s.recorder;
    s.recorder = null;
    if (recorder) {
      // detach BEFORE stop: a cancel or a file takeover must never let a late
      // onstop turn into an upload
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
      if (recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch {
          // already stopping
        }
      }
    }
    if (s.audioCtx) {
      void s.audioCtx.close().catch(() => {});
      s.audioCtx = null;
    }
    s.analyser = null;
    stopTracks(s.stream);
    s.stream = null;
    s.busy = false;
  }

  function fail(message: string) {
    s.busy = false;
    emit({ phase: 'failed', status: message, error: true, title: 'No identification' });
  }

  function open() {
    s.open = true;
    const op = ++s.op; // dismisses anything the previous open left running
    abortRequest();
    abortReadiness();
    teardown();
    s.busy = false;
    if (!micUsable()) {
      reset('nomic', 'The microphone needs a secure address (https).');
      emit({ note: { kind: 'http' } });
      return;
    }
    reset('preflight', 'Checking the recogniser…');
    void checkReadiness().then((ready) => {
      if (!isCurrent(op) || view.phase !== 'preflight') return;
      if (!ready.ok) {
        const reason = ready.reason || 'Song identification is unavailable.';
        emit({
          phase: 'unavailable',
          status: reason,
          error: true,
          note: { kind: 'plain', text: reason },
        });
        return;
      }
      emit({ phase: 'ready', status: 'Ready when you are.' });
    });
  }

  function close() {
    s.open = false;
    s.op += 1;
    s.busy = false;
    abortRequest();
    abortReadiness();
    teardown();
    emit({ open: false, phase: 'closed' });
  }

  function startListening() {
    if (!micUsable()) {
      fail('This browser cannot record audio. Try uploading a clip.');
      return;
    }
    if (s.busy) return;
    const op = ++s.op;
    abortRequest();
    abortReadiness();
    teardown();
    reset('starting', 'Checking the recogniser…');
    s.busy = true;
    void checkReadiness().then((ready) => {
      if (!isCurrent(op) || view.phase !== 'starting') return;
      if (!ready.ok) {
        fail(ready.reason || 'Song identification is unavailable.');
        return;
      }
      requestMic(op);
    });
  }

  function micFailed(error: unknown) {
    const err = error as { name?: string; message?: string } | null;
    const name = err?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      fail('Microphone access was refused. Allow it for this site, or upload a clip.');
    } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      fail('No microphone was found. Upload a clip instead.');
    } else {
      fail(`The microphone could not be opened: ${err?.message || name || 'unknown error'}`);
    }
  }

  function requestMic(op: number) {
    emit({ status: 'Waiting for the microphone…' });
    let request: Promise<MediaStream>;
    try {
      request = navigator.mediaDevices.getUserMedia({
        // echo cancelling and auto-gain were built for speech and chew music up,
        // and Shazam answers less often on processed audio
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch (error) {
      s.busy = false;
      micFailed(error);
      return;
    }
    request.then(
      (stream) => {
        // cancelled (or replaced by a file) while the permission prompt was up:
        // the browser still hands over a live stream, and it belongs to nobody
        if (!isCurrent(op)) {
          stopTracks(stream);
          return;
        }
        attachStream(stream, op);
      },
      (error: unknown) => {
        if (!isCurrent(op)) return;
        s.busy = false;
        micFailed(error);
      },
    );
  }

  function attachStream(stream: MediaStream, op: number) {
    s.stream = stream;
    s.chunks = [];
    s.peak = 0;
    s.busy = false;

    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (error) {
      console.error('[listen] MediaRecorder refused:', error);
      stopTracks(stream);
      s.stream = null;
      fail('This browser could not record audio. Try uploading a clip.');
      return;
    }

    s.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (!isCurrent(op) || s.recorder !== recorder) return;
      if (event.data && event.data.size) s.chunks.push(event.data);
    };
    recorder.onstop = () => finishRecording(recorder, mimeType, op);
    recorder.onerror = (event) => {
      if (!isCurrent(op) || s.recorder !== recorder) return;
      const message =
        (event as Event & { error?: { message?: string } }).error?.message ||
        'The recording stopped unexpectedly.';
      teardown();
      fail(message);
    };

    startMeter(stream, op);
    try {
      recorder.start();
    } catch (error) {
      console.error('[listen] start failed:', error);
      teardown();
      fail('The recording could not be started. Try again.');
      return;
    }

    emit({ phase: 'listening', status: 'Listening…' });
    window.clearTimeout(s.timer);
    s.timer = window.setTimeout(() => {
      if (isCurrent(op) && view.phase === 'listening') stopRecording();
    }, CLIP_MS);
    countdown(op);
  }

  function countdown(op: number) {
    const start = Date.now();
    const total = Math.round(CLIP_MS / 1000);
    const tick = () => {
      if (!isCurrent(op) || view.phase !== 'listening') return;
      const remain = Math.ceil(total - (Date.now() - start) / 1000);
      emit({ status: remain > 0 ? `Listening… ${remain}s` : 'Listening…' });
      s.countdown = remain > 0 ? window.setTimeout(tick, 500) : 0;
    };
    tick();
  }

  function startMeter(stream: MediaStream, op: number) {
    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      s.audioCtx = ctx;
      s.analyser = analyser;
      const data = new Uint8Array(analyser.frequencyBinCount);

      const frame = () => {
        // a stale frame must not read the NEXT capture's analyser
        if (!isCurrent(op) || s.analyser !== analyser || s.audioCtx !== ctx) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const sample of data) {
          const v = (sample - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        if (rms > s.peak) s.peak = rms;
        hooks.bars().forEach((bar, index) => {
          // a little wobble so the meter reads as live, not as a bar graph
          const jitter = 0.75 + 0.5 * Math.random();
          const height = 3 + Math.min(31, rms * 190 * jitter * (1 + index * 0.06));
          bar.style.height = `${height.toFixed(1)}px`;
          bar.style.opacity = rms > SILENCE_RMS ? '0.95' : '0.4';
        });
        s.raf = window.requestAnimationFrame(frame);
      };
      frame();
    } catch (error) {
      console.debug('[listen] level meter unavailable:', error);
    }
  }

  function stopRecording() {
    const recorder = s.recorder;
    if (!recorder || recorder.state !== 'recording' || view.phase !== 'listening') return;
    window.clearTimeout(s.timer);
    window.clearTimeout(s.countdown);
    s.timer = 0;
    s.countdown = 0;
    emit({ phase: 'identifying', status: 'Identifying…' });
    try {
      recorder.stop(); // onstop finishes the clip
    } catch (error) {
      console.error('[listen] stop failed:', error);
      teardown();
      fail('The recording could not be stopped. Try again.');
    }
  }

  function finishRecording(recorder: MediaRecorder, mimeType: string, op: number) {
    // only the operation that owns the recorder may turn it into an upload.
    // `listening` counts because a track can end on its own (device unplugged)
    if (!isCurrent(op) || s.recorder !== recorder) return;
    if (view.phase !== 'identifying' && view.phase !== 'listening') return;

    const type = recorder.mimeType || mimeType || 'audio/webm';
    let blob: Blob;
    try {
      blob = new Blob(s.chunks, { type });
    } catch {
      teardown();
      fail('The recording could not be read. Try again.');
      return;
    }
    const peak = s.peak;
    teardown();
    if (!isCurrent(op)) return;
    if (!blob.size) {
      fail('Nothing was recorded. Check the microphone and try again.');
      return;
    }
    if (peak < SILENCE_RMS) {
      emit({
        note: {
          kind: 'plain',
          text:
            'That clip was almost silent — the microphone may be muted, ' +
            'or the volume too low for the recogniser to hear the song.',
        },
      });
    }
    const form = new FormData();
    form.append('audio', blob, `clip.${extensionFor(type)}`);
    submitForm(form, op);
  }

  function uploadFile(file: File) {
    // a file is a whole source on its own: take ownership before the recorder
    // can stop, so its clip is never sent as well
    const op = ++s.op;
    s.open = true;
    abortRequest();
    abortReadiness();
    teardown();
    reset('identifying', 'Identifying…');
    const form = new FormData();
    form.append('audio', file, file.name || 'clip');
    submitForm(form, op);
  }

  function submitForm(form: FormData, op: number) {
    abortRequest();
    const ctrl = new AbortController();
    s.abort = ctrl;
    s.busy = true;
    emit({ phase: 'identifying', status: 'Identifying…' });

    postClip(form, ctrl.signal).then(
      (payload) => {
        if (!isCurrent(op)) return;
        if (s.abort === ctrl) s.abort = null;
        s.busy = false;
        settled(payload);
      },
      (error: unknown) => {
        if (!isCurrent(op) || isAbort(error)) return;
        if (s.abort === ctrl) s.abort = null;
        s.busy = false;
        console.error('[listen] identification failed:', error);
        fail((error as Error)?.message || 'The identification failed.');
      },
    );
  }

  function settled(payload: IdentifyPayload) {
    const ident = payload.identification;
    if (!ident) {
      fail(payload.message || 'The song was not recognised.');
      return;
    }
    const query = payload.query || [ident.artist, ident.title].filter(Boolean).join(' ');
    close();
    hooks.identified(ident, payload, query);
  }

  return {
    open,
    close,
    startListening,
    stopRecording,
    uploadFile,
    phase: () => view.phase,
  };
}

type Action = { label: string; kind?: 'primary' | 'ghost'; upload?: boolean; run: () => void };

/**
 * The search field's song-recognition button, and its sheet. Hidden while the
 * server says the recogniser isn't there.
 */
export function NoteSonar({ onSearch }: { onSearch: (query: string) => void }) {
  const status = useQuery({
    queryKey: ['listen-status'],
    queryFn: ({ signal }) => fetchListenStatus(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const available = status.data?.ok === true;

  const [view, setView] = useState<SonarView>(CLOSED);
  const [dragging, setDragging] = useState(false);
  const barsRef = useRef<Array<HTMLElement | null>>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const onSearchRef = useRef(onSearch);
  onSearchRef.current = onSearch;

  const [sonar] = useState(() =>
    createSonar({
      render: setView,
      bars: () => barsRef.current.filter((bar): bar is HTMLElement => bar !== null),
      identified: (ident, payload, query) => {
        const who = ident.artist ? `${ident.artist} — ` : '';
        const owned = payload.in_library ? ' (already in your library)' : '';
        const message = `Identified: ${who}${ident.title ?? ''}${owned}`;
        if (typeof window.showToast === 'function') window.showToast(message, 'success');
        else console.log('[listen]', message);
        // the song's own analysis box first, the search only if that can't open
        if (!openItemBox(ident, payload.match)) onSearchRef.current(query);
      },
    }),
  );

  // leaving the page lets go of the microphone and drops any late answer
  useEffect(() => () => sonar.close(), [sonar]);

  // a file dropped anywhere while the sheet is up is the clip
  useEffect(() => {
    if (!view.open) return;
    const hasFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes('Files');
    const over = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      setDragging(true);
    };
    const leave = (event: DragEvent) => {
      if (!event.relatedTarget) setDragging(false);
    };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      setDragging(false);
      const file = event.dataTransfer?.files?.[0];
      if (file) sonar.uploadFile(file);
    };
    document.addEventListener('dragover', over);
    document.addEventListener('dragleave', leave);
    document.addEventListener('drop', drop);
    return () => {
      document.removeEventListener('dragover', over);
      document.removeEventListener('dragleave', leave);
      document.removeEventListener('drop', drop);
      setDragging(false);
    };
  }, [view.open, sonar]);

  const chooseFile = () => fileRef.current?.click();
  const usable = micUsable();
  const actions = actionsFor(view.phase, usable, sonar, chooseFile);
  // an Upload button already says it; the footer would be the same action twice
  const showAlt = !actions.some((action) => action.upload) && view.phase !== 'nomic';
  const maskUrl = `url("${appURL('/static/note-sonar.png')}")`;
  const markStyle = { maskImage: maskUrl, WebkitMaskImage: maskUrl };
  const listening = view.phase === 'listening';

  return (
    <>
      {available ? (
        <button
          type="button"
          className={styles.mic}
          id="listen-mic-btn"
          title="Identify a song playing nearby (Shazam)"
          aria-label="Identify the song playing nearby"
          onClick={() => sonar.open()}
        >
          <span className={styles.mark} style={markStyle} aria-hidden="true" />
        </button>
      ) : null}
      <DialogFrame
        open={view.open}
        onOpenChange={(next) => {
          if (!next) sonar.close();
        }}
        viewportClassName={clsx(styles.overlay, dragging && styles.dragging)}
        className={styles.sheet}
      >
        <button
          type="button"
          className={styles.close}
          aria-label="Close"
          onClick={() => sonar.close()}
        >
          ×
        </button>
        <button
          type="button"
          className={clsx(styles.orb, listening && styles.orbListening)}
          aria-label={listening ? 'Stop and identify' : undefined}
          aria-hidden={listening ? undefined : true}
          tabIndex={listening ? 0 : -1}
          onClick={() => {
            if (sonar.phase() === 'listening') sonar.stopRecording();
          }}
        >
          <span className={styles.mark} style={markStyle} aria-hidden="true" />
        </button>
        <Dialog.Title className={styles.title}>{view.title}</Dialog.Title>
        <p
          className={clsx(styles.status, view.error && styles.statusError)}
          role="status"
          aria-live="polite"
        >
          {view.status}
        </p>
        {listening ? (
          <div className={styles.meter} aria-hidden="true">
            {Array.from({ length: METER_BARS }, (_, index) => (
              <b
                key={index}
                className={styles.bar}
                ref={(node) => {
                  barsRef.current[index] = node;
                }}
              />
            ))}
          </div>
        ) : null}
        <p className={styles.disclosure}>{DISCLOSURE}</p>
        <div className={styles.actions}>
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              className={clsx(
                styles.btn,
                action.kind === 'primary' && styles.primary,
                action.kind === 'ghost' && styles.ghost,
              )}
              onClick={action.run}
            >
              {action.label}
            </button>
          ))}
        </div>
        {view.note?.kind === 'http' ? (
          <div className={styles.note}>
            Browsers refuse microphone capture on a plain <code>http://</code> address. Over https —
            this server&apos;s own https address, for example — the microphone opens here. Until
            then a clip you already have can be identified instead.
          </div>
        ) : view.note?.kind === 'plain' ? (
          <div className={styles.note}>{view.note.text}</div>
        ) : null}
        {showAlt ? (
          <div className={styles.alt}>
            or{' '}
            <button type="button" className={styles.altLink} onClick={chooseFile}>
              upload a clip
            </button>
          </div>
        ) : null}
        <input
          ref={fileRef}
          type="file"
          accept="audio/*,video/*"
          hidden
          aria-label="Audio clip"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = '';
            if (file) sonar.uploadFile(file);
          }}
        />
      </DialogFrame>
    </>
  );
}

function actionsFor(
  phase: Phase,
  usable: boolean,
  sonar: ReturnType<typeof createSonar>,
  chooseFile: () => void,
): Action[] {
  const cancel: Action = { label: 'Cancel', kind: 'ghost', run: () => sonar.close() };
  const closeIt: Action = { label: 'Close', kind: 'ghost', run: () => sonar.close() };
  switch (phase) {
    case 'nomic':
      return [{ label: 'Upload a clip', kind: 'primary', upload: true, run: chooseFile }];
    case 'unavailable':
      return [{ label: 'Upload a clip', kind: 'primary', upload: true, run: chooseFile }, closeIt];
    case 'ready':
      return [
        { label: 'Start listening', kind: 'primary', run: () => sonar.startListening() },
        { label: 'Upload a clip', upload: true, run: chooseFile },
      ];
    case 'preflight':
    case 'starting':
      return [cancel];
    case 'listening':
      return [{ label: 'Identify now', kind: 'primary', run: () => sonar.stopRecording() }, cancel];
    case 'failed':
      return [
        ...(usable
          ? [{ label: 'Try again', kind: 'primary' as const, run: () => sonar.startListening() }]
          : []),
        {
          label: 'Upload a clip',
          kind: usable ? undefined : ('primary' as const),
          upload: true,
          run: chooseFile,
        },
        closeIt,
      ];
    default:
      return [];
  }
}
