import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2112: Settings > Playlist Folder Style's rebuild said 'No "organize by
 * playlist" playlists to rebuild yet.' and nothing about where that switch is.
 * it lives on each playlist (Playlists, the ⋯ menu, Settings), so the empty
 * result says so and links there.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');

async function rebuild(payload: Record<string, unknown>, nav?: (page: string) => void) {
  document.body.innerHTML =
    '<button id="playlists-rebuild-btn">Rebuild</button><div id="playlists-rebuild-status"></div>';
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const run = new Function(
    'fetch',
    'navigateToPage',
    `${extractFunction('rebuildPlaylistFolders', SRC)}
     return rebuildPlaylistFolders;`,
  )(async () => ({ ok: true, json: async () => payload }), nav) as () => Promise<void>;
  await run();
  return document.getElementById('playlists-rebuild-status') as HTMLElement;
}

describe('Settings > playlist folders rebuild', () => {
  it('with nothing to rebuild, says where Organize by playlist is and links there', async () => {
    const nav = vi.fn();
    const status = await rebuild({ success: true, count: 0, results: [] }, nav);
    expect(status.textContent).toContain('Turn it on per playlist');
    expect(status.textContent).toContain("open a playlist's ⋯ menu, Settings.");
    const link = status.querySelector('a') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/sync');
    link.click();
    expect(nav).toHaveBeenCalledWith('sync');
  });

  it('a rebuild that did work keeps its summary', async () => {
    const status = await rebuild({
      success: true,
      count: 2,
      results: [{ linked: 3, copied: 1, removed_stale: 0 }],
    });
    expect(status.textContent).toContain('Rebuilt 2 playlist folders');
    expect(status.querySelector('a')).toBeNull();
  });
});
