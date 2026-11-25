export type { AmbientColors, ColorBin, Triple } from './color';
export type { BandRange, FluxState } from './analysis';
export type { RGB, VizFrame } from './frame';
export type { Painter, TrailSpec } from './painters';
export type { VizStore } from './state';
export type { PaletteId, Quality, QualitySpec, ThemeId, ThemeInfo } from './themes';
export type { VisualizerOptions } from './visualizer';

export {
  VIZ_BANDS,
  VIZ_FFT,
  VIZ_FMAX,
  VIZ_FMIN,
  createFluxState,
  fluxBeat,
  idleSynth,
  logBandRanges,
  mapLogBands,
} from './analysis';
export {
  ambientColorsFromImage,
  extractAmbientColors,
  hue,
  pickAccentColor,
  punchUpColor,
} from './color';
export { analogous, createFrame, easeColor } from './frame';
export { paintMini } from './mini';
export { PAINTERS, TRAILS } from './painters';
export { createStore, withStore } from './state';
export {
  DEFAULT_THEME,
  PALETTES,
  PALETTE_LIST,
  THEMES,
  THEME_IDS,
  autocycleSeconds,
  clampVizDim,
  clampVizEnergy,
  isThemeId,
  nextTheme,
  normalizeTheme,
  qualitySpec,
} from './themes';
export { Visualizer } from './visualizer';
