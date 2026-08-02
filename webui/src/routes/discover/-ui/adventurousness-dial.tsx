import { useEffect, useRef, useState } from 'react';

import {
  advAreaPath,
  advNextPhase,
  advOrbTopPercent,
  advShouldDraw,
  advStyles,
  advValueFromX,
  advWavePath,
  ADV_VIEW_HEIGHT,
  ADV_VIEW_WIDTH,
} from '../-discover.adventurousness';

/**
 * The adventurousness dial.
 *
 * Transcribed from index.html 4523-4560 and discover.js 63-147.
 *
 * The wave animates, which means it needs a frame loop, which means it needs to
 * STOP costing anything when nobody can see it. The vanilla's guard is that the
 * track has no offsetParent — the Discover page is not displayed — and without
 * it a background tab rebuilds a 91-point path sixty times a second forever.
 */

export interface AdventurousnessDialProps {
  value: number;
  /** Live while dragging, committed on release. */
  onChange: (value: number) => void;
  onCommit: (value: number) => void;
}

export function AdventurousnessDial({ value, onChange, onCommit }: AdventurousnessDialProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [phase, setPhase] = useState(0);
  const [dragging, setDragging] = useState(false);
  const styles = advStyles(value);

  // The frame loop. `offsetParent === null` means the page is not on screen;
  // the rAF keeps ticking but computes nothing.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const track = trackRef.current;
      if (advShouldDraw(!!track && track.offsetParent !== null)) {
        setPhase((p) => advNextPhase(p, value));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  const valueAt = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return value;
    const rect = track.getBoundingClientRect();
    // Unclamped here on purpose — advStyles clamps, so a drag past either end
    // pins rather than wrapping.
    return advValueFromX(clientX, rect.left, rect.width);
  };

  useEffect(() => {
    if (!dragging) return;
    const move = (e: MouseEvent) => onChange(valueAt(e.clientX));
    const up = (e: MouseEvent) => {
      setDragging(false);
      onCommit(valueAt(e.clientX));
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging, onChange, onCommit]);

  const line = advWavePath(styles.value, phase);

  return (
    <div
      className="adv-wave"
      id="adv-wave"
      title="Pushes globally-popular artists down so more obscure picks surface in your recommendations"
    >
      <div className="adv-wave-head">
        <span className="adv-wave-label">Adventurousness</span>
        <span className="adv-wave-state" id="adv-wave-state" style={{ color: styles.colorBright }}>
          {styles.state}
        </span>
      </div>
      <div
        ref={trackRef}
        className="adv-wave-track"
        id="adv-wave-track"
        onMouseDown={(e) => {
          setDragging(true);
          onChange(valueAt(e.clientX));
        }}
      >
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
      </div>
      {/* The two poles (index.html 4543-4546) — the first draft dropped them,
          leaving the dial with no explanation of what its ends mean. */}
      <div className="adv-wave-ends">
        <span>Safe — artists you already like</span>
        <span>Adventurous — deep cuts</span>
      </div>
    </div>
  );
}
