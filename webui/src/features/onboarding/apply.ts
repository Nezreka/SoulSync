/**
 * The ready step: save the draft, then check every piece of it for real.
 *
 * Saving goes through `Effects` so the developer preview can swap in one that
 * records instead of writing. The checks always run (they only read), so the
 * preview shows the same green and amber rows a real install would.
 */

import { apiClient, readJson } from '@/app/api-client';

import type { Draft, FollowPick, StepId } from './types';

import { checkPaths, probeService, searchArtists } from './api';
import {
  buildSettingsPatch,
  buildVideoDownloadConfig,
  folderRows,
  hasMusic,
  hasVideo,
  serverConfig,
  serverLabel,
  SOURCE_INFO,
} from './plan';

export interface Effects {
  readonly preview: boolean;
  saveSettings(patch: Record<string, unknown>): Promise<void>;
  applyPreset(name: string): Promise<void>;
  updateProfile(name: string, color: string): Promise<void>;
  selectMusicLibrary(server: string, name: string): Promise<void>;
  saveVideoKeys(keys: Record<string, string>): Promise<void>;
  saveVideoDownloads(config: Record<string, unknown>): Promise<void>;
  saveVideoLibraries(movies: string | null, tv: string | null): Promise<void>;
  follow(artist: Required<Pick<FollowPick, 'id' | 'name'>> & { image?: string }): Promise<void>;
  complete(purposes: string[]): Promise<void>;
  lock(account: Draft['account']): Promise<void>;
}

async function postOk(url: string, json?: unknown, method: 'post' | 'put' = 'post'): Promise<void> {
  const body = await readJson<{ success?: boolean; error?: string; status?: string }>(
    apiClient[method](url, json === undefined ? {} : { json }),
  );
  if (body && body.success === false) throw new Error(body.error || `${url} failed`);
}

export const realEffects: Effects = {
  preview: false,
  saveSettings: (patch) => postOk('settings', patch),
  applyPreset: (name) => postOk(`quality-profile/preset/${name}`),
  updateProfile: (name, color) => postOk('profiles/1', { name, avatar_color: color }, 'put'),
  async selectMusicLibrary(server, name) {
    if (server === 'plex') return postOk('plex/select-music-library', { library_name: name });
    if (server === 'navidrome')
      return postOk('navidrome/select-music-folder', { folder_name: name });
    if (server === 'jellyfin') {
      // jellyfin libraries hang off a user, the admin one sees everything
      const users = await readJson<{ users?: { name?: string; is_admin?: boolean }[] }>(
        apiClient.get('jellyfin/users'),
      );
      const list = users.users ?? [];
      const pick = list.find((u) => u.is_admin) ?? list[0];
      if (pick?.name) await postOk('jellyfin/select-user', { username: pick.name });
      return postOk('jellyfin/select-music-library', { library_name: name });
    }
  },
  saveVideoKeys: (keys) => postOk('video/enrichment/config', keys),
  saveVideoDownloads: (config) => postOk('video/downloads/config', config),
  saveVideoLibraries: (movies, tv) => postOk('video/libraries', { movies, tv }),
  async follow(a) {
    const body = await readJson<{ success?: boolean; status?: string; error?: string }>(
      apiClient.post('watchlist/add', {
        json: { artist_id: a.id, artist_name: a.name, image_url: a.image ?? '' },
      }),
    );
    // coming back to this step re-runs it, an artist already followed is fine
    if (!body.success && body.status !== 'already_watching')
      throw new Error(body.error || 'follow failed');
  },
  complete: (purposes) => postOk('setup/complete', { purposes }),
  async lock(account) {
    if (account.lock === 'pin') {
      await postOk('profiles/1/set-pin', { pin: account.pin });
      await postOk('settings', { security: { require_pin_on_launch: true } });
      // this browser just chose the pin, it shouldn't be asked for it straight away
      await postOk('profiles/verify-launch-pin', { pin: account.pin });
    } else if (account.lock === 'password') {
      await postOk('profiles/1/set-password', { password: account.password });
      await postOk('settings', { security: { require_login: true } });
      await postOk('auth/login', { username: account.name.trim(), password: account.password });
    }
  },
};

/** the preview: every save is written down, nothing is sent */
export function previewEffects(log: string[] = []): Effects & { log: string[] } {
  const note =
    (what: string) =>
    async (..._args: unknown[]) => {
      log.push(what);
    };
  return {
    preview: true,
    log,
    saveSettings: note('settings'),
    applyPreset: note('quality preset'),
    updateProfile: note('profile'),
    selectMusicLibrary: note('music library'),
    saveVideoKeys: note('video keys'),
    saveVideoDownloads: note('video downloads'),
    saveVideoLibraries: note('video libraries'),
    follow: note('follow'),
    complete: note('complete'),
    lock: note('lock'),
  };
}

export type RowState = 'checking' | 'ok' | 'warn';

export interface ReadyRow {
  id: string;
  name: string;
  state: RowState;
  detail: string;
  /** the step that fixes an amber row */
  fix?: StepId;
}

type Emit = (row: ReadyRow) => void;

/** the row applyDraft returns alone when the settings themselves didn't save */
export const SAVE_FAILED = 'save';

function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join('');
  if (names.length === 2) return `${names[0]}, then ${names[1]}`;
  return `${names.slice(0, -1).join(', ')}, then ${names[names.length - 1]}`;
}

const PRESET_NAMES: Record<string, string> = {
  audiophile: 'Audiophile',
  balanced: 'Balanced',
  space_saver: 'Space saver',
};

/**
 * Looked up again by name here, after the settings save: an id picked
 * earlier came from whatever catalogue was active then, and the watchlist
 * stores ids against the catalogue chosen in this flow.
 */
async function resolveFollow(pick: FollowPick) {
  const hits = await searchArtists(pick.name);
  const exact = hits.find((h) => h.name.trim().toLowerCase() === pick.name.trim().toLowerCase());
  const hit = exact ?? hits[0];
  return hit ? { id: hit.id, name: hit.name, image: hit.image_url ?? pick.image } : null;
}

/**
 * Save the draft, then check it. Each row is emitted as checking first, then
 * settled, so the ready screen fills in live. A save that fails turns its row
 * amber with the server's own words instead of stopping the rest.
 */
export async function applyDraft(d: Draft, fx: Effects, emit: Emit): Promise<ReadyRow[]> {
  const rows: Record<string, ReadyRow> = {};
  const put = (row: ReadyRow) => {
    rows[row.id] = row;
    emit(row);
  };
  const music = hasMusic(d.purposes);
  const video = hasVideo(d.purposes);
  const s = d.server;

  // everything that shows up as a row starts out checking
  const plan: [string, string][] = [
    ['account', 'Admin profile'],
    ['server', 'Media server'],
    ['folders', 'Folders'],
  ];
  if (music) plan.push(['sources', 'Music sources'], ['catalogue', 'Catalogue']);
  if (video) plan.push(['video', 'Movies and TV']);
  if (music && d.follows.length) plan.push(['follows', 'Following']);
  for (const [id, name] of plan) put({ id, name, state: 'checking', detail: 'Checking…' });

  let saveError = '';
  try {
    await fx.saveSettings(buildSettingsPatch(d));
  } catch (error) {
    saveError = error instanceof Error ? error.message : 'Saving failed';
    // nothing below means anything without the settings, and finishing now
    // would mark an unconfigured install as set up. the ready screen offers a
    // retry instead
    for (const id of Object.keys(rows)) delete rows[id];
    put({ id: SAVE_FAILED, name: "Couldn't save", state: 'warn', detail: saveError });
    return Object.values(rows);
  }

  // admin profile
  try {
    await fx.updateProfile(d.account.name.trim(), d.account.color);
    const how =
      d.account.lock === 'pin'
        ? 'locked with a PIN'
        : d.account.lock === 'password'
          ? 'signs in with a password'
          : 'open on this network';
    put({
      id: 'account',
      name: 'Admin profile',
      state: 'ok',
      detail: `${d.account.name.trim()}, ${how}`,
    });
  } catch (error) {
    put({
      id: 'account',
      name: 'Admin profile',
      state: 'warn',
      detail: error instanceof Error ? error.message : "Couldn't save the profile",
      fix: 'account',
    });
  }

  // media server
  if (s.kind === 'soulsync' || !s.kind) {
    put({
      id: 'server',
      name: 'Media server',
      state: 'ok',
      detail: 'No server, SoulSync keeps the library',
    });
  } else {
    const probe = await probeService(s.kind, serverConfig(s));
    const libs = [
      s.musicLibrary,
      video ? s.moviesLibrary : null,
      video ? s.tvLibrary : null,
    ].filter((x): x is string => !!x);
    try {
      if (music && s.musicLibrary) await fx.selectMusicLibrary(s.kind, s.musicLibrary);
      if (video && (s.moviesLibrary || s.tvLibrary))
        await fx.saveVideoLibraries(s.moviesLibrary, s.tvLibrary);
    } catch {
      // the library pick is a convenience, settings can do it later
    }
    put({
      id: 'server',
      name: serverLabel(s.kind),
      state: probe.ok && !saveError ? 'ok' : 'warn',
      detail:
        saveError || (probe.ok ? (libs.length ? libs.join(', ') : probe.message) : probe.message),
      fix: probe.ok && !saveError ? undefined : 'server',
    });
  }

  // folders
  const keys = folderRows(d.purposes);
  const checks = await checkPaths(Object.fromEntries(keys.map((k) => [k, d.folders[k]])));
  const problems = keys.filter((k) => checks[k]?.state === 'problem');
  put({
    id: 'folders',
    name: 'Folders',
    state: problems.length ? 'warn' : 'ok',
    detail: problems.length
      ? `${problems.length} need${problems.length === 1 ? 's' : ''} a look: ${checks[problems[0]]?.message ?? ''}`
      : `${keys.length} folder${keys.length === 1 ? '' : 's'} ready`,
    fix: problems.length ? 'folders' : undefined,
  });

  // music sources + catalogue
  if (music) {
    try {
      await fx.applyPreset(d.preset);
    } catch {
      // the default profile stays, it's a fine start
    }
    const notes: string[] = [];
    if (d.chain.includes('soulseek')) {
      const r = await probeService('slskd', { slskd_url: d.slskd.url, api_key: d.slskd.apiKey });
      if (!r.ok) notes.push(r.message);
    }
    for (const id of ['tidal', 'qobuz'] as const) {
      if (d.chain.includes(id) && !d.signedIn[id])
        notes.push(`${SOURCE_INFO[id].name} isn't signed in`);
    }
    put({
      id: 'sources',
      name: 'Music sources',
      state: notes.length || saveError ? 'warn' : 'ok',
      detail:
        saveError ||
        notes[0] ||
        `${joinNames(d.chain.map((c) => SOURCE_INFO[c].name))}. ${PRESET_NAMES[d.preset]} quality`,
      fix: notes.length ? 'sources' : undefined,
    });

    const catalogue = { deezer: 'Deezer', itunes: 'Apple Music', spotify: 'Spotify' }[d.catalogue];
    const linked: string[] = [];
    let accountNote = '';
    if (d.listenbrainz.token.trim()) {
      const r = await probeService('listenbrainz', { token: d.listenbrainz.token });
      if (r.ok) linked.push('ListenBrainz');
      else accountNote = r.message;
    }
    if (d.lastfm.apiKey.trim()) linked.push('Last.fm');
    if (d.spotify.clientId.trim()) linked.push('Spotify');
    put({
      id: 'catalogue',
      name: 'Catalogue',
      state: accountNote ? 'warn' : 'ok',
      detail:
        accountNote ||
        (linked.length ? `${catalogue}, with ${linked.join(' and ')} linked` : catalogue),
      fix: accountNote ? 'metadata' : undefined,
    });
  }

  // movies and tv
  if (video) {
    const v = d.video;
    const parts: string[] = [];
    let problem = '';
    try {
      if (v.tmdbKey.trim()) await fx.saveVideoKeys({ tmdb_api_key: v.tmdbKey.trim() });
      const dl = buildVideoDownloadConfig(d);
      if (dl) await fx.saveVideoDownloads(dl);
    } catch (error) {
      problem = error instanceof Error ? error.message : "Couldn't save the video settings";
    }
    if (v.tmdbKey.trim()) {
      const r = await probeService('tmdb', { api_key: v.tmdbKey });
      if (r.ok) parts.push('TMDB');
      else problem ||= r.message;
    } else problem ||= 'No TMDB key yet';
    if (v.prowlarr.url.trim()) {
      const r = await probeService('prowlarr', { url: v.prowlarr.url, api_key: v.prowlarr.apiKey });
      if (r.ok) parts.push('Prowlarr');
      else problem ||= r.message;
    }
    if (v.client.url.trim()) {
      const r = await probeService(v.client.kind, {
        url: v.client.url,
        username: v.client.username,
        password: v.client.password,
        api_key: v.client.apiKey,
      });
      if (r.ok) parts.push(v.client.kind === 'qbittorrent' ? 'qBittorrent' : 'SABnzbd');
      else problem ||= r.message;
    }
    put({
      id: 'video',
      name: 'Movies and TV',
      state: problem ? 'warn' : 'ok',
      detail: problem || joinNames(parts).replace(', then ', ' and '),
      fix: problem ? 'video' : undefined,
    });
  }

  // follows
  if (music && d.follows.length) {
    let added = 0;
    const queue = [...d.follows];
    const worker = async () => {
      for (let pick = queue.shift(); pick; pick = queue.shift()) {
        try {
          const hit = await resolveFollow(pick);
          if (hit) {
            await fx.follow(hit);
            added += 1;
          }
        } catch {
          // one artist that won't resolve shouldn't sink the rest
        }
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    const missed = d.follows.length - added;
    put({
      id: 'follows',
      name: 'Following',
      state: missed ? 'warn' : 'ok',
      detail: missed
        ? `${added} artists, ${missed} couldn't be found in the catalogue`
        : `${added} artist${added === 1 ? '' : 's'}`,
    });
  }

  return Object.values(rows);
}
