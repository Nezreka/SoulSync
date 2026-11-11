import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * One page, no tabs.
 *
 * A library show's Episodes tab and its Your library tab were the same list
 * twice. The episode list (vanilla) now carries each episode's file itself:
 * one quiet line on the row, a tick box to act on several, and a slot the
 * React file cards mount into when the row is opened. The library data comes
 * from the React island (setLibrary); without it the rows are what they were.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

type Lib = { can_manage: boolean; eps: Record<number, unknown>; seasons?: Record<number, unknown> };

function harness(lib: Lib | null, source = 'library') {
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    function sizedArt(u) { return u || ''; }
    function mediaRes(r) { return r || ''; }
    function runtimeLabel(m) { return m ? m + 'm' : ''; }
    function prettyCodec(c) { return c ? String(c).toUpperCase() : ''; }
    function channelsLabel(n) { return n === 6 ? '5.1' : n === 2 ? 'Stereo' : ''; }
    function fmtBytes(n) { return n ? (Math.round(n / 1073741824 * 10) / 10) + ' GB' : ''; }
    var selectedSeason = 1;
    var data = { source: ${JSON.stringify(source === 'library' ? undefined : source)}, kind: 'show', id: 5 };
    var currentSource = ${JSON.stringify(source)};
    var epPicked = {};
    var libData = ${lib ? JSON.stringify({ can_manage: lib.can_manage }) : 'null'};
    var libEps = ${JSON.stringify(lib?.eps ?? {})};
    var libSeasons = ${JSON.stringify(lib?.seasons ?? {})};
    var _RES_TONE = { '4K': 'uhd', '1080p': 'hd', '720p': 'mid', 'SD': 'sd' };
    window.VideoGrab = {};
  `;
  const fns = [
    'libEp',
    'libBest',
    'libRange',
    'libFileLine',
    'libSeasonFacts',
    'episodeReportAttrs',
    'epAired',
    'epBadge',
    'epWatchedItem',
    'epPop',
    'episodeRow',
  ]
    .map((name) => extractFunction(name, JS))
    .join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `${preamble}\n${fns}\nreturn { episodeRow: episodeRow, libSeasonFacts: libSeasonFacts, pick: epPicked };`,
  )() as {
    episodeRow: (e: unknown) => string;
    libSeasonFacts: (s: unknown) => string;
    pick: Record<string, boolean>;
  };
}

function dom(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  return host;
}

const EP = {
  id: 11,
  episode_number: 1,
  title: 'Pilot',
  owned: true,
  resolution: '1080',
  air_date: '2008-01-20',
};
const FILE = {
  id: 31,
  best: true,
  quality: 'WEBDL-1080p',
  resolution_label: '1080p',
  video_codec: 'h264',
  audio_codec: 'eac3',
  audio_channels: 6,
  atmos: true,
  dynamic_range: 'DV',
  size_bytes: 5.7 * 1073741824,
  below_cutoff: false,
};

describe('an episode row carries its file', () => {
  it('says the file in one line, and gives the cards a slot above the extras', () => {
    const host = dom(harness({ can_manage: true, eps: { 11: { files: [FILE] } } }).episodeRow(EP));
    expect(host.querySelector('.vd-ep-file-res')?.textContent).toBe('1080p');
    expect(host.querySelector('.vd-ep-file-facts')?.textContent).toBe(
      'WEBDL-1080p · Dolby Vision · EAC3 5.1 Atmos · 5.7 GB',
    );
    expect(host.querySelector('.vd-ep-file-chip')).toBeNull();
    const slot = host.querySelector('[data-vd-ep-files="11"]') as HTMLElement;
    expect(slot.hidden).toBe(true);
    // row, then the cards, then the guest-star panel
    expect(slot.previousElementSibling?.matches('[data-vd-ep-key]')).toBe(true);
    expect(slot.nextElementSibling?.matches('[data-vd-ep-panel]')).toBe(true);
    // the resolution is on the file line now, not said twice in the meta
    expect(host.querySelector('.vd-ep-rt')?.textContent).not.toContain('1080');
  });

  it('adds a chip only when it says something', () => {
    const below = { ...FILE, best: false, below_cutoff: true, resolution_label: '720p' };
    const host = dom(
      harness({
        can_manage: true,
        eps: { 11: { files: [below, { ...FILE, best: false, id: 32 }] } },
      }).episodeRow(EP),
    );
    const chips = [...host.querySelectorAll('.vd-ep-file-chip')].map((c) => c.textContent);
    expect(chips).toEqual(['Below your target', '2 copies']);
  });

  it('leads with the best copy, whatever order the files came in', () => {
    const small = { ...FILE, id: 30, best: false, quality: 'HDTV-720p', resolution_label: '720p' };
    const host = dom(
      harness({ can_manage: true, eps: { 11: { files: [small, FILE] } } }).episodeRow(EP),
    );
    expect(host.querySelector('.vd-ep-file-facts')?.textContent).toContain('WEBDL-1080p');
  });

  it('offers the tick box to an admin only, and remembers a tick across a re-render', () => {
    const admin = harness({ can_manage: true, eps: { 11: { files: [FILE] } } });
    expect(dom(admin.episodeRow(EP)).querySelector('[data-vd-ep-check="11"]')).not.toBeNull();
    admin.pick['11'] = true;
    expect(
      (dom(admin.episodeRow(EP)).querySelector('[data-vd-ep-check="11"]') as HTMLInputElement)
        .checked,
    ).toBe(true);
    const viewer = harness({ can_manage: false, eps: { 11: { files: [FILE] } } });
    const row = dom(viewer.episodeRow(EP));
    expect(row.querySelector('[data-vd-ep-check]')).toBeNull();
    // the file itself is still said
    expect(row.querySelector('.vd-ep-file')).not.toBeNull();
  });

  it('is the row it always was until the library data arrives', () => {
    const host = dom(harness(null).episodeRow(EP));
    expect(host.querySelector('.vd-ep-file')).toBeNull();
    expect(host.querySelector('[data-vd-ep-files]')).toBeNull();
    expect(host.querySelector('[data-vd-ep-check]')).toBeNull();
    expect(host.querySelector('.vd-ep-rt')?.textContent).toContain('1080');
  });

  it('a missing episode has no file line, no box and no slot', () => {
    const host = dom(
      harness({ can_manage: true, eps: { 11: { files: [] } } }).episodeRow({ ...EP, owned: false }),
    );
    expect(host.querySelector('.vd-ep-file')).toBeNull();
    expect(host.querySelector('[data-vd-ep-files]')).toBeNull();
    expect(host.querySelector('[data-vd-ep-check]')).toBeNull();
  });

  it('a preview from tmdb never borrows a library episode with the same id', () => {
    const host = dom(
      harness({ can_manage: true, eps: { 11: { files: [FILE] } } }, 'tmdb').episodeRow(EP),
    );
    expect(host.querySelector('.vd-ep-file')).toBeNull();
  });
});

describe('the season line', () => {
  it('says the season’s size and its quality mix', () => {
    const lib = {
      can_manage: true,
      eps: {},
      seasons: {
        2: {
          size_bytes: 33.2 * 1073741824,
          res_mix: [
            { label: '1080p', count: 10 },
            { label: '720p', count: 1 },
          ],
        },
      },
    };
    const host = dom(harness(lib).libSeasonFacts({ season_number: 2 }));
    expect(host.textContent).toBe('33.2 GB1080p 10720p 1');
    expect(harness(lib).libSeasonFacts({ season_number: 1 })).toBe('');
    expect(harness(null).libSeasonFacts({ season_number: 2 })).toBe('');
  });
});

describe('the page has no tabs', () => {
  it('ships no tab bar and no tab script', () => {
    expect(HTML).not.toContain('data-vd-tabs');
    expect(HTML).not.toContain('data-vd-tab=');
    expect(JS).not.toContain('function setTab');
    expect(JS).not.toContain('vd-body--library');
  });

  it('gives a show the three places its library parts go', () => {
    // movie and show each have a hero slot; the list slots are the show's
    expect(HTML.match(/data-vd-libline/g)?.length).toBe(2);
    const select = HTML.indexOf('data-vd-libselect');
    const list = HTML.indexOf('data-vd-episodes');
    const foot = HTML.indexOf('data-vd-libfoot');
    expect(select).toBeGreaterThan(-1);
    expect(select).toBeLessThan(list);
    expect(list).toBeLessThan(foot);
  });

  it('opening an episode mounts its cards and closing it lets them go', () => {
    const toggle = extractFunction('toggleEpisode', JS);
    expect(toggle).toContain('mountEpisodeFiles(');
    expect(toggle).toContain('unmountEpisodeFiles(files)');
    // a re-render replaces the rows: their cards are released first, and an
    // episode that was open is opened again
    const render = extractFunction('renderEpisodes', JS);
    expect(render.indexOf('unmountEpisodeFiles()')).toBeLessThan(
      render.indexOf('host.innerHTML = seasonBar'),
    );
    expect(render).toContain('wasOpen.forEach');
  });

  it('a tick on a row is not a click that opens it', () => {
    const click = JS.slice(JS.indexOf("if (e.target.closest('.vd-ep-pick')) return;") - 200);
    expect(click.indexOf(".vd-ep-pick')) return;")).toBeLessThan(
      click.indexOf('toggleEpisode(epRow)'),
    );
  });
});
