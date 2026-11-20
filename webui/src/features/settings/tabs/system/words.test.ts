/**
 * The System tab's hero sentences (settings-bic phase 6), from sample
 * values: what each pill reads back.
 */

import { describe, expect, it } from 'vitest';

import {
  activitySentence,
  apiSentence,
  developerSentence,
  generalSentence,
  type HeroPart,
  logsSentence,
  maintenanceSentence,
  securitySentence,
  signInWays,
} from './words';

const read = (parts: HeroPart[]) => parts.map((p) => p.text).join('');
const warned = (parts: HeroPart[]) => parts.filter((p) => p.kind === 'warn').map((p) => p.text);

const open = {
  hasPin: false,
  hasPassword: false,
  requirePin: false,
  requireLogin: false,
  plex: false,
  oidc: false,
  oidcOnly: false,
  proxy: false,
};

describe('the System heroes', () => {
  it('General: autosave, workers, log level and file; autosave off is marked', () => {
    const on = generalSentence({
      autosave: true,
      workers: '5',
      level: 'INFO',
      path: 'logs/app.log',
    });
    expect(read(on)).toBe(
      'Settings save as you change them. Database updates run on 5 workers, and the server logs at info level to logs/app.log.',
    );
    expect(warned(on)).toEqual([]);
    const off = generalSentence({ autosave: false, workers: '3', level: 'DEBUG', path: 'x.log' });
    expect(warned(off)).toEqual(['Settings wait for the save button. ']);
    expect(read(off)).toContain('at debug level');
  });

  it('Security: open, PIN, sign-in with its ways, OIDC only, the proxy', () => {
    const anyone = securitySentence(open);
    expect(warned(anyone)).toEqual(['Anyone who can reach SoulSync can use it']);
    expect(read(anyone)).toContain('Sign-in needs an admin password first.');
    expect(read(securitySentence({ ...open, hasPin: true, requirePin: true }))).toBe(
      'SoulSync asks for the admin PIN on every visit. Sign-in needs an admin password first.',
    );
    const login = { ...open, hasPassword: true, requireLogin: true, plex: true, oidc: true };
    expect(read(securitySentence(login))).toBe('Everyone signs in with a password, Plex or OIDC.');
    expect(signInWays({ ...login, oidcOnly: true })).toEqual(['OIDC']);
    expect(read(securitySentence({ ...login, plex: false, oidc: false, proxy: true }))).toBe(
      'Everyone signs in with a password. It trusts the reverse proxy in front of it.',
    );
    // the admin profile not loaded yet: nothing said about the password
    expect(read(securitySentence({ ...open, hasPassword: null }))).not.toContain('password');
  });

  it('API: keys and their rights, and what SoulSync answers as', () => {
    expect(
      read(apiSentence({ keys: null, admin: 0, mcp: false, lidarr: false, seerr: false })),
    ).toBe('The API keys are loading. It answers no MCP, Lidarr or Seerr requests.');
    expect(read(apiSentence({ keys: 0, admin: 0, mcp: true, lidarr: false, seerr: false }))).toBe(
      'No API keys yet, so nothing outside can call SoulSync. SoulSync answers as MCP.',
    );
    expect(read(apiSentence({ keys: 1, admin: 0, mcp: false, lidarr: true, seerr: true }))).toBe(
      '1 API key, read-only. SoulSync answers as Lidarr and Seerr.',
    );
    expect(
      read(apiSentence({ keys: 1, admin: 1, mcp: false, lidarr: false, seerr: false })),
    ).toContain('1 API key, with admin rights.');
    expect(read(apiSentence({ keys: 3, admin: 3, mcp: true, lidarr: true, seerr: true }))).toBe(
      '3 API keys, all with admin rights. SoulSync answers as MCP, Lidarr and Seerr.',
    );
    expect(
      read(apiSentence({ keys: 3, admin: 2, mcp: false, lidarr: false, seerr: false })),
    ).toContain('3 API keys, 2 with admin rights.');
  });

  it('Maintenance: the database, what a compact frees, no room, the cache', () => {
    expect(
      read(
        maintenanceSentence({
          size: null,
          reclaim: null,
          room: true,
          cacheImages: null,
          cacheSize: null,
        }),
      ),
    ).toBe('The database figures are loading.');
    const full = maintenanceSentence({
      size: '11.2 GB',
      reclaim: '48 MB',
      room: false,
      cacheImages: 1234,
      cacheSize: '300.0 MB',
    });
    expect(read(full)).toBe(
      'The database is 11.2 GB, with 48 MB a compact would free. There is not enough free disk space to compact it right now. The artwork cache holds 1,234 images in 300.0 MB.',
    );
    expect(warned(full)).toEqual(['There is not enough free disk space to compact it right now.']);
    expect(
      read(
        maintenanceSentence({
          size: '1 GB',
          reclaim: null,
          room: true,
          cacheImages: 1,
          cacheSize: '1.0 MB',
        }),
      ),
    ).toBe('The database is 1 GB, fully compacted. The artwork cache holds 1 image in 1.0 MB.');
  });

  it('Developer: dev mode, and the experiments on the music side only', () => {
    expect(read(developerSentence({ devMode: true, experiments: [] }))).toBe(
      'Developer mode is on.',
    );
    expect(
      read(
        developerSentence({
          devMode: false,
          experiments: [
            { name: 'JioSaavn', on: true },
            { name: 'Bandcamp', on: true },
          ],
        }),
      ),
    ).toBe(
      'Developer mode is off. The preview tools work without it and save nothing. Trying out JioSaavn and Bandcamp.',
    );
    expect(
      read(developerSentence({ devMode: false, experiments: [{ name: 'JioSaavn', on: false }] })),
    ).toContain('No experimental feature is switched on.');
  });

  it('Logs and Server Activity', () => {
    expect(read(logsSentence({ level: 'WARNING', path: 'logs/app.log' }))).toBe(
      'The server writes warnings and errors only to logs/app.log. The viewer shows the last 300 lines of a log, then each new line as it is written.',
    );
    expect(
      read(
        activitySentence({
          retentionDays: 0,
          retentionLabel: 'forever',
          uploadMbps: null,
          geo: false,
        }),
      ),
    ).toBe(
      'Plays are kept forever. No upload speed is set, so Now playing shows no bandwidth bar. Remote addresses are never looked up.',
    );
    expect(
      read(
        activitySentence({
          retentionDays: 90,
          retentionLabel: '90 days',
          uploadMbps: 40,
          geo: true,
        }),
      ),
    ).toBe(
      'Plays are kept for 90 days. Remote streams are measured against 40 Mbps of upload. Remote viewers show the city they are in.',
    );
  });
});
