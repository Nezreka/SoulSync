import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

// #2026: the Sources tab read only the music chain, so usenet carrying every
// video download sat under Available as "not in chain".
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const body = ['_srcAllChains', '_srcChainUses', '_srcTileMarkup', 'buildSourceTiles']
  .map((name) => extractFunction(name, source))
  .join('\n');

const SOURCES = [
  { id: 'soulseek', name: 'Soulseek', icon: null, emoji: 's' },
  { id: 'deezer_dl', name: 'Deezer', icon: null, emoji: 'd' },
  { id: 'torrent', name: 'Torrent', icon: null, emoji: 't' },
  { id: 'usenet', name: 'Usenet', icon: null, emoji: 'u' },
];
const CONFIG = { soulseek: 'a', deezer_dl: 'b', torrent: 'c', usenet: 'd', prowlarr: 'e' };

function render(music: string[], video: string[] | null, books: string[]) {
  document.body.innerHTML = '<div id="source-tile-grid"></div>';
  const build = new Function(
    'document',
    'HYBRID_SOURCES',
    'EXTRA_SOURCE_TILES',
    'SOURCE_CONFIG_ID_BY_SRC',
    'getHybridOrder',
    '_audiobookActiveSources',
    '_videoChainOrder',
    '_hybridSourceStatus',
    '_hybridSourceUnready',
    '_ssLastTestMessage',
    'INVERT_BRAND_MARKS',
    'escapeHtml',
    `${body}; return buildSourceTiles;`,
  )(
    document,
    SOURCES,
    [{ id: 'prowlarr', name: 'Indexers', icon: null, emoji: 'i' }],
    CONFIG,
    () => music,
    () => books,
    video,
    {},
    {},
    {},
    new Set(),
    (s: string) => String(s),
  );
  build();
  const groups: Record<string, string[]> = {};
  document.querySelectorAll('.src-group').forEach((g) => {
    const label = g.querySelector('.src-group-label')!.textContent!;
    groups[label] = [...g.querySelectorAll('.src-tile')].map((t) => {
      const chips = [...t.querySelectorAll('.src-tile-chip')].map((c) => c.textContent);
      return `${(t as HTMLElement).dataset.sourceId}:${chips.join('|')}`;
    });
  });
  return groups;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

it('puts a source used only by the video chain in use, labelled video', () => {
  const g = render(['deezer_dl', 'soulseek'], ['usenet'], []);
  expect(g['In use']).toEqual(['deezer_dl:Music #1', 'soulseek:Music #2', 'usenet:Video #1']);
  expect(g['Available']).toEqual(['torrent:not in a chain']);
});

it('names every chain a source is in', () => {
  const g = render(['soulseek'], ['torrent', 'soulseek'], ['torrent']);
  expect(g['In use']).toEqual(['soulseek:Music #1|Video #2', 'torrent:Video #1|Audiobooks #1']);
  expect(g['Available']).toEqual(['deezer_dl:not in a chain', 'usenet:not in a chain']);
});

it('still renders from the music chain before the video chain is read', () => {
  const g = render(['soulseek'], null, []);
  expect(g['In use']).toEqual(['soulseek:Music #1']);
  expect(g['Indexers']).toEqual(['prowlarr:torrent & usenet']);
});
