/**
 * settings > advanced > developer > weather preview. the markup is static
 * in index.html; this fills the sky list and wires it to the sidebar's
 * session-only preview. nothing here touches the server.
 */

import {
  getWeatherPreview,
  isSidebarWeatherMounted,
  setWeatherPreview,
  weatherPreviewPresets,
} from './sidebar-weather';

const PRESET_ID = 'sw-preview-preset';
const DATE_ID = 'sw-preview-date';
const RESET_ID = 'sw-preview-reset';
const STATUS_ID = 'sw-preview-status';

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
  const label = weatherPreviewPresets().find((x) => x.key === p.preset)?.label ?? p.preset;
  el.textContent = p.date ? `Previewing ${label} on ${p.date}` : `Previewing ${label}`;
}

function sync(select: HTMLSelectElement, date: HTMLInputElement): void {
  const p = getWeatherPreview();
  select.value = p?.preset ?? '';
  date.value = p?.date ?? '';
  date.disabled = !p;
  status();
}

function apply(select: HTMLSelectElement, date: HTMLInputElement): void {
  const preset = select.value;
  setWeatherPreview(preset ? { preset, date: date.value || null } : null);
  sync(select, date);
}

/** wire the controls once. safe to call again (settings re-renders, tests) */
export function initWeatherPreviewSettings(): void {
  const select = document.getElementById(PRESET_ID) as HTMLSelectElement | null;
  const date = document.getElementById(DATE_ID) as HTMLInputElement | null;
  const reset = document.getElementById(RESET_ID);
  if (!select || !date) return;
  if (select.options.length <= 1) {
    for (const { key, label } of weatherPreviewPresets()) {
      const o = document.createElement('option');
      o.value = key;
      o.textContent = label;
      select.appendChild(o);
    }
  }
  sync(select, date);
  if (select.dataset.wired === '1') return;
  select.dataset.wired = '1';
  select.addEventListener('change', () => apply(select, date));
  date.addEventListener('change', () => apply(select, date));
  // the weather may finish loading after the page does: re-read on focus
  select.addEventListener('focus', status);
  reset?.addEventListener('click', () => {
    select.value = '';
    apply(select, date);
  });
}
