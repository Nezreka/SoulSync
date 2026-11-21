/**
 * the music view's background: the cover's colours, the cover itself
 * blurred, and soulsync's own visuals (the theater's seventeen, from
 * lib/visualizer) moving to the song. the visual picked is kept on this
 * screen; v steps through them. a viewer who asked for less motion starts
 * with them off.
 */

import { useEffect, useRef, useSyncExternalStore } from 'react';

import {
  ambientColorsFromImage,
  nextTheme,
  normalizeTheme,
  THEMES,
  Visualizer,
  type ThemeId,
} from '@/lib/visualizer';

import { canRetryArt, retryArt } from '../../-lib/images';
import { RetryImg } from '../retry-img';
import { VisualsIcon } from './icons';
import { PopMenu } from './menu';
import s from './music-view.module.css';
import { getEngine } from './use-player';

const KEY = 'ss-player:visual';
let theme: ThemeId | null = null;
const listeners = new Set<() => void>();

function reducedMotion() {
  return (
    typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function read(): ThemeId {
  try {
    const v = localStorage.getItem(KEY);
    if (v) return normalizeTheme(v);
  } catch {
    /* no storage */
  }
  return reducedMotion() ? 'none' : 'aurora';
}

export function setVisual(id: ThemeId) {
  theme = id;
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* private window: it lasts the visit */
  }
  for (const l of listeners) l();
}

export function cycleVisual() {
  setVisual(nextTheme(theme ?? read(), true));
}

export function useVisual(): ThemeId {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => (theme ??= read()),
    () => 'none',
  );
}

export function VisualsMenu() {
  const current = useVisual();
  return (
    <PopMenu
      label="Visuals"
      title="Visuals (v)"
      below
      icon={<VisualsIcon />}
      options={THEMES.map((t) => ({
        key: t.id,
        label: t.name,
        detail: t.id === 'none' ? 'Just the cover’s colours' : t.blurb,
        selected: t.id === current,
        keepOpen: true,
        onSelect: () => setVisual(t.id),
      }))}
    />
  );
}

export function Visuals({ art, music }: { art: string | null; music: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viz = useRef<Visualizer | null>(null);
  const current = useVisual();
  const on = music && current !== 'none';

  // the visuals: made while they're on, gone when they're off
  useEffect(() => {
    if (!on || !canvas.current) return;
    const engine = getEngine();
    const v = new Visualizer(canvas.current, {
      // asked for here, after the click that opened this view: the audio context may start
      analyser: () => engine.analyser(),
      playing: () => engine.getState().status === 'playing',
    });
    v.setReducedMotion(reducedMotion());
    viz.current = v;
    v.start();
    return () => {
      v.destroy();
      viz.current = null;
    };
  }, [on]);

  useEffect(() => {
    if (on) viz.current?.setTheme(current);
  }, [on, current]);

  // the cover: its colours tint everything; the painters that draw it get it too
  useEffect(() => {
    const view = root.current?.parentElement;
    if (!art || !view) return;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    let gone = false;
    let tries = 0;
    img.onerror = () => {
      // the library's art may be fetched on first ask: try again a moment later
      if (gone || !canRetryArt(art) || tries >= 3) return;
      tries += 1;
      window.setTimeout(() => {
        if (!gone) img.src = retryArt(art, tries);
      }, 1500 * tries);
    };
    img.onload = () => {
      if (gone) return;
      const colors = ambientColorsFromImage(img);
      if (colors) {
        view.style.setProperty('--amb-rgb', `${colors.main.r}, ${colors.main.g}, ${colors.main.b}`);
        view.style.setProperty(
          '--amb2-rgb',
          `${colors.accent.r}, ${colors.accent.g}, ${colors.accent.b}`,
        );
        viz.current?.setPalette(colors.main, colors.accent);
      }
      viz.current?.setArt(img);
    };
    img.src = art;
    return () => {
      gone = true;
    };
  }, [art, on]);

  return (
    <div ref={root} className={s.bg} aria-hidden="true">
      <div className={s.bgTint} />
      {art ? <RetryImg className={s.bgArt} src={art} alt="" /> : null}
      {on ? <canvas ref={canvas} className={s.bgCanvas} /> : null}
    </div>
  );
}
