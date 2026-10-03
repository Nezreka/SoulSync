import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { requestStems, stemAudioUrl, studioStemsStatusQueryOptions } from '../-sample-studio.api';
import { STEM_LABEL, type StemName, type StemsInfo } from '../-sample-studio.types';
import { PlayIcon, StopIcon } from './icons';
import styles from './stems-panel.module.css';

interface StemsPanelProps {
  trackId: number | null;
  /** The stem the editor is currently auditioning, or null for the full mix. */
  activeStem: StemName | null;
  onSelectStem: (stem: StemName | null) => void;
}

/** Map the worker status to a human message. */
function separationStatusMessage(info: StemsInfo | undefined, isFetching: boolean): string {
  if (!info) return 'Ready to separate';
  const s = info.status;
  if (s === 'done') return 'Stems ready';
  if (s.startsWith('error')) return s.slice('error:'.length).trim() || 'Separation failed';
  if (s === 'running') {
    const pct = typeof info.progress === 'number' ? Math.round(info.progress * 100) : null;
    return pct !== null ? `Separating… ${pct}%` : 'Separating…';
  }
  if (s === 'queued') return 'Queued…';
  if (isFetching) return 'Checking…';
  return 'Ready to separate';
}

/**
 * Shown instead of the button when the server can't run separation
 * (onnxruntime missing). A calm setup note, never a button that fails.
 */
function StemsSetupNote() {
  return (
    <div className={styles.setupBox}>
      <p className={styles.setupTitle}>Stem separation needs one small install</p>
      <p className={styles.hint}>
        Run this where you start SoulSync, then restart it. The Docker image already has it.
      </p>
      <pre className={styles.setupCode}>pip install onnxruntime</pre>
    </div>
  );
}

function useStemMixer(trackId: number | null, stems: StemName[]) {
  const ctxRef = useRef<AudioContext | null>(null);
  const gainsRef = useRef<Map<StemName, GainNode>>(new Map());
  const [playing, setPlaying] = useState(false);
  const [solo, setSolo] = useState<StemName | null>(null);
  const [muted, setMuted] = useState<Set<StemName>>(new Set());

  const stop = () => {
    if (ctxRef.current) {
      void ctxRef.current.close();
      ctxRef.current = null;
    }
    gainsRef.current = new Map();
    setPlaying(false);
  };

  // Reset the mixer whenever the track changes or a different method's
  // outputs arrive.
  useEffect(() => {
    stop();
    setSolo(null);
    setMuted(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackId, stems]);

  // Live solo/mute: retarget each stem's gain node as state changes.
  useEffect(() => {
    gainsRef.current.forEach((gain, stem) => {
      const audible = solo ? stem === solo : !muted.has(stem);
      gain.gain.setTargetAtTime(audible ? 1 : 0, gain.context.currentTime, 0.02);
    });
  }, [solo, muted]);

  const toggleMute = (stem: StemName) => {
    setMuted((prev) => {
      const next = new Set(prev);
      if (next.has(stem)) next.delete(stem);
      else next.add(stem);
      return next;
    });
  };

  const toggleSolo = (stem: StemName) => {
    setSolo((prev) => (prev === stem ? null : stem));
  };

  const playAll = async () => {
    if (trackId === null || stems.length === 0) return;
    if (playing) {
      stop();
      return;
    }
    const ctx = new AudioContext();
    ctxRef.current = ctx;
    try {
      const buffers = new Map<StemName, AudioBuffer>();
      await Promise.all(
        stems.map(async (stem) => {
          const res = await fetch(stemAudioUrl(trackId, stem));
          if (!res.ok) throw new Error(`stem ${stem}: ${res.status}`);
          const buf = await res.arrayBuffer();
          buffers.set(stem, await ctx.decodeAudioData(buf));
        }),
      );
      const master = ctx.createGain();
      master.connect(ctx.destination);
      const gains = new Map<StemName, GainNode>();
      buffers.forEach((buffer, stem) => {
        const gain = ctx.createGain();
        const audible = solo ? stem === solo : !muted.has(stem);
        gain.gain.value = audible ? 1 : 0;
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(gain);
        gain.connect(master);
        src.start();
        gains.set(stem, gain);
      });
      gainsRef.current = gains;
      setPlaying(true);
      // Stop the button state when the longest stem ends.
      const longest = Math.max(...[...buffers.values()].map((b) => b.duration));
      window.setTimeout(
        () => {
          if (gainsRef.current === gains) stop();
        },
        longest * 1000 + 300,
      );
    } catch (err) {
      void ctx.close();
      ctxRef.current = null;
      throw err;
    }
  };

  return { playing, solo, muted, toggleMute, toggleSolo, playAll, stop };
}

export default function StemsPanel({ trackId, activeStem, onSelectStem }: StemsPanelProps) {
  const [separating, setSeparating] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const statusQuery = useQuery(studioStemsStatusQueryOptions(trackId, separating));
  const info: StemsInfo | undefined = statusQuery.data;
  // Referentially stable so the mixer's reset effect doesn't loop.
  const stems = useMemo<StemName[]>(() => info?.stems ?? [], [info?.stems]);
  const done = info?.status === 'done';
  const failed = info?.status.startsWith('error:') ?? false;
  const busy = separating && (info?.status === 'queued' || info?.status === 'running');
  // Unknown until the first status poll lands — assume available so the
  // panel doesn't flash the setup note on every track change.
  const stemsAvailable = info?.stems_available ?? true;

  // Stop the "separating" poll flag once the worker settles.
  useEffect(() => {
    if (!info) return;
    if (info.status === 'done' || info.status.startsWith('error')) setSeparating(false);
  }, [info]);

  // A separation only makes sense for the track it ran on.
  useEffect(() => {
    setSeparating(false);
    setRequestError(null);
  }, [trackId]);

  const mixer = useStemMixer(trackId, stems);

  const startSeparation = async () => {
    if (trackId === null) return;
    setRequestError(null);
    setSeparating(true);
    mixer.stop();
    try {
      const result = await requestStems(trackId);
      if (result.status === 'done' || result.status.startsWith('error')) setSeparating(false);
      void statusQuery.refetch();
    } catch (err) {
      setSeparating(false);
      setRequestError(err instanceof Error ? err.message : 'Could not start separation');
    }
  };

  const labelFor = (slug: StemName): string => info?.labels?.[slug] ?? STEM_LABEL[slug] ?? slug;
  const message = separationStatusMessage(info, statusQuery.isFetching);

  return (
    <section className={styles.stemsPanel} aria-label="Stem separation">
      <header className={styles.panelHeader}>
        <span>Stems</span>
      </header>

      <div className={styles.body}>
        {!done && !busy && !failed && stemsAvailable && (
          <>
            <p className={styles.hint}>
              Split this track into drums, vocals, bass, and everything else, then chop from any one
              of them. Runs on your server, a few minutes for a full song, and it&apos;s saved once
              done.
            </p>
            <button type="button" className={styles.primary} onClick={() => void startSeparation()}>
              Separate stems
            </button>
          </>
        )}

        {!done && !busy && !stemsAvailable && <StemsSetupNote />}

        {busy && (
          <div className={styles.progress} role="status" aria-live="polite">
            <div className={styles.spinner} aria-hidden="true" />
            <span>{message}. This takes a few minutes, keep editing meanwhile.</span>
          </div>
        )}

        {failed && !busy && stemsAvailable && (
          <div className={styles.error} role="alert">
            <p className={styles.errorTitle}>Separation failed</p>
            <p className={styles.errorDetail}>{message}</p>
            <button type="button" className={styles.primary} onClick={() => void startSeparation()}>
              Try again
            </button>
          </div>
        )}

        {requestError && (
          <div className={styles.error} role="alert">
            <p className={styles.errorDetail}>{requestError}</p>
          </div>
        )}

        {done && (
          <>
            <div className={styles.transport}>
              <button
                type="button"
                className={styles.playAll}
                onClick={() =>
                  void mixer.playAll().catch(() => setRequestError('Could not play stems'))
                }
              >
                {mixer.playing ? <StopIcon size={12} /> : <PlayIcon size={12} />}
                {mixer.playing ? 'Stop' : 'Play all'}
              </button>
              <span className={styles.hint}>
                Solo or mute stems live, then tap one to chop from it.
              </span>
            </div>
            <ul className={styles.stemList}>
              {stems.map((stem) => {
                const selected = activeStem === stem;
                return (
                  <li key={stem} className={`${styles.stemRow} ${selected ? styles.selected : ''}`}>
                    <button
                      type="button"
                      className={styles.stemName}
                      onClick={() => onSelectStem(selected ? null : stem)}
                      title={selected ? 'Back to the full mix' : `Chop from ${labelFor(stem)}`}
                    >
                      {selected ? '◉' : '○'} {labelFor(stem)}
                    </button>
                    <div className={styles.stemControls}>
                      <button
                        type="button"
                        className={`${styles.mini} ${mixer.solo === stem ? styles.soloOn : ''}`}
                        onClick={() => mixer.toggleSolo(stem)}
                        aria-pressed={mixer.solo === stem}
                        title={`Solo ${labelFor(stem)}`}
                      >
                        S
                      </button>
                      <button
                        type="button"
                        className={`${styles.mini} ${mixer.muted.has(stem) ? styles.muteOn : ''}`}
                        onClick={() => mixer.toggleMute(stem)}
                        aria-pressed={mixer.muted.has(stem)}
                        title={`Mute ${labelFor(stem)}`}
                      >
                        M
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
            {activeStem && (
              <button type="button" className={styles.ghost} onClick={() => onSelectStem(null)}>
                ← Back to full mix
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
