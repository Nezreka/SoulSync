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
          style={{ background: styles.auraBackground }}
        />
        <svg
          className="adv-wave-svg"
          id="adv-wave-svg"
          viewBox={`0 0 ${ADV_VIEW_WIDTH} ${ADV_VIEW_HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path className="adv-wave-area" d={advAreaPath(line)} fill={styles.color} />
          <path
            className="adv-wave-line"
            d={line}
            fill="none"
            stroke={styles.color}
            strokeWidth="2"
          />
        </svg>
        <div
          className="adv-wave-orb"
          id="adv-wave-orb"
          style={{
            left: styles.orbLeft,
            top: advOrbTopPercent(styles.value, phase, trackRef.current?.clientWidth ?? 0),
            background: styles.colorBright,
            boxShadow: `0 0 12px ${styles.color}`,
          }}
        />
      </div>
    </div>
  );
}
