import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * One page, no tabs.
 *
 * A library show's Episodes tab and its Your library tab were the same list
 * twice. It is one list now, and that list is React
 * (features/video-library/episode-list.tsx, tested beside it): the row carries
 * its file, a tick box to act on several, and the file cards when opened.
 * What is pinned here is the page's side of the handover.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

describe('the page has no tabs', () => {
  it('ships no tab bar and no tab script', () => {
    expect(HTML).not.toContain('data-vd-tabs');
    expect(HTML).not.toContain('data-vd-tab=');
    expect(JS).not.toContain('function setTab');
    expect(JS).not.toContain('vd-body--library');
  });

  it('gives a show the places its library parts go', () => {
    // movie and show each have a hero slot; the foot is the show's, under its list
    expect(HTML.match(/data-vd-libline/g)?.length).toBe(2);
    const list = HTML.indexOf('data-vd-episodes');
    const foot = HTML.indexOf('data-vd-libfoot');
    expect(list).toBeGreaterThan(-1);
    expect(list).toBeLessThan(foot);
    // the bar for ticked episodes is the list's own now, not a slot
    expect(HTML).not.toContain('data-vd-libselect');
  });

  it('hands a show’s season to the React list and keeps a channel’s rows', () => {
    const render = extractFunction('renderEpisodes', JS);
    expect(render).toContain("data.source !== 'youtube') { mountEpisodes(host, season); return; }");
    // a channel writes markup into the same host: the React root goes first
    expect(render.indexOf('unmountEpisodes()')).toBeLessThan(render.indexOf('host.innerHTML ='));
    expect(render).toContain('eps.map(ytEpisodeRow)');
    // the old string-built TV row is gone, not left beside the new one
    expect(JS).not.toContain('function episodeRow(');
    expect(JS).not.toContain('function libFileLine(');
  });

  it('never writes markup over a mounted list', () => {
    // every writer of the episodes host takes the React root out first
    const writes = JS.split('\n').filter(
      (line) =>
        line.includes("'[data-vd-episodes]', '[data-vd-season-nav]'") && line.includes('innerHTML'),
    );
    expect(writes.length).toBe(3);
    for (const line of writes) {
      const before = JS.slice(Math.max(0, JS.indexOf(line) - 160), JS.indexOf(line));
      expect(before).toContain('unmountEpisodes();');
    }
    expect(extractFunction('loadTmdbSeason', JS)).toContain('setEpisodesHtml(host,');
  });

  it('reading the same show again keeps the list and the island mounted', () => {
    // a rename or an undo re-reads the page: what was open stays open, and
    // the undo strip is not torn down with the island
    const load = extractFunction('loadShow', JS);
    expect(load).toContain('resetExtras(again)');
    expect(load).toContain('if (!again) {');
    expect(extractFunction('resetExtras', JS)).toContain('if (!keepLibrary) unmountLibraryTab();');
  });

  it('pushes live download states into the list', () => {
    const apply = extractFunction('applyDlStates', JS);
    expect(apply).toContain('if (season && _epRoot) mountEpisodes(host, season);');
    const props = extractFunction('episodeListProps', JS);
    expect(props).toContain('downloads: Object.assign({}, _dlActive)');
    expect(props).toContain('finished: Object.assign({}, _dlDone)');
  });

  it('lends the list the page’s own flows', () => {
    for (const name of [
      'grabEpisode',
      'wishEpisode',
      'setWatched',
      'setSeasonMonitored',
      'grabSeason',
    ]) {
      expect(JS).toContain(`        ${name}: function (`);
    }
    // the season grab and the monitor flip are waited on by the list
    expect(extractFunction('grabSeasonInline', JS)).toContain('return VideoGrab.season(');
    expect(extractFunction('toggleSeasonMonitor', JS)).toContain(
      "return fetch('/api/video/detail/show/'",
    );
  });

  it('opens the island’s dialogs from the More menu', () => {
    const actions = extractFunction('renderActions', JS);
    for (const act of ['rename-files', 'move-library', 'file-history']) {
      expect(actions).toContain(`moreItem('${act}'`);
    }
    expect(JS).toContain("window.VideoLibraryTab.open('history')");
    expect(JS).toContain("window.VideoLibraryTab.open('rename')");
    expect(JS).toContain("window.VideoLibraryTab.open('move')");
  });
});
