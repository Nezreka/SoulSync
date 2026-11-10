import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2033: every ranked release says why it ranks where it does, and the
 * cross-source Auto pick compares the server's rank values instead of the
 * old flat score, so it takes what the wishlist and every other picker take.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-download-view.js'), 'utf8');
const ESC = JS.slice(JS.indexOf('function esc'), JS.indexOf('function toast'));
const SRC = JS.slice(JS.indexOf('function rankCompare'), JS.indexOf('function resultCardHTML'));

interface Row {
  accepted?: boolean;
  score?: number;
  rank?: number[];
  username?: string;
  breakdown?: { group: string; label: string; points: number }[];
}
// the vanilla file's functions on their own, as the other view tests load theirs
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const api = new Function(`${ESC}; ${SRC}; return { rankCompare, rankWhyHTML };`)() as {
  rankCompare: (a: Row, b: Row) => number;
  rankWhyHTML: (r: Row) => string;
};

describe('rankCompare', () => {
  it('compares the rank values in order', () => {
    // equal quality, the format decides; availability never gets a say
    const megusta = { rank: [1, 395, 100, 0, 0, 5, 600] };
    const other = { rank: [1, 395, 0, 15, 0, 50, 3000] };
    expect(api.rankCompare(megusta, other)).toBeGreaterThan(0);
    expect(api.rankCompare(other, megusta)).toBeLessThan(0);
    expect(api.rankCompare(megusta, { rank: [...megusta.rank] })).toBe(0);
  });

  it('falls back to accepted then score for a row without rank', () => {
    expect(
      api.rankCompare({ accepted: true, score: 1 }, { accepted: false, score: 900 }),
    ).toBeGreaterThan(0);
    expect(
      api.rankCompare({ accepted: true, score: 9 }, { accepted: true, score: 3 }),
    ).toBeGreaterThan(0);
  });

  it('is what the cross-source Auto pick uses', () => {
    const across = JS.slice(JS.indexOf('function _grabBestAcross'), JS.indexOf('function onClick'));
    expect(across).toContain('rankCompare(r, best) > 0');
    expect(across).not.toContain('r.score');
  });
});

describe('rankWhyHTML', () => {
  const row: Row = {
    username: '',
    breakdown: [
      { group: 'quality', label: 'WEB-DL 1080p tier (#8 on your ladder)', points: 355 },
      { group: 'quality', label: 'Preferred codec (HEVC)', points: 40 },
      { group: 'formats', label: 'Custom format: MeGusta', points: 100 },
      { group: 'tiebreak', label: '0.6 GB is 0.4 GB from your 1 GB preferred size', points: 0 },
    ],
  };

  it('lists every factor under its group, in compare order, with totals', () => {
    const html = api.rankWhyHTML(row);
    expect(html).toContain('<details class="vdl-why">');
    const q = html.indexOf('Quality');
    const f = html.indexOf('Custom formats');
    const t = html.indexOf('Tie-breaks');
    expect(q).toBeGreaterThan(-1);
    expect(f).toBeGreaterThan(q);
    expect(t).toBeGreaterThan(f);
    expect(html).toContain('<b>+395</b>');
    expect(html).toContain('Custom format: MeGusta');
    expect(html).toContain('<em>+100</em>');
    expect(html).toContain('closer wins');
    expect(html).toContain('then seeders, then the bigger file');
  });

  it('says free slots for a Soulseek row', () => {
    expect(api.rankWhyHTML({ ...row, username: 'peer' })).toContain('then free slots and queue');
  });

  it('escapes labels and draws nothing without a breakdown', () => {
    const html = api.rankWhyHTML({
      breakdown: [{ group: 'formats', label: '<b>x</b>', points: -5 }],
    });
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
    expect(html).toContain('<em>-5</em>');
    expect(html).not.toContain('Quality');
    expect(api.rankWhyHTML({})).toBe('');
  });

  it('is on every release card and pack card', () => {
    const card = JS.slice(JS.indexOf('function resultCardHTML'), JS.indexOf('var TRACK_LABEL'));
    expect(card).toContain('rankWhyHTML(r)');
    const pack = JS.slice(
      JS.indexOf('function _packCardHTML'),
      JS.indexOf('function _isSeasonPack'),
    );
    expect(pack).toContain('rankWhyHTML(r)');
  });
});

describe('preferred size setting', () => {
  const SETTINGS = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
  const INDEX = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

  it('has a movie and an episode slider that save the profile keys', () => {
    expect(INDEX).toContain('id="vq-movie-pref"');
    expect(INDEX).toContain('id="vq-episode-pref"');
    expect(SETTINGS).toContain(
      '_videoQuality.preferred_movie_gb = parseFloat(e.target.value) || 0',
    );
    expect(SETTINGS).toContain(
      '_videoQuality.preferred_episode_gb = parseFloat(e.target.value) || 0',
    );
    expect(SETTINGS).toContain("lab.textContent = v ? ('Around ' + v + ' GB') : 'Off'");
  });
});
