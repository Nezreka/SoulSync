import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  PreviewResult,
  SampleAnalysis,
  SamplePeaks,
  StashEntry,
  StemName,
  StudioTrack,
} from '../-sample-studio.types';

import {
  previewAudioUrl,
  requestPreview,
  stemAudioUrl,
  studioStreamUrl,
} from '../-sample-studio.api';
import {
  barBeatAt,
  barStartTime,
  beatInterval,
  clamp,
  downsamplePeaks,
  formatTime,
  visiblePeakSlice,
} from '../-sample-studio.helpers';
import { ChopTray } from './chop-tray';
import { PitchTempoPanel } from './pitch-tempo-panel';
import styles from './sample-studio-page.module.css';
import { SaveDialog } from './save-dialog';

interface WaveformEditorProps {
  track: StudioTrack;
  analysis: SampleAnalysis | undefined;
  analysisPending: boolean;
  analysisError: string | null;
  onRetryAnalysis: () => void;
  peaks: SamplePeaks | undefined;
  /** Which stem to edit/chop from — null means the full mix. */
  stemSource: StemName | null;
  onSavedStashEntry: (entry: StashEntry) => void;
}

const WAVE_HEIGHT = 220;
const MINIMAP_HEIGHT = 44;
const AMBER = '#f5b942';

/** Local playback bounds: the <audio> element may be the full track, a processed
 *  preview, or a one-shot slice audition. `offset` maps preview-local seconds
 *  back onto track time for the waveform playhead. */
interface PlaybackBounds {
  start: number;
  end: number;
  offset: number;
  seekMax: number;
}

export function WaveformEditor({
  track,
  analysis,
  analysisPending,
  analysisError,
  onRetryAnalysis,
  peaks,
  stemSource,
  onSavedStashEntry,
}: WaveformEditorProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const minimapRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef(0);
  const metroCtxRef = useRef<AudioContext | null>(null);
  const lastBeatRef = useRef(-1);
  const dragHandleRef = useRef<'in' | 'out' | null>(null);
  const previewTimerRef = useRef<number | null>(null);

  /** The unprocessed source: the selected stem, or the full mix. */
  const originalAudioUrl = useCallback(() => {
    return stemSource ? stemAudioUrl(track.id, stemSource) : studioStreamUrl(track.file_path ?? '');
  }, [track.id, track.file_path, stemSource]);

  const duration = peaks?.duration_s ?? analysis?.duration_s ?? track.duration ?? 0;
  const bpm = analysis?.bpm ?? track.bpm ?? null;

  const [view, setView] = useState({ start: 0, end: 0 });
  const [inPoint, setInPoint] = useState(0);
  const [outPoint, setOutPoint] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loopOn, setLoopOn] = useState(true);
  const [metroOn, setMetroOn] = useState(false);
  const [playhead, setPlayhead] = useState(0);

  // Phase 3: pitch/tempo audition + slice audition + save flow.
  const [pitchSt, setPitchSt] = useState(0);
  const [targetBpm, setTargetBpm] = useState<number | null>(null);
  const [mode, setMode] = useState<'original' | 'preview'>('original');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewRendering, setPreviewRendering] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [slicePreview, setSlicePreview] = useState<{
    id: string;
    duration: number;
    start: number;
  } | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // Progressive disclosure: the common path (play, loop, save) stays up
  // front; edit tools and pitch/tempo tuck behind toggles.
  const [toolsOpen, setToolsOpen] = useState(false);
  const [fxOpen, setFxOpen] = useState(false);

  // Refs mirror state for the rAF draw loop (avoids stale closures).
  const viewRef = useRef(view);
  const inRef = useRef(inPoint);
  const outRef = useRef(outPoint);
  const loopRef = useRef(loopOn);
  const metroRef = useRef(metroOn);
  const playingRef = useRef(playing);
  const modeRef = useRef(mode);
  const previewRef = useRef(preview);
  const sliceRef = useRef(slicePreview);
  const pitchRef = useRef(pitchSt);
  const targetBpmRef = useRef(targetBpm);
  const stemRef = useRef(stemSource);
  viewRef.current = view;
  inRef.current = inPoint;
  outRef.current = outPoint;
  loopRef.current = loopOn;
  metroRef.current = metroOn;
  playingRef.current = playing;
  modeRef.current = mode;
  previewRef.current = preview;
  sliceRef.current = slicePreview;
  pitchRef.current = pitchSt;
  targetBpmRef.current = targetBpm;
  stemRef.current = stemSource;

  /** Current local bounds for the <audio> element, given mode/slice state. */
  const playbackBounds = useCallback((): PlaybackBounds => {
    const slice = sliceRef.current;
    if (slice)
      return { start: 0, end: slice.duration, offset: slice.start, seekMax: slice.duration };
    const pv = previewRef.current;
    if (modeRef.current === 'preview' && pv) {
      return { start: 0, end: pv.duration_s, offset: inRef.current, seekMax: pv.duration_s };
    }
    return { start: inRef.current, end: outRef.current, offset: 0, seekMax: duration };
  }, [duration]);

  // Reset when the track changes.
  useEffect(() => {
    const d = peaks?.duration_s ?? analysis?.duration_s ?? track.duration ?? 0;
    setView({ start: 0, end: d });
    setInPoint(0);
    setOutPoint(d);
    setPlayhead(0);
    setPlaying(false);
    setPitchSt(0);
    setTargetBpm(null);
    setMode('original');
    setPreview(null);
    setPreviewErr(null);
    setPreviewRendering(false);
    setSlicePreview(null);
    setSaveOpen(false);
    if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    lastBeatRef.current = -1;
    const audio = audioRef.current;
    if (audio && track.file_path) {
      audio.src = originalAudioUrl();
      audio.load();
    }
  }, [
    track.id,
    track.file_path,
    stemSource,
    peaks?.duration_s,
    analysis?.duration_s,
    track.duration,
  ]);

  /** Point the <audio> element at the right source for the current mode. */
  const applyAudioSrc = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !track.file_path) return;
    const slice = sliceRef.current;
    const pv = previewRef.current;
    const next =
      slice != null
        ? previewAudioUrl(slice.id)
        : modeRef.current === 'preview' && pv != null
          ? previewAudioUrl(pv.preview_id)
          : originalAudioUrl();
    if (audio.getAttribute('src') !== next) {
      const wasPlaying = playingRef.current;
      audio.pause();
      audio.src = next;
      audio.load();
      if (wasPlaying) void audio.play().catch(() => setPlaying(false));
    }
  }, [originalAudioUrl]);

  useEffect(() => {
    applyAudioSrc();
  }, [track.id, mode, preview, slicePreview, stemSource, applyAudioSrc]);

  /** Render (or re-render) the processed preview of the in/out region. */
  const renderPreviewNow = useCallback(async () => {
    const start = inRef.current;
    const end = outRef.current;
    if (!(end > start) || !track.file_path) return;
    setPreviewRendering(true);
    setPreviewErr(null);
    try {
      const pv = await requestPreview(track.id, {
        start,
        end,
        pitchSt: pitchRef.current,
        targetBpm: targetBpmRef.current,
        stem: stemRef.current,
      });
      setPreview(pv);
    } catch (e) {
      setPreviewErr(e instanceof Error ? e.message : 'Preview failed');
      // Fall back to the original so transport never goes silent.
      setMode('original');
    } finally {
      setPreviewRendering(false);
    }
  }, [track.id, track.file_path]);

  // A preview is a render of the old region — moving in/out retires it.
  useEffect(() => {
    setPreview(null);
    setPreviewErr(null);
    setSlicePreview(null);
    sliceRef.current = null;
    if (modeRef.current === 'preview') setMode('original');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inPoint, outPoint]);

  /** Pitch/tempo change: hop to processed mode and debounce the re-render. */
  const onFxParamsChange = (nextPitch: number, nextBpm: number | null) => {
    setPitchSt(nextPitch);
    setTargetBpm(nextBpm);
    pitchRef.current = nextPitch;
    targetBpmRef.current = nextBpm;
    setSlicePreview(null);
    sliceRef.current = null;
    setMode('preview');
    setPreviewErr(null);
    if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    previewTimerRef.current = window.setTimeout(() => void renderPreviewNow(), 600);
  };

  const switchMode = (next: 'original' | 'preview') => {
    setSlicePreview(null);
    sliceRef.current = null;
    if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
    setMode(next);
    if (next === 'preview' && !previewRef.current) void renderPreviewNow();
  };

  /** One-shot audition of a chop-tray slice (neutral pitch/tempo). */
  const auditionSlice = (start: number, end: number) => {
    if (!(end > start) || !track.file_path) return;
    audioRef.current?.pause();
    setPlaying(false);
    void (async () => {
      try {
        const pv = await requestPreview(track.id, {
          start,
          end,
          pitchSt: 0,
          targetBpm: null,
          stem: stemSource,
        });
        setSlicePreview({ id: pv.preview_id, duration: pv.duration_s, start });
        // The src-sync effect swaps the <audio> source on re-render; start it after.
        window.setTimeout(() => {
          const a = audioRef.current;
          if (a && sliceRef.current?.id === pv.preview_id) {
            a.currentTime = 0;
            void a.play().then(
              () => setPlaying(true),
              () => setPlaying(false),
            );
          }
        }, 60);
      } catch (e) {
        setPreviewErr(e instanceof Error ? e.message : 'Slice audition failed');
      }
    })();
  };

  const mergeSlices = (start: number, end: number) => {
    setInPoint(start);
    setOutPoint(end);
    // The in/out effect retires the preview and slice.
  };

  /** A suggested chop becomes the loop region (the guided happy path). */
  const useSuggestion = useCallback((start: number, end: number) => {
    setInPoint(start);
    setOutPoint(Math.max(end, start + 0.05));
    if (audioRef.current) {
      audioRef.current.currentTime = start;
      setPlayhead(start);
    }
  }, []);

  const click = useCallback((accent: boolean) => {
    try {
      let ctx = metroCtxRef.current;
      if (!ctx) {
        ctx = new AudioContext();
        metroCtxRef.current = ctx;
      }
      if (ctx.state === 'suspended') void ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = accent ? 1568 : 1046; // G6 / C6
      gain.gain.setValueAtTime(accent ? 0.25 : 0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.06);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.07);
    } catch {
      // Metronome is best-effort; never break transport.
    }
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const pk = peaks;
    if (!canvas || !pk) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = WAVE_HEIGHT;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const v = viewRef.current;
    const span = Math.max(0.001, v.end - v.start);
    const pxPerSec = w / span;
    const t2x = (t: number) => ((t - v.start) / span) * w;

    // Waveform — slice to the visible window first so a zoomed view shows
    // the zoomed audio, then resample to the canvas width.
    const slice = visiblePeakSlice(pk.min, pk.max, pk.duration_s, v.start, v.end);
    const { min, max } = downsamplePeaks(slice.min, slice.max, Math.max(1, Math.floor(w)));
    ctx.fillStyle = '#a8a49a';
    const mid = h / 2;
    for (let x = 0; x < min.length; x++) {
      const y1 = mid - max[x] * (h * 0.46);
      const y2 = mid - min[x] * (h * 0.46);
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }

    // Beat grid.
    const interval = beatInterval(bpm);
    if (interval) {
      const firstBeat = Math.ceil(v.start / interval);
      const lastBeat = Math.floor(v.end / interval);
      ctx.textBaseline = 'top';
      for (let b = firstBeat; b <= lastBeat; b++) {
        const x = t2x(b * interval);
        const isBar = b % 4 === 0;
        ctx.strokeStyle = isBar ? 'rgba(245,185,66,0.5)' : 'rgba(255,255,255,0.14)';
        ctx.lineWidth = isBar ? 1.5 : 1;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
        // Numbered bars once zoomed in enough to read them.
        if (isBar && pxPerSec > 28) {
          ctx.fillStyle = '#f5b942';
          ctx.font = '11px system-ui';
          ctx.fillText(String(b / 4 + 1), x + 4, 4);
        }
      }
    }

    // Onset markers (transient ticks along the top).
    if (analysis && analysis.onsets.length > 0) {
      ctx.fillStyle = 'rgba(245,185,66,0.85)';
      for (const t of analysis.onsets) {
        if (t < v.start || t > v.end) continue;
        ctx.fillRect(t2x(t) - 1, 0, 2, 8);
      }
    }

    // Dim outside the in/out region.
    const ix = t2x(inRef.current);
    const ox = t2x(outRef.current);
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    if (ix > 0) ctx.fillRect(0, 0, Math.min(w, ix), h);
    if (ox < w) ctx.fillRect(Math.max(0, ox), 0, w - Math.max(0, ox), h);

    // Playhead — the <audio> element may hold a preview or slice, so map
    // its local time back onto track time with the bounds offset.
    const audio = audioRef.current;
    const pb = playbackBounds();
    const now = audio ? audio.currentTime + pb.offset : 0;
    if (now >= v.start && now <= v.end) {
      const x = t2x(now);
      ctx.strokeStyle = AMBER;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
  }, [peaks, bpm, analysis]);

  // Redraw on state changes.
  useEffect(() => {
    draw();
  }, [draw, view, inPoint, outPoint, playhead]);

  // rAF loop while playing: playhead, loop wrap, edge-follow, metronome.
  useEffect(() => {
    if (!playing) {
      cancelAnimationFrame(rafRef.current);
      return;
    }
    const tick = () => {
      const audio = audioRef.current;
      if (!audio) return;
      const pb = playbackBounds();
      const local = audio.currentTime;
      const now = local + pb.offset;
      // Loop wrap within the current bounds (in/out region, preview, or slice).
      if (loopRef.current && local >= pb.end && pb.end > pb.start) {
        audio.currentTime = pb.start;
      }
      // Edge-follow: keep the playhead on screen while zoomed.
      const v = viewRef.current;
      if (now > v.end - 0.15 * (v.end - v.start) || now < v.start) {
        const span = v.end - v.start;
        const full = duration || span;
        const start = clamp(now - span * 0.2, 0, Math.max(0, full - span));
        const nv = { start, end: start + span };
        viewRef.current = nv;
        setView(nv);
      }
      // Metronome clicks on beat crossings (original audio only — a processed
      // preview no longer lines up with the source grid).
      if (metroRef.current && modeRef.current === 'original' && !sliceRef.current) {
        const interval = beatInterval(bpm);
        if (interval) {
          const beatIdx = Math.floor(now / interval);
          if (beatIdx !== lastBeatRef.current) {
            lastBeatRef.current = beatIdx;
            click(beatIdx % 4 === 0);
          }
        }
      }
      setPlayhead(now);
      draw();
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [playing, draw, bpm, duration, click]);

  // Minimap.
  useEffect(() => {
    const canvas = minimapRef.current;
    if (!canvas || !peaks || !duration) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = MINIMAP_HEIGHT;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const { min, max } = downsamplePeaks(peaks.min, peaks.max, Math.max(1, Math.floor(w)));
    ctx.fillStyle = '#6e6b63';
    const mid = h / 2;
    for (let x = 0; x < min.length; x++) {
      const y1 = mid - max[x] * (h * 0.46);
      const y2 = mid - min[x] * (h * 0.46);
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
    // Viewport rect.
    const vx = (view.start / duration) * w;
    const vw = ((view.end - view.start) / duration) * w;
    ctx.strokeStyle = AMBER;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(vx, 1, Math.max(2, vw), h - 2);
  }, [peaks, duration, view]);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !track.file_path) return;
    if (playingRef.current) {
      audio.pause();
      setPlaying(false);
    } else {
      const pb = playbackBounds();
      // If the playhead sits outside the current bounds, start at their start.
      if (audio.currentTime < pb.start || audio.currentTime >= pb.end) {
        audio.currentTime = pb.start;
      }
      void audio.play().then(
        () => setPlaying(true),
        () => setPlaying(false),
      );
    }
  }, [track.file_path, playbackBounds]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onEnded = () => setPlaying(false);
    const onPause = () => setPlaying(false);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('pause', onPause);
    return () => {
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('pause', onPause);
    };
  }, [track.id]);

  const seekTo = useCallback(
    (clientX: number) => {
      const wrap = wrapRef.current;
      const audio = audioRef.current;
      if (!wrap || !audio || !duration) return;
      // Clicking the waveform ends a slice audition: clear it and point the
      // <audio> element back at the mode's source *before* seeking, or the
      // seek lands on the outgoing slice and is lost on the src swap.
      if (sliceRef.current) {
        sliceRef.current = null;
        setSlicePreview(null);
        applyAudioSrc();
      }
      const rect = wrap.getBoundingClientRect();
      const frac = clamp((clientX - rect.left) / rect.width, 0, 1);
      const v = viewRef.current;
      const pb = playbackBounds();
      audio.currentTime = clamp(v.start + frac * (v.end - v.start) - pb.offset, 0, pb.seekMax);
      setPlayhead(audio.currentTime + pb.offset);
      draw();
    },
    [duration, draw, playbackBounds, applyAudioSrc],
  );

  const zoom = useCallback(
    (factor: number) => {
      const v = viewRef.current;
      const span = v.end - v.start;
      const center = clamp(playhead, v.start, v.end);
      const anchor = span > 0 ? center : (v.start + v.end) / 2;
      const newSpan = clamp(span * factor, 1, duration || span);
      const start = clamp(anchor - newSpan / 2, 0, Math.max(0, (duration || newSpan) - newSpan));
      const nv = { start, end: start + newSpan };
      viewRef.current = nv;
      setView(nv);
    },
    [playhead, duration],
  );

  const setLoopBars = useCallback(
    (bars: number) => {
      const interval = beatInterval(bpm);
      if (!interval || !duration) return;
      const at = barBeatAt(playhead, bpm);
      const startBar = at ? at.bar : 1;
      const start = barStartTime(startBar, bpm) ?? 0;
      const end = clamp(start + bars * 4 * interval, 0, duration);
      setInPoint(start);
      setOutPoint(Math.max(end, start + interval));
      if (audioRef.current) {
        audioRef.current.currentTime = start;
        setPlayhead(start);
      }
    },
    [bpm, duration, playhead],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === ' ') {
        e.preventDefault();
        togglePlay();
      } else if (e.key === 'i' || e.key === 'I') {
        const t = audioRef.current?.currentTime ?? 0;
        setInPoint(Math.min(t, outRef.current - 0.05));
      } else if (e.key === 'o' || e.key === 'O') {
        const t = audioRef.current?.currentTime ?? 0;
        setOutPoint(Math.max(t, inRef.current + 0.05));
      } else if (e.key === '+' || e.key === '=') {
        zoom(0.5);
      } else if (e.key === '-' || e.key === '_') {
        zoom(2);
      } else if (e.key === '?') {
        setShortcutsOpen(true);
      }
    },
    [togglePlay, zoom],
  );

  // Handle dragging (pointer capture on the handle divs).
  const onHandlePointerDown = (which: 'in' | 'out') => (e: React.PointerEvent) => {
    e.stopPropagation();
    dragHandleRef.current = which;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onHandlePointerMove = (e: React.PointerEvent) => {
    const which = dragHandleRef.current;
    const wrap = wrapRef.current;
    if (!which || !wrap || !duration) return;
    const rect = wrap.getBoundingClientRect();
    const frac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const v = viewRef.current;
    const t = clamp(v.start + frac * (v.end - v.start), 0, duration);
    if (which === 'in') setInPoint(Math.min(t, outRef.current - 0.05));
    else setOutPoint(Math.max(t, inRef.current + 0.05));
  };
  const onHandlePointerUp = () => {
    dragHandleRef.current = null;
  };

  const onMinimapClick = (e: React.MouseEvent) => {
    const el = minimapRef.current;
    if (!el || !duration) return;
    const rect = el.getBoundingClientRect();
    const frac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const span = viewRef.current.end - viewRef.current.start;
    const start = clamp(frac * duration - span / 2, 0, Math.max(0, duration - span));
    const nv = { start, end: start + span };
    viewRef.current = nv;
    setView(nv);
  };

  const viewSpan = view.end - view.start;
  const inPct = viewSpan > 0 ? ((inPoint - view.start) / viewSpan) * 100 : 0;
  const outPct = viewSpan > 0 ? ((outPoint - view.start) / viewSpan) * 100 : 0;
  const bb = barBeatAt(playhead, bpm);
  const analysisReady = !!analysis && analysis.status === 'done';

  // Contextual one-liner: a first-time user always knows the next step.
  const guideText = analysisError
    ? 'Analysis failed, but you can still chop by ear — mark a region and save it.'
    : analysisPending && !peaks
      ? 'Analyzing your track — the waveform lands first, tempo and chops follow.'
      : analysisPending
        ? 'Waveform’s ready — finding the tempo and chop points…'
        : !(outPoint > inPoint)
          ? 'Mark your chop: drag the amber handles, or press I and O while playing.'
          : 'Audition a suggested chop below — or press Save chop to stash this region.';
  const fxActive = Math.abs(pitchSt) > 0.01 || targetBpm != null || mode === 'preview';

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>
        <span>Editor</span>
        {bpm ? (
          <span className={styles.resultCount}>
            {bpm.toFixed(1)} BPM{bb ? ` · bar ${bb.bar} beat ${bb.beat}` : ''}
          </span>
        ) : analysisPending ? (
          <span className={styles.listening}>Listening…</span>
        ) : (
          <span className={styles.resultCount}>tempo unknown</span>
        )}
      </div>
      <div className={styles.editorBody} tabIndex={0} onKeyDown={onKeyDown}>
        <div className={styles.trackHeader}>
          <h2 className={styles.trackHeaderTitle}>{track.title || 'Untitled'}</h2>
          <p className={styles.trackHeaderSub}>
            <span>{[track.artist_name, track.album_title].filter(Boolean).join(' · ')}</span>
            {duration > 0 && <span>{formatTime(duration)}</span>}
          </p>
        </div>

        <div className={styles.guideBar} role="status">
          {guideText}
        </div>

        <div className={styles.transportBar}>
          <button
            type="button"
            className={styles.transportBtn}
            onClick={togglePlay}
            disabled={!track.file_path}
          >
            {playing ? '⏸ Pause' : '▶ Play'}
          </button>
          <button
            type="button"
            className={styles.transportBtn}
            data-on={loopOn}
            onClick={() => setLoopOn((v) => !v)}
            title="Loop the in/out region"
          >
            🔁 Loop
          </button>
          <span className={styles.timeReadout}>
            {formatTime(playhead)} / {formatTime(duration)}
          </span>
          <button
            type="button"
            className={styles.transportBtn}
            onClick={() => setToolsOpen((v) => !v)}
            aria-expanded={toolsOpen}
            title="Edit tools: click track, bar loops, zoom"
          >
            🛠 Tools {toolsOpen ? '▾' : '▸'}
          </button>
          <button
            type="button"
            className={`${styles.transportBtn} ${styles.primaryBtn}`}
            data-on={true}
            onClick={() => setSaveOpen(true)}
            disabled={!track.file_path || !(outPoint > inPoint)}
            title="Save the in/out region as a stash chop"
          >
            💾 Save chop
          </button>
        </div>

        {toolsOpen && (
          <div className={styles.transportBar} data-kind="secondary">
            <button
              type="button"
              className={styles.transportBtn}
              data-on={metroOn}
              onClick={() => setMetroOn((v) => !v)}
              disabled={!bpm || mode !== 'original' || slicePreview != null}
              title="Metronome click (browser only, original audio)"
            >
              🥁 Click
            </button>
            <span className={styles.loopPresets}>
              <span className={styles.presetLabel}>Loop:</span>
              {[1, 2, 4].map((n) => (
                <button
                  key={n}
                  type="button"
                  className={styles.transportBtn}
                  onClick={() => setLoopBars(n)}
                  disabled={!bpm}
                  title={`Set loop to ${n} bar${n > 1 ? 's' : ''} from the playhead`}
                >
                  {n} bar{n > 1 ? 's' : ''}
                </button>
              ))}
            </span>
            <button
              type="button"
              className={styles.transportBtn}
              onClick={() => zoom(0.5)}
              title="Zoom in (+)"
            >
              ＋
            </button>
            <button
              type="button"
              className={styles.transportBtn}
              onClick={() => zoom(2)}
              title="Zoom out (−)"
            >
              －
            </button>
            <button
              type="button"
              className={styles.transportBtn}
              onClick={() => setShortcutsOpen(true)}
              title="Keyboard shortcuts (?)"
              aria-label="Show keyboard shortcuts"
            >
              ?
            </button>
          </div>
        )}

        {analysisError ? (
          <div className={styles.statusLine} data-tone="error" role="alert">
            <span>Couldn't analyze this track — {analysisError}. You can still chop by ear.</span>
            <button type="button" className={styles.retryButton} onClick={onRetryAnalysis}>
              Try again
            </button>
          </div>
        ) : (
          analysisPending && (
            <div className={styles.statusLine} data-tone="warn">
              {peaks
                ? 'Waveform’s up — finding the tempo and chop points…'
                : 'Analyzing track — the waveform appears first…'}
            </div>
          )
        )}

        <div
          ref={wrapRef}
          className={styles.waveWrap}
          onClick={(e) => {
            if (dragHandleRef.current) return;
            seekTo(e.clientX);
          }}
        >
          <canvas ref={canvasRef} className={styles.waveCanvas} />
          {duration > 0 && (
            <>
              <div
                className={styles.inHandle}
                style={{ left: `calc(${inPct}% - 5px)` }}
                onPointerDown={onHandlePointerDown('in')}
                onPointerMove={onHandlePointerMove}
                onPointerUp={onHandlePointerUp}
                title={`In-point ${formatTime(inPoint)} (I)`}
              />
              <div
                className={styles.outHandle}
                style={{ left: `calc(${outPct}% - 5px)` }}
                onPointerDown={onHandlePointerDown('out')}
                onPointerMove={onHandlePointerMove}
                onPointerUp={onHandlePointerUp}
                title={`Out-point ${formatTime(outPoint)} (O)`}
              />
            </>
          )}
        </div>

        {duration > 0 && viewSpan < duration - 0.01 && (
          <div
            className={styles.minimapWrap}
            onClick={onMinimapClick}
            title="Click to move the zoomed view"
          >
            <canvas ref={minimapRef} className={styles.minimapCanvas} />
          </div>
        )}

        <div className={styles.statusLine}>
          In {formatTime(inPoint)} · Out {formatTime(outPoint)} · Selection{' '}
          {formatTime(Math.max(0, outPoint - inPoint))}
          {' · '}Space play/pause · I/O set in/out · +/− zoom
        </div>
        {slicePreview != null && (
          <div className={styles.statusLine}>
            Auditioning slice {formatTime(slicePreview.start)} →{' '}
            {formatTime(slicePreview.start + slicePreview.duration)} — click the waveform or press
            play to return to the loop
          </div>
        )}
        {previewErr && (
          <div className={styles.statusLine} data-tone="warn">
            {previewErr}
          </div>
        )}

        <div className={styles.section}>
          <button
            type="button"
            className={styles.sectionToggle}
            onClick={() => setFxOpen((v) => !v)}
            aria-expanded={fxOpen}
          >
            <span>Pitch &amp; tempo</span>
            {fxActive && <span className={styles.fxBadge}>active</span>}
            <span className={styles.chev}>{fxOpen ? '▾' : '▸'}</span>
          </button>
          {fxOpen && (
            <PitchTempoPanel
              sourceBpm={bpm}
              pitchSt={pitchSt}
              targetBpm={targetBpm}
              onParamsChange={onFxParamsChange}
              rendering={previewRendering}
              previewEngine={preview?.engine ?? null}
              mode={mode}
              onModeChange={switchMode}
              disabled={!track.file_path || !duration}
            />
          )}
        </div>

        <ChopTray
          onsets={analysis?.onsets ?? []}
          inPoint={inPoint}
          outPoint={outPoint}
          bpm={bpm}
          durationS={duration}
          peaks={peaks}
          analysisReady={analysisReady}
          onAuditionSlice={auditionSlice}
          onMergeSlices={mergeSlices}
          onUseSuggestion={useSuggestion}
        />

        <audio ref={audioRef} preload="auto" />
      </div>

      <SaveDialog
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        track={track}
        start={inPoint}
        end={outPoint}
        pitchSt={pitchSt}
        targetBpm={targetBpm}
        stem={stemSource}
        onSaved={onSavedStashEntry}
      />

      {shortcutsOpen && (
        <div
          className={styles.shortcutOverlay}
          onClick={() => setShortcutsOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Keyboard shortcuts"
        >
          <div className={styles.shortcutCard} onClick={(e) => e.stopPropagation()}>
            <h3>Keyboard shortcuts</h3>
            <dl>
              <div>
                <dt>
                  <kbd>Space</kbd>
                </dt>
                <dd>Play / pause</dd>
              </div>
              <div>
                <dt>
                  <kbd>I</kbd>
                </dt>
                <dd>Set in-point at playhead</dd>
              </div>
              <div>
                <dt>
                  <kbd>O</kbd>
                </dt>
                <dd>Set out-point at playhead</dd>
              </div>
              <div>
                <dt>
                  <kbd>+</kbd>
                </dt>
                <dd>Zoom in on the playhead</dd>
              </div>
              <div>
                <dt>
                  <kbd>−</kbd>
                </dt>
                <dd>Zoom out</dd>
              </div>
              <div>
                <dt>
                  <kbd>?</kbd>
                </dt>
                <dd>This panel</dd>
              </div>
            </dl>
            <p className={styles.shortcutHint}>
              Click the waveform to seek. Drag the amber handles to set the chop region.
            </p>
            <button
              type="button"
              className={styles.shortcutClose}
              onClick={() => setShortcutsOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
