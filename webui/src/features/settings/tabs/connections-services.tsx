/**
 * Connections > Services (SETTINGS_PORT.md, step 10a): the Music / Video
 * tabs, the tile grid and the modal a tile opens. settings.js
 * buildServiceTiles, switchServiceKind, openServiceModal, closeServiceModal.
 *
 * The forms are the service cards (ServiceFrame), kept in the hidden API
 * Configuration groups. A tile opens the real card in the side sheet, never a
 * copy, so every id stays one element: a card portals into a wrapper of its
 * own and the wrapper is what moves (React hears a portal's events wherever
 * its wrapper sits). The sheet's foot calls the card's own Test and main
 * action (serviceActions), so the request and its answer are the card's.
 *
 * A tile's name and logo come from its card; its status comes from what
 * SoulSync knows (serviceStatus): the server's config check
 * (/api/settings/config-status), the /status poll (Spotify's sign-in), the
 * Qobuz sign-in, Hydrabase's link, the keys the server holds, and the last
 * Test of this visit. Never from "a text box has something in it": a
 * pre-filled Redirect URI made Spotify, Deezer and Tidal read as set up.
 */

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';

import type { SettingsPayload } from '../settings-store';

import { settingsStore } from '../settings-store';
import {
  Btn,
  Mark,
  Note,
  SectionLabel,
  Segmented,
  Sheet,
  SheetFootSay,
  StatusWords,
  Tile,
  Tiles,
  type Tone,
} from '../ui';
import s from '../ui/settings.module.css';
import {
  hydrabaseLink,
  onQobuzAuth,
  type QobuzAuthState,
  type StatusFrame,
  statusFrames,
} from './connections';
import { type TestResult, useTestResults } from './connections-servers';
import { useVideoKeys } from './video-keys';
import { markVideoShown } from './video-shown';

export type Kind = 'music' | 'video';

export const SERVICE_KINDS: Record<Kind, { label: string; attr: string }> = {
  music: { label: 'Music', attr: 'data-service' },
  video: { label: 'Video', attr: 'data-video-service' },
};

// real brand art wherever the repo has it; a service with none gets the
// glyph the video dashboard already uses for it, else a monogram
const SERVICE_LOGOS: Record<string, string> = {
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  deezer: '/static/img/brands/deezer.png',
  discogs: '/static/img/brands/discogs.svg',
  tidal: '/static/img/brands/tidal.svg',
  qobuz: '/static/img/brands/qobuz.svg',
  lastfm: '/static/img/brands/lastfm.png',
  genius: '/static/img/brands/genius.png',
  musicbrainz: '/static/img/brands/musicbrainz.png',
  listenbrainz: '/static/img/brands/listenbrainz.png',
  tmdb: '/static/img/brands/tmdb.svg',
  tvdb: '/static/img/brands/tvdb.svg',
  ytextras: '/static/img/brands/youtube.svg',
};

// dark marks that vanish on a dark tile (the download chain keeps the same)
const INVERT_SERVICE_MARKS = new Set(['tidal', 'qobuz', 'discogs', 'tvdb']);

const SERVICE_GLYPHS: Record<string, string> = {
  omdb: '★',
  fanart: '🎨',
  'artist-fanart': '🎨',
  opensubtitles: '💬',
  trakt: '★',
  nokey: '📺',
};

// by what each service does for you. order inside a group is the order here;
// a service not listed lands in Other, so a new one never vanishes
const SERVICE_GROUPS: Record<Kind, Array<{ label: string; hint: string; ids: string[] }>> = {
  music: [
    {
      label: 'Metadata',
      hint: 'artists, albums and tracks',
      ids: ['musicbrainz', 'spotify', 'itunes', 'deezer', 'discogs', 'tidal', 'qobuz', 'hydrabase'],
    },
    {
      label: 'Scrobbling',
      hint: 'listening history and recommendations',
      ids: ['lastfm', 'listenbrainz'],
    },
    {
      label: 'Extras',
      hint: 'lyrics, concerts, fingerprint checks, cover art and artist artwork',
      ids: ['genius', 'concerts', 'acoustid', 'pollinations', 'artist-fanart'],
    },
  ],
  video: [
    { label: 'Metadata', hint: 'movies, shows and episodes', ids: ['tmdb', 'tvdb', 'nokey'] },
    {
      label: 'Ratings & lists',
      hint: 'scores and list collections',
      ids: ['omdb', 'trakt', 'mdblist'],
    },
    {
      label: 'Subtitles',
      hint: 'found and downloaded for your library',
      ids: ['opensubtitles', 'subdl', 'subsource'],
    },
    {
      label: 'Artwork & extras',
      hint: 'posters, fanart and trailers',
      ids: ['fanart', 'ytextras'],
    },
  ],
};

// ── what is open, and which side the tiles show ──

interface ServicesState {
  kind: Kind;
  /** an explicit pick outranks the side the page is on */
  chosen: boolean;
  open: { id: string; kind: Kind } | null;
  /** goes up whenever the tiles should read the cards again */
  version: number;
}

function createServicesModel() {
  let state: ServicesState = { kind: 'music', chosen: false, open: null, version: 0 };
  const listeners = new Set<() => void>();
  const set = (next: Partial<ServicesState>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };
  /** follow the side the page is on, as the download chain does */
  const sideKind = (): Kind =>
    state.chosen
      ? state.kind
      : document.body.getAttribute('data-side') === 'video'
        ? 'video'
        : 'music';
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    /** settings.js buildServiceTiles: read the cards again */
    rebuild: () => set({ kind: sideKind(), version: state.version + 1 }),
    switchKind: (kind: string) => {
      if (kind !== 'music' && kind !== 'video') return;
      set({ kind, chosen: true, version: state.version + 1 });
    },
    open: (id: string) => set({ open: { id, kind: state.kind } }),
    close: () => set({ open: null, kind: sideKind(), version: state.version + 1 }),
    /** for tests */
    reset: () => set({ kind: 'music', chosen: false, open: null, version: 0 }),
  };
}

export const servicesModel = createServicesModel();
export type ServicesModel = typeof servicesModel;

function useServices(model: ServicesModel = servicesModel) {
  return useSyncExternalStore(model.subscribe, model.get);
}

// ── a React card's place: its wrapper, at home or in the modal ──

/** the group the cards live in while closed (the hidden API Configuration
 *  group): they sit after its heading, before the row of test buttons */
export const ServiceHome = createContext<HTMLElement | null>(null);

function placeHome(home: HTMLElement, wrap: HTMLElement) {
  const before = home.querySelector(':scope > .api-test-buttons');
  // back in page order, whichever came home last
  const wraps = [
    wrap,
    ...[...home.children].filter((c) => (c as HTMLElement).dataset.svcWrap !== undefined),
  ];
  wraps
    .sort(
      (a, b) => Number((a as HTMLElement).dataset.order) - Number((b as HTMLElement).dataset.order),
    )
    .forEach((c) => home.insertBefore(c, before));
}

/**
 * The wrapper a React card portals into, and whether it is the open one.
 * It sits in the card's home slot, or in the modal body while open. The
 * video side's cards are React too (SETTINGS_PORT.md video pass V3a).
 */
export function useServicePlace(service: string, order: number, kind: Kind = 'music') {
  const home = useContext(ServiceHome);
  const { open } = useServices();
  const isOpen = open?.kind === kind && open.id === service;
  const [wrap] = useState(() => {
    const div = document.createElement('div');
    div.style.display = 'contents';
    div.dataset.svcWrap = service;
    div.dataset.order = String(order);
    return div;
  });
  useLayoutEffect(() => {
    const body = document.getElementById('svc-modal-body');
    if (isOpen && body) body.appendChild(wrap);
    else if (home) placeHome(home, wrap);
  }, [isOpen, home, wrap]);
  useEffect(() => () => wrap.remove(), [wrap]);
  return { wrap, open: isOpen };
}

// ── a card ──

/** what the sheet's foot does for a card: its Test, and its main action
 *  (Authenticate) in place of Done */
export interface CardActions {
  test?: () => unknown;
  primary?: { label: string; onClick: () => unknown; id?: string };
}

/** by `${kind}:${service}`; a card keeps its own entry current */
export const serviceActions = new Map<string, CardActions>();
export const actionKey = (kind: Kind, service: string) => `${kind}:${service}`;

/**
 * One service's card: its fields on the parts, kept at home while closed and
 * in the service sheet while its tile is open. The tile reads its name,
 * colour, logo and whether it is required from the data attributes.
 */
export function ServiceFrame({
  kind = 'music',
  service,
  order,
  title,
  colour,
  logo,
  required,
  id,
  extra,
  test,
  primary,
  children,
}: {
  kind?: Kind;
  service: string;
  order: number;
  /** "Deezer (Favorites & Playlists)": the tile shows the name, the sheet the rest */
  title: string;
  colour: string;
  logo?: string;
  required?: boolean;
  id?: string;
  extra?: Record<string, string>;
  test?: () => unknown;
  primary?: CardActions['primary'];
  children: ReactNode;
}) {
  const { wrap } = useServicePlace(service, order, kind);
  const key = actionKey(kind, service);
  // every render: the actions read what is on screen now
  useEffect(() => {
    serviceActions.set(key, { test, primary });
  });
  useEffect(() => () => void serviceActions.delete(key), [key]);
  const attr = SERVICE_KINDS[kind].attr;
  return createPortal(
    <div
      className="stg-service"
      data-svc-frame=""
      data-react-panel=""
      id={id}
      {...{ [attr]: service }}
      data-svc-title={title}
      data-svc-colour={colour}
      data-svc-logo={logo}
      data-svc-required={required ? '' : undefined}
      {...extra}
    >
      <div className={s.svcBody} data-ui="" data-svc-body="">
        {children}
      </div>
    </div>,
    wrap,
  );
}

// ── reading the cards ──

function frames(kind: Kind): HTMLElement[] {
  const attr = SERVICE_KINDS[kind].attr;
  // the Connections tab only: Detail Pages, on the Library tab, is a video
  // preferences panel, not a service
  return [
    ...document.querySelectorAll<HTMLElement>(`#settings-page [data-svc-frame][${attr}]`),
    // a card in the sheet is still a service: while it is open, and for the
    // moment after it closes, its frame sits in the sheet's body and not in
    // its home slot. the tiles are read again on close, before the card has
    // gone home, and counting home slots alone dropped its tile (#2285).
  ].filter((f) => f.closest('[data-svc-legacy]') || f.closest('#svc-modal-body'));
}

export interface ServiceMeta {
  id: string;
  name: string;
  detail: string;
  logo: string | null;
  colour: string;
  required: boolean;
  inverted: boolean;
  glyph: string | null;
  monogram: string;
}

export function serviceMeta(frame: HTMLElement): ServiceMeta {
  const title = frame.dataset.svcTitle || '';
  const id = frame.getAttribute('data-service') || frame.getAttribute('data-video-service') || '';
  const logo = SERVICE_LOGOS[id] || frame.dataset.svcLogo || null;
  const colour = frame.dataset.svcColour || '';
  // "Deezer (Favorites & Playlists)": the tile shows the name, the modal the rest
  const paren = title.indexOf('(');
  const name = (paren > 0 ? title.slice(0, paren) : title).trim();
  return {
    id,
    name,
    detail:
      paren > 0
        ? title
            .slice(paren + 1)
            .replace(/\)\s*$/, '')
            .trim()
        : '',
    logo,
    colour,
    required: frame.hasAttribute('data-svc-required'),
    inverted: INVERT_SERVICE_MARKS.has(id),
    glyph: SERVICE_GLYPHS[id] || null,
    monogram: (name.replace(/[^A-Za-z0-9]/g, '')[0] || '?').toUpperCase(),
  };
}

/** a credential is filled in; a keyless service is switched on; null when
 *  there is nothing to judge by */
export function serviceConfigured(frame: HTMLElement): boolean | null {
  const fields = frame.querySelectorAll<HTMLInputElement>(
    'input[type="text"], input[type="password"], input[type="email"]',
  );
  if (fields.length) return [...fields].some((f) => String(f.value || '').trim() !== '');
  const toggles = frame.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  for (const t of toggles) if (t.checked) return true;
  return null;
}

interface TileInfo extends ServiceMeta {
  set: boolean | null;
}
interface Group {
  label: string;
  hint: string;
  tiles: TileInfo[];
}

function readGroups(kind: Kind): Group[] | null {
  const all = frames(kind);
  if (!all.length) return null;
  const attr = SERVICE_KINDS[kind].attr;
  const byId = new Map(all.map((f) => [f.getAttribute(attr) || '', f]));
  const tile = (f: HTMLElement): TileInfo => ({ ...serviceMeta(f), set: serviceConfigured(f) });
  const groups = SERVICE_GROUPS[kind].map((g) => ({
    label: g.label,
    hint: g.hint,
    tiles: g.ids.flatMap((id) => (byId.has(id) ? [tile(byId.get(id)!)] : [])),
  }));
  const listed = new Set(SERVICE_GROUPS[kind].flatMap((g) => g.ids));
  groups.push({
    label: 'Other',
    hint: '',
    tiles: all.filter((f) => !listed.has(f.getAttribute(attr) || '')).map(tile),
  });
  return groups.filter((g) => g.tiles.length);
}

// ── what each service's state is, from what SoulSync knows ──

export interface ServiceState {
  tone: Tone;
  text: string;
  /** set up: counts in the hero */
  set: boolean;
  /** needs nothing to work */
  keyless?: boolean;
}

/** the server's config check: { spotify: { configured }, ... } */
export type ConfigStatus = Record<string, { configured?: boolean } | undefined>;

/** keyless music services: they work with nothing filled in */
const KEYLESS = new Set(['itunes', 'musicbrainz', 'deezer']);

/** the saved key a music service without a config-status entry needs */
const SAVED_KEYS: Record<string, Array<[string, string]>> = {
  concerts: [
    ['concerts', 'ticketmaster_api_key'],
    ['concerts', 'setlistfm_api_key'],
  ],
  pollinations: [['pollinations', 'api_key']],
  'artist-fanart': [['fanart', 'api_key']],
};

/** the video cards' key, by card */
const VIDEO_KEY: Record<string, string> = {
  tmdb: 'tmdb',
  tvdb: 'tvdb',
  omdb: 'omdb',
  fanart: 'fanart',
  opensubtitles: 'opensubtitles',
  subdl: 'subdl',
  subsource: 'subsource',
  trakt: 'trakt',
  mdblist: 'mdblist',
};
const VIDEO_REQUIRED = new Set(['tmdb', 'tvdb']);
/** the keyless video cards: on by their switches */
const VIDEO_SWITCHES: Record<string, string[]> = {
  nokey: ['tvmaze', 'anilist', 'wikidata'],
  ytextras: ['ryd', 'sponsorblock', 'dearrow'],
};

export interface StateInputs {
  config: ConfigStatus | null;
  frame: StatusFrame | null;
  qobuz: QobuzAuthState | null;
  hydrabase: boolean;
  settings: SettingsPayload;
  videoKeys: Record<string, string>;
  videoOn: Record<string, boolean>;
  tests: Record<string, TestResult>;
}

const filled = (value: unknown) => typeof value === 'string' && value.trim() !== '';

/** one service's state in words. Order: a sign-in SoulSync can see, the last
 *  Test of this visit, the server's config check, the saved keys. What none
 *  of those can say is said as "Not tested yet" */
export function serviceStatus(kind: Kind, id: string, inputs: StateInputs): ServiceState {
  const tested = inputs.tests[id];
  if (kind === 'video') {
    const switches = VIDEO_SWITCHES[id];
    if (switches) {
      const on = switches.filter((k) => inputs.videoOn[k]).length;
      return on
        ? { tone: 'ok', text: `${on} of ${switches.length} on`, set: true, keyless: true }
        : { tone: 'idle', text: 'All off', set: false, keyless: true };
    }
    const field = VIDEO_KEY[id];
    if (!field) return { tone: 'idle', text: 'Not tested yet', set: false };
    if (filled(inputs.videoKeys[field])) return { tone: 'ok', text: 'Key saved', set: true };
    return VIDEO_REQUIRED.has(id)
      ? { tone: 'warn', text: 'Required: no key yet', set: false }
      : { tone: 'idle', text: 'Not set up', set: false };
  }
  if (id === 'spotify') {
    const spotify = inputs.frame?.spotify;
    if (spotify?.authenticated) return { tone: 'ok', text: 'Signed in', set: true };
    if (tested) return { tone: tested.tone, text: tested.text, set: tested.tone !== 'bad' };
    if (inputs.config?.spotify?.configured)
      return { tone: 'warn', text: 'Not signed in', set: false };
    return { tone: 'idle', text: 'Not set up', set: false };
  }
  if (id === 'qobuz') {
    if (inputs.qobuz?.authenticated) {
      const who = inputs.qobuz.user?.display_name;
      return { tone: 'ok', text: who ? `Signed in as ${who}` : 'Signed in', set: true };
    }
    if (inputs.qobuz) return { tone: 'idle', text: 'Not signed in', set: false };
  }
  if (id === 'hydrabase' && inputs.hydrabase) return { tone: 'ok', text: 'Connected', set: true };
  if (tested) return { tone: tested.tone, text: tested.text, set: tested.tone !== 'bad' };
  if (KEYLESS.has(id)) {
    // Deezer works keyless; its sign-in adds favourites and playlists
    if (id === 'deezer' && filled((inputs.settings.deezer as Record<string, unknown>)?.arl))
      return { tone: 'ok', text: 'Set up', set: true, keyless: true };
    return { tone: 'idle', text: 'No key needed', set: false, keyless: true };
  }
  const known = inputs.config?.[id];
  if (known && typeof known.configured === 'boolean')
    return known.configured
      ? { tone: 'ok', text: 'Set up', set: true }
      : { tone: 'idle', text: 'Not set up', set: false };
  const keys = SAVED_KEYS[id];
  if (keys) {
    const saved = keys.some(([section, key]) =>
      filled((inputs.settings[section] as Record<string, unknown> | undefined)?.[key]),
    );
    return saved
      ? { tone: 'ok', text: 'Key saved', set: true }
      : { tone: 'idle', text: 'Not set up', set: false };
  }
  return { tone: 'idle', text: 'Not tested yet', set: false };
}

/** everything serviceStatus reads, kept current */
export function useStateInputs(opened = 0): StateInputs {
  const frame = useSyncExternalStore(statusFrames.subscribe, statusFrames.get);
  const hydrabase = useSyncExternalStore(hydrabaseLink.subscribe, hydrabaseLink.get);
  const settings = useSyncExternalStore(
    settingsStore.subscribe,
    () => settingsStore.getState().settings,
  );
  const loads = useSyncExternalStore(settingsStore.subscribe, () => settingsStore.getState().loads);
  const tests = useTestResults();
  const video = useVideoKeys();
  const [qobuz, setQobuz] = useState<QobuzAuthState | null>(null);
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  useEffect(() => onQobuzAuth(setQobuz), []);
  // the server's own check, each time the tab opens and after each load
  useEffect(() => {
    let live = true;
    fetch('/api/settings/config-status')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: ConfigStatus | null) => {
        if (live && data && typeof data === 'object') setConfig(data);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [opened, loads]);
  return {
    config,
    frame,
    qobuz,
    hydrabase,
    settings,
    videoKeys: video.keys as Record<string, string>,
    videoOn: video.on as Record<string, boolean>,
    tests,
  };
}

// ── the marks ──

export function ServiceMark({ meta, size }: { meta: ServiceMeta; size?: 'sm' | 'lg' }) {
  return (
    <Mark
      src={meta.logo}
      invert={meta.inverted}
      pad={!!meta.logo && meta.id !== 'spotify'}
      icon={meta.glyph ? 'box' : 'link'}
      brand={meta.colour ? brandRgb(meta.colour) : undefined}
      size={size}
    />
  );
}

/** a card dot's colour ("rgb(29, 185, 84)" or "#1DB954") as "r, g, b" */
function brandRgb(colour: string): string | undefined {
  const rgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(colour);
  if (rgb) return `${rgb[1]}, ${rgb[2]}, ${rgb[3]}`;
  const hex = /^#([0-9a-f]{6})$/i.exec(colour.trim());
  if (!hex) return undefined;
  const n = parseInt(hex[1], 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

// ── the switch, the tiles, the sheet ──

function frameOf(open: { id: string; kind: Kind }) {
  const attr = SERVICE_KINDS[open.kind].attr;
  return document.querySelector<HTMLElement>(
    `#settings-page [data-svc-frame][${attr}="${CSS.escape(open.id)}"]`,
  );
}

export function ServicesPart({
  model = servicesModel,
  opened = 0,
  onCounts,
}: {
  model?: ServicesModel;
  opened?: number;
  /** what the hero counts: set up, keyless, not set up (this kind) */
  onCounts?: (counts: { set: number; keyless: number; unset: number; total: number }) => void;
}) {
  const state = useServices(model);
  const loads = useSyncExternalStore(settingsStore.subscribe, () => settingsStore.getState().loads);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [meta, setMeta] = useState<ServiceMeta | null>(null);
  const inputs = useStateInputs(opened);

  // the tiles follow the side the page is on each time Settings opens, and
  // each load (the cards are other roots: let them draw the load first)
  useEffect(() => {
    const t = setTimeout(() => model.rebuild(), 0);
    return () => clearTimeout(t);
  }, [loads, opened, model]);

  useLayoutEffect(() => {
    // nothing until the first build, as the page had it
    if (!state.version) return;
    setGroups(readGroups(state.kind));
  }, [state.kind, state.version]);

  // Video picked on the music side: the video server, keys and download
  // sections are drawn but only ever loaded when the video side's settings
  // page is shown, so they sat at Loading... Load them here too.
  useEffect(() => {
    if (state.kind === 'video' && document.body.getAttribute('data-side') !== 'video')
      markVideoShown();
  }, [state.kind, opened]);

  const tiles = (groups || []).flatMap((g) => g.tiles);
  const statusOf = (id: string) => serviceStatus(state.kind, id, inputs);
  const all = groups ? tiles.map((t) => statusOf(t.id)) : null;
  const countKey = all
    ? [
        all.filter((x) => x.set && !x.keyless).length,
        all.filter((x) => x.keyless).length,
        all.filter((x) => !x.set && !x.keyless).length,
        all.length,
      ].join(',')
    : '';
  // told only when a number changes (a new object each render would loop)
  useEffect(() => {
    if (!onCounts || !countKey) return;
    const [set, keyless, unset, total] = countKey.split(',').map(Number);
    onCounts({ set, keyless, unset, total });
  }, [countKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // open: the card's name over it (the card moves in by its wrapper)
  useLayoutEffect(() => {
    const open = state.open;
    if (!open) {
      setMeta(null);
      return;
    }
    const frame = frameOf(open);
    if (!frame) {
      model.close();
      return;
    }
    setMeta(serviceMeta(frame));
  }, [state.open, model]);

  function close() {
    model.close();
    // a key typed seconds before closing would sit on the autosave with the
    // sheet gone and nothing saying so: land it now
    const w = window as Window & {
      landPendingSettingsSave?: () => void;
      _settingsAutosaveOff?: boolean;
    };
    w.landPendingSettingsSave?.();
    if (w._settingsAutosaveOff !== true && settingsStore.hasPending()) void settingsStore.flush();
  }

  const open = state.open;
  const openState = open ? serviceStatus(open.kind, open.id, inputs) : null;
  const lastTest = open ? inputs.tests[open.id] : undefined;
  const actions = open ? serviceActions.get(actionKey(open.kind, open.id)) : undefined;

  return (
    <>
      <SectionLabel
        title={state.kind === 'video' ? 'Video services' : 'Services'}
        hint="Accounts and API keys SoulSync connects to"
        action={
          state.version ? (
            <Segmented
              id="svc-tabs"
              label="Which side's services"
              value={state.kind}
              options={(Object.keys(SERVICE_KINDS) as Kind[]).map((k) => ({
                value: k,
                label: SERVICE_KINDS[k].label,
              }))}
              onChange={(k) => model.switchKind(k)}
            />
          ) : null
        }
      />
      {state.kind === 'video' ? (
        <div className="svc-required-note">
          <Note>
            <strong>TMDB and TVDB are required.</strong> The video side matches, enriches and
            automates everything from these (library matching, artwork, airing shows, collections),
            so nothing works until both keys are set. Both are free. OMDb and fanart.tv are optional
            extras.
          </Note>
        </div>
      ) : null}
      <div id="service-tile-grid" data-ui="">
        {groups === null ? (
          state.version ? (
            <p>No services on this side yet.</p>
          ) : null
        ) : (
          groups.map((g) => (
            <div key={g.label} data-svc-group={g.label}>
              <SectionLabel title={g.label} hint={g.hint || undefined} />
              <Tiles>
                {g.tiles.map((t) => {
                  const st = statusOf(t.id);
                  return (
                    <Tile
                      key={t.id}
                      data-service-tile={t.id}
                      mark={<ServiceMark meta={t} />}
                      name={t.name}
                      tone={st.tone}
                      status={st.text}
                      empty={!st.set && !st.keyless}
                      on={state.kind === 'music' && primary(inputs.settings) === t.id}
                      aria-label={`${t.name}: ${st.text}. Settings`}
                      onClick={() => model.open(t.id)}
                    />
                  );
                })}
              </Tiles>
            </div>
          ))
        )}
      </div>
      <Sheet
        open={!!open}
        onClose={close}
        id="service-config-modal"
        title={meta ? meta.name : 'Service'}
        titleId="svc-modal-title"
        sub={<span id="svc-modal-sub">{meta?.detail || ''}</span>}
        mark={<span id="svc-modal-icon">{meta ? <ServiceMark meta={meta} /> : null}</span>}
        status={
          openState ? <StatusWords tone={openState.tone}>{openState.text}</StatusWords> : null
        }
        bodyId="svc-modal-body"
        bodyClass="src-modal-open"
        foot={
          <>
            {actions?.test ? (
              <Btn
                icon="refresh"
                id="svc-modal-test"
                onClick={() => {
                  // the card's own, read now: what is typed is what it tests
                  if (open) void serviceActions.get(actionKey(open.kind, open.id))?.test?.();
                }}
              >
                Test connection
              </Btn>
            ) : null}
            <SheetFootSay>
              <StatusWords id="svc-modal-result" tone={lastTest?.tone} live>
                {lastTest?.text || ''}
              </StatusWords>
            </SheetFootSay>
            {actions?.primary ? (
              <Btn
                kind="primary"
                id={actions.primary.id}
                onClick={() => {
                  if (open)
                    void serviceActions.get(actionKey(open.kind, open.id))?.primary?.onClick();
                }}
              >
                {actions.primary.label}
              </Btn>
            ) : (
              <Btn kind="primary" onClick={close}>
                Done
              </Btn>
            )}
          </>
        }
      />
    </>
  );
}

/** the primary metadata source, so its tile carries the accent */
function primary(settings: SettingsPayload): string {
  const meta = (settings.metadata || {}) as Record<string, unknown>;
  return typeof meta.fallback_source === 'string' ? meta.fallback_source : 'deezer';
}
