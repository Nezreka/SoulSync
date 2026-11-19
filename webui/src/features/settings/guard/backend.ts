/**
 * The guard's backend: every GET answered from the recorded fixtures, every
 * other request written down and answered with a plausible yes. It keeps
 * what was saved the way the server does (WRITES below), so a page that
 * reads back after a save sees its change, and so a load probe can lay a
 * recorded save onto the fixtures and ask the page to show it.
 */

import endpointFixtures from './fixtures/endpoints.json';
import settingsFixture from './fixtures/settings.json';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Dict = Record<string, Json>;
export type Payload = Record<string, Dict>;

/** a request that was not a GET */
export interface Recorded {
  method: string;
  url: string;
  body: Json;
}

/** what a change sent */
export interface Saves {
  /** the keys posted to /api/settings, as posted ({section: {key: value}}) */
  settings?: Payload;
  /** every other request that was not a GET, in order */
  requests?: Recorded[];
}

export const SETTINGS_URL = '/api/settings';
export const BASELINE_SETTINGS = settingsFixture as unknown as Payload;
export const ENDPOINTS = endpointFixtures as unknown as Dict;

export const clone = <T>(value: T): T => structuredClone(value);
export const plain = (value: unknown): value is Dict =>
  !!value && typeof value === 'object' && !Array.isArray(value);

/** a posted settings body laid onto a payload the way the server stores it:
 *  each key whole, a dotted key at its path (settings-store.ts `apply`) */
export function applyBody(base: Payload, body: Payload): Payload {
  const out = clone(base);
  for (const [section, values] of Object.entries(body)) {
    if (!plain(values)) {
      // a key of the payload itself (active_media_server)
      (out as Dict)[section] = clone(values);
      continue;
    }
    const target: Dict = (out[section] ??= {});
    for (const [key, value] of Object.entries(values)) {
      const path = key.split('.');
      let node = target;
      for (const part of path.slice(0, -1)) {
        if (!plain(node[part])) node[part] = {};
        node = node[part] as Dict;
      }
      node[path[path.length - 1]] = clone(value);
    }
  }
  return out;
}

/** every leaf of a payload as section.key(.key); a list is a leaf */
export function leaves(value: Json, prefix = ''): string[] {
  if (!plain(value)) return prefix ? [prefix] : [];
  return Object.keys(value).flatMap((key) => leaves(value[key], prefix ? `${prefix}.${key}` : key));
}

/** the keys a recorded /api/settings body carries */
export const savedKeys = (saves: Saves | null) =>
  saves?.settings ? leaves(applyBody({}, saves.settings) as Json).sort() : [];

/** what the backend holds: the settings payload and every answer that is no
 *  longer the fixture's */
export interface State {
  settings: Payload;
  answers: Map<string, Json>;
}

export const answerOf = (state: State, url: string): Json | undefined =>
  state.answers.has(url) ? state.answers.get(url) : ENDPOINTS[url];

// ── what a write changes on the server ──

interface Write {
  /** matched against "METHOD /path" */
  on: RegExp;
  /** lay the body onto the answers the page reads back */
  lay: (ctx: {
    match: RegExpMatchArray;
    body: Dict;
    get: (url: string) => Json | undefined;
    put: (url: string, answer: Json) => void;
  }) => void;
  /** the response, when the page reads more than `success` from it */
  answer?: (ctx: { body: Dict; get: (url: string) => Json | undefined }) => Json;
}

const rowsUnder =
  (url: string, list: string): Write['lay'] =>
  ({ body, get, put }) => {
    const answer = get(url);
    if (!plain(answer) || !Array.isArray(answer[list])) return;
    const rows = answer[list] as Dict[];
    const at = rows.findIndex((row) => row.id === body.id);
    put(url, {
      ...answer,
      [list]: at < 0 ? [...rows, body] : rows.map((row, i) => (i === at ? body : row)),
    });
  };
const selected =
  (url: string, from: string, to = 'selected'): Write['lay'] =>
  ({ body, get, put }) => {
    const answer = get(url);
    if (plain(answer)) put(url, { ...answer, [to]: body[from] ?? null });
  };

const WRITES: Write[] = [
  {
    // the music quality profile: the list's row, and the active profile when it is that one
    on: /^POST \/api\/quality-profile\/custom\/(\d+)\/update$/,
    lay: ({ match, body, get, put }) => {
      const id = Number(match[1]);
      const list = get('/api/quality-profile/custom');
      if (plain(list) && Array.isArray(list.profiles)) {
        const row = {
          ...body,
          ...('ranked_targets' in body
            ? { ranked_targets: JSON.stringify(body.ranked_targets) }
            : {}),
        };
        put('/api/quality-profile/custom', {
          ...list,
          profiles: (list.profiles as Dict[]).map((p) => (p.id === id ? { ...p, ...row } : p)),
        });
      }
      const active = get('/api/quality-profile');
      if (plain(active) && plain(active.profile) && active.profile.id === id)
        put('/api/quality-profile', { ...active, profile: { ...active.profile, ...body } });
    },
    answer: ({ get }) => ({ ...(get('/api/quality-profile/custom') as Dict), success: true }),
  },
  {
    // the video quality profile on screen (the default one, id 0)
    on: /^POST \/api\/video\/downloads\/quality$/,
    lay: ({ body, get, put }) => {
      const url = '/api/video/downloads/quality/profiles';
      const answer = get(url);
      if (!plain(answer) || !Array.isArray(answer.profiles)) return;
      put(url, {
        ...answer,
        profiles: (answer.profiles as Dict[]).map((p, i) =>
          i === 0 ? { ...p, profile: body } : p,
        ),
      });
    },
    answer: ({ body }) => body,
  },
  {
    on: /^POST \/api\/video\/downloads\/quality\/defaults$/,
    lay: ({ body, get, put }) => {
      const url = '/api/video/downloads/quality/defaults';
      const answer = get(url);
      if (plain(answer) && typeof body.kind === 'string')
        put(url, { ...answer, [body.kind]: body.profile_id ?? null });
    },
    answer: ({ get }) => get('/api/video/downloads/quality/defaults') ?? {},
  },
  {
    on: /^POST \/api\/video\/downloads\/quality\/formats$/,
    lay: rowsUnder('/api/video/downloads/quality/formats', 'formats'),
  },
  {
    on: /^POST \/api\/video\/notifications$/,
    lay: rowsUnder('/api/video/notifications', 'connections'),
  },
  {
    on: /^POST \/api\/video\/downloads\/config\/import-lists$/,
    lay: rowsUnder('/api/video/downloads/config/import-lists', 'lists'),
  },
  {
    on: /^POST \/api\/video\/libraries$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/video/libraries');
      if (plain(answer)) put('/api/video/libraries', { ...answer, selected: body });
    },
    answer: () => ({ status: 'saved' }),
  },
  {
    on: /^POST \/api\/video\/jellyfin\/user$/,
    lay: selected('/api/video/jellyfin/users', 'user'),
  },
  {
    // the addresses come back; a typed secret comes back as its mask
    on: /^POST \/api\/video\/server-config$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/video/server-config');
      if (!plain(answer)) return;
      const next: Dict = { ...answer };
      for (const server of Object.keys(body)) {
        const sent = body[server];
        if (plain(sent) && plain(next[server]))
          next[server] = { ...(next[server] as Dict), base_url: sent.base_url ?? '' };
      }
      put('/api/video/server-config', next);
    },
  },
  {
    on: /^PUT \/api\/weather\/display$/,
    lay: selected('/api/weather', 'enabled', 'enabled'),
  },
  {
    on: /^PUT \/api\/weather\/holidays$/,
    lay: selected('/api/weather', 'enabled', 'holidays'),
  },
  {
    on: /^PUT \/api\/weather\/units$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/weather');
      if (plain(answer))
        put('/api/weather', { ...answer, units: body.use_celsius ? 'celsius' : 'fahrenheit' });
    },
  },
  {
    on: /^PUT \/api\/weather\/location$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/weather');
      if (plain(answer))
        put('/api/weather', {
          ...answer,
          location: { ...(plain(answer.location) ? answer.location : {}), query: body.location },
        });
    },
    answer: ({ get }) => ({ ...(get('/api/weather') as Dict), success: true }),
  },
  {
    // the addresses come back, a key only as "set"
    on: /^PUT \/api\/metasync\/settings$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/metasync');
      if (!plain(answer)) return;
      const next: Dict = { ...answer };
      for (const key of ['node_url', 'pool_url']) if (key in body) next[key] = body[key];
      put('/api/metasync', next);
    },
  },
  {
    // one video library's row
    on: /^PUT \/api\/video\/libraries\/folders\/(\d+)$/,
    lay: ({ match, body, get, put }) => {
      const url = '/api/video/libraries/folders';
      const answer = get(url);
      if (!plain(answer) || !Array.isArray(answer.libraries)) return;
      put(url, {
        ...answer,
        libraries: (answer.libraries as Dict[]).map((row) => {
          if (row.id !== Number(match[1])) return row;
          // the row's own fields, and the rest under `options`
          const { name, path, rules, quality_profile_id, enabled, ...options } = body;
          const own = { name, path, rules, quality_profile_id, enabled };
          const next: Dict = { ...row, options: { ...(row.options as Dict), ...options } };
          for (const [key, value] of Object.entries(own))
            if (value !== undefined) next[key] = value;
          return next;
        }),
      });
    },
  },
  {
    on: /^POST \/api\/plex\/select-music-library$/,
    lay: selected('/api/plex/music-libraries', 'library_name'),
  },
  {
    on: /^POST \/api\/jellyfin\/select-music-library$/,
    lay: selected('/api/jellyfin/music-libraries', 'library_name'),
  },
  {
    on: /^POST \/api\/jellyfin\/select-user$/,
    lay: selected('/api/jellyfin/users', 'username', 'current'),
  },
  {
    on: /^POST \/api\/navidrome\/select-music-folder$/,
    lay: selected('/api/navidrome/music-folders', 'folder_name'),
  },
  {
    on: /^POST \/api\/music-assistant\/select-source$/,
    lay: selected('/api/music-assistant/sources', 'instance_id'),
  },
  {
    on: /^POST \/api\/server-activity\/settings$/,
    lay: ({ body, get, put }) => {
      const answer = get('/api/server-activity/settings');
      if (plain(answer) && plain(answer.settings))
        put('/api/server-activity/settings', {
          ...answer,
          settings: { ...answer.settings, ...body },
        });
    },
    answer: ({ get }) => get('/api/server-activity/settings') ?? { ok: true },
  },
];

/** one write applied to the state; returns what the server answers */
export function write(state: State, request: Recorded): Json {
  const path = request.url.split('?')[0];
  const body = request.body;
  if (path === SETTINGS_URL && request.method === 'POST') {
    if (plain(body)) state.settings = applyBody(state.settings, body as Payload);
    return { success: true };
  }
  if (!plain(body)) return { success: true };
  const get = (url: string) => answerOf(state, url);
  const put = (url: string, answer: Json) => {
    state.answers.set(url, answer);
  };
  const signature = `${request.method} ${path}`;
  for (const rule of WRITES) {
    const match = signature.match(rule.on);
    if (!match) continue;
    rule.lay({ match, body, get, put });
    return rule.answer ? rule.answer({ body, get }) : { ...body, success: true };
  }
  // an endpoint read where it is written keeps the keys it was sent
  const read = get(path);
  if (request.method === 'POST' && plain(read)) {
    put(path, { ...read, ...body });
    return { ...read, ...body, success: true };
  }
  return { ...body, success: true };
}

// ── the fetch the page gets ──

export const backend = {
  /** every request that was not a GET, in order */
  requests: [] as Recorded[],
  /** GETs no fixture answers */
  unanswered: new Set<string>(),
  state: { settings: clone(BASELINE_SETTINGS), answers: new Map<string, Json>() } as State,
  inflight: 0,
  take(): Recorded[] {
    const taken = this.requests;
    this.requests = [];
    return taken;
  },
  /** hold exactly this, forgetting every write since */
  set(state: State) {
    this.state = { settings: clone(state.settings), answers: new Map(state.answers) };
  },
};

function parseBody(raw: unknown): Json {
  if (raw == null || raw === '') return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as Json;
    } catch {
      return raw;
    }
  }
  if (raw instanceof URLSearchParams) return Object.fromEntries(raw.entries());
  if (raw instanceof FormData) {
    const out: Dict = {};
    for (const [key, value] of raw.entries())
      out[key] = typeof value === 'string' ? value : `file:${value.name}`;
    return out;
  }
  return '[binary]';
}

export async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  backend.inflight += 1;
  try {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : String(input), window.location.origin);
    const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
    const key = url.pathname + url.search;
    let answer: Json;
    let status = 200;
    if (method === 'GET') {
      const found =
        url.pathname === SETTINGS_URL
          ? backend.state.settings
          : (answerOf(backend.state, key) ?? answerOf(backend.state, url.pathname));
      if (found === undefined) {
        backend.unanswered.add(key);
        status = 404;
      }
      answer = found === undefined ? { success: false, error: 'no fixture' } : found;
    } else {
      const body = parseBody(init?.body ?? (request ? await request.clone().text() : null));
      const recorded = { method, url: key, body };
      backend.requests.push(recorded);
      answer = write(backend.state, recorded);
    }
    return new Response(JSON.stringify(answer), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  } finally {
    backend.inflight -= 1;
  }
}

// ── what a recorded save changes, leaf by leaf ──

/** one changed leaf: in the settings payload, or in a GET answer */
export interface Leaf {
  where: string;
  path: string[];
  value: Json | undefined;
}

function diff(before: Json | undefined, after: Json | undefined, path: string[], out: string[][]) {
  if (plain(before) && plain(after)) {
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)]))
      diff(before[key], after[key], [...path, key], out);
  } else if (JSON.stringify(before) !== JSON.stringify(after)) {
    out.push(path);
  }
}
const at = (value: Json | undefined, path: string[]) =>
  path.reduce<Json | undefined>((node, key) => (plain(node) ? node[key] : undefined), value);

/** the leaves a list of recorded requests changes, starting from `base` */
export function changes(base: State, requests: Recorded[]): Leaf[] {
  const after: State = { settings: clone(base.settings), answers: new Map(base.answers) };
  for (const request of requests) write(after, request);
  const out: Leaf[] = [];
  const paths: string[][] = [];
  diff(base.settings as Json, after.settings as Json, [], paths);
  for (const path of paths)
    out.push({ where: SETTINGS_URL, path, value: at(after.settings as Json, path) });
  for (const [url, answer] of after.answers) {
    const found: string[][] = [];
    diff(answerOf(base, url), answer, [], found);
    for (const path of found) out.push({ where: url, path, value: at(answer, path) });
  }
  return out;
}

const sameStart = (a: string[], b: string[]) =>
  a.slice(0, Math.min(a.length, b.length)).every((part, i) => part === b[i]);

/** true when two sets of leaves cannot be laid together */
export function clash(a: Leaf[], b: Leaf[]): boolean {
  return a.some((x) =>
    b.some(
      (y) =>
        x.where === y.where &&
        sameStart(x.path, y.path) &&
        (x.path.length !== y.path.length || JSON.stringify(x.value) !== JSON.stringify(y.value)),
    ),
  );
}

/** `base` with the leaves laid on */
export function lay(base: State, leaves: Leaf[]): State {
  const out: State = { settings: clone(base.settings), answers: new Map(base.answers) };
  for (const leaf of leaves) {
    let root: Json;
    if (leaf.where === SETTINGS_URL) root = out.settings as Json;
    else {
      root = clone(answerOf(out, leaf.where) ?? {});
      out.answers.set(leaf.where, root);
    }
    if (leaf.path.length === 0) {
      if (leaf.where !== SETTINGS_URL) out.answers.set(leaf.where, clone(leaf.value ?? null));
      continue;
    }
    let node = root as Dict;
    for (const part of leaf.path.slice(0, -1)) {
      if (!plain(node[part])) node[part] = {};
      node = node[part] as Dict;
    }
    const last = leaf.path[leaf.path.length - 1];
    if (leaf.value === undefined) delete node[last];
    else node[last] = clone(leaf.value);
  }
  return out;
}
