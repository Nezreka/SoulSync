/**
 * The silent auto-probe used to walk only the music chain, so a user whose
 * audiobooks come over torrent saw grey torrent and indexer tiles forever.
 * Test all is in the React sources model now (SETTINGS_PORT.md step 7a).
 */

import { afterEach, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { createSourcesModel } from '@/features/settings/tabs/sources-model';

const shell = window as unknown as Record<string, unknown>;
let probed: string[];

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.getAudiobookActiveSources;
});

async function run(musicMode: string, books: string[], videoChain: string[] = []) {
  probed = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      let data: unknown = { success: true, available: true, authenticated: true, reachable: true };
      if (url === '/api/video/downloads/config') {
        data = { download_mode: 'hybrid', hybrid_order: videoChain };
      } else if (url === '/api/test-connection') {
        probed.push((JSON.parse(init!.body as string) as { service: string }).service);
      } else {
        probed.push(url);
      }
      return Promise.resolve({ json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.getAudiobookActiveSources = () => books;
  const model = createSourcesModel({ store: createSettingsStore({ delay: 0 }) });
  model.load({
    download_source: { mode: musicMode, hybrid_order: ['deezer_dl', 'tidal', 'soulseek'] },
  });
  await model.testAll({ silent: true });
  return model.getState().status;
}

it('probes torrent and prowlarr when audiobooks use torrent and music does not', async () => {
  const status = await run('hybrid', ['torrent']);
  expect(status.torrent).toBe('ok');
  expect(status.prowlarr).toBe('ok');
  expect(probed).not.toContain('usenet_client');
});

it('probes the whole audiobook chain in hybrid mode', async () => {
  const status = await run('soulseek', ['usenet', 'torrent']);
  expect(status.usenet).toBe('ok');
  expect(status.torrent).toBe('ok');
  expect(status.prowlarr).toBe('ok');
});

it('leaves torrent alone when neither chain uses it', async () => {
  const status = await run('hybrid', ['soulseek']);
  expect(probed).not.toContain('torrent_client');
  expect(probed).not.toContain('prowlarr');
  expect(status.torrent).toBeUndefined();
});

it('probes the video chain too, so a usenet-only video setup gets a dot (#2026)', async () => {
  const status = await run('hybrid', ['soulseek'], ['usenet', 'extto']);
  expect(status.usenet).toBe('ok');
  expect(status.prowlarr).toBe('ok');
  expect(status.extto).toBeUndefined();
});
