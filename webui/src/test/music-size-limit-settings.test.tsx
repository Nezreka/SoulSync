/**
 * The music size cap (MB per minute) loads and saves the way settings.js did.
 * Filtering is React now (src/features/settings/tabs/downloads-tab.tsx,
 * SETTINGS_PORT.md step 5): it was settings.js's load line and collector.
 */

import { fireEvent, render, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import type { SettingsPayload } from '@/features/settings/settings-store';

import { createSettingsStore } from '@/features/settings/settings-store';
import { DownloadsTab } from '@/features/settings/tabs/downloads-tab';

function tab(saved: SettingsPayload) {
  const posted: SettingsPayload[] = [];
  const store = createSettingsStore({
    post: (body) => {
      posted.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  store.hydrate(saved);
  render(<DownloadsTab store={store} part="filtering" opened={1} active />);
  const input = document.getElementById('music-max-mb-per-minute') as HTMLInputElement;
  return { store, posted, input };
}

it.each(['0', '10', '0.5'])('loads and saves the size cap %s', async (value) => {
  const { posted, input } = tab({ download_source: { max_mb_per_minute: Number(value) } });
  expect(input.value).toBe(value);
  fireEvent.change(input, { target: { value: '7' } });
  fireEvent.change(input, { target: { value } });
  await waitFor(() =>
    expect(posted.at(-1)).toEqual({ download_source: { max_mb_per_minute: Number(value) } }),
  );
});

it('a negative or unreadable cap saves 0, never a negative number', async () => {
  const { posted, input } = tab({});
  fireEvent.change(input, { target: { value: '-4' } });
  await waitFor(() => expect(posted.at(-1)).toEqual({ download_source: { max_mb_per_minute: 0 } }));
});

it('leaves the stored cap alone until the field is changed', () => {
  const { store } = tab({ download_source: { max_mb_per_minute: 12 } });
  expect(store.hasPending()).toBe(false);
});
