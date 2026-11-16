/**
 * The Appearance tab of Settings, ported from index.html and settings.js
 * (SETTINGS_PORT.md, step 2). Same markup, classes and ids as the vanilla
 * page, so settings-premier.css, the settings search, the tour and
 * media-player.js's visualizer listener all find what they did before.
 *
 * Two slots hold it, because the page lays its blocks out in columns:
 *  - part "ui": Show only what you use, and UI Appearance
 *  - part "weather": the Sidebar Weather tile
 */

import {
  type MouseEvent,
  useEffect,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { SettingsTabProps } from '../host';

import {
  ACCENT_PRESETS,
  VISUALIZERS,
  effectFlags,
  resolveAppearance,
  savedEffectValue,
} from './appearance';

const TAB = 'appearance';

function toast(message: string, type: string) {
  window.showToast?.(message, type);
}

/** the page's collapsible tile, exactly as its inline handler did it. the
 *  settings search and the tab switch change these classes on the nodes
 *  themselves, so this stays out of React state */
function toggleSection(event: MouseEvent<HTMLElement>) {
  const header = event.currentTarget;
  header.classList.toggle('collapsed');
  const body = header.nextElementSibling as HTMLElement | null;
  if (!body) return;
  body.classList.toggle('collapsed');
  body.style.display = body.classList.contains('collapsed') ? 'none' : '';
}

function UiAppearance({ store }: SettingsTabProps) {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  // the effect switches live in window flags the appliers own: re-read after each
  const [, reread] = useReducer((n: number) => n + 1, 0);
  const values = resolveAppearance(state.settings);
  const flags = effectFlags();
  const swatch = useRef<HTMLDivElement>(null);

  // applyAccentColor paints the swatch by id, and ran before this mounted
  useEffect(() => {
    if (swatch.current) swatch.current.style.background = values.accent;
  }, [values.accent]);

  const pickPreset = (preset: string) => {
    window.applyAccentColor?.(preset === 'custom' ? values.custom : preset);
    store.set('ui_appearance', 'accent_preset', preset);
  };
  const pickCustom = (hex: string) => {
    window.applyAccentColor?.(hex);
    store.set('ui_appearance', 'accent_color', hex);
  };
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
  const locked = flags.maxPerformance ? 'form-group setting-overridden' : 'form-group';

  return (
    <>
      {/* what shows in the app (#1909). per profile, so it lives in the
          picker, this is just a door to it */}
      <div className="settings-group" data-stg={TAB}>
        <h3>Show only what you use</h3>
        <div className="form-group">
          <small className="settings-hint">
            Hide the parts of SoulSync you don't use, like podcasts, audiobooks or the video side.
            They keep working and come back with one switch. Each profile picks its own.
          </small>
          <button
            type="button"
            className="test-button"
            id="settings-open-feature-picker"
            onClick={() => window.openFeaturePicker?.()}
          >
            Customize SoulSync
          </button>
        </div>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>UI Appearance</h3>
        <div className="form-group">
          <label>Accent Color:</label>
          <div className="accent-color-selector">
            <select
              id="accent-preset"
              className="form-select"
              value={values.preset}
              onChange={(event) => pickPreset(event.target.value)}
            >
              {ACCENT_PRESETS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <div className="accent-preview-swatch" id="accent-preview-swatch" ref={swatch}></div>
          </div>
          <small className="settings-hint">Changes the accent color across the entire UI</small>
        </div>
        <div
          className="form-group"
          id="custom-color-group"
          style={{ display: values.preset === 'custom' ? undefined : 'none' }}
        >
          <label>Custom Color:</label>
          <input
            type="color"
            id="accent-custom-color"
            value={values.custom}
            onChange={(event) => pickCustom(event.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Sidebar Visualizer:</label>
          <select
            id="sidebar-visualizer-type"
            className="form-select"
            value={values.visualizer}
            // media-player.js hears the same change event and swaps the visualizer
            onChange={(event) =>
              store.set('ui_appearance', 'sidebar_visualizer', event.target.value)
            }
          >
            {VISUALIZERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <small className="settings-hint">
            Audio-reactive visualizer on the sidebar edge when music is playing
          </small>
        </div>
        <div className={locked}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="particles-enabled"
              checked={flags.particles}
              disabled={flags.maxPerformance}
              onChange={(event) =>
                effect(
                  'particles',
                  'particles_enabled',
                  window.applyParticlesSetting,
                  event.target.checked,
                )
              }
            />
            Background Particles
          </label>
          <small className="settings-hint">
            Animated particle effects behind each page. Off by default — enable for the eye candy
            (uses GPU).
          </small>
        </div>
        <div className={locked}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="worker-orbs-enabled"
              checked={flags.workerOrbs}
              disabled={flags.maxPerformance}
              onChange={(event) =>
                effect(
                  'workerOrbs',
                  'worker_orbs_enabled',
                  window.applyWorkerOrbsSetting,
                  event.target.checked,
                )
              }
            />
            Worker Orbs
          </label>
          <small className="settings-hint">
            Dashboard header buttons animate as floating orbs. Hover the header to expand. Desktop
            only.
          </small>
        </div>
        <div className={locked}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="reduce-effects-enabled"
              checked={flags.reduceEffects}
              disabled={flags.maxPerformance}
              onChange={(event) =>
                effect(
                  'reduceEffects',
                  'reduce_effects',
                  window.applyReduceEffects,
                  event.target.checked,
                )
              }
            />
            Reduce Visual Effects
          </label>
          <small className="settings-hint">
            Disables backdrop blur and shadows (the GPU-heavy bits). Keeps functional motion like
            loading spinners. Good for low-end devices.
          </small>
        </div>
        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="max-performance-enabled"
              checked={flags.maxPerformance}
              onChange={(event) =>
                effect(
                  'maxPerformance',
                  'max_performance',
                  window.applyMaxPerformance,
                  event.target.checked,
                )
              }
            />
            Max Performance
          </label>
          <small className="settings-hint">
            Nuclear low-power mode for software-rendered / no-GPU setups (e.g. Docker, remote
            desktop). Turns off Worker Orbs, Particles, all blur/shadows AND every animation &amp;
            transition (loading spinners go static). Overrides the three options above.
          </small>
        </div>
      </div>
    </>
  );
}

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

/** the line under the controls: where, how warm, how fresh */
export function weatherStatus(data: WeatherData): string {
  if (data.location && data.snapshot) {
    const current = data.snapshot.current;
    const temp = current && typeof current.temp === 'number' ? Math.round(current.temp) : null;
    const unit = data.units === 'celsius' ? '°C' : '°F';
    return temp === null
      ? `${data.location.name} — waiting for first forecast`
      : `${data.location.name} — ${temp}${unit}, updated ${data.snapshot.fetched_at || 'recently'}`;
  }
  if (data.location) {
    // switched off also means no snapshot: say that, not "unavailable"
    return data.enabled === false
      ? `${data.location.name} — weather display off`
      : `${data.location.name} — forecast unavailable right now`;
  }
  return 'Off — set a location to turn it on';
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

function SidebarWeather({ opened }: SettingsTabProps) {
  const [location, setLocation] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [holidays, setHolidays] = useState(true);
  const [celsius, setCelsius] = useState(false);
  const [status, setStatus] = useState('Loading...');
  const input = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const data = await weatherApi('GET', '');
      // GET /api/weather always answers success:true. a missing snapshot
      // just means no forecast is cached yet
      if (!data || data.success !== true) {
        setStatus('Could not load weather settings');
        return;
      }
      // not while it is being typed in
      if (document.activeElement !== input.current) setLocation(data.location?.query || '');
      setEnabled(data.enabled !== false);
      setCelsius(data.units === 'celsius');
      setHolidays(data.holidays !== false);
      setStatus(weatherStatus(data));
    } catch (error) {
      console.error('sidebar weather settings failed', error);
      setStatus('Could not load weather settings');
    }
  };

  // every time the tab is opened, as the vanilla page did. not before: the
  // tab is put on the page when Settings opens on any tab (for the search)
  useEffect(() => {
    if (opened > 0) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

  const saveLocation = async () => {
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
  };

  const clearLocation = async () => {
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
  };

  const toggleEnabled = async (next: boolean) => {
    setEnabled(next);
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
  };

  const toggleHolidays = async (next: boolean) => {
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
  };

  const toggleUnits = async (next: boolean) => {
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
  };

  const inline = { display: 'flex', alignItems: 'center', gap: '8px' } as const;

  return (
    <>
      <div
        className="settings-section-header collapsed"
        data-stg={TAB}
        onClick={toggleSection}
        role="button"
        tabIndex={0}
        aria-expanded="false"
      >
        <span className="settings-section-arrow">&#9660;</span>
        <h3>Sidebar Weather</h3>
        <span className="settings-section-hint">Time, temp &amp; forecast line</span>
      </div>
      <div className="settings-section-body collapsed" data-stg={TAB} style={{ display: 'none' }}>
        <div className="settings-group" data-stg={TAB}>
          <div className="form-group">
            <label>Location:</label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                type="text"
                id="sw-location"
                placeholder="ZIP or city, e.g. 97501 or Medford"
                ref={input}
                value={location}
                onChange={(event) => setLocation(event.target.value)}
              />
              <button className="test-button" onClick={() => void saveLocation()}>
                Save
              </button>
              <button className="test-button" onClick={() => void clearLocation()}>
                Clear
              </button>
            </div>
            <div className="help-text">
              No API keys, no setup beyond this — the server looks up the location and fetches the
              forecast (Open-Meteo, cached 30 min). Clear to turn the feature off.
            </div>
          </div>

          <div className="form-group">
            <label htmlFor="sw-enabled" style={inline}>
              <input
                type="checkbox"
                id="sw-enabled"
                checked={enabled}
                onChange={(event) => void toggleEnabled(event.target.checked)}
              />
              Show weather in the sidebar
            </label>
          </div>
          <div className="help-text">
            Master switch for the weather line, forecast popover and particle scene. The scene also
            respects your OS "reduce motion" setting.
          </div>

          <div className="form-group">
            <label htmlFor="sw-holidays" style={inline}>
              <input
                type="checkbox"
                id="sw-holidays"
                checked={holidays}
                onChange={(event) => void toggleHolidays(event.target.checked)}
              />
              Holiday decorations
            </label>
          </div>
          <div className="help-text">
            Halloween, Thanksgiving, Christmas, New Year's and Lunar New Year in the sidebar around
            each, by your location's date. Christmas Day always snows.
          </div>

          <div className="form-group">
            <label htmlFor="sw-celsius" style={inline}>
              <input
                type="checkbox"
                id="sw-celsius"
                checked={celsius}
                onChange={(event) => void toggleUnits(event.target.checked)}
              />
              Use &deg;C instead of &deg;F
            </label>
          </div>
          <div className="help-text">
            Auto-detected from the location (&deg;F for US, &deg;C elsewhere) — flip it here if you
            prefer the other.
          </div>

          <div className="form-group">
            <label>Status:</label>
            <span id="sw-status" className="readonly-field">
              {status}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}

export function AppearanceTab(props: SettingsTabProps) {
  return props.part === 'weather' ? <SidebarWeather {...props} /> : <UiAppearance {...props} />;
}
