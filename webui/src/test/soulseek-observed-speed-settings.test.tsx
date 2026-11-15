/**
 * The observed-speed fallback (a slow Soulseek transfer gives way to the next
 * candidate) loads and saves both of its values. The Soulseek source panel is
 * React now (SETTINGS_PORT.md step 7f): this runs the real panel.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { createSettingsStore, type SettingsPayload } from '@/features/settings/settings-store';
import { SoulseekPanel } from '@/features/settings/tabs/source-panels';

function setup(soulseek: Record<string, unknown>) {
  const posted: SettingsPayload[] = [];
  const store = createSettingsStore({
    post: (body) => {
      posted.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  act(() => store.hydrate({ soulseek }));
  render(<SoulseekPanel store={store} />);
  const box = (id: string) => document.getElementById(id) as HTMLInputElement;
  return { store, posted, box };
}

it.each(['0', '250', '1200'])('loads and saves the observed-speed minimum %s', async (value) => {
  const { store, posted, box } = setup({ min_observed_download_speed_kbps: Number(value) });
  const field = box('soulseek-min-observed-download-speed');
  expect(field.value).toBe(value);
  // typed back in, it saves as the number
  fireEvent.change(field, { target: { value: '7' } });
  fireEvent.change(field, { target: { value } });
  await waitFor(() => expect(store.hasPending()).toBe(false));
  expect(posted.at(-1)).toEqual({ soulseek: { min_observed_download_speed_kbps: Number(value) } });
});

it.each([true, false])('loads and saves observed-speed enabled=%s', async (enabled) => {
  const { store, posted, box } = setup({ observed_speed_fallback_enabled: enabled });
  const tick = box('soulseek-observed-speed-fallback-enabled');
  expect(tick.checked).toBe(enabled);
  fireEvent.click(tick);
  fireEvent.click(tick);
  await waitFor(() => expect(store.hasPending()).toBe(false));
  expect(posted.at(-1)).toEqual({ soulseek: { observed_speed_fallback_enabled: enabled } });
});

it('saves nothing for the setting until it is changed, instead of overwriting it', async () => {
  const { store, posted } = setup({});
  await act(() => store.flush());
  expect(posted).toEqual([]);
});
