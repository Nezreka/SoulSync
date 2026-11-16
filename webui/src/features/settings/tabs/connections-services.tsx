/**
 * Connections > Services (SETTINGS_PORT.md, step 10a): the Music / Video
 * tabs, the tile grid and the modal a tile opens. settings.js
 * buildServiceTiles, switchServiceKind, openServiceModal, closeServiceModal.
 *
 * The forms are the service cards in the hidden API Configuration groups. A
 * tile opens the real card in the modal, never a copy, so every id stays one
 * element. A React card portals into a wrapper of its own and the wrapper is
 * what moves (React hears a portal's events wherever its wrapper sits); a
 * card still vanilla (the video side's) is moved itself and put back in the
 * exact slot it came from.
 *
 * The tiles read the cards as they are on the page, never a copy of the
 * settings: the title, the dot's colour, whether a credential is filled in.
 */

import {
  createContext,
  type CSSProperties,
  type ReactNode,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  useSyncExternalStore,
} from 'react';

import { settingsStore } from '../settings-store';

type Kind = 'music' | 'video';

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

/** the slot a part's cards live in while closed */
export const ServiceHome = createContext<() => HTMLElement | null>(() => null);

function placeHome(home: HTMLElement, wrap: HTMLElement) {
  home.appendChild(wrap);
  // back in page order, whichever came home last
  const wraps = [...home.children].filter((c) => (c as HTMLElement).dataset.svcWrap !== undefined);
  wraps
    .sort(
      (a, b) => Number((a as HTMLElement).dataset.order) - Number((b as HTMLElement).dataset.order),
    )
    .forEach((c) => home.appendChild(c));
}

/**
 * The wrapper a music card portals into, and whether it is the open one.
 * It sits in the card's home slot, or in the modal body while open.
 */
export function useServicePlace(service: string, order: number) {
  const home = useContext(ServiceHome);
  const { open } = useServices();
  const isOpen = open?.kind === 'music' && open.id === service;
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
    else {
      const slot = home();
      if (slot) placeHome(slot, wrap);
    }
  }, [isOpen, home, wrap]);
  useEffect(() => () => wrap.remove(), [wrap]);
  return { wrap, open: isOpen };
}

// ── reading the cards ──

function frames(kind: Kind): HTMLElement[] {
  const attr = SERVICE_KINDS[kind].attr;
  // the Connections tab only: Detail Pages, on the Library tab, is a video
  // preferences panel, not a service
  return [
    ...document.querySelectorAll<HTMLElement>(`#settings-page .api-service-frame[${attr}]`),
  ].filter((f) => f.closest('[data-stg="connections"]'));
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
  const title = frame.querySelector('.service-title')?.textContent?.trim() || '';
  const id = frame.getAttribute('data-service') || frame.getAttribute('data-video-service') || '';
  const logo =
    SERVICE_LOGOS[id] || frame.querySelector('.video-svc-logo')?.getAttribute('src') || null;
  const colour = frame.querySelector<HTMLElement>('.stg-service-dot')?.style.color || '';
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
    required: !!frame.querySelector('.stg-req-pill'),
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

interface Tile extends ServiceMeta {
  set: boolean | null;
}
interface Group {
  label: string;
  hint: string;
  tiles: Tile[];
}

function readGroups(kind: Kind): Group[] | null {
  const all = frames(kind);
  if (!all.length) return null;
  const attr = SERVICE_KINDS[kind].attr;
  const byId = new Map(all.map((f) => [f.getAttribute(attr) || '', f]));
  const tile = (f: HTMLElement): Tile => ({ ...serviceMeta(f), set: serviceConfigured(f) });
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

// ── the marks ──

function Fallback({ meta }: { meta: Pick<ServiceMeta, 'glyph' | 'monogram'> }) {
  return meta.glyph ? (
    <span className="svc-glyph">{meta.glyph}</span>
  ) : (
    <span className="svc-monogram">{meta.monogram}</span>
  );
}

/** the logo, and its fallback if the image does not load */
function Art({ meta }: { meta: ServiceMeta }) {
  const [broken, setBroken] = useState(false);
  if (!meta.logo || broken) return <Fallback meta={meta} />;
  return (
    <img
      className={meta.inverted ? 'svc-mark is-inverted' : 'svc-mark'}
      src={meta.logo}
      alt=""
      onError={() => setBroken(true)}
    />
  );
}

function chip(t: Tile) {
  if (t.required && !t.set)
    return <span className="src-tile-chip src-tile-chip--warn">required</span>;
  if (t.set === null) return <span className="src-tile-chip">no key needed</span>;
  if (t.set) return <span className="src-tile-chip src-tile-chip--on">configured</span>;
  return <span className="src-tile-chip">not set up</span>;
}

// ── the tabs, the grid, the modal ──

/** where an opened vanilla card came from, to put it back exactly there */
let lifted: { frame: HTMLElement; parent: Node | null; next: Node | null } | null = null;

function frameOf(open: { id: string; kind: Kind }) {
  const attr = SERVICE_KINDS[open.kind].attr;
  return document.querySelector<HTMLElement>(
    `#settings-page .api-service-frame[${attr}="${CSS.escape(open.id)}"]`,
  );
}

export function ServicesPart({
  model = servicesModel,
  opened = 0,
}: {
  model?: ServicesModel;
  opened?: number;
}) {
  const state = useServices(model);
  const loads = useSyncExternalStore(settingsStore.subscribe, () => settingsStore.getState().loads);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [meta, setMeta] = useState<ServiceMeta | null>(null);

  // the tiles follow the side the page is on each time Settings opens, and
  // each load (they are only truthful once the cards hold it)
  useEffect(() => {
    // the cards are other roots: let them draw the load first
    const t = setTimeout(() => model.rebuild(), 0);
    return () => clearTimeout(t);
  }, [loads, opened, model]);

  useLayoutEffect(() => {
    // nothing until the first build, as the page had it
    if (!state.version) return;
    setGroups(readGroups(state.kind));
    // the tab owns the whole tab: the matching side's server section with it
    for (const g of document.querySelectorAll<HTMLElement>(
      '#settings-page .settings-group[data-svc-side]',
    ))
      g.classList.toggle('svc-side-active', g.getAttribute('data-svc-side') === state.kind);
  }, [state.kind, state.version]);

  // open: the card's name over it, a vanilla card lifted in by hand
  useLayoutEffect(() => {
    const open = state.open;
    if (!open) {
      setMeta(null);
      return;
    }
    const frame = frameOf(open);
    const body = document.getElementById('svc-modal-body');
    if (!frame || !body) {
      model.close();
      return;
    }
    setMeta(serviceMeta(frame));
    document.body.classList.add('src-modal-open');
    if (!frame.hasAttribute('data-react-panel')) {
      lifted = { frame, parent: frame.parentNode, next: frame.nextSibling };
      body.appendChild(frame);
      // collapsed by default as an accordion; in the modal it is the content
      frame.querySelector<HTMLElement>('.stg-service-body')?.style.setProperty('display', 'block');
    }
    return () => {
      // home before the modal hides, so its ids never sit in a hidden modal
      if (lifted && lifted.frame === frame) {
        frame.querySelector<HTMLElement>('.stg-service-body')?.style.removeProperty('display');
        lifted.parent?.insertBefore(frame, lifted.next);
        lifted = null;
      }
      document.body.classList.remove('src-modal-open');
    };
  }, [state.open, model]);

  useEffect(() => {
    if (!state.open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [state.open]); // eslint-disable-line react-hooks/exhaustive-deps

  function close() {
    model.close();
    // a key typed seconds before closing would sit on the autosave with the
    // modal gone and nothing saying so: land it now
    const w = window as Window & {
      landPendingSettingsSave?: () => void;
      _settingsAutosaveOff?: boolean;
    };
    w.landPendingSettingsSave?.();
    if (w._settingsAutosaveOff !== true && settingsStore.hasPending()) void settingsStore.flush();
  }

  return (
    <>
      <div className="dlchain-tabs" id="svc-tabs" role="tablist" data-stg="connections">
        {(state.version ? (Object.keys(SERVICE_KINDS) as Kind[]) : []).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            className={k === state.kind ? 'dlchain-tab active' : 'dlchain-tab'}
            onClick={() => model.switchKind(k)}
          >
            {SERVICE_KINDS[k].label}
          </button>
        ))}
      </div>
      <div className="svc-required-note" data-stg="connections" data-video-only="">
        <p className="stg-required-note">
          <strong>TMDB &amp; TVDB are required.</strong> The video side matches, enriches and
          automates everything from these — library matching, artwork, airing shows, collections —
          so nothing works until both keys are set. Both are free. OMDb and fanart.tv below are
          optional extras.
        </p>
      </div>
      <div className="src-tile-grid" id="service-tile-grid" data-stg="connections">
        {groups === null ? (
          state.version ? (
            <div className="dlchain-empty">No services on this side yet.</div>
          ) : null
        ) : (
          groups.map((g) => (
            <div className="src-group" key={g.label}>
              <div className="src-group-head">
                <span className="src-group-label">{g.label}</span>
                <span className="src-group-count">{g.tiles.length}</span>
                {g.hint ? <span className="src-group-hint">{g.hint}</span> : null}
              </div>
              <div className="svc-tile-row">
                {g.tiles.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={t.set ? 'svc-tile is-active' : 'svc-tile'}
                    data-service-tile={t.id}
                    onClick={() => model.open(t.id)}
                    style={{ '--svc-brand': t.colour || '#8a8a8a' } as CSSProperties}
                    title={`Configure ${t.name}`}
                  >
                    <span
                      className={`src-tile-dot hss-${t.set === null ? 'na' : t.set ? 'ok' : 'warn'}`}
                    ></span>
                    <span className="svc-tile-art">
                      <Art meta={t} />
                    </span>
                    <span className="svc-tile-name">{t.name}</span>
                    {chip(t)}
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
      <div
        className="src-modal-overlay"
        id="service-config-modal"
        data-stg="connections"
        hidden={!state.open}
        onClick={(event) => {
          if (event.target === event.currentTarget) close();
        }}
      >
        <div
          className="src-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="svc-modal-title"
        >
          <div className="src-modal-header">
            <span className="src-modal-icon" id="svc-modal-icon">
              {meta ? (
                meta.logo ? (
                  <img
                    className={meta.inverted ? 'svc-mark is-inverted' : 'svc-mark'}
                    src={meta.logo}
                    alt=""
                  />
                ) : (
                  <Fallback meta={meta} />
                )
              ) : null}
            </span>
            <span className="src-modal-titles">
              <h3 id="svc-modal-title">{meta ? meta.name : 'Service'}</h3>
              <span className="src-modal-sub" id="svc-modal-sub">
                {meta?.detail || ''}
              </span>
            </span>
            <button type="button" className="src-modal-close" onClick={close} aria-label="Close">
              ×
            </button>
          </div>
          {/* the open card is placed in here: React renders no children */}
          <div className="src-modal-body" id="svc-modal-body" />
        </div>
      </div>
    </>
  );
}

/** a part whose cards portal themselves: the slot is their home */
export function CardsHome({ part, children }: { part: string; children: ReactNode }) {
  const [home] = useState(
    () => () =>
      document.querySelector<HTMLElement>(
        `[data-stg-react="connections"][data-stg-part="${part}"]`,
      ),
  );
  return <ServiceHome.Provider value={home}>{children}</ServiceHome.Provider>;
}
