/**
 * What the Appearance tab shows and what opening Settings applies, worked out
 * the way settings.js loadSettingsData did (SETTINGS_PORT.md, step 2).
 *
 * The four effect switches are per device. init.js's appliers own them: they
 * set the body classes, stop and start the canvas loops, and keep the choice
 * in window flags and localStorage. The tab reads those flags, it does not
 * keep its own copy.
 */

import type { SettingsPayload } from '../settings-store';

export const ACCENT_PRESETS = [
  { value: '#1db954', label: 'Spotify Green' },
  { value: '#1d8ab9', label: 'Ocean Blue' },
  { value: '#a78bfa', label: 'Purple' },
  { value: '#8b5cf6', label: 'Boulder Purple' },
  { value: '#f59e0b', label: 'Sunset Orange' },
  { value: '#f43f5e', label: 'Rose' },
  { value: '#14b8a6', label: 'Teal' },
  { value: 'custom', label: 'Custom' },
] as const;

export const VISUALIZERS = [
  { value: 'bars', label: 'Bars' },
  { value: 'wave', label: 'Wave' },
  { value: 'spectrum', label: 'Spectrum' },
  { value: 'mirror', label: 'Mirror' },
  { value: 'equalizer', label: 'Equalizer' },
  { value: 'none', label: 'None' },
] as const;

const DEFAULT_ACCENT = '#1db954';

type EffectsWindow = Window & {
  _particlesEnabled?: boolean;
  _workerOrbsEnabled?: boolean;
  _reduceEffectsActive?: boolean;
  _maxPerfActive?: boolean;
  setSidebarVisualizerType?: (type: string) => void;
};

function local(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export interface AppearanceValues {
  /** the select's value: a preset hex, or 'custom' for a colour that is not a preset */
  preset: string;
  /** the custom picker's colour */
  custom: string;
  /** the colour in use */
  accent: string;
  visualizer: string;
  particles: boolean;
  workerOrbs: boolean;
  reduceEffects: boolean;
  maxPerformance: boolean;
}

/** The saved appearance settings, resolved for this device. */
export function resolveAppearance(settings: SettingsPayload): AppearanceValues {
  const ui = settings.ui_appearance ?? {};
  const savedPreset = (ui.accent_preset as string) || DEFAULT_ACCENT;
  const custom = (ui.accent_color as string) || DEFAULT_ACCENT;
  // a saved colour that is not one of the options shows as Custom
  const preset = ACCENT_PRESETS.some((option) => option.value === savedPreset)
    ? savedPreset
    : 'custom';
  // unset: the server's browser-aware default, already on the window flag
  // (off on Firefox). an explicit saved value always wins
  const orbs = ui.worker_orbs_enabled;
  const workerOrbs =
    orbs === undefined || orbs === null
      ? (window as EffectsWindow)._workerOrbsEnabled !== false
      : orbs !== false;
  // device-scoped: this machine's choice wins, the server value is the
  // default for a device that has never chosen
  const localReduce = local('soulsync-reduce-effects');
  const localMaxPerf = local('soulsync-max-performance');
  return {
    preset,
    custom,
    accent: preset === 'custom' ? custom : preset,
    visualizer: (ui.sidebar_visualizer as string) || 'bars',
    particles: ui.particles_enabled === true,
    workerOrbs,
    reduceEffects: localReduce !== null ? localReduce === '1' : ui.reduce_effects === true,
    maxPerformance: localMaxPerf !== null ? localMaxPerf === '1' : ui.max_performance === true,
  };
}

/**
 * Opening Settings applies the saved appearance, on whatever tab it opens:
 * the accent, the visualizer type, then the four effect switches. Max
 * Performance goes last so it can override the three before it.
 */
export function applyAppearanceOnLoad(settings: SettingsPayload): void {
  const values = resolveAppearance(settings);
  const w = window as EffectsWindow;
  w.applyAccentColor?.(values.accent);
  w.setSidebarVisualizerType?.(values.visualizer);
  w.applyParticlesSetting?.(values.particles);
  w.applyWorkerOrbsSetting?.(values.workerOrbs);
  w.applyReduceEffects?.(values.reduceEffects);
  w.applyMaxPerformance?.(values.maxPerformance);
}

/** The four switches as the appliers left them, which is also what is saved. */
export function effectFlags() {
  const w = window as EffectsWindow;
  const maxPerformance = w._maxPerfActive === true;
  return {
    maxPerformance,
    // Max Performance overrides the other three: they show off and locked
    // while it is on, and the person's own choices come back when it goes off
    particles: !maxPerformance && w._particlesEnabled === true,
    workerOrbs: !maxPerformance && w._workerOrbsEnabled !== false,
    reduceEffects: !maxPerformance && w._reduceEffectsActive === true,
  };
}

/** What settings.js's save sent for the four switches: read from the flags,
 *  not the boxes, so a box locked off by Max Performance never saves as off. */
export function savedEffectValue(
  key: 'particles' | 'workerOrbs' | 'reduceEffects' | 'maxPerformance',
) {
  const w = window as EffectsWindow;
  if (key === 'particles') return w._particlesEnabled !== false;
  if (key === 'workerOrbs') return w._workerOrbsEnabled !== false;
  if (key === 'reduceEffects') return w._reduceEffectsActive === true;
  return w._maxPerfActive === true;
}
