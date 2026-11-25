import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2199 on the video requests page: an approved movie, show or episode can
 * wait for an admin to pick the release. The row tells the requester, the
 * approve menu offers the other way than the setting, and a decider picks a
 * held movie or episode with the video side's own release picker.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-requests.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

type Group = Record<string, unknown>;
type Env = {
  admin: boolean;
  moderator: boolean;
  mine: boolean;
  acquisition: Record<string, string> | null;
};

function helpers(env: Env, win: Record<string, unknown> = {}) {
  const names = ['requestStatus', 'progressOf', 'holdsByDefault', 'picks', 'pickRelease'];
  const body = names.map((n) => extractFunction(n, JS)).join('\n');
  const toast = vi.fn();
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const made = new Function(
    'env',
    'window',
    'toast',
    `var state = { moderator: env.moderator, acquisition: env.acquisition };
     function isAdmin() { return env.admin; }
     function mineIn() { return env.mine; }
     ${body}
     return { ${names.join(', ')} };`,
  )(env, win, toast) as {
    requestStatus: (r: Group) => { text: string; tone: string };
    holdsByDefault: (kind: string) => boolean;
    picks: (g: Group) => boolean;
    pickRelease: (g: Group) => void;
  };
  return { ...made, toast };
}

const base: Env = { admin: true, moderator: false, mine: false, acquisition: null };

describe('a request held for a release pick', () => {
  it('tells the requester it waits for a release to be picked', () => {
    const h = helpers(base);
    expect(
      h.requestStatus({ status: 'approved', kind: 'movie', awaiting_selection: true }),
    ).toEqual({ text: 'Approved, waiting for a release to be picked', tone: 'held' });
    // a pick downloading reads like any approved title
    expect(h.requestStatus({ status: 'approved', kind: 'movie', picking: true }).text).toBe(
      'On the way',
    );
    expect(CSS).toContain('.vreq-status--held');
  });

  it('reads the kind’s setting: movies and TV, never youtube', () => {
    const h = helpers({ ...base, acquisition: { movie: 'manual', tv: 'auto' } });
    expect(h.holdsByDefault('movie')).toBe(true);
    expect(h.holdsByDefault('episode')).toBe(false);
    expect(h.holdsByDefault('youtube')).toBe(false);
    expect(helpers(base).holdsByDefault('movie')).toBe(false);
  });

  it('lets admins and other people’s moderators pick, nobody else', () => {
    const held = { held: true, kind: 'movie' };
    expect(helpers(base).picks(held)).toBe(true);
    expect(helpers(base).picks({ held: false, kind: 'movie' })).toBe(false);
    expect(helpers({ ...base, admin: false }).picks(held)).toBe(false);
    expect(helpers({ ...base, admin: false, moderator: true }).picks(held)).toBe(true);
    expect(helpers({ ...base, admin: false, moderator: true, mine: true }).picks(held)).toBe(false);
  });

  it('opens the video release picker on the title, as tmdb', () => {
    const manualSearch = vi.fn();
    const h = helpers(base, { VideoDownload: { manualSearch } });
    h.pickRelease({
      kind: 'episode',
      title: 'Severance S01E02',
      tmdb_id: 95396,
      season_number: 1,
      episode_number: 2,
    });
    expect(manualSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Severance',
        scope: 'episode',
        season: 1,
        episode: 2,
        mediaId: 95396,
        mediaSource: 'tmdb',
        tmdbId: 95396,
      }),
    );
    const missing = helpers(base, {});
    missing.pickRelease({ kind: 'movie', title: 'Heat', tmdb_id: 949 });
    expect(missing.toast).toHaveBeenCalledWith(expect.stringContaining('Reload'), 'error');
  });

  it('wires the menu choices and the pick button', () => {
    for (const hook of [
      "{ id: 'approve-auto', label: 'Approve & auto-download' }",
      "{ id: 'approve-pick', label: 'Approve & pick release' }",
      "{ id: 'pick-auto', label: 'Download automatically instead' }",
      "approve(g, { acquisition: 'manual' })",
      'data-vreq-pick=',
      "'/api/video/requests/' + g.rows[0].id + '/auto'",
      'if (opts.acquisition) body.acquisition = opts.acquisition;',
    ]) {
      expect(JS).toContain(hook);
    }
  });
});
