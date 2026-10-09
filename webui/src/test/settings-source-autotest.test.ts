import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

// the silent auto-probe used to walk only the music chain, so a user whose
// audiobooks come over torrent saw grey torrent and indexer tiles forever.
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const body = ['_audiobookActiveSources', 'testAllSources']
  .map((name) => extractFunction(name, source))
  .join('\n');

let status: Record<string, string>;
let probed: string[];

function run(musicMode: string, bookMode: string, bookChain: string[]) {
  document.body.innerHTML = `
    <select id="download-source-mode"><option value="${musicMode}" selected></option></select>
    <select id="audiobook-download-mode"><option value="${bookMode}" selected></option></select>`;
  status = {};
  probed = [];
  const probe = (id: string) => () => {
    probed.push(id);
    return Promise.resolve(true);
  };
  const testAllSources = new Function(
    'document',
    'getHybridOrder',
    '_hybridSourceStatus',
    'HYBRID_SOURCE_PROBE',
    '_ssTestConn',
    '_ssLastTestWarned',
    'buildSourceTiles',
    'buildHybridSourceList',
    '_audiobookHybrid',
    'AUDIOBOOK_SOURCES',
    `${body}; return testAllSources;`,
  )(
    document,
    () => ['deezer', 'tidal', 'soulseek'],
    status,
    {
      deezer: probe('deezer'),
      tidal: probe('tidal'),
      soulseek: probe('soulseek'),
      torrent: probe('torrent'),
      usenet: probe('usenet'),
    },
    (svc: string) => {
      probed.push(svc);
      return Promise.resolve(true);
    },
    {},
    vi.fn(),
    vi.fn(),
    bookChain,
    ['torrent', 'usenet', 'soulseek'],
  );
  return testAllSources({ silent: true });
}

beforeEach(() => {
  document.body.innerHTML = '';
});

it('probes torrent and prowlarr when audiobooks use torrent and music does not', async () => {
  await run('hybrid', 'torrent', ['torrent', 'usenet', 'soulseek']);
  expect(status.torrent).toBe('ok');
  expect(status.prowlarr).toBe('ok');
  expect(probed).not.toContain('usenet');
});

it('probes the whole audiobook chain in hybrid mode', async () => {
  await run('soulseek', 'hybrid', ['usenet', 'torrent']);
  expect(status.usenet).toBe('ok');
  expect(status.torrent).toBe('ok');
  expect(status.prowlarr).toBe('ok');
});

it('leaves torrent alone when neither chain uses it', async () => {
  await run('hybrid', 'soulseek', ['torrent']);
  expect(probed).not.toContain('torrent');
  expect(probed).not.toContain('prowlarr');
  expect(status.torrent).toBeUndefined();
});
