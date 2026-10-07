/**
 * settings > advanced: the weather controls that live in typescript.
 *
 * - sidebar weather > holiday decorations: a server setting (on by default)
 * - developer > weather preview: pick a sky, a holiday and/or a pretend
 *   date, session-only, nothing touches the server
 *
 * the markup is static in index.html; this fills the lists and wires it up.
 */

import { HOLIDAY_LABELS } from './holidays';
import {
  bootSidebarWeather,
  getWeatherPreview,
  isSidebarWeatherMounted,
  setWeatherPreview,
  weatherPreviewPresets,
} from './sidebar-weather';

const PRESET_ID = 'sw-preview-preset';
const HOLIDAY_ID = 'sw-preview-holiday';
const DATE_ID = 'sw-preview-date';
const RESET_ID = 'sw-preview-reset';
const STATUS_ID = 'sw-preview-status';
const HOLIDAYS_TOGGLE_ID = 'sw-holidays';

function status(): void {
  const el = document.getElementById(STATUS_ID);
  if (!el) return;
  const p = getWeatherPreview();
  if (!isSidebarWeatherMounted()) {
    el.textContent = 'Turn on Sidebar Weather with a location first, the preview paints over it';
    return;
  }
  if (!p) {
    el.textContent = 'Live weather';
    return;
  }
  const sky = weatherPreviewPresets().find((x) => x.key === p.preset)?.label;
  const holiday =
    p.holiday === 'none'
      ? 'no holiday'
      : p.holiday
        ? HOLIDAY_LABELS[p.holiday as keyof typeof HOLIDAY_LABELS]
        : null;
  const parts = [sky ?? 'live sky', holiday].filter(Boolean).join(', ');
  el.textContent = p.date ? `Previewing ${parts} on ${p.date}` : `Previewing ${parts}`;
}

interface Controls {
  preset: HTMLSelectElement;
  holiday: HTMLSelectElement | null;
  date: HTMLInputElement;
}

function sync(c: Controls): void {
  const p = getWeatherPreview();
  c.preset.value = p?.preset ?? '';
  if (c.holiday) c.holiday.value = p?.holiday ?? '';
  c.date.value = p?.date ?? '';
  status();
}

function apply(c: Controls): void {
  setWeatherPreview({
    preset: c.preset.value,
    date: c.date.value || null,
    holiday: c.holiday?.value || '',
  });
  sync(c);
}

function fill(
  select: HTMLSelectElement,
  items: Array<{ key: string; label: string }>,
  keep: number,
): void {
  if (select.options.length > keep) return;
  for (const { key, label } of items) {
    const o = document.createElement('option');
    o.value = key;
    o.textContent = label;
    select.appendChild(o);
  }
}

/** the holiday decorations switch: a server setting, reboots the sidebar */
async function wireHolidaysToggle(): Promise<void> {
  const box = document.getElementById(HOLIDAYS_TOGGLE_ID) as HTMLInputElement | null;
  if (!box || box.dataset.wired === '1') return;
  box.dataset.wired = '1';
  try {
    const resp = await fetch('/api/weather', { headers: { Accept: 'application/json' } });
    if (resp.ok) {
      const data = (await resp.json()) as { holidays?: boolean };
      box.checked = data.holidays !== false;
    }
  } catch {
    // keep the default; the switch still works
  }
  box.addEventListener('change', async () => {
    try {
      await fetch('/api/weather/holidays', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ enabled: box.checked }),
      });
    } finally {
      await bootSidebarWeather();
    }
  });
}

/** wire the controls once. safe to call again (settings re-renders, tests) */
export function initWeatherPreviewSettings(): void {
  void wireHolidaysToggle();
  const preset = document.getElementById(PRESET_ID) as HTMLSelectElement | null;
  const holiday = document.getElementById(HOLIDAY_ID) as HTMLSelectElement | null;
  const date = document.getElementById(DATE_ID) as HTMLInputElement | null;
  const reset = document.getElementById(RESET_ID);
  if (!preset || !date) return;
  const c: Controls = { preset, holiday, date };
  fill(preset, weatherPreviewPresets(), 1);
  if (holiday) {
    fill(
      holiday,
      Object.entries(HOLIDAY_LABELS).map(([key, label]) => ({ key, label })),
      2,
    );
  }
  sync(c);
  if (preset.dataset.wired === '1') return;
  preset.dataset.wired = '1';
  preset.addEventListener('change', () => apply(c));
  holiday?.addEventListener('change', () => apply(c));
  date.addEventListener('change', () => apply(c));
  // the weather may finish loading after the page does: re-read on focus
  preset.addEventListener('focus', status);
  reset?.addEventListener('click', () => {
    preset.value = '';
    if (holiday) holiday.value = '';
    date.value = '';
    apply(c);
  });
}
