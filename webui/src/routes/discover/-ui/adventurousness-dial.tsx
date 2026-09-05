import { useEffect, useRef, useState } from 'react';

import {
  advAreaPath,
  advNextPhase,
  advOrbTopPercent,
  advStyles,
  advWavePath,
  ADV_VIEW_HEIGHT,
  ADV_VIEW_WIDTH,
} from '../-discover.adventurousness';

/**
 * The adventurousness dial.
 *
 * Transcribed from index.html 4523-4560 and discover.js 63-147, then made
 * operable.
 *
 * the input is a native range slider now. the wave is decoration on top of it.
 * before this the only way to move the dial was mousedown + window mousemove:
 * no keyboard, no touch, no screen reader value, and the rAF ran whenever the
 * element had an offsetParent, which says nothing about being on screen.
 */

export interface AdventurousnessDialProps {
  value: number;
  /** Live while dragging, committed on release. */
  onChange: (value: number) => void;
  onCommit: (value: number) => void;
}

/** how long after the last key press a keyboard change counts as settled. */
const COMMIT_DEBOUNCE_MS = 320;

export function AdventurousnessDial({ value, onChange, onCommit }: AdventurousnessDialProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState(0);
  const styles = advStyles(value);

  // the frame loop, but only while the wave is actually visible and motion is
  // allowed. an off-screen or background dial rebuilds nothing.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (reduced?.matches) return;

    let raf = 0;
    let onScreen = true;
    const tick = () => {
      if (onScreen && !document.hidden) setPhase((p) => advNextPhase(p, value));
      raf = requestAnimationFrame(tick);
    };
    let observer: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== 'undefined') {
      onScreen = false;
      observer = new IntersectionObserver((entries) => {
        onScreen = entries[0]?.isIntersecting ?? false;
      });
      observer.observe(track);
    }
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
    };
  }, [value]);

  // live on every input event, saved once the gesture settles. one save per
  // drag or key run: holding an arrow key used to be impossible, and a save
  // per pixel would let an older response land last.
  const commitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(commitTimer.current), []);
  const slide = (next: number) => {
    onChange(next);
    clearTimeout(commitTimer.current);
    commitTimer.current = setTimeout(() => onCommit(next), COMMIT_DEBOUNCE_MS);
  };

  const line = advWavePath(styles.value, phase);

  return (
    <div className="adv-wave" id="adv-wave">
      <div className="adv-wave-head">
        <span className="adv-wave-label" id="adv-wave-label">
          Adventurousness
        </span>
        <span className="adv-wave-state" id="adv-wave-state" style={{ color: styles.colorBright }}>
          {styles.state}
        </span>
      </div>
      <div ref={trackRef} className="adv-wave-track" id="adv-wave-track">
        <div
          className="adv-wave-aura"
          id="adv-wave-aura"
          // The colour wash FOLLOWS the orb (103-105); background alone leaves
          // it parked at the left edge.
          style={{ left: styles.orbLeft, background: styles.auraBackground }}
        />
        <svg
          className="adv-wave-svg"
          id="adv-wave-svg"
          viewBox={`0 0 ${ADV_VIEW_WIDTH} ${ADV_VIEW_HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {/* The luminous area is a GRADIENT fill fading to nothing, not a
              solid colour — _advApply recolours only the top stop (95). The
              first draft filled it solid and dropped the vanilla's ids. */}
          <defs>
            <linearGradient id="adv-wave-fill" x1="0" y1="0" x2="0" y2="1">
              <stop id="adv-wave-fill-top" offset="0" stopColor={styles.color} stopOpacity="0.32" />
              <stop offset="1" stopColor="#1DB954" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path id="adv-wave-area" d={advAreaPath(line)} fill="url(#adv-wave-fill)" stroke="none" />
          <path
            id="adv-wave-path"
            d={line}
            fill="none"
            stroke={styles.color}
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            // The line's glow (93) — set with the colour, not per frame.
            style={{ filter: `drop-shadow(0 0 7px ${styles.color})` }}
          />
        </svg>
        <div
          className="adv-wave-orb"
          id="adv-wave-orb"
          aria-hidden="true"
          style={{
            left: styles.orbLeft,
            top: advOrbTopPercent(styles.value, phase, trackRef.current?.clientWidth ?? 0),
            // currentColor drives the pulsing ring (98); the fill is the
            // brighter tone and the shadow pairs an outer glow with the inner
            // white ring (99-100).
            color: styles.color,
            background: styles.colorBright,
            boxShadow: `0 0 9px 0 ${styles.color}, inset 0 0 0 2px rgba(255,255,255,0.5)`,
          }}
        />
        {/* the real control. transparent and stretched over the wave, so the
            orb stays the thing you see and the browser owns the interaction. */}
        <input
          ref={inputRef}
          type="range"
          className="adv-wave-input"
          min={0}
          max={1}
          step={0.01}
          value={styles.value}
          aria-labelledby="adv-wave-label"
          aria-valuetext={`${styles.state}, ${Math.round(styles.value * 100)}%`}
          aria-describedby="adv-wave-help"
          onChange={(e) => slide(Number(e.target.value))}
        />
      </div>
      {/* The two poles (index.html 4543-4546) — the first draft dropped them,
          leaving the dial with no explanation of what its ends mean. */}
      <div className="adv-wave-ends">
        <span>Safe — artists you already like</span>
        <span>Adventurous — deep cuts</span>
      </div>
      <p className="adv-wave-help" id="adv-wave-help">
        Pushes globally popular artists down, so more obscure picks surface in Listening
        Recommendations and Recommended Artists.
      </p>
    </div>
  );
}
