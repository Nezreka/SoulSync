import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { bootSidebarWeather, getWeatherPreview, weatherPreviewPresets } from './sidebar-weather';
import { initWeatherPreviewSettings } from './weather-preview-settings';

/** the developer section's controls, as index.html ships them */
function dom() {
  document.body.innerHTML = `
    <div class="sidebar" id="app-sidebar">
      <div class="sidebar-header"><div id="profile-indicator"></div></div>
    </div>
    <select id="sw-preview-preset"><option value="">Live weather</option></select>
    <select id="sw-preview-holiday"><option value="">Holiday by date</option><option value="none">No holiday</option></select>
    <input type="date" id="sw-preview-date">
    <button id="sw-preview-reset" type="button">Back to live</button>
    <span id="sw-preview-status"></span>`;
}

const PAYLOAD = {
  success: true,
  enabled: true,
  location: {
    query: '97501',
    name: 'Medford',
    latitude: 42.32,
    longitude: -122.87,
    country_code: 'US',
  },
  units: 'fahrenheit',
  snapshot: {
    fetched_at: '2026-10-06T21:00:00+00:00',
    utc_offset_seconds: -25200,
    current: { temp: 84, weather_code: 0, condition: 'Clear sky', wind_speed: 3 },
    daily: [],
  },
  scene: 'clear',
};

const select = () => document.getElementById('sw-preview-preset') as HTMLSelectElement;
const date = () => document.getElementById('sw-preview-date') as HTMLInputElement;
const statusText = () => document.getElementById('sw-preview-status')!.textContent;
const change = (el: HTMLElement) => el.dispatchEvent(new Event('change'));

beforeEach(() => {
  dom();
  server.use(http.get('*/api/weather', () => HttpResponse.json(PAYLOAD)));
});
afterEach(() => {
  sessionStorage.clear();
  document.body.innerHTML = '';
  server.resetHandlers();
});

describe('the weather preview controls', () => {
  it('lists every sky after Live weather', () => {
    initWeatherPreviewSettings();
    const options = [...select().options].map((o) => o.value);
    expect(options[0]).toBe('');
    expect(options.slice(1)).toEqual(weatherPreviewPresets().map((p) => p.key));
    expect(options.length).toBeGreaterThan(10);
  });

  it('picking a sky previews it; the date rides along; back to live clears it', async () => {
    await bootSidebarWeather();
    initWeatherPreviewSettings();
    expect(statusText()).toBe('Live weather');

    select().value = 'thunderstorm';
    change(select());
    expect(getWeatherPreview()).toEqual({ preset: 'thunderstorm', date: null, holiday: '' });
    expect(statusText()).toBe('Previewing Thunderstorm');

    date().value = '2026-12-24';
    change(date());
    expect(getWeatherPreview()).toEqual({
      preset: 'thunderstorm',
      date: '2026-12-24',
      holiday: '',
    });
    expect(statusText()).toBe('Previewing Thunderstorm on 2026-12-24');

    document.getElementById('sw-preview-reset')!.click();
    expect(getWeatherPreview()).toBeNull();
    expect(select().value).toBe('');
    expect(statusText()).toBe('Live weather');
  });

  it('a holiday alone previews over the live sky; a date alone picks the holiday', async () => {
    await bootSidebarWeather();
    initWeatherPreviewSettings();
    const holiday = document.getElementById('sw-preview-holiday') as HTMLSelectElement;
    expect([...holiday.options].map((o) => o.value)).toEqual([
      '',
      'none',
      'halloween',
      'thanksgiving',
      'lunar-new-year',
    ]);

    holiday.value = 'lunar-new-year';
    change(holiday);
    expect(getWeatherPreview()).toEqual({ preset: '', date: null, holiday: 'lunar-new-year' });
    expect(statusText()).toBe('Previewing live sky, Lunar New Year');

    holiday.value = '';
    date().value = '2026-10-31';
    change(date());
    expect(getWeatherPreview()).toEqual({ preset: '', date: '2026-10-31', holiday: '' });
  });

  it('says so when there is no weather line to paint over', async () => {
    // the module keeps its mount between tests: switch the feature off first
    server.use(http.get('*/api/weather', () => HttpResponse.json({ ...PAYLOAD, enabled: false })));
    await bootSidebarWeather();
    initWeatherPreviewSettings();
    expect(statusText()).toContain('Turn on Sidebar Weather');
  });

  it('wiring twice never doubles the list or the listeners', async () => {
    await bootSidebarWeather();
    initWeatherPreviewSettings();
    initWeatherPreviewSettings();
    expect(select().options.length).toBe(weatherPreviewPresets().length + 1);
  });

  it('shows a preview already running in this tab', async () => {
    sessionStorage.setItem(
      'soulsync-weather-preview',
      JSON.stringify({ preset: 'fog', date: '2026-10-31' }),
    );
    await bootSidebarWeather();
    initWeatherPreviewSettings();
    expect(select().value).toBe('fog');
    expect(date().value).toBe('2026-10-31');
  });
});
