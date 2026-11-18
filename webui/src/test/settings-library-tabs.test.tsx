/**
 * The Library tab's two media cards, Folders and Organization, are React
 * (SETTINGS_PORT.md steps 8a and 8b). Their own tests cover each card; this
 * keeps the rules they share: one tab order, every control in exactly one
 * panel, and the settings search landing
 * on a card's tab.
 */

import { act, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { LibraryTab } from '@/features/settings/tabs/library-folders';

// the search is shell.ts's (settings.js's until SETTINGS_PORT.md step V4b)
const source = readFileSync(resolve(process.cwd(), 'src/features/settings/shell.ts'), 'utf8');

function cards() {
  const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
  return render(
    <div id="settings-page">
      <LibraryTab store={store} part="folders" opened={0} active={false} />
      <LibraryTab store={store} part="organization" opened={0} active={false} />
    </div>,
  );
}

describe('Library settings media cards', () => {
  it('orders the tabs music, audiobooks, podcasts, video on both cards', () => {
    cards();
    const found = [...document.querySelectorAll('.stg-media-card')];
    expect(found).toHaveLength(2);
    for (const card of found)
      expect([...card.querySelectorAll('[role="tab"]')].map((t) => t.textContent)).toEqual([
        'Music',
        'Audiobooks',
        'Podcasts',
        'Video',
      ]);
  });

  it('keeps all four sets of controls in exactly one panel each', () => {
    cards();
    for (const [id, panel] of [
      ['transfer-path', 'folders-music-panel'],
      ['download-path', 'folders-music-panel'],
      ['video-movies-path', 'folders-video-panel'],
      ['audiobooks-path', 'folders-audiobooks-panel'],
      ['podcasts-path', 'folders-podcasts-panel'],
      ['template-album-path', 'organization-music-panel'],
      ['template-sample-path', 'organization-music-panel'],
      ['disc-label', 'organization-music-panel'],
      ['vo-movie-template', 'organization-video-panel'],
      ['template-audiobook-path', 'organization-audiobooks-panel'],
      ['audiobook-recycle-deletes', 'organization-audiobooks-panel'],
      ['audiobook-format-first', 'organization-audiobooks-panel'],
      ['audiobook-marketplace', 'organization-audiobooks-panel'],
      ['audiobook-write-nfo', 'organization-audiobooks-panel'],
      ['template-podcast-path', 'organization-podcasts-panel'],
      ['podcast-media-format', 'organization-podcasts-panel'],
    ]) {
      expect(document.querySelectorAll(`#${id}`).length).toBe(1);
      expect(document.getElementById(id)!.closest('[role="tabpanel"]')!.id).toBe(panel);
    }
  });

  it('the settings search opens a card on its tab by clicking it', () => {
    // filterSettings: the matched label's panel, then its tab
    expect(source).toContain(
      'document.querySelector<HTMLElement>(`[aria-controls="${panel.id}"]`)?.click();',
    );
    cards();
    act(() => document.getElementById('organization-video-tab')!.click());
    expect(document.getElementById('organization-video-panel')!.hidden).toBe(false);
    expect(document.getElementById('organization-music-panel')!.hidden).toBe(true);
    // the other card is its own
    expect(document.getElementById('folders-music-panel')!.hidden).toBe(false);
  });
});
