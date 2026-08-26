import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Basic Search's Identify → grab path, EXECUTED.
 *
 * Basic Search shows hits from five sources that are fetched in three different
 * ways: Soulseek needs a peer + filename, Prowlarr indexers and EXT.to need a
 * magnet/NZB link. Every one of them now goes through ONE identify modal, so the
 * only thing standing between "the right release" and "a download that never
 * starts" is this descriptor picking the right carrier fields per source.
 *
 * A source-text assertion cannot see that. `source: 'extto'` looked correct in
 * the file right up until the monitor tried to poll slskd for a torrent, so
 * these run the real functions out of the real file over every source the tab
 * offers.
 */

/** The IIFE body, de-indented so extractFunction's `^function` anchor matches. */
function searchSource(): string {
  const raw = readFileSync(resolve(process.cwd(), 'static/video/video-search.js'), 'utf8');
  expect(
    raw.includes('`'),
    'video-search.js grew a template literal — the dedent below is no longer safe',
  ).toBe(false);
  return raw.replace(/^ {4}/gm, '');
}

interface Row {
  title?: string;
  filename?: string;
  username?: string;
  download_url?: string;
  magnet_uri?: string;
  indexer_id?: string;
  protocol?: string;
  info_url?: string;
  magnet_id?: string;
  season?: number | null;
  episode?: number | null;
  accepted?: boolean;
  size_bytes?: number;
  files?: unknown[];
}
interface Lifted {
  basicHitGrabbable: (r: Row, sourceId: string) => boolean;
  identifyCanGrab: () => boolean;
  setIdentify: (row: Row, sourceId: string) => void;
  basicDefaultIdentifyMode: (r: Row, category: string) => string;
  identifyGrabDescriptor: (
    r: Row,
    sourceId: string,
    siblings: Row[] | null,
  ) => Record<string, unknown>;
  buildPayload: (
    row: Row,
    sourceId: string,
    mode: string,
    item: Record<string, unknown>,
    fields: { season: number | null; episode: number | null },
  ) => Record<string, any>;
}

/** Evaluate the three pure helpers with only BASIC_SEARCH_SOURCES in scope. */
function lift(): Lifted {
  const src = searchSource();
  const config = src.slice(
    src.indexOf('var BASIC_SEARCH_SOURCES = {'),
    src.indexOf('var BASIC_TORRENT_SOURCES'),
  );
  expect(config).toContain('soulseek');
  const body = [
    config,
    extractFunction('canFetchRelease', src),
    extractFunction('basicHitGrabbable', src),
    extractFunction('basicDefaultIdentifyMode', src),
    extractFunction('identifyGrabDescriptor', src),
    // freshGrabPayload reads two things from outside itself: the modal state and the
    // season/episode inputs. Stub only those, so the payload assembly under test is real.
    'var freshIdentify = null; var _fields = { season: null, episode: null };',
    "function freshNumInput(sel) { return sel.indexOf('season') !== -1 ? _fields.season : _fields.episode; }",
    extractFunction('freshGrabPayload', src),
    extractFunction('identifyCanGrab', src),
    `function setIdentify(row, sourceId) {
       freshIdentify = { row: row, mode: 'movie', selected: { tmdb_id: 1, title: 'x' },
                         grab: identifyGrabDescriptor(row, sourceId, [row]) };
     }`,
    `function buildPayload(row, sourceId, mode, item, fields) {
       _fields = fields;
       freshIdentify = { row: row, mode: mode, selected: item,
                         grab: identifyGrabDescriptor(row, sourceId, [row]) };
       return freshGrabPayload();
     }`,
    'return { basicHitGrabbable: basicHitGrabbable, basicDefaultIdentifyMode: basicDefaultIdentifyMode,' +
      ' identifyGrabDescriptor: identifyGrabDescriptor, buildPayload: buildPayload,' +
      ' identifyCanGrab: identifyCanGrab, setIdentify: setIdentify };',
  ].join('\n');
  return new Function(body)() as Lifted;
}

const SOULSEEK: Row = {
  title: 'Silo S03E08 1080p WEB',
  filename: '@@silo/Silo.S03E08.mkv',
  username: 'peer1',
  season: 3,
  episode: 8,
  size_bytes: 5,
};
const TORRENT: Row = {
  title: 'Silo S03E08 1080p WEB',
  download_url: 'https://prowlarr/dl.torrent',
  magnet_uri: 'magnet:?xt=1',
  indexer_id: 42,
  protocol: 'torrent',
  season: 3,
  episode: 8,
} as unknown as Row;
const EXTTO: Row = {
  title: 'Silo S03E08 1080p WEB',
  magnet_uri: 'magnet:?xt=2',
  season: 3,
  episode: 8,
};

describe('Basic Search grab descriptor', () => {
  it('sends a Soulseek hit as a Soulseek grab, with the other accepted hits as its retry pool', () => {
    const { identifyGrabDescriptor } = lift();
    const siblings: Row[] = [
      SOULSEEK,
      { username: 'peer2', filename: 'other.mkv', accepted: true, size_bytes: 9 },
      { username: 'peer3', filename: 'rejected.mkv', accepted: false },
      { username: '', filename: 'nouser.mkv', accepted: true },
    ];
    const d = identifyGrabDescriptor(SOULSEEK, 'soulseek', siblings);
    expect(d.source).toBe('soulseek');
    expect(d.username).toBe('peer1');
    expect(d.filename).toBe('@@silo/Silo.S03E08.mkv');
    // only OTHER accepted hits with a peer — the row itself, rejects and blanks are out
    expect(d.candidates).toEqual([
      {
        username: 'peer2',
        filename: 'other.mkv',
        size_bytes: 9,
        quality_label: undefined,
        title: undefined,
      },
    ]);
    expect(d.download_url).toBeUndefined();
  });

  it('sends a Prowlarr torrent hit with its carriers and its own indexer id', () => {
    const { identifyGrabDescriptor } = lift();
    const d = identifyGrabDescriptor(TORRENT, 'thepiratebay', [TORRENT]);
    expect(d.source).toBe('torrent');
    expect(d.download_url).toBe('https://prowlarr/dl.torrent');
    expect(d.magnet_uri).toBe('magnet:?xt=1'); // #1139 fallback when the .torrent fetch fails
    expect(d.indexer_id).toBe(42); // the hit's real id, not the tab's slug
    expect(d.candidates).toEqual([]);
  });

  it('falls back to the tab for an indexer id the hit did not carry', () => {
    const { identifyGrabDescriptor } = lift();
    expect(
      identifyGrabDescriptor({ title: 'x', magnet_uri: 'magnet:?y' }, '1337x', null).indexer_id,
    ).toBe('1337x');
  });

  it('keeps EXT.to hits branded as EXT.to and lets the server map them to a torrent', () => {
    const { identifyGrabDescriptor } = lift();
    const d = identifyGrabDescriptor(EXTTO, 'extto', null);
    expect(d.source).toBe('extto');
    expect(d.username).toBe('EXT.to');
    expect(d.indexer_id).toBe('extto');
    // fresh rows carry no `filename`; the release title is what identifies them
    expect(d.filename).toBe('Silo S03E08 1080p WEB');
    expect(d.download_url).toBe('magnet:?xt=2'); // magnet stands in for a missing .torrent
  });

  it('carries the EXT.to detail page so the server can resolve the magnet at grab time', () => {
    // EXT.to lists releases WITHOUT magnets — each one costs its own Cloudflare-
    // challenged detail fetch, so the list ships link-less and the server resolves
    // the one you pick. Dropping info_url here is what made every hit unreachable.
    const { identifyGrabDescriptor } = lift();
    const listed: Row = {
      title: 'Interstellar 2014 1080p BluRay',
      info_url: 'https://ext.to/interstellar-2014-1014669/',
      magnet_id: '1014669',
    } as Row;
    const d = identifyGrabDescriptor(listed, 'extto', null);
    expect(d.info_url).toBe('https://ext.to/interstellar-2014-1014669/');
    expect(d.magnet_id).toBe('1014669');
    expect(d.download_url).toBeUndefined(); // there genuinely isn't one yet
  });

  it('sends usenet hits to the usenet client', () => {
    const { identifyGrabDescriptor } = lift();
    expect(
      identifyGrabDescriptor({ title: 'x', download_url: 'https://i/x.nzb' }, 'usenet', null)
        .source,
    ).toBe('usenet');
  });
});

describe('Basic Search grabbability', () => {
  it('needs a peer and a file for Soulseek, and a link for everything else', () => {
    const { basicHitGrabbable } = lift();
    expect(basicHitGrabbable(SOULSEEK, 'soulseek')).toBe(true);
    expect(basicHitGrabbable({ title: 'x', username: 'peer' }, 'soulseek')).toBe(false); // listing with no file
    expect(basicHitGrabbable(SOULSEEK, 'thepiratebay')).toBe(false); // no magnet/.torrent
    expect(basicHitGrabbable(TORRENT, 'thepiratebay')).toBe(true);
    expect(basicHitGrabbable(EXTTO, 'extto')).toBe(true); // magnet only is enough
    // an EXT.to hit with ONLY a detail page is still grabbable — the magnet is
    // resolved server-side at grab time. Requiring a link here is what put 'No link'
    // on every single EXT.to result.
    expect(basicHitGrabbable({ title: 'x', info_url: 'https://ext.to/x-1/' } as Row, 'extto')).toBe(
      true,
    );
    // ...but a detail page means nothing to a Prowlarr indexer, which must have a link
    expect(
      basicHitGrabbable({ title: 'x', info_url: 'https://tpb/x' } as Row, 'thepiratebay'),
    ).toBe(false);
  });
});

describe('Basic Search identify mode', () => {
  it('reads the release name first — a search of release titles is not told what it found', () => {
    const { basicDefaultIdentifyMode } = lift();
    expect(basicDefaultIdentifyMode({ season: 3, episode: 8 }, 'all')).toBe('episode');
    expect(basicDefaultIdentifyMode({ season: 3, episode: null }, 'all')).toBe('season');
    expect(basicDefaultIdentifyMode({ season: null, episode: null }, 'all')).toBe('movie');
    // the release name WINS over the category — a movie category cannot make S03E08 a movie,
    // and a TV category cannot make a season pack a single episode (which is what filing it
    // as an episode would do: one file judged, the rest of the pack abandoned on disk)
    expect(basicDefaultIdentifyMode({ season: 3, episode: 8 }, 'movie')).toBe('episode');
    expect(basicDefaultIdentifyMode({ season: 3, episode: null }, 'tv')).toBe('season');
    expect(basicDefaultIdentifyMode({ season: 3, episode: null }, 'anime')).toBe('season');
  });

  it('falls back to the category only when the name parses to nothing', () => {
    const { basicDefaultIdentifyMode } = lift();
    expect(basicDefaultIdentifyMode({ season: null, episode: null }, 'tv')).toBe('episode');
    expect(basicDefaultIdentifyMode({ season: null, episode: null }, 'anime')).toBe('episode');
    expect(basicDefaultIdentifyMode({ season: null, episode: null }, 'doc')).toBe('movie');
  });
});

/**
 * The payloads the modal actually POSTs. These are the exact shapes
 * tests/test_video_search_recents.py replays against the real grab endpoint, so
 * the two halves of the boundary are checked against the same bodies rather than
 * against each other's assumptions.
 */
describe('Basic Search grab payload', () => {
  const SHOW = { tmdb_id: 125988, title: 'Silo', year: 2023, poster: '/p.jpg' };
  const FILM = { tmdb_id: 157336, title: 'Interstellar', year: 2014, poster: '/i.jpg' };

  it('scopes an episode grab to that episode', () => {
    const p = lift().buildPayload(TORRENT, 'thepiratebay', 'episode', SHOW, {
      season: 3,
      episode: 8,
    });
    expect(p.kind).toBe('show');
    expect(p.source).toBe('torrent');
    expect(p.title).toBe('Silo'); // the TMDB title, not the release name
    expect(p.release_title).toBe('Silo S03E08 1080p WEB');
    expect(p.media_id).toBe(125988);
    expect(p.media_source).toBe('tmdb');
    expect(p.search_ctx).toEqual({
      scope: 'episode',
      title: 'Silo',
      year: 2023,
      season: 3,
      episode: 8,
    });
  });

  it('scopes a season grab to the season with NO episode — that is what makes the monitor map the folder', () => {
    const p = lift().buildPayload(TORRENT, 'thepiratebay', 'season', SHOW, {
      season: 3,
      episode: 8,
    });
    expect(p.kind).toBe('show');
    expect(p.search_ctx.scope).toBe('season');
    expect(p.search_ctx.season).toBe(3);
    expect(p.search_ctx.episode).toBeNull();
  });

  it('scopes a movie grab with no season/episode at all', () => {
    const p = lift().buildPayload(
      { title: 'Interstellar 2014 1080p BluRay', magnet_uri: 'magnet:?m' },
      'extto',
      'movie',
      FILM,
      { season: null, episode: null },
    );
    expect(p.kind).toBe('movie');
    expect(p.source).toBe('extto');
    expect(p.search_ctx).toEqual({ scope: 'movie', title: 'Interstellar', year: 2014 });
    expect(p.search_ctx.season).toBeUndefined();
  });

  it('carries the Soulseek peer and never leaks the pack file list onto the wire', () => {
    const p = lift().buildPayload(SOULSEEK, 'soulseek', 'episode', SHOW, { season: 3, episode: 8 });
    expect(p.source).toBe('soulseek');
    expect(p.username).toBe('peer1');
    expect(p.filename).toBe('@@silo/Silo.S03E08.mkv');
    expect('files' in p).toBe(false); // grab-pack's fan-out list, not a grab field
  });
});

/**
 * The card and the modal must never disagree about whether a release is fetchable.
 *
 * They did: `basicHitGrabbable` was taught that an EXT.to detail page counts as a
 * link, and `identifyCanGrab` — a second copy of the same rule, over the descriptor
 * instead of the row — was not. Every EXT.to hit then showed an Identify button that
 * opened a modal whose Start download stayed greyed out no matter what you picked.
 * They share one function now; this proves it over every source.
 */
describe('the card and the modal agree on what is grabbable', () => {
  const CASES: { label: string; row: Row; sourceId: string; grabbable: boolean }[] = [
    {
      label: 'EXT.to hit with only a detail page',
      sourceId: 'extto',
      grabbable: true,
      row: { title: 'Interstellar 2014 1080p', info_url: 'https://ext.to/x-1/', magnet_id: '1' },
    },
    { label: 'EXT.to hit with a magnet', sourceId: 'extto', grabbable: true, row: EXTTO },
    {
      label: 'EXT.to hit with nothing at all',
      sourceId: 'extto',
      grabbable: false,
      row: { title: 'Interstellar 2014 1080p' },
    },
    {
      label: 'Prowlarr torrent with a link',
      sourceId: 'thepiratebay',
      grabbable: true,
      row: TORRENT,
    },
    {
      label: 'Prowlarr torrent with only a detail page',
      sourceId: 'thepiratebay',
      grabbable: false,
      row: { title: 'x', info_url: 'https://tpb/x' },
    },
    {
      label: 'Soulseek hit with a peer and a file',
      sourceId: 'soulseek',
      grabbable: true,
      row: SOULSEEK,
    },
    {
      label: 'Soulseek hit with no file',
      sourceId: 'soulseek',
      grabbable: false,
      row: { title: 'x', username: 'peer1' },
    },
    {
      label: 'usenet hit with an NZB',
      sourceId: 'usenet',
      grabbable: true,
      row: { title: 'x', download_url: 'https://i/x.nzb' },
    },
  ];

  it.each(CASES)('$label', ({ row, sourceId, grabbable }) => {
    const l = lift();
    expect(l.basicHitGrabbable(row, sourceId), 'the result card disagreed').toBe(grabbable);
    l.setIdentify(row, sourceId);
    expect(l.identifyCanGrab(), 'the modal disagreed with the card').toBe(grabbable);
  });
});
