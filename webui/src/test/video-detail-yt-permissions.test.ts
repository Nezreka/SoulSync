import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * YouTube video rows on the channel detail page.
 *
 * A profile without download rights can't grab or wishlist — it gets the
 * request flow instead (the server 403s the grab/wishlist endpoints for it).
 * canDownload() answers "yes" until the profile finishes loading, and the
 * detail page used to render once and never correct itself, so a member
 * whose profile arrived late saw grab + wishlist buttons that all failed.
 * The page now re-renders when the profile lands or changes (but only when
 * the permission outcome actually changed — avatar edits must not collapse
 * open episode panels).
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

function row(
  ep: Record<string, unknown>,
  opts: { canDownload: boolean; requests: boolean },
): string {
  const preamble = `
    var selectedSeason = 2024;
    var data = { _channel: { youtube_id: 'UC123', title: 'Test Channel' } };
    function esc(s) { return String(s == null ? '' : s); }
    function fmtDate(s) { return String(s || ''); }
    function canDownload() { return ${opts.canDownload ? 'true' : 'false'}; }
    ${
      opts.requests
        ? `window.VideoRequests = { cardButton: function (o) { return '<button type="button" data-vreq-card data-youtube="' + o.youtubeId + '">Request</button>'; } };`
        : `window.VideoRequests = undefined;`
    }
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('ytEpisodeRow', SRC)}\nreturn ytEpisodeRow;`,
  )() as (ep: unknown) => string;
  return build(ep);
}

function seasonBar(season: Record<string, unknown>, opts: { canDownload: boolean }): string {
  const preamble = `
    var data = { kind: 'show', source: 'youtube' };
    var ytFilter = { q: '', state: 'all', duration: 'all' };
    function canDownload() { return ${opts.canDownload ? 'true' : 'false'}; }
    window.VideoGrab = {};
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('seasonActionsHtml', SRC)}\nreturn seasonActionsHtml;`,
  )() as (s: unknown) => string;
  return build(season);
}

/**
 * Behavioral test for the profile-change handler: simulates the permission
 * flip (canDownload true → false) and verifies the UI re-renders only when
 * the outcome actually changes.
 */
function profileChangeSequence(): { actions: number; episodes: number }[] {
  const results: { actions: number; episodes: number }[] = [];
  // Extract the handler source and run it in a controlled scope.
  const handlerSrc = extractFunction('onProfileChanged', SRC);
  const preamble = `
    var _lastCanDl = null;
    var data = { kind: 'show' };
    function root() { return document.createElement('div'); }
    var actions = 0, episodes = 0;
    function renderActions(d) { actions++; }
    function renderEpisodes() { episodes++; }
    var canDlValue = true;
    function canDownload() { return canDlValue; }
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const run = new Function(
    `${preamble}\n${handlerSrc}\n` +
      `return function (canDl) { canDlValue = canDl; onProfileChanged(); return { actions, episodes }; };`,
  )() as (canDl: boolean) => { actions: number; episodes: number };
  // null -> true (first profile load): renders
  results.push(run(true));
  // true -> true (avatar edit): skips
  results.push(run(true));
  // true -> false (permission revoked): renders
  results.push(run(false));
  // false -> false (another edit): skips
  results.push(run(false));
  return results;
}

const EP = { episode_number: 1, youtube_id: 'vid1', title: 'Test Video', owned: false };
const SEASON = {
  season_number: 2024,
  episode_monitored: 0,
  episodes: [
    { episode_number: 1, owned: false },
    { episode_number: 2, owned: true },
  ],
};

describe('ytEpisodeRow permission gating', () => {
  it('shows grab + wishlist for a profile that can download', () => {
    const html = row(EP, { canDownload: true, requests: true });
    expect(html).toContain('data-vd-yt-grab');
    expect(html).toContain('data-vd-yt-wish');
    expect(html).not.toContain('data-vreq-card');
  });

  it('shows a request button instead of grab/wishlist when the profile cannot download', () => {
    const html = row(EP, { canDownload: false, requests: true });
    expect(html).toContain('data-vreq-card');
    expect(html).not.toContain('data-vd-yt-grab');
    expect(html).not.toContain('data-vd-yt-wish');
  });

  it('falls back to grab + wishlist when the request module is absent', () => {
    // VideoRequests failed to load: the old buttons are the only option.
    // (They'll 403 for a no-download member — a separate failure mode.)
    const html = row(EP, { canDownload: false, requests: false });
    expect(html).toContain('data-vd-yt-grab');
    expect(html).toContain('data-vd-yt-wish');
    expect(html).not.toContain('data-vreq-card');
  });

  it('keeps owned videos on the download treatment even without download rights', () => {
    const html = row({ ...EP, owned: true }, { canDownload: false, requests: true });
    expect(html).not.toContain('data-vreq-card');
    expect(html).toContain('Downloaded');
  });
});

describe('seasonActionsHtml permission gating', () => {
  it('shows Grab/Wishlist year for a profile that can download', () => {
    const html = seasonBar(SEASON, { canDownload: true });
    expect(html).toContain('data-vd-season-grab');
    expect(html).toContain('data-vd-season-wish');
  });

  it('hides the acquisition buttons when the profile cannot download', () => {
    // The server 403s these endpoints for a no-download member — showing them
    // is the same dead-button bug as the per-row controls.
    const html = seasonBar(SEASON, { canDownload: false });
    expect(html).not.toContain('data-vd-season-grab');
    expect(html).not.toContain('data-vd-season-wish');
  });
});

describe('video detail profile timing', () => {
  it('re-renders only when the permission outcome changes', () => {
    const [first, second, third, fourth] = profileChangeSequence();
    // null -> true: first profile load renders
    expect(first).toEqual({ actions: 1, episodes: 1 });
    // true -> true: unrelated edit skips (open panels survive)
    expect(second).toEqual({ actions: 1, episodes: 1 });
    // true -> false: permission change re-renders
    expect(third).toEqual({ actions: 2, episodes: 2 });
    // false -> false: unrelated edit skips
    expect(fourth).toEqual({ actions: 2, episodes: 2 });
  });

  it('seeds permission state even when the first event fires before data loads', () => {
    // Regression: on fresh page loads the profile event fires while data is
    // null (listener registers on DOMContentLoaded, profile fetch resolves
    // async). The old code seeded _lastCanDl only after the early return, so
    // the next unrelated event (e.g. avatar edit) saw null !== canDl and
    // spuriously re-rendered, collapsing open episode panels.
    const handlerSrc = extractFunction('onProfileChanged', SRC);
    const preamble = `
    var _lastCanDl = null;
    var data = null;
    function root() { return document.createElement('div'); }
    var actions = 0, episodes = 0;
    function renderActions(d) { actions++; }
    function renderEpisodes() { episodes++; }
    var canDlValue = true;
    function canDownload() { return canDlValue; }
  `;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const harness = new Function(
      `${preamble}\n${handlerSrc}\n` +
        `return { fire: function () { onProfileChanged(); }, setData: function (d) { data = d; }, counts: function () { return { actions, episodes }; } };`,
    )() as {
      fire: () => void;
      setData: (d: unknown) => void;
      counts: () => { actions: number; episodes: number };
    };
    // Event 1: profile lands before page data — seeds _lastCanDl, no render.
    harness.fire();
    expect(harness.counts()).toEqual({ actions: 0, episodes: 0 });
    // Event 2: unrelated edit after data loads — permission unchanged, no render.
    harness.setData({ kind: 'show' });
    harness.fire();
    expect(harness.counts()).toEqual({ actions: 0, episodes: 0 });
  });
});
