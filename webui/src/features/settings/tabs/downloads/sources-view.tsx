/**
 * Downloads > Sources (settings-bic DESIGN.md section 5): where downloads come
 * from and in what order. One list per kind (music, video, audiobooks): the
 * order itself, the sources not used under it, the indexers, and each source's
 * settings in the side sheet.
 *
 * It draws the sources model (sources-model.ts), which is unchanged: a source
 * is on by being in the order, music and audiobooks save with the page, video
 * saves at once.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';
import type { SettingsStore } from '../../settings-store';

import {
  Btn,
  Card,
  Hero,
  HeroWarn,
  KindBar,
  KindSay,
  Mark,
  More,
  Note,
  type OrderChange,
  type OrderItem,
  OrderList,
  type PoolItem,
  Row,
  SectionLabel,
  Segmented,
  Sheet,
  SheetFootSay,
  StatusWords,
  type Tone,
} from '../../ui';
import { AudiobookDownloadOptions } from '../library-audiobooks';
import { SourcePanels } from '../source-panels';
import { MoreSourcePanels } from '../source-panels-more';
import {
  CHAIN_KINDS,
  type ChainKind,
  DLCHAIN_GROUPS,
  SOURCE_CONFIG_ID_BY_SRC,
  type SourcesModel,
  chainUses,
  sourcesModel,
} from '../sources-model';
import {
  KIND_WORD,
  changeText,
  dropText,
  heroSentence,
  heroText,
  lookOf,
  roleText,
  statusOf,
  usedIn,
} from './words';

type ShellFns = Window & {
  probeSourceOnOpen?: (id: string) => void;
  landPendingSettingsSave?: () => void;
  _settingsAutosaveOff?: boolean;
  showToast?: (
    message: string,
    type?: string,
    context?: string | number | null,
    opts?: { action?: { label: string; run: () => void } },
  ) => void;
};
const shell = () => window as unknown as ShellFns;

function useModel(model: SourcesModel) {
  return useSyncExternalStore(model.subscribe, model.getState);
}

export function SourceMark({ id, size }: { id: string; size?: 'sm' | 'lg' }) {
  const look = lookOf(id);
  return (
    <Mark
      src={look.logo}
      pad={look.pad}
      invert={look.invert}
      icon={look.icon}
      brand={look.brand}
      size={size}
    />
  );
}

const KIND_OPTIONS = (Object.keys(CHAIN_KINDS) as ChainKind[]).map((kind) => ({
  value: kind,
  label: CHAIN_KINDS[kind].label,
}));

const ORDER_HINT: Record<ChainKind, string> = {
  music: 'Tried from the top. The next one only runs when the one above finds nothing.',
  video:
    'Tried from the top. The next one only runs when the one above finds nothing. This order saves at once.',
  audiobooks:
    'Asked from the top. A book is fetched as a whole folder, so this decides who is asked first.',
};

/** how many sources in the music order the server will skip until they are set up */
export function useSourcesAttention(model: SourcesModel = sourcesModel): number {
  const count = () => {
    const state = model.getState();
    return state.music.filter((id) => state.unready[id]).length;
  };
  return useSyncExternalStore(model.subscribe, count, () => 0);
}

/** the server's fresh word on which sources are set up, after a sheet closes */
async function refreshReadiness(store: SettingsStore, model: SourcesModel) {
  try {
    const fresh = (await (await fetch('/api/settings')).json()) as Record<string, unknown> | null;
    if (!fresh || typeof fresh !== 'object' || fresh.error || !fresh._source_status) return;
    model.load({
      ...store.getState().settings,
      _source_status: fresh._source_status as Record<string, unknown>,
    });
  } catch {
    // the next settings load says it
  }
}

// ── the sheet ──

export function SourceSheet({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const state = useModel(model);
  // where a source is used follows the settings too (the audiobook order)
  useSyncExternalStore(store.subscribe, () => store.getState().settings);
  const id = state.open;
  const [testing, setTesting] = useState(false);
  const [answer, setAnswer] = useState<{ tone: Tone; text: string }>({ tone: 'idle', text: '' });

  // open: what is known is shown, and the source is probed (Qobuz's sign-in)
  useEffect(() => {
    if (!id) return;
    if (!document.getElementById(SOURCE_CONFIG_ID_BY_SRC[id] || '')) {
      model.closeModal();
      return;
    }
    const known = model.getState();
    const dot = known.status[id] || 'unknown';
    setAnswer(
      known.said[id] && dot !== 'unknown' ? statusOf(id, known) : { tone: 'idle', text: '' },
    );
    setTesting(false);
    try {
      shell().probeSourceOnOpen?.(id);
    } catch {
      // a probe that throws must not stop the sheet
    }
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  /** an edit made seconds ago would sit on the 2 s autosave: land it now */
  async function land() {
    shell().landPendingSettingsSave?.();
    if (shell()._settingsAutosaveOff !== true && store.hasPending()) await store.flush();
  }

  function close() {
    model.closeModal();
    // then ask the server again whether it is set up, so "Needs setup" goes
    // without a reload
    void land().then(() => refreshReadiness(store, model));
  }

  async function test() {
    if (!id) return;
    if (!model.canTest(id)) {
      setAnswer({ tone: 'idle', text: 'This source has no connection test.' });
      return;
    }
    setTesting(true);
    // what is typed is what gets tested
    await land();
    const dot = (await model.testOne(id)) || 'fail';
    const said = statusOf(id, model.getState());
    const good = dot === 'ok' || dot === 'warn';
    setAnswer({
      tone: said.tone,
      text: model.getState().said[id] ? said.text : good ? 'Connected.' : 'Connection failed.',
    });
    setTesting(false);
  }

  const uses = id ? chainUses(id, model.chains()) : [];
  const sub = !id
    ? ''
    : id === 'prowlarr'
      ? 'Searched by Torrent and Usenet'
      : uses.length
        ? usedIn(uses).replaceAll(', ', ' · ')
        : 'Not in an order yet';
  const status = id ? statusOf(id, state, { inOrder: state.music.includes(id) }) : null;

  return (
    <Sheet
      open={!!id}
      onClose={close}
      id="source-config-modal"
      title={id ? lookOf(id).name : 'Source'}
      titleId="src-modal-title"
      sub={<span id="src-modal-sub">{sub}</span>}
      mark={
        <span id="src-modal-icon" data-src={id || undefined}>
          {id ? <SourceMark id={id} /> : null}
        </span>
      }
      status={
        status ? (
          <span id="src-modal-meta">
            <StatusWords tone={status.tone}>{status.text}</StatusWords>
          </span>
        ) : null
      }
      bodyId="src-modal-body"
      bodyClass="src-modal-open"
      foot={
        <>
          <Btn
            id="src-modal-test"
            icon="refresh"
            busy={testing}
            hidden={!!id && !model.canTest(id)}
            onClick={() => void test()}
          >
            {testing ? 'Testing…' : 'Test connection'}
          </Btn>
          <SheetFootSay>
            <StatusWords id="src-modal-result" tone={testing ? 'busy' : answer.tone} live>
              {testing ? 'Testing…' : answer.text}
            </StatusWords>
          </SheetFootSay>
          <Btn kind="primary" onClick={close}>
            Done
          </Btn>
        </>
      }
    />
  );
}

/** every source's settings panel, parked hidden until its sheet opens (the
 *  open one moves itself into the sheet's body, source-panels.tsx Panel) */
export function SourcePanelsHome(props: SettingsTabProps) {
  return (
    <div id="source-config-home" hidden>
      <SourcePanels {...props} part="soulseek" />
      <SourcePanels {...props} part="tidal-qobuz" />
      <SourcePanels {...props} part="hifi" />
      <SourcePanels {...props} part="deezer-soundcloud-lidarr" />
      <MoreSourcePanels {...props} part="youtube" />
      <MoreSourcePanels {...props} part="prowlarr-torrent-usenet" />
    </div>
  );
}

// ── the view ──

export function SourcesView({
  store,
  opened,
  active,
  model = sourcesModel,
}: SettingsTabProps & { model?: SourcesModel }) {
  const state = useModel(model);
  const settings = useSyncExternalStore(store.subscribe, () => store.getState().settings);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);

  // each settings load re-reads the open order (the audiobook order is a
  // settings key)
  useEffect(() => {
    if (loads) void model.loadChain();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  // arriving: the statuses fill once, and the video order (video.db can
  // change from the video side) is read again
  useEffect(() => {
    if (!active || !opened) return;
    model.autoTestOnce();
    void model.loadChain();
    void model.loadVideoChain();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const kind = state.kind;
  const spec = CHAIN_KINDS[kind];
  const all = spec.sources();
  const full = state.order;
  const order = full.filter((id) => all.includes(id));
  // an id this page does not know stays in the saved order, after the known ones
  const unknown = full.filter((id) => !all.includes(id));
  // grouped as the kinds of source are: peer-to-peer, streaming, video, indexers
  const grouped = [...DLCHAIN_GROUPS.flatMap((group) => group.ids), ...all];
  const unused = [...new Set(grouped)].filter((id) => all.includes(id) && !order.includes(id));
  const unready = kind === 'music' ? state.unready : {};
  const chains = model.chains();
  const hasPanel = (id: string) => !!SOURCE_CONFIG_ID_BY_SRC[id];

  function change(next: string[], what: OrderChange) {
    const previous = full.slice();
    model.commit([...next, ...unknown]);
    shell().showToast?.(changeText(kind, what, next), 'success', null, {
      action: {
        label: 'Undo',
        // the same save, with the order it had
        run: () => {
          if (model.getState().kind !== kind) model.switchChain(kind);
          model.commit(previous);
        },
      },
    });
  }

  const settingsButton = (id: string): ReactNode => {
    if (!hasPanel(id)) return null;
    return unready[id] ? (
      <Btn
        kind="primary"
        small
        data-open-source={id}
        aria-label={`Set up ${lookOf(id).name}`}
        onClick={() => model.openModal(id)}
      >
        Set up
      </Btn>
    ) : (
      <Btn
        kind="ghost"
        small
        icon="gear"
        data-open-source={id}
        aria-label={`${lookOf(id).name} settings`}
        onClick={() => model.openModal(id)}
      >
        Settings
      </Btn>
    );
  };

  const items: OrderItem[] = order.map((id) => ({
    id,
    name: lookOf(id).name,
    mark: <SourceMark id={id} />,
    status: statusOf(id, { ...state, unready }, { inOrder: true }),
    warn: !!unready[id],
    action: settingsButton(id),
    menu: [
      ...(hasPanel(id)
        ? [
            {
              key: 'settings',
              label: unready[id] ? 'Set up' : 'Settings',
              icon: 'gear' as const,
              onSelect: () => model.openModal(id),
            },
          ]
        : []),
      ...(model.canTest(id)
        ? [
            {
              key: 'test',
              label: 'Test',
              icon: 'refresh' as const,
              onSelect: () => void model.testOne(id),
            },
          ]
        : []),
    ],
  }));

  const pool: PoolItem[] = unused.map((id) => {
    const look = lookOf(id);
    const elsewhere = chainUses(
      id,
      chains.filter((chain) => chain.label !== spec.label),
    );
    return {
      id,
      name: look.name,
      mark: <SourceMark id={id} size="sm" />,
      line: `${look.group} · ${elsewhere.length ? usedIn(elsewhere) : statusOf(id, state).text}`,
      action: hasPanel(id) ? (
        <Btn
          kind="ghost"
          small
          iconOnly
          icon="gear"
          data-open-source={id}
          aria-label={`${look.name} settings`}
          onClick={() => model.openModal(id)}
        />
      ) : null,
    };
  });

  const connected = order.filter((id) => ['ok', 'warn'].includes(state.status[id] || '')).length;
  const word = KIND_WORD[kind];
  const savedUrl = settings.prowlarr?.url;
  const prowlarrUrl = typeof savedUrl === 'string' ? savedUrl.trim() : '';
  const prowlarr = statusOf('prowlarr', state);

  return (
    <>
      <Hero
        stats={[
          { value: order.length, label: 'In use' },
          { value: connected, label: 'Connected' },
          { value: unused.length, label: 'Not used' },
        ]}
      >
        {heroSentence(kind, order, unready).map((part, index) =>
          part.kind === 'name' ? (
            // eslint-disable-next-line react/no-array-index-key
            <em key={index}>{part.text}</em>
          ) : part.kind === 'warn' ? (
            // eslint-disable-next-line react/no-array-index-key
            <HeroWarn key={index}>{part.text}</HeroWarn>
          ) : (
            part.text
          ),
        )}
      </Hero>

      <div id="download-chain-widget" data-kind={kind}>
        <KindBar>
          <Segmented
            id="dlchain-tabs"
            label="Which downloads this order is for"
            value={kind}
            options={KIND_OPTIONS}
            panel="dlchain-panel"
            onChange={(next) => model.switchChain(next)}
          />
          <KindSay>Each has its own order. Accounts and clients are shared.</KindSay>
          <Btn
            id="test-all-sources-btn"
            icon="refresh"
            busy={state.testingAll}
            title="Checks every source in use and every configured connection"
            onClick={() => void model.testAll()}
          >
            {state.testingAll ? 'Testing…' : 'Test all sources'}
          </Btn>
        </KindBar>

        <div id="dlchain-panel" role="tabpanel" aria-label={`${spec.label} download order`}>
          <SectionLabel title="Download order" hint={ORDER_HINT[kind]} />
          <OrderList
            label={`Download order for ${word}`}
            items={items}
            role={(id, index, live) => roleText(kind, id, index, live, unready)}
            dropText={(id, index) => dropText(kind, id, index)}
            afterText={(live) => heroText(kind, live)}
            onChange={change}
            pool={pool}
            poolHead={
              <SectionLabel
                title={`Not used for ${word}`}
                hint="Set one up any time. It only downloads once you add it to the order."
              />
            }
            poolEmpty={`Every source is in the ${word} order.`}
            removeLabel={`Stop using for ${word}`}
            keepText="Keep at least one source"
            menuBodyClass="stg-menu-open"
          />
        </div>
      </div>

      {/* where books come from, besides the order (DESIGN 3.3: from Library) */}
      <div hidden={kind !== 'audiobooks'} data-audiobook-options="">
        <SectionLabel title="Audiobook download options" hint="Only books use these" />
        <AudiobookDownloadOptions store={store} />
      </div>

      <SectionLabel title="Indexers" hint="Searched by Torrent and Usenet" />
      <Card>
        <Row
          field
          mark={<SourceMark id="prowlarr" size="sm" />}
          label="Prowlarr"
          help={prowlarrUrl || 'Not set up yet'}
        >
          <StatusWords tone={prowlarr.tone}>{prowlarr.text}</StatusWords>
          <Btn
            kind="ghost"
            small
            icon="gear"
            data-open-source="prowlarr"
            aria-label="Prowlarr settings"
            onClick={() => model.openModal('prowlarr')}
          >
            Settings
          </Btn>
        </Row>
        <More label="Filesystem access: the easiest setup" names="read once when you install">
          <Row
            stack
            label="Point every downloader at the same folder"
            help={
              <>
                Torrent and usenet clients write files to{' '}
                <strong>their own download folders</strong>, not Soulseek&apos;s. SoulSync needs
                read access to those folders to import the files. The simplest fix: point all your
                downloaders (Soulseek, qBittorrent, SABnzbd or NZBGet) at the <em>same</em> download
                folder. One folder, one path to get right.
              </>
            }
          >
            <Note info>
              <ul>
                <li>
                  <strong>Bare-metal:</strong> set every client&apos;s download path to the same
                  folder (your existing slskd folder works).
                </li>
                <li>
                  <strong>Docker:</strong> reuse the <code>./downloads</code> mount you already have
                  for slskd, and point qBit and SAB at <code>/downloads</code> inside their
                  containers too. Or use the commented placeholders in{' '}
                  <code>docker-compose.yml</code> if you want separate folders.
                </li>
                <li>
                  <strong>Remote client (NAS or another host):</strong> mount the remote folder
                  locally (SMB, NFS or rclone) before SoulSync can see it.
                </li>
              </ul>
            </Note>
          </Row>
        </More>
      </Card>
    </>
  );
}
