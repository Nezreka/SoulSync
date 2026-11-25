/**
 * The video side's server (SETTINGS_PORT.md, video pass V3b): which server it
 * reads (/api/video/server), its own Plex and Jellyfin credentials
 * (/api/video/server-config, pre-filled from Music but stored apart), the
 * Jellyfin user, and which libraries the scan reads (/api/video/libraries).
 * video-settings.js loaded all of it each time the video settings page was
 * shown; so does this.
 */

import { useSyncExternalStore } from 'react';

import { onVideoSave, videoShown } from './video-shown';

/** soulsync: Standalone, its own folder scan is the library (no media server) */
export type Server = 'plex' | 'jellyfin' | 'soulsync';
/** the two that have a connection to fill in and test */
export type MediaServer = Exclude<Server, 'soulsync'>;
export const SERVER_URL = '/api/video/server';
export const CONN_URL = '/api/video/server-config';
export const LIBS_URL = '/api/video/libraries';
export const LINKS_URL = '/api/video/server/links';

export interface Conn {
  'plex-url': string;
  'plex-token': string;
  'jellyfin-url': string;
  'jellyfin-key': string;
}
export type ConnField = keyof Conn;

interface Library {
  title: string;
}
export interface Libraries {
  movies: Library[];
  tv: Library[];
  /** the picks */
  movie: string;
  show: string;
  /** the other TV libraries ticked Scan, and those ticked Anime */
  tvMore: string[];
  anime: string[];
}

/** plex and jellyfin as one library (core/video/server_links.py) */
export interface Links {
  enabled: boolean;
  /** the connected server that isn't the video server; null with only one */
  other: MediaServer | null;
  movies: number;
  movies_total: number;
  shows: number;
  shows_total: number;
  last?: { state?: string; error?: string; unmatched_count?: number };
}

/** linksWords: how much of the library the other server has too, in words */
export function linksWords(l: Links): string {
  const other = l.other === 'plex' ? 'Plex' : 'Jellyfin';
  if (l.last?.state === 'running') return `Reading ${other}…`;
  if (l.last?.state === 'error') return `Couldn't read ${other}: ${l.last.error || 'no answer'}`;
  if (!l.enabled) return `Off. ${other} is connected too.`;
  if (!l.movies && !l.shows) return `Nothing linked yet. Link now reads ${other}.`;
  const n = (v: number) => v.toLocaleString('en-US');
  return `${n(l.movies)} of ${n(l.movies_total)} movies and ${n(l.shows)} of ${n(l.shows_total)} shows are on ${other} too.`;
}

export interface User {
  id: string;
  name: string;
  admin?: boolean;
}

interface State {
  /** null until the server is known: no panel shows */
  active: Server | null;
  conn: Conn;
  notes: Record<MediaServer, string>;
  /** null: the picker is hidden */
  users: { list: User[]; selected: string } | null;
  /** null until the libraries load: the pickers say Loading... */
  libs: Libraries | null;
  status: string;
  /** null: one server (or none) connected, so there is nothing to link */
  links: Links | null;
  /** what the last Test of each server said, in words */
  said: Partial<Record<Server, { tone: 'ok' | 'bad'; text: string }>>;
}

const NAME: Record<Server, string> = { plex: 'Plex', jellyfin: 'Jellyfin', soulsync: 'Standalone' };

/** connNote: what is still missing, or where the connection came from */
export function connNote(
  server: MediaServer,
  hasUrl: boolean,
  hasSecret: boolean,
  inherited: boolean,
) {
  const secret = server === 'plex' ? 'token' : 'API key';
  if (!hasUrl && !hasSecret) return `Not connected: add a server URL and ${secret}.`;
  if (!hasSecret) return `Add the ${secret} to finish this connection.`;
  if (!hasUrl) return 'Add the server URL to finish this connection.';
  if (inherited)
    return `Inherited from your music ${NAME[server]} connection. Change it to use a different server for video.`;
  return 'Custom video connection.';
}

/** the other TV libraries (the main pick is always scanned) */
export const otherTv = (libs: Libraries) => libs.tv.filter((l) => l.title && l.title !== libs.show);

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);
const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
  body: JSON.stringify(body),
});
const asText = (v: unknown) => (typeof v === 'string' ? v : '');

function createVideoServer() {
  let state: State = {
    active: null,
    conn: { 'plex-url': '', 'plex-token': '', 'jellyfin-url': '', 'jellyfin-key': '' },
    notes: { plex: '', jellyfin: '' },
    users: null,
    libs: null,
    status: '',
    links: null,
    said: {},
  };
  const listeners = new Set<() => void>();
  const set = (next: Partial<State>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };

  /** loadServer: the pick, else the configured one, else Plex */
  async function loadServer() {
    try {
      const r = await fetch(SERVER_URL, { headers: { Accept: 'application/json' } });
      const d = ((r.ok ? await r.json() : null) || {}) as {
        server?: Server;
        plex?: unknown;
        jellyfin?: unknown;
      };
      const active: Server = d.server || (d.jellyfin && !d.plex ? 'jellyfin' : 'plex');
      set({ active });
      if (active === 'jellyfin') void loadUsers();
    } catch {
      set({ active: 'plex' });
    }
  }

  async function loadConn() {
    try {
      const r = await fetch(CONN_URL, { headers: { Accept: 'application/json' } });
      const d = r.ok
        ? ((await r.json()) as {
            plex?: Record<string, unknown>;
            jellyfin?: Record<string, unknown>;
          } | null)
        : null;
      if (!d) return;
      const p = d.plex || {};
      const j = d.jellyfin || {};
      const fresh: Conn = {
        'plex-url': asText(p.base_url),
        'plex-token': p.has_token ? asText(p.token) : '',
        'jellyfin-url': asText(j.base_url),
        'jellyfin-key': j.has_key ? asText(j.api_key) : '',
      };
      // never the field being typed in: a save fires on change, so this can
      // land while the next field is being filled in
      const typing = (document.activeElement as HTMLElement | null)?.getAttribute?.(
        'data-video-conn',
      ) as ConnField | null;
      const conn = { ...fresh };
      if (typing && typing in conn) conn[typing] = state.conn[typing];
      set({
        conn,
        notes: {
          plex: connNote('plex', !!p.base_url, !!p.has_token, !!p.inherited),
          jellyfin: connNote('jellyfin', !!j.base_url, !!j.has_key, !!j.inherited),
        },
      });
    } catch {
      // ignore
    }
  }

  async function saveConn(silent = false) {
    const c = state.conn;
    try {
      await fetch(
        CONN_URL,
        json({
          plex: { base_url: c['plex-url'], token: c['plex-token'] },
          jellyfin: { base_url: c['jellyfin-url'], api_key: c['jellyfin-key'] },
        }),
      );
      if (!silent) toast('Connection saved', 'success');
      // the notes once the save has landed
      await loadConn();
    } catch {
      if (!silent) toast('Could not save connection', 'error');
    }
  }

  async function loadUsers() {
    try {
      const r = await fetch('/api/video/jellyfin/users', {
        headers: { Accept: 'application/json' },
      });
      const d = r.ok
        ? ((await r.json()) as { success?: boolean; users?: User[]; selected?: string } | null)
        : null;
      if (!d || !d.success || !d.users || !d.users.length) {
        set({ users: null });
        return;
      }
      set({ users: { list: d.users, selected: d.selected || '' } });
    } catch {
      set({ users: null });
    }
  }

  async function loadLibs() {
    set({ status: 'Loading…' });
    try {
      const r = await fetch(LIBS_URL, { headers: { Accept: 'application/json' } });
      const d = r.ok
        ? ((await r.json()) as {
            error?: string;
            movies?: Library[];
            tv?: Library[];
            selected?: { movies?: string; tv?: string; tv_more?: string[]; anime?: string[] };
          } | null)
        : null;
      if (!d || d.error) {
        set({ status: 'Could not load libraries' });
        return;
      }
      const sel = d.selected || {};
      const libs: Libraries = {
        movies: d.movies || [],
        tv: d.tv || [],
        movie: sel.movies || '',
        show: sel.tv || '',
        tvMore: sel.tv_more || [],
        anime: sel.anime || [],
      };
      // a pick the server no longer lists shows None, as the page's select did
      if (!libs.movies.some((l) => l.title === libs.movie)) libs.movie = '';
      if (!libs.tv.some((l) => l.title === libs.show)) libs.show = '';
      let status = '';
      if (!libs.movies.length && !libs.tv.length)
        status =
          'No movie or TV libraries found. Check the server connection and that the user above can see them.';
      else if (!sel.movies && !sel.tv) status = 'Pick a library or the scan has nothing to read.';
      set({ libs, status });
    } catch {
      set({ status: 'Could not load libraries' });
    }
  }

  let linksTimer: ReturnType<typeof setTimeout> | undefined;
  /** the link state; while a pass runs, asked again until it is done */
  async function loadLinks() {
    try {
      const r = await fetch(LINKS_URL, { headers: { Accept: 'application/json' } });
      const d = r.ok ? ((await r.json()) as (Links & { success?: boolean }) | null) : null;
      set({ links: d && d.success && d.other ? d : null });
      clearTimeout(linksTimer);
      if (d?.last?.state === 'running') linksTimer = setTimeout(() => void loadLinks(), 3000);
    } catch {
      set({ links: null });
    }
  }

  async function postLinks(body: { enabled?: boolean; run?: boolean }) {
    try {
      const r = await fetch(LINKS_URL, json(body));
      const d = (await r.json()) as Links & {
        success?: boolean;
        error?: string;
        started?: boolean;
      };
      if (!d.success) {
        toast(d.error || 'Could not change linking', 'error');
        return;
      }
      set({ links: d.other ? d : null });
      if (d.started) {
        toast(`Reading ${d.other === 'plex' ? 'Plex' : 'Jellyfin'} to link it`, 'info');
        clearTimeout(linksTimer);
        linksTimer = setTimeout(() => void loadLinks(), 2000);
      }
    } catch {
      toast('Could not change linking', 'error');
    }
  }

  async function saveLibs(silent = false) {
    const l = state.libs;
    if (!l) return;
    const shown = otherTv(l).map((x) => x.title);
    // the ticks in the order the list shows them, as the page read them
    const tvMore = shown.filter((t) => l.tvMore.includes(t));
    // the main TV pick is always scanned; it keeps an Anime mark it had
    const anime = [
      ...shown.filter((t) => l.anime.includes(t)),
      ...(l.show && l.anime.includes(l.show) ? [l.show] : []),
    ];
    set({ libs: { ...l, tvMore, anime }, status: 'Saving…' });
    try {
      const r = await fetch(
        LIBS_URL,
        json({ movies: l.movie, tv: l.show, tv_more: tvMore, anime }),
      );
      await r.json();
      set({ status: 'Saved' });
      if (!silent) toast('Library selection saved', 'success');
    } catch {
      set({ status: 'Save failed' });
      if (!silent) toast('Could not save libraries', 'error');
    }
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    /** everything, as each showing of the video settings page did */
    load: () => {
      void loadServer();
      void loadConn();
      void loadLibs();
      void loadLinks();
    },
    /** linking the two servers: on or off, and Link now */
    setLinks: (enabled: boolean) => postLinks({ enabled }),
    linkNow: () => postLinks({ run: true }),
    saveConn,
    saveLibs,
    type: (field: ConnField, value: string) => set({ conn: { ...state.conn, [field]: value } }),
    /** pickServer: show its fields now, and make it the video server */
    pick: async (server: Server) => {
      set({ active: server });
      if (server === 'jellyfin') void loadUsers();
      try {
        await fetch(SERVER_URL, json({ server }));
        void loadLibs();
        void loadLinks();
        toast(
          server === 'soulsync'
            ? 'Video library set to Standalone: SoulSync scans your folders'
            : `Video server set to ${NAME[server]}`,
          'success',
        );
      } catch {
        // ignore
      }
    },
    /** testConn: save quietly, then ask the server */
    test: async (server: MediaServer) => {
      const name = NAME[server];
      toast(`Testing ${name} connection…`, 'info');
      try {
        await saveConn(true);
        const r = await fetch(`${CONN_URL}/test`, json({ server }));
        const res = (await r.json()) as { success?: boolean; message?: string; error?: string };
        const said = (tone: 'ok' | 'bad', text: string) =>
          set({ said: { ...state.said, [server]: { tone, text } } });
        if (res && res.success) {
          said('ok', res.message || 'Connected');
          toast(res.message || `${name} connection successful`, 'success');
          // its user and libraries
          if (server === 'jellyfin') {
            void loadUsers();
            void loadLibs();
          }
        } else {
          said('bad', (res && res.error) || 'Connection failed');
          toast(`${name} connection failed: ${(res && res.error) || 'unknown'}`, 'error');
        }
      } catch {
        set({
          said: { ...state.said, [server]: { tone: 'bad', text: 'Could not reach SoulSync' } },
        });
        toast(`Failed to test ${name} connection`, 'error');
      }
    },
    /** selectJellyfinUser: its libraries next */
    pickUser: async (id: string) => {
      if (state.users) set({ users: { ...state.users, selected: id } });
      try {
        await fetch('/api/video/jellyfin/user', json({ user: id }));
        void loadLibs();
        if (id) toast('Jellyfin user updated', 'success');
      } catch {
        // ignore
      }
    },
    /** a library picked or ticked: saved at once */
    setLibs: (next: Partial<Libraries>) => {
      if (!state.libs) return Promise.resolve();
      set({ libs: { ...state.libs, ...next } });
      return saveLibs();
    },
    /** for tests */
    reset: () => {
      state = {
        active: null,
        conn: { 'plex-url': '', 'plex-token': '', 'jellyfin-url': '', 'jellyfin-key': '' },
        notes: { plex: '', jellyfin: '' },
        users: null,
        libs: null,
        status: '',
        links: null,
        said: {},
      };
    },
  };
}

export const videoServer = createVideoServer();
export type VideoServer = typeof videoServer;

export function useVideoServer() {
  return useSyncExternalStore(videoServer.subscribe, videoServer.get);
}

if (typeof document !== 'undefined') {
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    videoServer.load();
  });
  // the video side's save button saves both, quietly
  onVideoSave(() => Promise.all([videoServer.saveConn(true), videoServer.saveLibs(true)]));
}
