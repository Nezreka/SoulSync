import type { DelayParams, DelayTime, RenderFx } from '../-sample-studio.types';

import styles from './sample-studio-page.module.css';

interface FxPanelProps {
  fx: RenderFx;
  onChange: (fx: RenderFx) => void;
  /** Delay is beat-synced — it needs a known BPM (target or analyzed). */
  bpmKnown: boolean;
  disabled: boolean;
}

const DELAY_TIMES: DelayTime[] = ['1/4', '1/8', '1/2'];
const DEFAULT_DELAY: DelayParams = { time: '1/4', feedback: 0.35, mix: 0.25 };

/**
 * Render-funnel FX: peak normalize, reverse, edge fade, reverb space, and
 * beat-synced delay. Same plain language as the pitch/tempo panel; every
 * param flows identically to preview and save.
 */
export function FxPanel({ fx, onChange, bpmKnown, disabled }: FxPanelProps) {
  const set = (patch: Partial<RenderFx>) => onChange({ ...fx, ...patch });
  const setDelay = (patch: Partial<DelayParams>) =>
    set({ delay: { ...(fx.delay ?? DEFAULT_DELAY), ...patch } });
  const delayOn = fx.delay != null;
  const spaceOn = fx.space != null;

  return (
    <div className={styles.pitchPanel}>
      <div className={styles.pitchRow}>
        <button
          type="button"
          className={styles.transportBtn}
          data-on={fx.normalize}
          disabled={disabled}
          onClick={() => set({ normalize: !fx.normalize })}
          title="Match the loudest peak to full scale"
          aria-pressed={fx.normalize}
        >
          {fx.normalize ? '◉' : '○'} Peak normalize
        </button>
        <span className={styles.pitchValue}>loudest peak to full scale — not a loudness boost</span>
      </div>

      <div className={styles.pitchRow}>
        <button
          type="button"
          className={styles.transportBtn}
          data-on={fx.reverse}
          disabled={disabled}
          onClick={() => set({ reverse: !fx.reverse })}
          title="Play the chop backwards"
          aria-pressed={fx.reverse}
        >
          {fx.reverse ? '◉' : '○'} Reverse
        </button>
        <span className={styles.pitchValue}>flips the chop end to front</span>
      </div>

      <div className={styles.pitchRow}>
        <label className={styles.pitchLabel}>
          Fade
          <input
            type="range"
            min={0.5}
            max={1000}
            step={0.5}
            value={fx.fadeMs}
            disabled={disabled}
            onChange={(e) => set({ fadeMs: Number(e.target.value) })}
            className={styles.sliderCtl}
            aria-label="Fade length in milliseconds"
          />
        </label>
        <span className={styles.pitchValue}>{fx.fadeMs} ms in/out — so it never clicks</span>
      </div>

      <div className={styles.pitchRow}>
        <button
          type="button"
          className={styles.transportBtn}
          data-on={spaceOn}
          disabled={disabled}
          onClick={() => set({ space: spaceOn ? null : 0.5 })}
          title="A little room on the chop"
          aria-pressed={spaceOn}
        >
          {spaceOn ? '◉' : '○'} Space
        </button>
        {spaceOn && (
          <>
            <input
              type="range"
              min={0.2}
              max={1.5}
              step={0.05}
              value={fx.space ?? 0.5}
              disabled={disabled}
              onChange={(e) => set({ space: Number(e.target.value) })}
              className={styles.sliderCtl}
              aria-label="Reverb length in seconds"
            />
            <span className={styles.pitchValue}>{(fx.space ?? 0.5).toFixed(2)} s of room</span>
          </>
        )}
        {!spaceOn && <span className={styles.pitchValue}>dry — no reverb</span>}
      </div>

      <div className={styles.pitchRow}>
        <button
          type="button"
          className={styles.transportBtn}
          data-on={delayOn}
          disabled={disabled || !bpmKnown}
          onClick={() => set({ delay: delayOn ? null : { ...DEFAULT_DELAY } })}
          title={
            bpmKnown
              ? 'Echo that follows the beat'
              : "Delay follows the beat — it needs the track's tempo first"
          }
          aria-pressed={delayOn}
        >
          {delayOn ? '◉' : '○'} Delay
        </button>
        {!bpmKnown ? (
          <span className={styles.pitchValue}>
            needs the track&apos;s tempo — analyzing when you open it
          </span>
        ) : delayOn ? (
          <>
            <select
              value={fx.delay?.time ?? '1/4'}
              disabled={disabled}
              onChange={(e) => setDelay({ time: e.target.value as DelayTime })}
              className={styles.bpmInput}
              aria-label="Delay time"
            >
              {DELAY_TIMES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="range"
              min={0}
              max={0.99}
              step={0.01}
              value={fx.delay?.feedback ?? DEFAULT_DELAY.feedback}
              disabled={disabled}
              onChange={(e) => setDelay({ feedback: Number(e.target.value) })}
              className={styles.sliderCtl}
              aria-label="Delay feedback"
              title="Feedback: how much echo feeds back into itself"
            />
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={fx.delay?.mix ?? DEFAULT_DELAY.mix}
              disabled={disabled}
              onChange={(e) => setDelay({ mix: Number(e.target.value) })}
              className={styles.sliderCtl}
              aria-label="Delay mix"
              title="Mix: how loud the echo sits"
            />
            <span className={styles.pitchValue}>
              {fx.delay?.time ?? '1/4'} · fb {(fx.delay?.feedback ?? 0).toFixed(2)} · mix{' '}
              {(fx.delay?.mix ?? 0).toFixed(2)}
            </span>
          </>
        ) : (
          <span className={styles.pitchValue}>echo that follows the beat</span>
        )}
      </div>

      <div className={styles.statusLine}>
        What you hear in the preview is what lands in the stash — FX render into both.
      </div>
    </div>
  );
}
