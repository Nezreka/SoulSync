/**
 * The Appearance tab's hero sentence (settings-bic DESIGN.md 3.4): the look
 * read back in words. Pure, so it is tested from sample settings.
 */

import type { HeroPart } from '../downloads/words';

export type { HeroPart };

const name = (text: string): HeroPart => ({ text, kind: 'name' });
const say = (text: string): HeroPart => ({ text });

/** what this device draws: Max Performance overrides the rest */
export type EffectsLevel = 'max' | 'reduced' | 'full';

export const EFFECTS_WORDS: Record<EffectsLevel, string> = {
  max: 'Max performance',
  reduced: 'Reduced',
  full: 'Full',
};

export function effectsLevel(flags: { maxPerformance: boolean; reduceEffects: boolean }) {
  return flags.maxPerformance ? 'max' : flags.reduceEffects ? 'reduced' : 'full';
}

export interface WeatherLine {
  /** the place's name, empty when no location is set */
  place: string;
  enabled: boolean;
}

export function appearanceSentence(o: {
  /** the preset's name, or null for a custom colour */
  accent: string | null;
  /** the visualizer's name, lower case; 'none' for none */
  visualizer: string;
  effects: EffectsLevel;
  particles: boolean;
  /** null until it loaded */
  weather: WeatherLine | null;
}): HeroPart[] {
  const parts: HeroPart[] = o.accent
    ? [say('SoulSync is in '), name(o.accent)]
    : [say('SoulSync is in '), name('your own color')];
  if (o.visualizer === 'none') parts.push(say(', with no visualizer on the sidebar.'));
  else
    parts.push(
      say(', with the '),
      name(o.visualizer),
      say(' visualizer on the sidebar while music plays.'),
    );
  if (o.effects === 'max')
    parts.push(
      say(' This device runs in '),
      name('max performance'),
      say(': no effects and no animation.'),
    );
  else if (o.effects === 'reduced')
    parts.push(say(' This device runs with '), name('reduced effects'), say('.'));
  else if (o.particles) parts.push(say(' Background particles are on.'));
  if (o.weather) {
    if (!o.weather.place) parts.push(say(' Sidebar weather is off until you set a location.'));
    else if (!o.weather.enabled) parts.push(say(' Sidebar weather is switched off.'));
    else parts.push(say(' The sidebar shows the weather for '), name(o.weather.place), say('.'));
  }
  return parts;
}
