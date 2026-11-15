/**
 * The download chain (Downloads > Source Settings) and the Sources tab's
 * tiles, ported from settings.js (SETTINGS_PORT.md, step 7a). Same markup,
 * classes and ids. Both read the one sources model (sources-model.ts).
 *
 * The Sources tab's config panels and the modal a tile opens are still
 * vanilla until step 7b: a tile calls window.openSourceModal.
 */

import {
  type DragEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { SettingsTabProps } from '../host';

import { settingsStore } from '../settings-store';
import { SourcePanels } from './source-panels';
import { MORE_SOURCE_PARTS, MoreSourcePanels } from './source-panels-more';
import {
  CHAIN_KINDS,
  type ChainKind,
  DLCHAIN_GROUPS,
  EXTRA_SOURCE_TILES,
  HYBRID_SOURCES,
  INVERT_BRAND_MARKS,
  SOURCE_CONFIG_ID_BY_SRC,
  type SourceMeta,
  type SourcesModel,
  chainUses,
  sourcesModel,
} from './sources-model';

function useModel(model: SourcesModel) {
  return useSyncExternalStore(model.subscribe, model.getState);
}

/** a brand mark, or its emoji when the image fails, as the old onerror did */
function Mark({ meta, className }: { meta: SourceMeta; className?: string }) {
  const [broken, setBroken] = useState(false);
  if (!meta.icon || broken) return <span className="emoji-icon">{meta.emoji}</span>;
  return <img className={className} src={meta.icon} alt="" onError={() => setBroken(true)} />;
}

const markClass = (id: string, base?: string) =>
  [base, INVERT_BRAND_MARKS.has(id) ? 'is-inverted' : ''].filter(Boolean).join(' ') || undefined;

// ── the chain ──

export function ChainWidget({
  model = sourcesModel,
  opened,
  active,
  loads,
}: {
  model?: SourcesModel;
  opened: number;
  active: boolean;
  loads: number;
}) {
  const state = useModel(model);
  const [query, setQuery] = useState('');
  const [dragging, setDragging] = useState<string | null>(null);
  // a step or the end slot under the pointer; the list and the pool light up
  // on their own as well (the old listeners did, the event bubbling up)
  const [over, setOver] = useState<string | null>(null);
  const [listOver, setListOver] = useState(false);
  const [poolOver, setPoolOver] = useState(false);

  // each settings load re-reads the open chain (the audiobook chain is read
  // from the Library tab's state)
  useEffect(() => {
    if (loads) void model.loadChain();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  // opening Downloads: the dots fill once, and the video chain (video.db can
  // change from the video side) is read again
  useEffect(() => {
    if (!active || !opened) return;
    model.autoTestOnce();
    void model.loadChain();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const kind = state.kind;
  const spec = CHAIN_KINDS[kind];
  const all = spec.sources();
  const order = state.order.filter((id) => all.includes(id));
  const available = all.filter((id) => !order.includes(id));
  const full = state.order;

  const commit = (next: string[]) => model.commit(next);
  const move = (id: string, delta: number) => {
    const next = full.slice();
    const i = next.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    commit(next);
  };
  const add = (id: string) => commit(full.includes(id) ? full.slice() : [...full, id]);
  const remove = (id: string) => {
    // an empty chain means nothing can download: all three refuse it
    if (full.length <= 1) {
      window.showToast?.('Keep at least one source — downloads need somewhere to go', 'info');
      return;
    }
    commit(full.filter((x) => x !== id));
  };

  const dragged = (event: DragEvent) => event.dataTransfer?.getData('text/plain') || dragging;
  const startDrag = (id: string) => (event: DragEvent) => {
    setDragging(id);
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', id);
    }
  };
  const endDrag = () => {
    setDragging(null);
    setOver(null);
    setListOver(false);
    setPoolOver(false);
  };
  const overOn = (key: string) => (event: DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    setOver(key);
  };
  const leave = (key: string) => () => setOver((o) => (o === key ? null : o));

  const q = query.trim().toLowerCase();
  const hit = (id: string) => {
    const name = spec.meta(id).name.toLowerCase();
    return !q || name.includes(q) || id.toLowerCase().includes(q);
  };

  const tile = (id: string) => {
    const meta = spec.meta(id);
    return (
      <button
        key={id}
        type="button"
        className={dragging === id ? 'dlchain-tile dragging' : 'dlchain-tile'}
        draggable
        data-src={id}
        hidden={!hit(id)}
        title={`Add ${meta.name} to the chain`}
        onClick={() => add(id)}
        onDragStart={startDrag(id)}
        onDragEnd={endDrag}
      >
        <span className="dlchain-tile-art">
          <Mark meta={meta} className={markClass(id, 'dlchain-mark')} />
        </span>
        <span className="dlchain-tile-name">{meta.name}</span>
      </button>
    );
  };

  const groups: Array<{ label: string; ids: string[] }> = [];
  const seen = new Set<string>();
  for (const g of DLCHAIN_GROUPS) {
    const ids = g.ids.filter((id) => available.includes(id));
    ids.forEach((id) => seen.add(id));
    if (ids.length) groups.push({ label: g.label, ids });
  }
  const other = available.filter((id) => !seen.has(id));
  if (other.length) groups.push({ label: 'Other', ids: other });
  const shown = available.filter(hit).length;

  const step = (id: string, position: number, total: number) => {
    const meta = spec.meta(id);
    const role = position === 1 ? 'Tried first' : `Fallback ${position - 1}`;
    const key = `step:${id}`;
    return (
      <div
        key={id}
        className={[
          'dlchain-step',
          dragging === id ? 'dragging' : '',
          over === key ? 'drag-over' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        draggable
        data-src={id}
        title={`${meta.name} - ${role}`}
        aria-label={`${meta.name}, ${role}`}
        onDragStart={startDrag(id)}
        onDragEnd={endDrag}
        onDragOver={overOn(key)}
        onDragLeave={leave(key)}
        onDrop={(event) => {
          // dropping on a step puts the dragged source before it
          event.preventDefault();
          event.stopPropagation();
          setOver(null);
          const src = dragged(event);
          if (!src || src === id) return;
          const next = full.filter((x) => x !== src);
          const at = next.indexOf(id);
          next.splice(at < 0 ? next.length : at, 0, src);
          commit(next);
        }}
      >
        <span className="dlchain-grip" aria-hidden="true">
          &#x283F;
        </span>
        <span className="dlchain-step-rank">{position}</span>
        <span className="dlchain-step-art">
          <Mark meta={meta} className={markClass(id, 'dlchain-mark')} />
        </span>
        <span className="dlchain-step-text">
          <span className="dlchain-step-name">{meta.name}</span>
          <span className="dlchain-step-role">{role}</span>
        </span>
        <span className="dlchain-step-move">
          <button
            type="button"
            className="dlchain-move"
            title={`Move ${meta.name} up`}
            aria-label={`Move ${meta.name} up`}
            disabled={position === 1}
            onClick={() => move(id, -1)}
          >
            &#9650;
          </button>
          <button
            type="button"
            className="dlchain-move"
            title={`Move ${meta.name} down`}
            aria-label={`Move ${meta.name} down`}
            disabled={position === total}
            onClick={() => move(id, 1)}
          >
            &#9660;
          </button>
        </span>
        <button
          type="button"
          className="dlchain-btn"
          title={`Remove ${meta.name} from the chain`}
          aria-label={`Remove ${meta.name} from the chain`}
          onClick={() => remove(id)}
        >
          &times;
        </button>
      </div>
    );
  };

  const lastName = order.length ? spec.meta(order[order.length - 1]).name : '';
  const flow: ReactNode[] = [];
  order.forEach((id, i) => {
    if (i > 0) flow.push(<div key={`c${i}`} className="dlchain-connector" />);
    flow.push(step(id, i + 1, order.length));
  });
  if (order.length) flow.push(<div key="c-end" className="dlchain-connector" />);

  return (
    <>
      <div className="settings-section-header settings-section-static" data-stg="downloads">
        <h3>Source Settings</h3>
        <span className="settings-section-hint">Source, stream &amp; concurrency</span>
      </div>
      <div className="settings-section-body" data-stg="downloads">
        <div className="settings-group source-settings-wrapper" data-stg="downloads">
          <div className="settings-subcard">
            <div id="download-chain-widget">
              <div className="dlchain-tabs" id="dlchain-tabs" role="tablist">
                {(Object.keys(CHAIN_KINDS) as ChainKind[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    role="tab"
                    className={k === kind ? 'dlchain-tab active' : 'dlchain-tab'}
                    onClick={() => model.switchChain(k)}
                  >
                    {CHAIN_KINDS[k].label}
                  </button>
                ))}
              </div>
              <div className="setting-help-text" id="dlchain-help">
                {spec.help}
              </div>
              <div className="dlchain-columns">
                <div className="dlchain-col">
                  <div className="dlchain-col-head">
                    <span className="dlchain-col-label">Available</span>
                    <span className="dlchain-col-hint">drag one across, or click it</span>
                    <input
                      type="search"
                      id="dlchain-filter"
                      className="dlchain-filter"
                      placeholder="Filter"
                      aria-label="Filter available sources"
                      autoComplete="off"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </div>
                  <div
                    className={poolOver ? 'dlchain-pool drag-over' : 'dlchain-pool'}
                    id="dlchain-pool"
                    onDragOver={(event) => {
                      event.preventDefault();
                      setPoolOver(true);
                    }}
                    onDragLeave={() => setPoolOver(false)}
                    onDrop={(event) => {
                      // dragging a step back to the pool removes it
                      event.preventDefault();
                      setPoolOver(false);
                      const src = dragged(event);
                      if (src && full.includes(src)) remove(src);
                    }}
                  >
                    {!available.length ? (
                      <div className="dlchain-empty">Every source is in the chain.</div>
                    ) : (
                      <>
                        {groups.map((g) => (
                          <div key={g.label} className="dlchain-group" hidden={!g.ids.some(hit)}>
                            <div className="dlchain-group-head">
                              <span className="dlchain-group-label">{g.label}</span>
                              <span className="dlchain-group-count">{g.ids.length}</span>
                            </div>
                            <div className="dlchain-group-row">{g.ids.map(tile)}</div>
                          </div>
                        ))}
                        <div
                          className="dlchain-empty"
                          id="dlchain-no-match"
                          hidden={!(q && shown === 0)}
                        >
                          Nothing matches that.
                        </div>
                      </>
                    )}
                  </div>
                </div>
                <div className="dlchain-col">
                  <div className="dlchain-col-head">
                    <span className="dlchain-col-label">Download chain</span>
                    <span className="dlchain-col-hint" id="dlchain-mode-hint">
                      {order.length > 1
                        ? `hybrid — ${order.length} sources`
                        : order.length === 1
                          ? 'single source'
                          : 'nothing selected'}
                    </span>
                  </div>
                  <div
                    className={listOver ? 'dlchain-list drag-over' : 'dlchain-list'}
                    id="dlchain-list"
                    onDragOver={(event) => {
                      event.preventDefault();
                      setListOver(true);
                    }}
                    onDragLeave={() => setListOver(false)}
                    onDrop={(event) => {
                      // anywhere else in the chain column appends
                      event.preventDefault();
                      setListOver(false);
                      const src = dragged(event);
                      if (!src || full.includes(src)) return;
                      commit([...full, src]);
                    }}
                  >
                    {flow}
                    <div
                      className={[
                        'dlchain-slot',
                        order.length ? '' : 'first',
                        over === 'slot' ? 'drag-over' : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      id="dlchain-slot"
                      onDragOver={overOn('slot')}
                      onDragLeave={leave('slot')}
                      onDrop={(event) => {
                        // the slot at the end always appends
                        event.preventDefault();
                        event.stopPropagation();
                        setOver(null);
                        const src = dragged(event);
                        if (!src) return;
                        commit([...full.filter((x) => x !== src), src]);
                      }}
                    >
                      <span className="dlchain-slot-title">
                        {order.length
                          ? `Drag a source here — tried after ${lastName}`
                          : 'Drag a source here — this one is tried first'}
                      </span>
                      <span className="dlchain-slot-hint">
                        {order.length
                          ? 'It only runs if everything above it came up empty.'
                          : 'Downloads need at least one source. Add more to fall back in order.'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ── the Sources tab's tiles ──

const DOT_LABEL: Record<string, string> = {
  unknown: 'Not tested yet',
  testing: 'Testing…',
  ok: 'Connected',
  na: 'No connection test for this source',
};

export function SourceTiles({
  model = sourcesModel,
  opened,
  active,
}: {
  model?: SourcesModel;
  opened: number;
  active: boolean;
}) {
  const state = useModel(model);

  // arriving on Sources: the video chain is read again and the dots fill once
  useEffect(() => {
    if (!active || !opened) return;
    void model.loadVideoChain();
    model.autoTestOnce();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const chains = model.chains();
  const configurable = HYBRID_SOURCES.concat(EXTRA_SOURCE_TILES).filter(
    (src) => SOURCE_CONFIG_ID_BY_SRC[src.id],
  );
  const isIndexer = (src: SourceMeta) => EXTRA_SOURCE_TILES.some((x) => x.id === src.id);
  const indexers = configurable.filter(isIndexer);
  const sources = configurable.filter((src) => !isIndexer(src));
  // in use in ANY chain (#2026): music first, then video, then audiobooks
  const rank = (src: SourceMeta) => {
    const u = chainUses(src.id, chains)[0];
    return u ? chains.findIndex((c) => c.label === u.label) * 1000 + u.pos : Infinity;
  };
  const inChain = sources
    .filter((src) => chainUses(src.id, chains).length)
    .sort((a, b) => rank(a) - rank(b));
  const available = sources.filter((src) => !chainUses(src.id, chains).length);

  const tile = (src: SourceMeta) => {
    const uses = chainUses(src.id, chains);
    const used = uses.length > 0;
    const dot = state.status[src.id] || 'unknown';
    const unready = state.unready[src.id];
    const said = state.said[src.id];
    const dotLabel =
      dot === 'warn'
        ? said || 'Working, but needs attention'
        : dot === 'fail'
          ? said || 'Connection failed'
          : DOT_LABEL[dot];
    let chip: ReactNode;
    if (src.id === 'prowlarr') {
      // Prowlarr is what torrent and usenet search through, never in a chain
      chip = <span className="src-tile-chip">torrent &amp; usenet</span>;
    } else if (unready || dot === 'warn') {
      chip = <span className="src-tile-chip src-tile-chip--warn">needs setup</span>;
    } else if (used) {
      chip = (
        <span className="src-tile-chips">
          {uses.map((u) => (
            <span
              key={u.label}
              className="src-tile-chip src-tile-chip--on"
              title={`${u.label} chain, tried ${u.pos === 1 ? 'first' : '#' + u.pos}`}
            >
              {`${u.label} #${u.pos}`}
            </span>
          ))}
        </span>
      );
    } else {
      chip = <span className="src-tile-chip">not in a chain</span>;
    }
    return (
      <button
        key={src.id}
        type="button"
        className={used ? 'src-tile is-active' : 'src-tile'}
        data-source-id={src.id}
        title={`Configure ${src.name}`}
        onClick={() => model.openModal(src.id)}
      >
        <span className={`src-tile-dot hss-${dot}`} title={String(dotLabel)} />
        <span className="src-tile-art">
          <Mark meta={src} className={markClass(src.id)} />
        </span>
        <span className="src-tile-name">{src.name}</span>
        {chip}
      </button>
    );
  };

  const section = (label: string, hint: string, list: SourceMeta[]) =>
    list.length ? (
      <div className="src-group" key={label}>
        <div className="src-group-head">
          <span className="src-group-label">{label}</span>
          <span className="src-group-count">{list.length}</span>
          <span className="src-group-hint">{hint}</span>
        </div>
        <div className="src-tile-row">{list.map(tile)}</div>
      </div>
    ) : null;

  return (
    <div className="src-tile-grid" id="source-tile-grid">
      {section('In use', 'the chains each source is in, and where it is tried', inChain)}
      {section('Available', 'configure now, add to the chain when you want it', available)}
      {section('Indexers', 'searched by torrent and usenet', indexers)}
    </div>
  );
}

// ── the modal a tile opens ──

/** what each source's modal says under its name */
export const SOURCE_CARD_HINTS: Record<string, string> = {
  soulseek: 'slskd connection, timeouts, peers',
  youtube: 'Cookies, transcoding, rate limit',
  tidal: 'Account and stream quality',
  qobuz: 'Account and stream quality',
  hifi: 'Public API instances',
  deezer_dl: 'ARL token',
  soundcloud: 'Anonymous — nothing to set up',
  lidarr: 'URL and API key',
  torrent: 'Runs on Prowlarr plus a torrent client',
  usenet: 'Runs on Prowlarr plus a usenet client',
  prowlarr: 'Prowlarr — searched by both torrent and usenet',
};

type ShellFns = Window & {
  probeSourceOnOpen?: (id: string) => void;
  landPendingSettingsSave?: () => void;
  _settingsAutosaveOff?: boolean;
};

/**
 * One source's settings, over the page. The panels are still vanilla markup
 * parked in #source-config-home (until each is ported): the open one is MOVED
 * into the modal body and back on close, never copied, so every id stays one
 * element and the page save keeps reading the field the person typed in.
 */
export function SourceModal({ model = sourcesModel }: { model?: SourcesModel }) {
  const state = useModel(model);
  const id = state.open;
  const body = useRef<HTMLDivElement>(null);
  const [result, setResult] = useState<{ cls: string; text: string }>({
    cls: 'src-modal-result',
    text: '',
  });
  const [testing, setTesting] = useState(false);
  const [broken, setBroken] = useState(false);

  // open: the panel moves in, what is known is shown, the source is probed
  useEffect(() => {
    if (!id) return;
    const panel = document.getElementById(SOURCE_CONFIG_ID_BY_SRC[id] || '');
    if (!panel || !body.current) {
      model.closeModal();
      return;
    }
    const w = window as ShellFns;
    // (YouTube's panel reads its yt-dlp version itself when it opens)
    const known = model.getState();
    const dot = known.status[id] || 'unknown';
    const said = known.said[id];
    // an amber dot on the tile still explains itself once inside
    setResult(
      said && dot !== 'unknown'
        ? { cls: `src-modal-result is-${dot}`, text: said }
        : { cls: 'src-modal-result', text: '' },
    );
    setTesting(false);
    setBroken(false);
    // a panel ported to React portals itself into the body (source-panels.tsx)
    const vanilla = !panel.hasAttribute('data-react-panel');
    if (vanilla) body.current.appendChild(panel);
    document.body.classList.add('src-modal-open');
    try {
      w.probeSourceOnOpen?.(id);
    } catch {
      // a probe that throws must not stop the modal
    }
    const focus = setTimeout(() => {
      body.current
        ?.querySelector<HTMLElement>('input:not([type=hidden]), select, textarea')
        ?.focus();
    }, 40);
    return () => {
      clearTimeout(focus);
      // back home before the modal hides, so a later open never moves a
      // node that is still sitting in a hidden modal
      if (vanilla) document.getElementById('source-config-home')?.appendChild(panel);
      document.body.classList.remove('src-modal-open');
    };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!id) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  function close() {
    model.closeModal();
    // a field edited seconds before closing would sit on the 2 s autosave
    // with the modal gone and nothing saying so: land it now
    const w = window as ShellFns;
    w.landPendingSettingsSave?.();
    if (w._settingsAutosaveOff !== true && settingsStore.hasPending()) void settingsStore.flush();
  }

  async function test() {
    if (!id) return;
    if (!model.canTest(id)) {
      setResult({ cls: 'src-modal-result is-na', text: 'This source has no connection test.' });
      return;
    }
    setTesting(true);
    setResult({ cls: 'src-modal-result is-testing', text: 'Testing…' });
    const dot = (await model.testOne(id)) || 'fail';
    const good = dot === 'ok' || dot === 'warn';
    const said = model.getState().said[id];
    setResult({
      cls: `src-modal-result is-${dot}`,
      text: said || (good ? 'Connected.' : 'Connection failed.'),
    });
    setTesting(false);
  }

  // a source not in the registry (Prowlarr) is named by its id, as before
  const src = (id && HYBRID_SOURCES.find((x) => x.id === id)) || {
    id: id || '',
    name: id || 'Source',
    emoji: '🎵',
    icon: null,
  };
  const dot = (id && state.status[id]) || 'unknown';

  return (
    <div
      id="source-config-modal"
      className="src-modal-overlay"
      hidden={!id}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="src-modal" role="dialog" aria-modal="true" aria-labelledby="src-modal-title">
        <div className="src-modal-header">
          <span className="src-modal-icon" id="src-modal-icon">
            {id ? (
              src.icon && !broken ? (
                <img src={src.icon} alt="" onError={() => setBroken(true)} />
              ) : (
                <span className="emoji-icon">{src.emoji}</span>
              )
            ) : null}
          </span>
          <div className="src-modal-titles">
            <h3 id="src-modal-title">{id ? src.name : 'Source'}</h3>
            <span className="src-modal-sub" id="src-modal-sub">
              {id ? SOURCE_CARD_HINTS[id] || '' : ''}
            </span>
          </div>
          <span className="src-modal-meta" id="src-modal-meta">
            {id ? <span className={`src-tile-dot hss-${dot}`} /> : null}
          </span>
          <button
            type="button"
            className="src-modal-test"
            id="src-modal-test"
            disabled={testing}
            style={id && !model.canTest(id) ? { display: 'none' } : undefined}
            onClick={() => void test()}
          >
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="button" className="src-modal-close" aria-label="Close" onClick={close}>
            &times;
          </button>
        </div>
        <div className={result.cls} id="src-modal-result" role="status" aria-live="polite">
          {result.text}
        </div>
        {/* the open panel is moved in here by hand: React renders no children */}
        <div className="src-modal-body" id="src-modal-body" ref={body} />
      </div>
    </div>
  );
}

/** the Sources tab: the tiles and the modal (its panels are still vanilla) */
export function SourcesTab(props: SettingsTabProps) {
  if (props.part === 'modal') return <SourceModal />;
  if (props.part && MORE_SOURCE_PARTS.includes(props.part)) return <MoreSourcePanels {...props} />;
  if (props.part !== 'tiles') return <SourcePanels {...props} />;
  return <SourceTiles opened={props.opened} active={props.active} />;
}
