import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2127: Search > Releases showed only two trackers. With torrents on, the
 * tab searched two hardcoded public trackers (The Pirate Bay and 1337x, each
 * a named-indexer filter) plus EXT.to, so every other indexer in Prowlarr,
 * private trackers included, was never asked. It now searches every Prowlarr
 * torrent indexer in one tab, in every category, and names who answered.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-search.js'), 'utf8').replace(
  /^ {4}/gm,
  '',
);

function lift() {
  const config = SRC.slice(
    SRC.indexOf('var BASIC_SEARCH_SOURCES = {'),
    SRC.indexOf('var basicSeq'),
  );
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `function esc(s) { return String(s); }
     ${config}
     ${extractFunction('basicIdsFromDownloadConfig', SRC)}
     ${extractFunction('basicSearchBody', SRC)}
     ${extractFunction('basicIndexerHTML', SRC)}
     return { basicIdsFromDownloadConfig, basicSearchBody, basicIndexerHTML };`,
  )() as {
    basicIdsFromDownloadConfig: (c: object) => string[];
    basicSearchBody: (q: string, id: string) => Record<string, unknown>;
    basicIndexerHTML: (rows: { username?: string }[]) => string;
  };
}

describe('Releases searches every indexer (#2127)', () => {
  it('torrent mode searches all prowlarr torrent indexers, not two named trackers', () => {
    const { basicIdsFromDownloadConfig } = lift();
    expect(basicIdsFromDownloadConfig({ download_mode: 'torrent' })).toEqual(['prowlarr', 'extto']);
  });

  it('asks with no indexer filter and in every category', () => {
    const body = lift().basicSearchBody('Primal', 'prowlarr');
    expect(body.source).toBe('torrent');
    expect(body.indexer).toBeNull();
    expect(body.basic).toBe(true);
  });

  it('names each indexer that answered, busiest first', () => {
    const html = lift().basicIndexerHTML([
      { username: 'YGGTorrent' },
      { username: '1337x' },
      { username: 'YGGTorrent' },
    ]);
    expect(html.indexOf('YGGTorrent 2')).toBeGreaterThan(-1);
    expect(html.indexOf('YGGTorrent 2')).toBeLessThan(html.indexOf('1337x 1'));
    expect(lift().basicIndexerHTML([{ username: 'one' }])).toBe('');
  });
});
