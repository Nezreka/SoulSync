/**
 * The Appearance tab (settings-bic DESIGN.md 3.3), one page on the parts:
 * the hero reading the look back, Show only what you use (a door to the
 * feature picker), Look (accent, custom colour, sidebar visualizer), Effects
 * (the four switches, the two device-only ones marked), Sidebar weather
 * (open, with its own Save and Clear as before) and, on the video side,
 * Video pages.
 *
 * Same ids, keys and saves as the page it replaces. The accent applies at
 * once (applyAccentColor paints the whole app, and #accent-preview-swatch),
 * media-player.js still hears the visualizer select's change event, and the
 * four effect switches show init.js's window flags, which own them.
 */

import { type KeyboardEvent, useEffect, useReducer, useRef, useState } from 'react';

import { useFeaturesLocked } from '@/platform/features';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { useNav } from '../nav';
import { Btn, Card, FieldLine, Row, Say, Select, Switch, Tag, TextField } from '../ui';
import s from '../ui/settings.module.css';
import {
  ACCENT_PRESETS,
  VISUALIZERS,
  effectFlags,
  resolveAppearance,
  savedEffectValue,
} from './appearance';
import {
  appearanceSentence,
  EFFECTS_WORDS,
  effectsLevel,
  type WeatherLine,
} from './appearance/words';
import { useChanged, useValues } from './downloads/behaviour-view';
import { ViewPanel } from './downloads/downloads-page';
import { LibHero } from './library-card';
import { VideoPagesCard } from './library-video-prefs';

const TAB = 'appearance';

function toast(message: string, type: string) {
  window.showToast?.(message, type);
}

// ── sidebar weather ──

interface WeatherData {
  success?: boolean;
  error?: string;
  enabled?: boolean;
  units?: string;
  holidays?: boolean;
  location?: { name?: string; query?: string } | null;
  snapshot?: { fetched_at?: string; current?: { temp?: number } | null } | null;
}

async function weatherApi(method: string, path: string, body?: unknown): Promise<WeatherData> {
  const response = await fetch('/api/weather' + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await response.json()) as WeatherData;
}

/** the line under the location: where, how warm, how fresh */
export function weatherStatus(data: WeatherData): string {
  if (data.location && data.snapshot) {
    const current = data.snapshot.current;
    const temp = current && typeof current.temp === 'number' ? Math.round(current.temp) : null;
    const unit = data.units === 'celsius' ? '°C' : '°F';
    return temp === null
      ? `${data.location.name}: waiting for the first forecast`
      : `${data.location.name}, ${temp}${unit}, updated ${data.snapshot.fetched_at || 'recently'}`;
  }
  if (data.location) {
    // switched off also means no snapshot: say that, not "unavailable"
    return data.enabled === false
      ? `${data.location.name}: weather display off`
      : `${data.location.name}: forecast unavailable right now`;
  }
  return 'Off: set a location to turn it on';
}

/** the sidebar line, popover and scene pick the change up without a reload */
function rebootSidebar() {
  if (typeof window.bootSidebarWeather !== 'function') {
    console.warn('sidebar weather re-boot skipped: bootSidebarWeather is not on window');
    return;
  }
  try {
    Promise.resolve(window.bootSidebarWeather()).catch((error: unknown) => {
      console.warn('sidebar weather re-boot failed', error);
    });
  } catch (error) {
    console.warn('sidebar weather re-boot failed', error);
  }
}

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** the weather settings: loaded each time the tab is opened, as before */
function useWeather(opened: number) {
  const [location, setLocation] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [holidays, setHolidays] = useState(true);
  const [celsius, setCelsius] = useState(false);
  const [status, setStatus] = useState('Loading…');
  const [failed, setFailed] = useState(false);
  /** for the hero: null until it loaded */
  const [line, setLine] = useState<WeatherLine | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const data = await weatherApi('GET', '');
      // GET /api/weather always answers success:true. a missing snapshot
      // just means no forecast is cached yet
      if (!data || data.success !== true) {
        setStatus('Could not load weather settings');
        setFailed(true);
        return;
      }
      // not while it is being typed in
      if (document.activeElement !== input.current) setLocation(data.location?.query || '');
      setEnabled(data.enabled !== false);
      setCelsius(data.units === 'celsius');
      setHolidays(data.holidays !== false);
      setStatus(weatherStatus(data));
      setFailed(false);
      setLine({ place: data.location?.name || '', enabled: data.enabled !== false });
    } catch (error) {
      console.error('sidebar weather settings failed', error);
      setStatus('Could not load weather settings');
      setFailed(true);
    }
  };

  // every time the tab is opened, as the vanilla page did. not before: the
  // tab is put on the page when Settings opens on any tab (for the search)
  useEffect(() => {
    if (opened > 0) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  return {
    input,
    location,
    setLocation,
    enabled,
    holidays,
    celsius,
    status,
    failed,
    line,
    async saveLocation() {
      const query = location.trim();
      if (!query) {
        toast('Enter a ZIP or city first', 'error');
        return;
      }
      try {
        const data = await weatherApi('PUT', '/location', { location: query });
        if (data.success) {
          toast(`Weather location set to ${data.location?.name}`, 'success');
          void load();
          rebootSidebar();
        } else {
          toast(data.error || 'Could not resolve that location', 'error');
        }
      } catch (error) {
        toast('Could not save location: ' + message(error), 'error');
      }
    },
    async clearLocation() {
      try {
        const data = await weatherApi('PUT', '/location', { location: '' });
        if (data.success) {
          toast('Sidebar weather turned off', 'success');
          setLocation('');
          void load();
          rebootSidebar();
        } else {
          toast(data.error || 'Could not clear the location', 'error');
        }
      } catch (error) {
        toast('Could not clear location: ' + message(error), 'error');
      }
    },
    async toggleEnabled(next: boolean) {
      setEnabled(next);
      setLine((l) => (l ? { ...l, enabled: next } : l));
      try {
        const data = await weatherApi('PUT', '/display', { enabled: next });
        if (data.success) {
          toast(next ? 'Sidebar weather on' : 'Sidebar weather off', 'success');
          rebootSidebar();
        } else {
          toast(data.error || 'Could not update the setting', 'error');
        }
      } catch (error) {
        toast('Could not update the setting: ' + message(error), 'error');
      }
    },
    async toggleHolidays(next: boolean) {
      setHolidays(next);
      try {
        await fetch('/api/weather/holidays', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ enabled: next }),
        });
      } catch {
        // the sidebar is re-read either way and shows what the server holds
      } finally {
        rebootSidebar();
      }
    },
    async toggleUnits(next: boolean) {
      setCelsius(next);
      try {
        const data = await weatherApi('PUT', '/units', { use_celsius: next });
        if (data.success) {
          toast(`Using °${next ? 'C' : 'F'}`, 'success');
          void load();
          rebootSidebar();
        } else {
          toast(data.error || 'Could not update units', 'error');
        }
      } catch (error) {
        toast('Could not update units: ' + message(error), 'error');
      }
    },
  };
}

type Weather = ReturnType<typeof useWeather>;

function WeatherCard({ weather }: { weather: Weather }) {
  return (
    <Card
      title="Sidebar weather"
      hint="Time, temperature and a forecast line in the sidebar. Each change saves at once."
    >
      <Row
        wide
        label="Location"
        htmlFor="sw-location"
        help="A ZIP or a city. No API key and no other setup: the server looks the place up and fetches the forecast."
        more="The forecast comes from Open-Meteo and is kept for 30 minutes. Clear turns the feature off."
      >
        <FieldLine>
          <TextField
            full
            id="sw-location"
            placeholder="ZIP or city, like 97501 or Medford"
            ref={weather.input}
            value={weather.location}
            onChange={(event) => weather.setLocation(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void weather.saveLocation();
            }}
          />
          <Btn small kind="primary" onClick={() => void weather.saveLocation()}>
            Save
          </Btn>
          <Btn small kind="ghost" onClick={() => void weather.clearLocation()}>
            Clear
          </Btn>
        </FieldLine>
        <Say id="sw-status" tone={weather.failed ? 'bad' : undefined}>
          {weather.status}
        </Say>
      </Row>
      <Row
        label="Show weather in the sidebar"
        htmlFor="sw-enabled"
        help="The weather line, the forecast popover and the particle scene. The scene also follows your system's reduce motion setting."
      >
        <Switch
          id="sw-enabled"
          checked={weather.enabled}
          onChange={(event) => void weather.toggleEnabled(event.target.checked)}
        />
      </Row>
      <Row
        label="Holiday decorations"
        htmlFor="sw-holidays"
        help="Halloween, Thanksgiving, Christmas, New Year's and Lunar New Year in the sidebar around each, by your location's date."
        more="Christmas Day always snows."
      >
        <Switch
          id="sw-holidays"
          checked={weather.holidays}
          onChange={(event) => void weather.toggleHolidays(event.target.checked)}
        />
      </Row>
      <Row
        label="Use °C instead of °F"
        htmlFor="sw-celsius"
        help="Picked from the location: °F in the US, °C elsewhere. Flip it if you prefer the other."
      >
        <Switch
          id="sw-celsius"
          checked={weather.celsius}
          onChange={(event) => void weather.toggleUnits(event.target.checked)}
        />
      </Row>
    </Card>
  );
}

// ── the look ──

/** the accent presets as swatches, one pick of eight. The select it was:
 *  same values, same key */
function AccentSwatches({
  value,
  custom,
  onPick,
}: {
  value: string;
  custom: string;
  onPick: (value: string) => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const at = ACCENT_PRESETS.findIndex((o) => o.value === value);
    const next = ACCENT_PRESETS[(at + step + ACCENT_PRESETS.length) % ACCENT_PRESETS.length];
    onPick(next.value);
    event.currentTarget
      .querySelector<HTMLElement>(`[data-value="${CSS.escape(next.value)}"]`)
      ?.focus();
  };
  return (
    <div
      id="accent-preset"
      className={s.swatches}
      role="radiogroup"
      aria-label="Accent color"
      data-value={value}
      onKeyDown={onKeyDown}
    >
      {ACCENT_PRESETS.map((option) => {
        const on = option.value === value;
        const isCustom = option.value === 'custom';
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={option.label}
            title={option.label}
            tabIndex={on ? 0 : -1}
            data-value={option.value}
            className={isCustom ? s.swatchCustom : s.swatch}
            onClick={() => onPick(option.value)}
          >
            <span
              className={s.swatchDot}
              style={{ background: isCustom ? custom : option.value }}
            />
            {isCustom ? 'Custom' : null}
          </button>
        );
      })}
    </div>
  );
}

function LookCard({ store }: { store: SettingsStore }) {
  const { state } = useValues(store);
  const values = resolveAppearance(state.settings);
  const changed = (key: string) => useChanged(store, 'ui_appearance', key); // eslint-disable-line react-hooks/rules-of-hooks

  const pickPreset = (preset: string) => {
    window.applyAccentColor?.(preset === 'custom' ? values.custom : preset);
    store.set('ui_appearance', 'accent_preset', preset);
  };
  const pickCustom = (hex: string) => {
    window.applyAccentColor?.(hex);
    store.set('ui_appearance', 'accent_color', hex);
  };

  return (
    <Card title="Look" hint="The color of the app and what plays on the sidebar">
      <Row
        field
        label="Accent color"
        changed={changed('accent_preset')}
        help="The color of what is picked, switched on and pressed, across the whole app. It changes as you pick."
      >
        <AccentSwatches value={values.preset} custom={values.custom} onPick={pickPreset} />
      </Row>
      <Row
        sub
        id="custom-color-group"
        hidden={values.preset !== 'custom'}
        label="Custom color"
        htmlFor="accent-custom-color"
        changed={changed('accent_color')}
        help="Any color you like: the app follows it as you move."
      >
        <input
          type="color"
          id="accent-custom-color"
          className={s.colorPick}
          value={values.custom}
          onChange={(event) => pickCustom(event.target.value)}
        />
      </Row>
      <Row
        field
        label="Sidebar visualizer"
        htmlFor="sidebar-visualizer-type"
        changed={changed('sidebar_visualizer')}
        help="An audio-reactive visualizer on the sidebar's edge while music plays."
      >
        <Select
          id="sidebar-visualizer-type"
          value={values.visualizer}
          options={VISUALIZERS.map((o) => ({ ...o }))}
          // media-player.js hears the same change event and swaps the visualizer
          onChange={(event) => store.set('ui_appearance', 'sidebar_visualizer', event.target.value)}
        />
      </Row>
    </Card>
  );
}

function EffectsCard({ store, reread }: { store: SettingsStore; reread: () => void }) {
  useValues(store);
  const flags = effectFlags();
  const max = flags.maxPerformance;
  const effect = (
    key: 'particles' | 'workerOrbs' | 'reduceEffects' | 'maxPerformance',
    setting: string,
    apply: ((enabled: boolean) => void) | undefined,
    enabled: boolean,
  ) => {
    apply?.(enabled);
    store.set('ui_appearance', setting, savedEffectValue(key));
    reread();
  };
  const locked = 'Off while max performance is on. It comes back as it was.';
  const device = (
    <Tag>
      <span title="Kept by this browser; another device keeps its own">This device</span>
    </Tag>
  );

  return (
    <Card
      title="Effects"
      hint="What moves and glows. The last two are kept per device, so a slow machine can go light on its own."
    >
      <Row
        off={max}
        label="Background particles"
        htmlFor="particles-enabled"
        help={
          max ? locked : 'Animated particles behind each page. Off by default; they use the GPU.'
        }
      >
        <Switch
          id="particles-enabled"
          checked={flags.particles}
          disabled={max}
          onChange={(event) =>
            effect(
              'particles',
              'particles_enabled',
              window.applyParticlesSetting,
              event.target.checked,
            )
          }
        />
      </Row>
      <Row
        off={max}
        label="Worker orbs"
        htmlFor="worker-orbs-enabled"
        help={
          max
            ? locked
            : 'The Dashboard header buttons float as orbs; hover the header to open them. Desktop only.'
        }
      >
        <Switch
          id="worker-orbs-enabled"
          checked={flags.workerOrbs}
          disabled={max}
          onChange={(event) =>
            effect(
              'workerOrbs',
              'worker_orbs_enabled',
              window.applyWorkerOrbsSetting,
              event.target.checked,
            )
          }
        />
      </Row>
      <Row
        off={max}
        label={<>Reduce visual effects {device}</>}
        htmlFor="reduce-effects-enabled"
        help={
          max
            ? locked
            : 'Turns off backdrop blur and shadows, the GPU-heavy parts. Loading spinners keep moving. Good for low-end devices.'
        }
      >
        <Switch
          id="reduce-effects-enabled"
          checked={flags.reduceEffects}
          disabled={max}
          onChange={(event) =>
            effect(
              'reduceEffects',
              'reduce_effects',
              window.applyReduceEffects,
              event.target.checked,
            )
          }
        />
      </Row>
      <Row
        label={<>Max performance {device}</>}
        htmlFor="max-performance-enabled"
        help="For setups with no GPU, like Docker or remote desktop: orbs, particles, blur, shadows and every animation go, loading spinners included."
        more="It overrides the three above while it is on, and they come back as they were when it goes off."
      >
        <Switch
          id="max-performance-enabled"
          checked={max}
          onChange={(event) =>
            effect(
              'maxPerformance',
              'max_performance',
              window.applyMaxPerformance,
              event.target.checked,
            )
          }
        />
      </Row>
    </Card>
  );
}

/** what shows in the app (#1909). per profile, so it lives in the picker:
 *  this is a door to it */
function FeaturesCard() {
  return (
    <Card
      title="Show only what you use"
      hint="Hide the parts of SoulSync you don't use, like podcasts, audiobooks or the video side. They keep working and come back with one switch. Each profile picks its own."
      actions={
        <Btn small id="settings-open-feature-picker" onClick={() => window.openFeaturePicker?.()}>
          Customize SoulSync
        </Btn>
      }
    >
      {null}
    </Card>
  );
}

function AppearanceHero({ store, weather }: { store: SettingsStore; weather: WeatherLine | null }) {
  const { state } = useValues(store);
  const values = resolveAppearance(state.settings);
  const flags = effectFlags();
  const level = effectsLevel(flags);
  const preset = ACCENT_PRESETS.find((o) => o.value === values.preset && o.value !== 'custom');
  const visualizer = VISUALIZERS.find((o) => o.value === values.visualizer);
  const swatch = useRef<HTMLSpanElement>(null);
  // applyAccentColor paints the swatch by id, and ran before this mounted
  useEffect(() => {
    if (swatch.current) swatch.current.style.background = values.accent;
  }, [values.accent]);
  return (
    <LibHero
      parts={appearanceSentence({
        accent: preset?.label ?? null,
        visualizer:
          visualizer?.value === 'none' ? 'none' : (visualizer?.label ?? 'Bars').toLowerCase(),
        effects: level,
        particles: flags.particles,
        weather,
      })}
      stats={[
        {
          value: (
            <span className={s.accentStat}>
              <span className={s.swatchDot} id="accent-preview-swatch" ref={swatch} />
              {preset?.label ?? 'Custom'}
            </span>
          ),
          label: 'Accent',
        },
        { value: visualizer?.label ?? 'Bars', label: 'Visualizer' },
        { value: EFFECTS_WORDS[level], label: 'Effects' },
      ]}
    />
  );
}

export function AppearancePage({ store, opened }: SettingsTabProps) {
  const state = useNav();
  const here = state.tab === TAB;
  // the effect switches live in window flags the appliers own: re-read after each
  const [, reread] = useReducer((n: number) => n + 1, 0);
  const weather = useWeather(opened);
  // an admin set what this profile shows and locked it: no door to the picker (#2164)
  const featuresLocked = useFeaturesLocked();

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="all" open={here}>
        <AppearanceHero store={store} weather={weather.line} />
        <div className={s.cards}>
          {featuresLocked ? null : <FeaturesCard />}
          <LookCard store={store} />
          <EffectsCard store={store} reread={reread} />
          <WeatherCard weather={weather} />
          <VideoPagesCard />
        </div>
      </ViewPanel>
    </div>
  );
}

/** the Appearance tab: one page (settings-bic phase 5) */
export function AppearanceTab(props: SettingsTabProps) {
  if (props.part === 'page') return <AppearancePage {...props} />;
  return null;
}
