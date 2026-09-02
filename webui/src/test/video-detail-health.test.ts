import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The health band under the detail hero.
 *
 * It exists to answer the one question the hero can't: does automation have the
 * ids it needs to go find this thing. Everything else it could say - owned vs
 * wanted, coverage percent, episode counts, format badges - is already in the
 * meta line one row up, so a chip that repeats those is a regression, not a
 * feature. The first cut of this band shipped six of them.
 *
 * These run the REAL functions out of video-detail.js. The version of this file
 * that greps the source for chip literals passed happily while the band painted
 * a green "0/24 episodes" and an amber "Library ID: Missing" on every preview.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

type Chip = { label: string; value: string; mode: string };

/** renderHealth, wired to a scratch host node instead of the page. */
function mount(): { host: HTMLElement; render: (d: unknown) => void } {
  const host = document.createElement('div');
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    function q() { return host; }
  `;
  const bodies = ['countLabel', 'healthChip', 'idChip', 'renderHealth']
    .map((n) => extractFunction(n, SRC))
    .join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const render = new Function('host', `${preamble}\n${bodies}\nreturn renderHealth;`)(host) as (
    d: unknown,
  ) => void;
  return { host, render };
}

function chips(host: HTMLElement): Chip[] {
  return Array.from(host.querySelectorAll('.vd-health-chip')).map((el) => ({
    label: el.querySelector('.vd-health-k')?.textContent ?? '',
    value: el.querySelector('.vd-health-v')?.textContent ?? '',
    mode:
      Array.from(el.classList)
        .find((c) => c.startsWith('vd-health-chip--'))
        ?.replace('vd-health-chip--', '') ?? '',
  }));
}

function labels(host: HTMLElement): string[] {
  return chips(host).map((c) => c.label);
}

function band(d: unknown): { host: HTMLElement; chips: Chip[] } {
  const { host, render } = mount();
  render(d);
  return { host, chips: chips(host) };
}

const SHOW = {
  kind: 'show',
  source: 'library',
  id: 41,
  tmdb_id: 1396,
  tvdb_id: 81189,
  imdb_id: 'tt0903747',
  episode_total: 62,
  episode_owned: 0,
};

describe('the detail health band', () => {
  it('flags an identity gap and only an identity gap', () => {
    const { chips: c } = band({ ...SHOW, tvdb_id: null });
    expect(c.find((x) => x.label === 'TVDB')).toEqual({
      label: 'TVDB',
      value: 'Missing',
      mode: 'missing',
    });
    expect(c.find((x) => x.label === 'TMDB')?.mode).toBe('ok');
    expect(c.find((x) => x.label === 'IMDb')?.mode).toBe('ok');
  });

  it('never restates what the hero meta line already says', () => {
    // owned:false, 0 of 62 episodes, a file with a quality - every hook the old
    // band hung a duplicate chip on.
    const { host } = band({ ...SHOW, owned: false, _vw_watched: true, file: { quality: '1080p' } });
    expect(labels(host)).toEqual(['Library ID', 'TMDB', 'TVDB', 'IMDb']);
  });

  it('does not report a coverage verdict, green or otherwise', () => {
    // "0/62 episodes" is a truthy string, which is how the first cut painted a
    // show you own none of in the same green as a healthy one.
    const { chips: c } = band(SHOW);
    expect(c.some((x) => x.value.includes('/'))).toBe(false);
    expect(c.every((x) => x.mode !== 'ok' || x.value !== 'Missing')).toBe(true);
  });

  it('omits the library id on a preview instead of flagging it missing', () => {
    // A tmdb preview has no library row yet. That is the normal state of every
    // browsed title, not a gap worth an amber chip.
    const { host } = band({ kind: 'movie', source: 'tmdb', tmdb_id: 27205, imdb_id: 'tt1375666' });
    expect(labels(host)).toEqual(['TMDB', 'IMDb']);
  });

  it('keeps the library id once the title resolves to a row', () => {
    const { chips: c } = band({ kind: 'movie', source: 'library', id: 9, tmdb_id: 27205, imdb_id: 'tt1375666' });
    expect(c[0]).toEqual({ label: 'Library ID', value: '9', mode: 'ok' });
  });

  it('leaves TVDB out for a movie', () => {
    const { host } = band({ kind: 'movie', source: 'library', id: 9, tmdb_id: 27205, imdb_id: null });
    expect(labels(host)).toEqual(['Library ID', 'TMDB', 'IMDb']);
  });

  it('states a youtube channel by the id the downloader keys on', () => {
    const { host, chips: c } = band({
      kind: 'channel',
      source: 'youtube',
      id: 'UCabc',
      handle: '@someone',
      episode_owned: 1,
      episode_total: 812,
    });
    expect(labels(host)).toEqual(['Channel ID', 'Handle', 'Downloaded']);
    expect(c[2]).toEqual({ label: 'Downloaded', value: '1 download', mode: 'neutral' });
  });

  it('claims no video total for a channel, so it claims no missing count', () => {
    // video-detail.js already refuses to print "N videos" in the meta line:
    // youtube does not expose a number worth trusting. A "Missing 811 videos"
    // verdict derived from that number is the same lie with a scarier face.
    const { host } = band({ kind: 'channel', source: 'youtube', id: 'UCabc', episode_owned: 1, episode_total: 812 });
    expect(labels(host)).not.toContain('Videos');
    expect(labels(host)).not.toContain('Missing');
  });

  it('hides the band when there is no title', () => {
    const { host, render } = mount();
    render(SHOW);
    expect(host.hidden).toBe(false);
    render(null);
    expect(host.hidden).toBe(true);
    expect(host.innerHTML).toBe('');
  });
});

/**
 * The count behind the band's one number, and the season pills' "N / N eps".
 * Three builders hardcoded `episode_owned: 0`, so a fully-downloaded playlist
 * read "0 downloads" forever.
 */
describe('youtube owned counts', () => {
  function loadYt() {
    const preamble = `
      var ytVideoMap = {};
      var data = null;
      var ytFilter = { q: '', sort: 'newest' };
      function ytProx(u) { return u || ''; }
    `;
    const bodies = ['ytOwnedCount', 'ytEpisodeOf', 'ytPlaylistToShow', 'ytFlatSeason']
      .map((n) => extractFunction(n, SRC))
      .join('\n');
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      `${preamble}\n${bodies}\nreturn { ytOwnedCount: ytOwnedCount, ytPlaylistToShow: ytPlaylistToShow, ytFlatSeason: ytFlatSeason };`,
    )() as {
      ytOwnedCount: (v: unknown[]) => number;
      ytPlaylistToShow: (r: unknown) => { episode_owned: number; seasons: { episode_owned: number }[] };
      ytFlatSeason: (v: unknown[]) => { episode_owned: number };
    };
  }

  const VIDEOS = [
    { youtube_id: 'a', title: 'one', downloaded: true },
    { youtube_id: 'b', title: 'two', downloaded: false },
    { youtube_id: 'c', title: 'three', downloaded: true },
  ];

  it('counts what is on disk, not what is wished', () => {
    const yt = loadYt();
    expect(yt.ytOwnedCount(VIDEOS)).toBe(2);
    expect(yt.ytOwnedCount([{ youtube_id: 'd', wished: true, downloaded: false }])).toBe(0);
    expect(yt.ytOwnedCount([])).toBe(0);
  });

  it('gives a playlist a real downloaded count', () => {
    const yt = loadYt();
    const pl = yt.ytPlaylistToShow({ playlist: { playlist_id: 'PL1', videos: VIDEOS } });
    expect(pl.episode_owned).toBe(2);
    expect(pl.seasons[0].episode_owned).toBe(2);
  });

  it('keeps the count when a channel collapses into a flat search season', () => {
    const yt = loadYt();
    expect(yt.ytFlatSeason(VIDEOS).episode_owned).toBe(2);
  });
});

describe('the health band is mounted and reset', () => {
  const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

  it('mounts in both the movie and show detail shells', () => {
    expect(HTML.match(/data-vd-health/g) ?? []).toHaveLength(2);
  });

  it('is hidden on load so it cannot show the previous title', () => {
    // resetExtras runs before the fetch; without the band in that list the
    // chips from the last page stay up while the next one loads.
    const reset = SRC.slice(SRC.indexOf('function resetExtras'), SRC.indexOf('function resetExtras') + 1200);
    expect(reset).toContain('[data-vd-health]');
  });
});
