/**
 * The System tab's hero sentences (settings-bic phase 6): each pill read back
 * in words. Pure, so the copy is tested from sample values.
 */

import type { HeroPart } from '../downloads/words';

export type { HeroPart };

const name = (text: string): HeroPart => ({ text, kind: 'name' });
const warn = (text: string): HeroPart => ({ text, kind: 'warn' });
const say = (text: string): HeroPart => ({ text });

/** the four log levels, as the server names them, in words */
export const LOG_LEVEL_WORDS: Record<string, { label: string; shows: string }> = {
  DEBUG: { label: 'Debug', shows: 'every detail, for chasing a problem' },
  INFO: { label: 'Info', shows: 'what SoulSync does and every problem' },
  WARNING: { label: 'Warning', shows: 'warnings and errors only' },
  ERROR: { label: 'Error', shows: 'errors only' },
};

const levelWord = (level: string) => (LOG_LEVEL_WORDS[level]?.label ?? level).toLowerCase();

/** General: how settings save, the database workers, the log level */
export function generalSentence(o: {
  autosave: boolean;
  workers: string;
  level: string;
  path: string;
}): HeroPart[] {
  return [
    o.autosave
      ? say('Settings save as you change them. ')
      : warn('Settings wait for the save button. '),
    say('Database updates run on '),
    name(`${o.workers} workers`),
    say(', and the server logs at '),
    name(levelWord(o.level)),
    say(' level to '),
    name(o.path),
    say('.'),
  ];
}

export interface SecurityFacts {
  /** null until the admin profile loaded */
  hasPin: boolean | null;
  hasPassword: boolean | null;
  requirePin: boolean;
  requireLogin: boolean;
  plex: boolean;
  oidc: boolean;
  oidcOnly: boolean;
  proxy: boolean;
}

/** the ways in a sign-in screen offers, in words */
export function signInWays(f: SecurityFacts): string[] {
  if (f.oidcOnly && f.oidc) return ['OIDC'];
  const ways = ['a password'];
  if (f.plex) ways.push('Plex');
  if (f.oidc) ways.push('OIDC');
  return ways;
}

const join = (words: string[], last: 'or' | 'and') =>
  words.length <= 1
    ? (words[0] ?? '')
    : `${words.slice(0, -1).join(', ')} ${last} ${words[words.length - 1]}`;

/** Security: who can get in, and how */
export function securitySentence(f: SecurityFacts): HeroPart[] {
  const parts: HeroPart[] = [];
  if (f.requireLogin) {
    parts.push(say('Everyone signs in with '), name(join(signInWays(f), 'or')), say('.'));
  } else if (f.requirePin) {
    parts.push(say('SoulSync asks for the '), name('admin PIN'), say(' on every visit.'));
  } else {
    parts.push(
      warn('Anyone who can reach SoulSync can use it'),
      say(': there is no PIN and no sign-in.'),
    );
  }
  if (!f.requireLogin && f.hasPassword === false)
    parts.push(say(' Sign-in needs an admin password first.'));
  if (f.proxy) parts.push(say(' It trusts the '), name('reverse proxy'), say(' in front of it.'));
  return parts;
}

/** API: the keys, and what SoulSync answers as */
export function apiSentence(o: {
  /** null until the list loaded */
  keys: number | null;
  admin: number;
  mcp: boolean;
  lidarr: boolean;
  seerr: boolean;
}): HeroPart[] {
  const parts: HeroPart[] = [];
  if (o.keys === null) parts.push(say('The API keys are loading.'));
  else if (o.keys === 0) parts.push(say('No API keys yet, so nothing outside can call SoulSync.'));
  else
    parts.push(
      name(`${o.keys} API key${o.keys === 1 ? '' : 's'}`),
      say(
        !o.admin
          ? ', read-only.'
          : o.keys === 1
            ? ', with admin rights.'
            : o.admin === o.keys
              ? ', all with admin rights.'
              : `, ${o.admin} with admin rights.`,
      ),
    );
  const answers = [o.mcp && 'MCP', o.lidarr && 'Lidarr', o.seerr && 'Seerr'].filter(
    (x): x is string => !!x,
  );
  if (answers.length)
    parts.push(say(' SoulSync answers as '), name(join(answers, 'and')), say('.'));
  else parts.push(say(' It answers no MCP, Lidarr or Seerr requests.'));
  return parts;
}

/** Maintenance: how big the database and the artwork cache are */
export function maintenanceSentence(o: {
  /** null until it loaded */
  size: string | null;
  reclaim: string | null;
  room: boolean;
  cacheImages: number | null;
  cacheSize: string | null;
}): HeroPart[] {
  const parts: HeroPart[] = [];
  if (o.size === null) parts.push(say('The database figures are loading.'));
  else {
    parts.push(say('The database is '), name(o.size));
    parts.push(
      o.reclaim ? say(`, with ${o.reclaim} a compact would free.`) : say(', fully compacted.'),
    );
    if (!o.room)
      parts.push(say(' '), warn('There is not enough free disk space to compact it right now.'));
  }
  if (o.cacheImages !== null && o.cacheSize !== null)
    parts.push(
      say(' The artwork cache holds '),
      name(`${o.cacheImages.toLocaleString('en-US')} image${o.cacheImages === 1 ? '' : 's'}`),
      say(` in ${o.cacheSize}.`),
    );
  return parts;
}

/** Developer: dev mode and the experiments switched on */
export function developerSentence(o: {
  devMode: boolean;
  /** the experiments this side shows, by name, with their state */
  experiments: Array<{ name: string; on: boolean }>;
}): HeroPart[] {
  const parts: HeroPart[] = o.devMode
    ? [say('Developer mode is '), name('on'), say('.')]
    : [say('Developer mode is off. The preview tools work without it and save nothing.')];
  if (o.experiments.length) {
    const on = o.experiments.filter((e) => e.on).map((e) => e.name);
    if (on.length) parts.push(say(' Trying out '), name(join(on, 'and')), say('.'));
    else parts.push(say(' No experimental feature is switched on.'));
  }
  return parts;
}

/** Logs: what the server writes, and what the viewer shows */
export function logsSentence(o: { level: string; path: string }): HeroPart[] {
  return [
    say('The server writes '),
    name(LOG_LEVEL_WORDS[o.level]?.shows ?? o.level),
    say(' to '),
    name(o.path),
    say('. The viewer shows the last 300 lines of a log, then each new line as it is written.'),
  ];
}

/** Server Activity: the play log and what Now playing measures */
export function activitySentence(o: {
  /** 0 is forever */
  retentionDays: number;
  retentionLabel: string;
  uploadMbps: number | null;
  geo: boolean;
}): HeroPart[] {
  const parts: HeroPart[] =
    o.retentionDays === 0
      ? [say('Plays are kept '), name('forever'), say('.')]
      : [say('Plays are kept for '), name(o.retentionLabel), say('.')];
  if (o.uploadMbps)
    parts.push(
      say(' Remote streams are measured against '),
      name(`${o.uploadMbps} Mbps`),
      say(' of upload.'),
    );
  else parts.push(say(' No upload speed is set, so Now playing shows no bandwidth bar.'));
  parts.push(
    o.geo
      ? say(' Remote viewers show the city they are in.')
      : say(' Remote addresses are never looked up.'),
  );
  return parts;
}
