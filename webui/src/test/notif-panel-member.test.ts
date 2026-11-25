import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2170: the notification panel is everyone's, but what the server is doing
 * is the admins'. a member's panel keeps their own notes, their own
 * downloads, their own automations and imports; it never shows the system
 * numbers (downloads across the server, uptime, memory), health, library
 * scans, repair, the wishlist run or the video jobs, and it never asks the
 * server for them. these run the real functions out of downloads.js.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');

function lift<T>(names: string[], returns: string, scope: Record<string, unknown> = {}): T {
  const keys = Object.keys(scope);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    ...keys,
    `${names.map((n) => extractFunction(n, JS)).join('\n')}\nreturn ${returns};`,
  );
  return fn(...keys.map((k) => scope[k])) as T;
}

const esc = (s: unknown) => String(s);

describe('who counts as an admin here', () => {
  const isAdmin = (ctx: unknown) =>
    lift<() => boolean>(['_notifViewerIsAdmin'], '_notifViewerIsAdmin', {
      getCurrentProfileContext: () => ctx,
    })();

  it('follows the profile, the same rule as server activity', () => {
    expect(isAdmin({ profileId: 1, isAdmin: false })).toBe(true);
    expect(isAdmin({ profileId: 4, isAdmin: true })).toBe(true);
    expect(isAdmin({ profileId: 4, isAdmin: false })).toBe(false);
  });

  it('fails closed with no profile yet', () => {
    expect(isAdmin(null)).toBe(false);
  });
});

describe('the system footer', () => {
  const stats = {
    active_downloads: 3,
    download_speed: '2 MB/s',
    uptime: '0:26:15',
    memory_usage: '13.5%',
  };
  const footer = (admin: boolean) =>
    lift<() => string>(['_notifSysHTML'], '_notifSysHTML', {
      _notifState: { sysStats: stats },
      _notifViewerIsAdmin: () => admin,
      _notifGlyphs: { down: '', clock: '', chip: '' },
      _escToast: esc,
      _escAttr: esc,
    })();

  it('shows an admin the downloads, uptime and memory', () => {
    const html = footer(true);
    expect(html).toContain('downloading');
    expect(html).toContain('0:26:15');
    expect(html).toContain('13.5%');
  });

  it('shows a member none of it', () => {
    expect(footer(false)).toBe('');
  });

  it('never fetches the numbers for a member', () => {
    const fetched: string[] = [];
    lift<() => void>(['_seedNotifSys'], '_seedNotifSys', {
      _notifViewerIsAdmin: () => false,
      fetch: (url: string) => {
        fetched.push(url);
        return Promise.resolve({ ok: false });
      },
    })();
    expect(fetched).toEqual([]);
  });

  it('keeps the server-wide download count off a member bell', () => {
    const el = document.createElement('span');
    el.id = 'notif-bell-dl';
    document.body.appendChild(el);
    try {
      lift<() => void>(['_updateNotifDlBadge'], '_updateNotifDlBadge', {
        _notifState: { sysStats: stats },
        _notifViewerIsAdmin: () => false,
      })();
      expect(el.style.display).toBe('none');
    } finally {
      el.remove();
    }
  });
});

describe('running now', () => {
  const cards = {
    _musicAutomationActiveHTML: () => '[automation]',
    _musicSyncActiveHTML: () => '[everyone syncing]',
    _musicWishlistActiveHTML: () => '[wishlist run]',
    _lastfmImportActiveHTML: () => '[lastfm]',
    _listenbrainzImportActiveHTML: () => '[listenbrainz]',
    _musicWatchlistActiveHTML: () => '[watchlist scan]',
    _musicMediaScanActiveHTML: () => '[media scan]',
    _musicRepairActiveHTML: () => '[repair]',
    _memberSyncActiveHTML: () => '[my downloads]',
  };
  const music = (admin: boolean) =>
    lift<() => string>(['_memberActiveHTML', '_musicActiveHTML'], '_musicActiveHTML', {
      ...cards,
      _notifViewerIsAdmin: () => admin,
    })();

  it('an admin sees every job', () => {
    const html = music(true);
    for (const c of [
      'wishlist run',
      'media scan',
      'repair',
      'watchlist scan',
      'everyone syncing',
    ]) {
      expect(html).toContain(c);
    }
  });

  it('a member sees only their own', () => {
    const html = music(false);
    expect(html).toContain('[automation]');
    expect(html).toContain('[my downloads]');
    expect(html).toContain('[lastfm]');
    expect(html).toContain('[listenbrainz]');
    for (const c of [
      'wishlist run',
      'media scan',
      'repair',
      'watchlist scan',
      'everyone syncing',
    ]) {
      expect(html).not.toContain(c);
    }
  });

  it('a member never gets the video jobs either', () => {
    const panel = document.createElement('div');
    panel.id = 'notif-panel';
    panel.innerHTML = '<div data-notif-active-host></div>';
    document.body.appendChild(panel);
    try {
      const render = (admin: boolean) => {
        lift<() => void>(['_renderActiveHost'], '_renderActiveHost', {
          _notifViewerIsAdmin: () => admin,
          _notifState: { jobsOpen: true },
          _NOTIF_JOBS_SHOWN: 3,
          _musicActiveHTML: () => '<div>mine</div>',
          _overlayActiveHTML: () => '<div>overlays</div>',
          _colSyncActiveHTML: () => '<div>collections</div>',
          _colArtActiveHTML: () => '<div>artwork</div>',
          _videoBulkActiveHTML: () => '<div>bulk</div>',
        })();
        return panel.textContent ?? '';
      };
      expect(render(true)).toContain('overlays');
      const member = render(false);
      expect(member).toContain('mine');
      for (const c of ['overlays', 'collections', 'artwork', 'bulk']) {
        expect(member).not.toContain(c);
      }
    } finally {
      panel.remove();
    }
  });

  it('a member asks the server for their own jobs only', () => {
    const asked: string[] = [];
    const seeders = [
      '_seedMusicAutomationTask',
      '_seedMusicDownloadTask',
      '_seedLastfmImportTask',
      '_seedListenbrainzImportTask',
      '_seedOverlayTask',
      '_seedCollectionSyncTask',
      '_seedCollectionArtTask',
      '_seedVideoBulkTask',
      '_seedMusicRepairTask',
      '_seedMusicMediaScanTask',
    ];
    const run = (admin: boolean) => {
      asked.length = 0;
      lift<() => void>(['_seedNotifTasks'], '_seedNotifTasks', {
        _notifViewerIsAdmin: () => admin,
        ...Object.fromEntries(seeders.map((n) => [n, () => asked.push(n)])),
      })();
      return [...asked];
    };
    expect(run(true)).toEqual(expect.arrayContaining(seeders));
    expect(run(false).sort()).toEqual(
      [
        '_seedLastfmImportTask',
        '_seedListenbrainzImportTask',
        '_seedMusicAutomationTask',
        '_seedMusicDownloadTask',
      ].sort(),
    );
  });

  it('opening the panel goes through that one list', () => {
    const open = extractFunction('_openNotifPanel', JS);
    expect(open).toContain('_seedNotifTasks()');
    for (const n of ['_seedMusicRepairTask', '_seedMusicMediaScanTask', '_seedOverlayTask']) {
      expect(open).not.toContain(n);
    }
  });

  it('the bell only glows for a member over their own work', () => {
    const glow = (admin: boolean) =>
      lift<() => boolean>(['_adminTasksActive'], '_adminTasksActive', {
        _notifViewerIsAdmin: () => admin,
        _overlayTaskActive: () => true,
        _colSyncTaskActive: () => false,
        _colArtTaskActive: () => false,
        _videoBulkTaskActive: () => false,
      })();
    expect(glow(true)).toBe(true);
    expect(glow(false)).toBe(false);
  });
});
