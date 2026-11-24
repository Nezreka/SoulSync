/**
 * The last four source panels: YouTube with its yt-dlp updater, Prowlarr, the
 * torrent client and the usenet client, on the settings parts (see
 * source-panels.tsx). Same ids, keys and save rules as before.
 *
 * The torrent and usenet panels each have a "For music" and a "For video"
 * group: the video side's seeding goals with their per-indexer rows, and what
 * happens to a usenet job once imported, are keys of the video downloads
 * config (video-source-blocks.tsx).
 *
 * The torrent and usenet clients are tested by the sheet's own Test (the same
 * request their Test Connection buttons sent); their connection light follows
 * what that test said.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import {
  ActionRow,
  Btn,
  FieldNote,
  Item,
  Items,
  More,
  Note,
  Pre,
  Row,
  Say,
  SecretField,
  Select,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextArea,
  TextField,
  type Tone,
  Value,
  itemClass,
} from '../ui';
import { useDraft } from './parts';
import {
  CHECKING,
  DraftNumber,
  Panel,
  type Status,
  json,
  secretNote,
  text,
  toast,
  useOnOpen,
  useSettings,
} from './source-panels';
import { type SourcesModel, sourcesModel } from './sources-model';
import {
  SEED_MODES,
  SEED_MORE,
  USENET_REMOVE,
  VideoSeedingGoals,
  VideoUsenetBlocks,
} from './video-source-blocks';

type ShellTools = Window & { saveSettings?: () => Promise<void> };

/** the vanilla page saved first, so the server tests what is on screen */
async function saveFirst() {
  await (window as ShellTools).saveSettings?.();
}

/** the connection light of the *arr-style panels: kept by id for anything
 *  that reads it; the sheet's head says the same thing in words */
type Dot = 'unknown' | 'connected' | 'error';
const DOT_TITLE: Record<Dot, string> = {
  unknown: 'Not tested',
  connected: 'Connected',
  error: 'Connection failed',
};
function StatusDot({ id, dot }: { id: string; dot: Dot }) {
  return (
    <span
      className={`ind-status-dot ind-status-dot-${dot}`}
      id={id}
      title={DOT_TITLE[dot]}
      hidden
    />
  );
}

/** the light from the sources model: what the sheet's Test last said */
function useModelDot(model: SourcesModel, id: string): Dot {
  const state = useSyncExternalStore(model.subscribe, () => model.getState().status[id]);
  return state === 'ok' || state === 'warn' ? 'connected' : state === 'fail' ? 'error' : 'unknown';
}

/** Prowlarr's own Test: save the page, ask the server, light the dot */
function useConnectionTest(service: string) {
  const [status, setStatus] = useState<Status>({ text: '' });
  const [dot, setDot] = useState<Dot>('unknown');
  async function run(after?: () => void) {
    setStatus(CHECKING);
    try {
      await saveFirst();
      const data = await json('/api/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service }),
      });
      if (data.success) {
        setStatus({ text: text(data.message) || 'Connected', tone: 'ok' });
        setDot('connected');
        after?.();
      } else {
        setStatus({ text: text(data.error) || 'Connection failed', tone: 'bad' });
        setDot('error');
      }
    } catch {
      setStatus({ text: 'Connection error', tone: 'bad' });
      setDot('error');
    }
  }
  return { status, dot, run };
}

const str = (value: unknown, fallback = '') => text(value) || fallback;
const float = (raw: string, fallback: number) => {
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : fallback;
};
const integer = (raw: string, fallback: number) => {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : fallback;
};

// ── Prowlarr ──

interface Indexer {
  id?: number | string;
  name?: string;
  protocol?: string;
  privacy?: string;
  enable?: boolean;
}

export function ProwlarrPanel({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const settings = useSettings(store);
  const p = (settings.prowlarr || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('prowlarr', key, value);
  const test = useConnectionTest('prowlarr');
  const sheetDot = useModelDot(model, 'prowlarr');
  const key = str(p.api_key);
  const [list, setList] = useState<
    | null
    | { state: 'loading' }
    | { state: 'error'; text: string }
    | { state: 'empty' }
    | { state: 'rows'; rows: Indexer[] }
  >(null);

  async function loadIndexers() {
    setList({ state: 'loading' });
    try {
      const data = await json('/api/prowlarr/indexers');
      if (!data.success) {
        setList({ state: 'error', text: text(data.error) || 'Prowlarr not configured.' });
        return;
      }
      const rows = Array.isArray(data.indexers) ? (data.indexers as Indexer[]) : [];
      setList(rows.length ? { state: 'rows', rows } : { state: 'empty' });
    } catch (error) {
      setList({
        state: 'error',
        text: `Failed to load indexers: ${error instanceof Error ? error.message : String(error)}`,
      });
    }
  }

  let indexers: ReactNode;
  if (!list) {
    indexers = (
      <FieldNote>
        Test and load indexers to see the indexers SoulSync can search through Prowlarr.
      </FieldNote>
    );
  } else if (list.state === 'loading') {
    indexers = <FieldNote>Loading…</FieldNote>;
  } else if (list.state === 'error') {
    indexers = <StatusWords tone="bad">{list.text}</StatusWords>;
  } else if (list.state === 'empty') {
    indexers = <FieldNote>No indexers in Prowlarr yet. Add some there, under Indexers.</FieldNote>;
  } else {
    indexers = list.rows.map((idx, i) => (
      <Item key={`${text(idx.id)}-${i}`} off={!idx.enable} data-indexer="">
        <span className={itemClass.dim}>#{text(idx.id)}</span>
        <span className={itemClass.main}>{text(idx.name)}</span>
        {idx.privacy ? <span className={itemClass.dim}>{idx.privacy}</span> : null}
        <span className={itemClass.dim}>{idx.protocol === 'usenet' ? 'Usenet' : 'Torrent'}</span>
      </Item>
    ));
  }

  return (
    <Panel id="prowlarr-settings-container">
      <StatusDot id="prowlarr-status-dot" dot={test.dot !== 'unknown' ? test.dot : sheetDot} />
      <SheetGroup title="Connection" />
      <SheetField
        label="Prowlarr URL"
        htmlFor="prowlarr-url"
        help="The full address of your Prowlarr, for example http://192.168.1.100:9696."
        more={
          <>
            Prowlarr keeps your usenet and torrent indexers in one place. SoulSync searches every
            indexer at once through it, for the Torrent and Usenet sources. No Prowlarr yet? Get it
            from <code>prowlarr.com</code> (or your *arr stack), point it at your indexers, then
            point SoulSync at Prowlarr.
          </>
        }
      >
        <TextField
          full
          id="prowlarr-url"
          placeholder="http://localhost:9696"
          value={str(p.url)}
          onChange={(event) => set('url', event.target.value)}
        />
      </SheetField>
      <SheetField
        label="API key"
        htmlFor="prowlarr-api-key"
        help={secretNote(key, 'In Prowlarr: Settings, General, Security, API Key.')}
      >
        <SecretField
          id="prowlarr-api-key"
          placeholder="Your Prowlarr API key"
          value={key}
          onChange={(value) => set('api_key', value)}
        />
      </SheetField>
      <SheetField
        label="FlareSolverr URL"
        htmlFor="flaresolverr-url"
        help="For the sites behind a browser check: EXT.to (Fresh Releases and its search) and Beatport when it blocks."
        more="FlareSolverr, or a compatible solver such as Byparr, opens those pages in a real browser. Often the same one your Prowlarr uses for its Cloudflare-protected indexers."
      >
        <TextField
          full
          id="flaresolverr-url"
          placeholder="http://localhost:8191"
          value={str((settings.flaresolverr as Record<string, unknown> | undefined)?.url)}
          onChange={(event) => store.set('flaresolverr', 'url', event.target.value)}
        />
      </SheetField>

      <SheetGroup title="Indexers" />
      <ActionRow label="Indexers" help="Tests the connection, then lists what Prowlarr searches.">
        <StatusWords id="prowlarr-connection-status" tone={test.status.tone} live>
          {test.status.text}
        </StatusWords>
        <Btn id="prowlarr-test-btn" small onClick={() => void test.run(() => void loadIndexers())}>
          Test and load indexers
        </Btn>
        <Btn
          id="prowlarr-refresh-indexers-btn"
          small
          kind="ghost"
          onClick={() => void loadIndexers()}
        >
          Refresh Indexer List
        </Btn>
      </ActionRow>
      <Items id="prowlarr-indexer-list">{indexers}</Items>

      <SheetGroup title="Searching" />
      {/* #2061: the pacing was always there (core/prowlarr_throttle.py), with
          no way to turn it down for a strict tracker. ?? not ||: 0 is off */}
      <Row
        field
        label="Seconds between searches"
        htmlFor="prowlarr-search-min-gap"
        help="The shortest wait between two searches sent to Prowlarr, music and video together. Default 2. Raise it if a tracker answers 429 Too Many Requests or Prowlarr marks an indexer unhealthy: a tracker that asks for 11 seconds wants 12 or more here. 0 turns the wait off."
      >
        <DraftNumber
          store={store}
          id="prowlarr-search-min-gap"
          unit="seconds"
          min="0"
          max="600"
          step="0.5"
          placeholder="2"
          value={text(p.search_min_gap_seconds ?? 2)}
          save={(raw) => set('search_min_gap_seconds', float(raw, 2))}
        />
      </Row>
      <Row
        field
        label="Searches per minute, at most"
        htmlFor="prowlarr-max-searches-per-minute"
        help="A ceiling on top of the wait above, so a long Wishlist run cannot keep up a fast pace for hours. Default 20. 0 turns the ceiling off. A search you start by hand waits its turn behind background searches."
      >
        <DraftNumber
          store={store}
          id="prowlarr-max-searches-per-minute"
          unit="a minute"
          min="0"
          max="600"
          step="1"
          placeholder="20"
          value={text(p.max_searches_per_minute ?? 20)}
          save={(raw) => set('max_searches_per_minute', integer(raw, 20))}
        />
      </Row>
      <More count={1} names="only search some indexers">
        <SheetField
          label="Restrict to indexer IDs (optional)"
          htmlFor="prowlarr-indexer-ids"
          help="Comma-separated Prowlarr indexer IDs. Leave it blank to search every enabled indexer. Restrict it when you have a private tracker you want to put first, or a noisy public one to leave out."
        >
          <TextField
            full
            id="prowlarr-indexer-ids"
            placeholder="e.g. 1,3,7 (blank = all enabled indexers)"
            value={str(p.indexer_ids)}
            onChange={(event) => set('indexer_ids', event.target.value)}
          />
        </SheetField>
      </More>
    </Panel>
  );
}

// ── the torrent client ──

/** a client's category: what is typed stays on screen (emptied, it stays
 *  empty while the next one is typed); blank saves the default, as _cfgStr's
 *  fallback did. a settings load shows the stored one again */
function CategoryInput({
  id,
  store,
  value,
  save,
}: {
  id: string;
  store: SettingsStore;
  value: string;
  save: (value: string) => void;
}) {
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const draft = useDraft(value, (raw) => save(raw || 'soulsync'));
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <TextField
      full
      id={id}
      placeholder="soulsync"
      value={draft.value}
      onChange={(event) => draft.onChange(event.target.value)}
    />
  );
}

export function TorrentPanel({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const settings = useSettings(store);
  const t = (settings.torrent_client || {}) as Record<string, unknown>;
  const d = (settings.download_source || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('torrent_client', key, value);
  const setSource = (key: string, value: unknown) => store.set('download_source', key, value);
  const dot = useModelDot(model, 'torrent');
  const password = str(t.password);

  const stall = d.torrent_stall_timeout_seconds;
  const minSeeders = d.torrent_min_seeders;

  return (
    <Panel id="torrent-client-settings-container">
      <StatusDot id="torrent-client-status-dot" dot={dot} />
      <SheetGroup title="Connection" />
      <SheetField
        label="Client"
        htmlFor="torrent-client-type"
        more="Where SoulSync sends torrents once Prowlarr finds them. Pick the client you already use; one can be active at a time. Add the address of the client's WebUI, sign in if it asks, and Test connection."
      >
        <Select
          id="torrent-client-type"
          value={str(t.type, 'qbittorrent')}
          onChange={(event) => set('type', event.target.value || 'qbittorrent')}
          options={[
            { value: 'qbittorrent', label: 'qBittorrent' },
            { value: 'transmission', label: 'Transmission' },
            { value: 'deluge', label: 'Deluge 2.x' },
            { value: 'aria2', label: 'Aria2 (RPC)' },
          ]}
        />
        <FieldNote id="torrent-client-help">
          qBittorrent: WebUI port (default 8080). Transmission: RPC port (default 9091). Deluge:
          WebUI port (default 8112). Aria2: RPC port (default 6800); leave Username blank and put
          your <code>--rpc-secret</code> in Password.
        </FieldNote>
      </SheetField>
      <SheetField label="WebUI URL" htmlFor="torrent-client-url">
        <TextField
          full
          id="torrent-client-url"
          placeholder="http://localhost:8080"
          value={str(t.url)}
          onChange={(event) => set('url', event.target.value)}
        />
      </SheetField>
      <SheetField
        label="Username"
        htmlFor="torrent-client-username"
        id="torrent-client-username-group"
        help="qBittorrent and Transmission use a username and password. Deluge uses the password only."
      >
        <TextField
          full
          id="torrent-client-username"
          placeholder="leave blank if WebUI doesn't require auth"
          value={str(t.username)}
          onChange={(event) => set('username', event.target.value)}
        />
      </SheetField>
      <SheetField label="Password" htmlFor="torrent-client-password" help={secretNote(password)}>
        <SecretField
          id="torrent-client-password"
          placeholder="WebUI password"
          value={password}
          onChange={(value) => set('password', value)}
        />
      </SheetField>
      <More count={3} names="category, save folder, start torrents">
        <SheetField
          label="Category or label"
          htmlFor="torrent-client-category"
          help="SoulSync tags every torrent it adds with this, so it is easy to spot in your client. Deluge needs the Label plugin for it to stick."
        >
          <CategoryInput
            id="torrent-client-category"
            store={store}
            value={str(t.category, 'soulsync')}
            save={(value) => set('category', value)}
          />
        </SheetField>
        <SheetField
          label="Save path (optional)"
          htmlFor="torrent-client-save-path"
          help="Where the torrent client writes downloads, if not its own default."
          more={
            <>
              Overrides where the torrent client writes downloads. This path is on the{' '}
              <strong>torrent client's</strong> machine, not SoulSync's.
            </>
          }
        >
          <TextField
            full
            mono
            id="torrent-client-save-path"
            placeholder="leave blank to use the client's default"
            value={str(t.save_path)}
            onChange={(event) => set('save_path', event.target.value)}
          />
        </SheetField>
        <Row
          label="Start the torrents SoulSync adds"
          htmlFor="torrent-client-start-torrents"
          help="qBittorrent only. Turn it on if your qBittorrent adds torrents stopped, or stops them once the metadata is in. SoulSync's torrents then start anyway, and what other apps add keeps your qBittorrent setting."
        >
          <Switch
            id="torrent-client-start-torrents"
            checked={t.start_torrents === true}
            onChange={(event) => set('start_torrents', event.target.checked)}
          />
        </Row>
      </More>

      <SheetGroup title="For music" />
      <Row
        field
        label="Minimum seeders"
        htmlFor="torrent-min-seeders"
        help="Skip a release with fewer seeders than this instead of waiting on something nobody is serving. A release that reports no seeder count (usenet, and indexers that leave it out) is never skipped. 0 turns it off."
      >
        <DraftNumber
          store={store}
          id="torrent-min-seeders"
          min="0"
          step="1"
          placeholder="1"
          value={text(minSeeders === undefined || minSeeders === null ? 1 : Number(minSeeders))}
          // #1139: blank or junk is 1; 0 stays 0 (gate off)
          save={(raw) => {
            const n = parseInt(raw, 10);
            setSource('torrent_min_seeders', Number.isFinite(n) && n >= 0 ? n : 1);
          }}
        />
      </Row>
      <Row
        field
        label="Stalled torrent timeout"
        htmlFor="torrent-stall-timeout"
        help="Give up on a torrent that makes no download progress for this long."
        more={
          <>
            Give up on a torrent that makes <strong>no download progress</strong> for this long: a
            dead magnet stuck on "downloading metadata", or a swarm with no seeders. Without it, a
            dud torrent holds a download slot for the full 6-hour limit.{' '}
            <strong>0 turns it off</strong> (wait the full limit).
          </>
        }
      >
        <DraftNumber
          store={store}
          id="torrent-stall-timeout"
          unit="minutes"
          min="0"
          step="1"
          placeholder="10"
          // stored in seconds, shown in minutes
          value={text(stall === undefined || stall === null ? 10 : Math.round(Number(stall) / 60))}
          save={(raw) => {
            const m = parseInt(raw, 10);
            setSource(
              'torrent_stall_timeout_seconds',
              (Number.isFinite(m) && m >= 0 ? m : 10) * 60,
            );
          }}
        />
      </Row>
      <Row
        field
        label="When a torrent stalls"
        htmlFor="torrent-stall-action"
        help="Abandon frees the slot so the next source can try; it and its partial data are removed and the download fails. Pause keeps the torrent in your client so you can look at it or resume it yourself."
      >
        <Select
          id="torrent-stall-action"
          value={str(d.torrent_stall_action, 'abandon')}
          onChange={(event) => setSource('torrent_stall_action', event.target.value || 'abandon')}
          options={[
            { value: 'abandon', label: 'Abandon and remove it' },
            { value: 'pause', label: 'Pause it in the client' },
          ]}
        />
      </Row>
      {/* seeding lifecycle, torrent grabs only. Music twin of the video side */}
      <div id="music-seeding-goals">
        <Row
          field
          label="Seed until a ratio of"
          htmlFor="music-seed-ratio"
          help="Remove a finished torrent once it reaches this ratio or the hours below, whichever comes first."
          more={SEED_MORE}
        >
          <DraftNumber
            store={store}
            id="music-seed-ratio"
            min="0"
            step="0.1"
            placeholder="0"
            value={text(t.seed_ratio_goal ?? 0)}
            save={(raw) => set('seed_ratio_goal', float(raw, 0))}
          />
        </Row>
        <Row field sub label="Or hours seeded" htmlFor="music-seed-hours">
          <DraftNumber
            store={store}
            id="music-seed-hours"
            unit="hours"
            min="0"
            step="1"
            placeholder="0"
            value={text(t.seed_time_goal_hours ?? 0)}
            save={(raw) => set('seed_time_goal_hours', integer(raw, 0))}
          />
        </Row>
        <Row sub label="Also delete the client's copy" htmlFor="music-seed-remove-data">
          <Switch
            id="music-seed-remove-data"
            checked={t.seed_remove_data !== false}
            onChange={(event) => set('seed_remove_data', event.target.checked)}
          />
        </Row>
        <Row field sub label="Enforced by" htmlFor="music-seed-mode">
          <Select
            id="music-seed-mode"
            value={str(t.seed_mode, 'soulsync')}
            onChange={(event) => set('seed_mode', event.target.value || 'soulsync')}
            options={SEED_MODES}
          />
        </Row>
      </div>
      <More count={1} names="where SoulSync reads finished torrents">
        <SheetField
          label="Completed downloads folder, as SoulSync sees it (optional)"
          htmlFor="torrent-download-path"
          help="Where SoulSync reads finished torrents, in its own (container) path."
          more={
            <>
              Where <strong>SoulSync</strong> can read finished torrents, in its own in-container
              path. Set it when your client saves to a category folder (a "Music" category at{' '}
              <code>/data/downloads/music</code>) that is mounted here at a different path. SoulSync
              finds the release by name under this folder. Blank uses the Soulseek download folder
              and the music library folder.
            </>
          }
        >
          <TextField
            full
            mono
            id="torrent-download-path"
            placeholder="leave blank for the shared-volume default"
            value={str(d.torrent_download_path)}
            onChange={(event) => setSource('torrent_download_path', event.target.value)}
          />
        </SheetField>
      </More>

      <SheetGroup title="For video" />
      {/* the video side's seeding goals (video-source-blocks.tsx) */}
      <VideoSeedingGoals />
    </Panel>
  );
}

// ── the usenet client ──

export function UsenetPanel({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const settings = useSettings(store);
  const u = (settings.usenet_client || {}) as Record<string, unknown>;
  const d = (settings.download_source || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('usenet_client', key, value);
  const dot = useModelDot(model, 'usenet');
  const type = str(u.type, 'sabnzbd');
  const key = str(u.api_key);
  const password = str(u.password);

  return (
    <Panel id="usenet-client-settings-container">
      <StatusDot id="usenet-client-status-dot" dot={dot} />
      <SheetGroup title="Connection" />
      <SheetField
        label="Client"
        htmlFor="usenet-client-type"
        help="SABnzbd: default WebUI port 8080. NZBGet: default WebUI port 6789."
        more="Where SoulSync sends NZBs once Prowlarr finds them. Pick one usenet downloader. SABnzbd signs in with an API key; NZBGet with a username and password. SABnzbd: default WebUI port 8080. NZBGet: default WebUI port 6789."
      >
        <Select
          id="usenet-client-type"
          value={type}
          onChange={(event) => set('type', event.target.value || 'sabnzbd')}
          options={[
            { value: 'sabnzbd', label: 'SABnzbd' },
            { value: 'nzbget', label: 'NZBGet' },
          ]}
        />
      </SheetField>
      <SheetField label="WebUI URL" htmlFor="usenet-client-url">
        <TextField
          full
          id="usenet-client-url"
          placeholder="http://localhost:8080"
          value={str(u.url)}
          onChange={(event) => set('url', event.target.value)}
        />
      </SheetField>
      {/* SABnzbd signs in with a key, NZBGet with a login */}
      <SheetField
        label="API key"
        htmlFor="usenet-client-api-key"
        id="usenet-apikey-group"
        hidden={type !== 'sabnzbd'}
        help={secretNote(key, 'In SABnzbd: Config, General, API Key. SABnzbd only.')}
      >
        <SecretField
          id="usenet-client-api-key"
          placeholder="SABnzbd API key"
          value={key}
          onChange={(value) => set('api_key', value)}
        />
      </SheetField>
      <SheetField
        label="Username"
        htmlFor="usenet-client-username"
        id="usenet-username-group"
        hidden={type === 'sabnzbd'}
      >
        <TextField
          full
          id="usenet-client-username"
          placeholder="NZBGet WebUI username"
          value={str(u.username)}
          onChange={(event) => set('username', event.target.value)}
        />
      </SheetField>
      <SheetField
        label="Password"
        htmlFor="usenet-client-password"
        id="usenet-password-group"
        hidden={type === 'sabnzbd'}
        help={secretNote(password)}
      >
        <SecretField
          id="usenet-client-password"
          placeholder="NZBGet WebUI password"
          value={password}
          onChange={(value) => set('password', value)}
        />
      </SheetField>
      <More count={1} names="category">
        <SheetField
          label="Category or label"
          htmlFor="usenet-client-category"
          help="SoulSync tags every NZB with this category, so it lands in a predictable post-processing folder."
        >
          <CategoryInput
            id="usenet-client-category"
            store={store}
            value={str(u.category, 'soulsync')}
            save={(value) => set('category', value)}
          />
        </SheetField>
      </More>

      <SheetGroup title="For music" />
      <Row
        stack
        id="music-usenet-remove-completed-group"
        label="When a download has imported"
        htmlFor="music-usenet-remove-completed"
        help="Music grabs copy the audio out, so without this the whole release stays in your client's completed folder. Only downloads SoulSync has imported are touched: an album goes once every track in it imported, and a failed or unmatched download stays for you."
      >
        <Select
          id="music-usenet-remove-completed"
          value={str(u.remove_completed, 'off')}
          onChange={(event) => set('remove_completed', event.target.value || 'off')}
          options={USENET_REMOVE}
        />
      </Row>
      <More count={1} names="where SoulSync reads finished downloads">
        <SheetField
          label="Completed downloads folder, as SoulSync sees it (optional)"
          htmlFor="usenet-download-path"
          help="Where SoulSync reads finished NZB downloads, in its own (container) path."
          more={
            <>
              Where <strong>SoulSync</strong> can read finished NZB downloads, in its own
              in-container path. Set it when SABnzbd or NZBGet writes that category to a folder (
              <code>/data/downloads/music</code>) mounted here at a different path. SoulSync finds
              the release by name under this folder. Blank uses the Soulseek download folder and the
              music library folder.
            </>
          }
        >
          <TextField
            full
            mono
            id="usenet-download-path"
            placeholder="leave blank for the shared-volume default"
            value={str(d.usenet_download_path)}
            onChange={(event) =>
              store.set('download_source', 'usenet_download_path', event.target.value)
            }
          />
        </SheetField>
      </More>

      <SheetGroup title="For video" />
      <VideoUsenetBlocks />
    </Panel>
  );
}

// ── YouTube ──

/** Chromium browsers seal their cookies with App-Bound Encryption on
 *  Windows, which yt-dlp cannot read (yt-dlp issue 10927) */
const ABE_BROWSERS = ['chrome', 'edge', 'brave', 'opera', 'vivaldi', 'chromium'];
const COOKIE_BROWSERS: Array<[string, string]> = [
  ['', 'None (No cookies)'],
  ['chrome', 'Chrome'],
  ['firefox', 'Firefox'],
  ['edge', 'Edge'],
  ['brave', 'Brave'],
  ['opera', 'Opera'],
  ['safari', 'Safari'],
  ['custom', 'Paste cookies.txt (server / Docker)'],
];

interface YtdlpState {
  installed: string;
  installedWarn: boolean;
  latest: string;
  latestWarn: boolean;
  badge: { hidden: boolean; title: string };
}

const YTDLP_LOADING: YtdlpState = {
  installed: 'Loading...',
  installedWarn: false,
  latest: 'Loading...',
  latestWarn: false,
  badge: { hidden: true, title: '' },
};

export function YoutubePanel({
  store,
  model = sourcesModel,
  opened,
  active,
}: {
  store: SettingsStore;
  model?: SourcesModel;
  opened: number;
  active: boolean;
}) {
  const settings = useSettings(store);
  const y = (settings.youtube || {}) as Record<string, unknown>;
  const d = (settings.download_source || {}) as Record<string, unknown>;
  const env = (settings._environment || {}) as Record<string, unknown>;
  const windows = !!env.windows;
  const set = (key: string, value: unknown) => store.set('youtube', key, value);

  const browser = str(y.cookies_browser);
  const transcode = y.transcode !== false;
  const codec = str(y.transcode_codec, 'mp3');
  const rawBitrate = str(y.transcode_bitrate, '320');
  // Opus tops out at 256 kbps: 320 is greyed out and falls to 256
  const bitrate = codec === 'opus' && rawBitrate === '320' ? '256' : rawBitrate;
  const [paste, setPaste] = useState('');

  // ── yt-dlp ──
  const [channel, setChannel] = useState('nightly');
  const [ytdlp, setYtdlp] = useState<YtdlpState>(YTDLP_LOADING);
  const [updating, setUpdating] = useState(false);
  const [update, setUpdate] = useState<{ text: string; tone: Tone } | null>(null);
  const [detail, setDetail] = useState('');
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);

  async function loadYtdlp(chosen = channel) {
    setYtdlp((s) => ({ ...s, installed: 'Loading...', latest: 'Loading...' }));
    try {
      const data = await json(
        '/api/ytdlp/status?channel=' + encodeURIComponent(chosen || 'nightly'),
      );
      // after an update the running yt-dlp and the one on disk differ until a
      // restart: say both, or a good update reads as "still behind"
      const pending = !!data.restart_pending;
      setYtdlp({
        installed: pending
          ? `${text(data.installed)} running, ${text(data.on_disk)} installed. Restart to finish.`
          : text(data.installed) || 'Not installed',
        installedWarn: pending,
        // a PyPI outage is not "up to date"
        latest:
          text(data.latest) ||
          (data.lookup_error ? "Couldn't check: no connection to PyPI" : 'Unknown'),
        latestWarn: !!data.behind,
        badge: {
          hidden: !(data.behind || pending),
          title: pending
            ? 'yt-dlp is updated on disk. Restart SoulSync to use it'
            : data.behind
              ? 'A newer yt-dlp is available'
              : '',
        },
      });
    } catch (error) {
      setYtdlp((s) => ({ ...s, installed: 'Unknown', latest: 'Unknown' }));
      console.error('yt-dlp status failed:', error);
    }
  }

  // read where it now lives: arriving on Sources, and opening YouTube
  useEffect(() => {
    if (active && opened) void loadYtdlp();
  }, [opened, active]); // eslint-disable-line react-hooks/exhaustive-deps
  useOnOpen(model, 'youtube', () => void loadYtdlp());

  async function runUpdate() {
    setUpdating(true);
    setDetail('');
    setUpdate({
      text: 'Installing the newest ' + channel + ' build. This can take a minute...',
      tone: 'idle',
    });
    try {
      const data = await json('/api/ytdlp/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel }),
      });
      setUpdate({
        text: text(data.message) || (data.success ? 'Done.' : 'Update failed.'),
        tone: data.success ? (data.restart_required ? 'warn' : 'ok') : 'bad',
      });
      // pip's own words: each failure needs a different fix, and they say which
      if (data.detail) setDetail(text(data.detail));
      if (data.success) {
        toast(
          data.restart_required
            ? 'yt-dlp updated. Restart SoulSync to use it'
            : text(data.message) || 'Already up to date',
          data.restart_required ? 'info' : 'success',
        );
      } else {
        toast('yt-dlp update failed. See Settings for details', 'error');
      }
      void loadYtdlp();
    } catch (error) {
      setUpdate({ text: 'Update request failed: ' + String(error), tone: 'bad' });
      toast('yt-dlp update failed', 'error');
    } finally {
      setUpdating(false);
    }
  }

  // each settings load forgets a half-pasted cookie file, as the page reload did
  useEffect(() => setPaste(''), [loads]);

  const abe = windows && ABE_BROWSERS.includes(browser);
  const browserLabel = COOKIE_BROWSERS.find(([v]) => v === browser)?.[1] || browser;

  return (
    <Panel id="youtube-settings-container" model={model}>
      <SheetGroup title="Cookies" />
      <SheetField
        label="Browser cookies"
        htmlFor="youtube-cookies-browser"
        help="Only needed for private playlists, your Liked Music, age-gated videos and Premium audio. Public videos download fine with None."
        more={
          <>
            Only needed for private playlists, your Liked Music, age-gated videos and Premium audio
            qualities. Public videos download fine with <strong>None</strong>. Reading a browser
            only works when that browser is on the same machine as SoulSync, so on a server or in
            Docker choose <strong>Paste cookies.txt</strong>. On Windows, Chrome and the other
            Chromium browsers seal their cookies in a way yt-dlp cannot read at all: use{' '}
            <strong>Firefox</strong> or <strong>Paste cookies.txt</strong>.
          </>
        }
      >
        <Select
          id="youtube-cookies-browser"
          value={browser}
          onChange={(event) => set('cookies_browser', event.target.value)}
        >
          {COOKIE_BROWSERS.map(([value, label]) => (
            <option key={value} value={value}>
              {/* marked, not disabled: a selected disabled option reads as ''
                  and the next save would wipe the setting */}
              {windows && ABE_BROWSERS.includes(value)
                ? `${label} (not supported on Windows)`
                : label}
            </option>
          ))}
        </Select>
      </SheetField>
      <div id="youtube-cookie-abe-warning" role="status" hidden={!abe}>
        {abe ? (
          <Note>
            {`${browserLabel} seals its cookies with App-Bound Encryption on Windows, which yt-dlp cannot read. This will not work. Choose "Paste cookies.txt" instead, or "None" if you only download public videos.`}
          </Note>
        ) : null}
      </div>
      <SheetField
        label="Paste cookies.txt"
        htmlFor="youtube-cookies-paste"
        id="youtube-cookies-paste-group"
        hidden={browser !== 'custom'}
        help="For a server or Docker install with no browser on it. Paste the whole Netscape cookies.txt file."
        more={
          <>
            For server and Docker installs with no local browser. Paste a Netscape/Mozilla{' '}
            <code>cookies.txt</code>. Export it with a "Get cookies.txt LOCALLY" extension and paste
            the whole file: the <code># Netscape HTTP Cookie File</code> header alone is not enough.
            Needed for private playlists like your <strong>Liked Music</strong>, and for Premium
            audio. Cookies expire now and then: paste again if YouTube stops working.
          </>
        }
      >
        <TextArea
          id="youtube-cookies-paste"
          mono
          rows={6}
          // the saved file is never sent back (a secret, kept in a file): say
          // one is there, so a blank save is not mistaken for a wipe
          placeholder={
            y.cookies_file
              ? 'A cookies.txt is saved. Paste again to replace it, or leave blank to keep it.'
              : '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...\tNAME\tVALUE'
          }
          value={paste}
          onChange={(event) => {
            setPaste(event.target.value);
            set('cookies_paste', event.target.value);
          }}
        />
      </SheetField>

      <SheetGroup title="Downloading" />
      <Row
        field
        label="Download delay"
        htmlFor="youtube-download-delay"
        help="Time between YouTube downloads, so YouTube does not take SoulSync for a bot. Default 3."
      >
        <DraftNumber
          store={store}
          id="youtube-download-delay"
          unit="seconds"
          min="0"
          max="30"
          step="1"
          placeholder="3"
          value={text(y.download_delay ?? 3)}
          save={(raw) => set('download_delay', parseInt(raw, 10) || 3)}
        />
      </Row>
      <Row
        label="Download whole albums from YouTube Music"
        htmlFor="youtube-album-bundle"
        help="For an album, find the album itself on YouTube Music and download its playlist, instead of searching for each song. Helps with albums whose songs never show up in a song search. The artist and album title must both match; if they don't, SoulSync searches track by track as usual. Some albums need YouTube Music Premium cookies. Off by default."
      >
        {/* #1851: it lives in download_source because the album flow reads it */}
        <Switch
          id="youtube-album-bundle"
          checked={d.youtube_album_bundle === true}
          onChange={(event) =>
            store.set('download_source', 'youtube_album_bundle', event.target.checked)
          }
        />
      </Row>
      <Row
        label="Re-encode YouTube audio"
        htmlFor="youtube-transcode"
        help="YouTube serves Opus or AAC, not FLAC or MP3. With this on, the quality list must include the converted format (default MP3 320). With it off, add Opus or AAC to the list, or turn Fallback on."
      >
        <Switch
          id="youtube-transcode"
          checked={transcode}
          onChange={(event) => set('transcode', event.target.checked)}
        />
      </Row>
      <div id="youtube-transcode-options" hidden={!transcode}>
        <Row field sub label="Codec" htmlFor="youtube-transcode-codec">
          <Select
            id="youtube-transcode-codec"
            value={codec}
            onChange={(event) => {
              const next = event.target.value || 'mp3';
              // the bitrate the field shows goes with it: a stored 320 under Opus
              // shows 256, and the old page's save wrote what it showed
              store.patch('youtube', {
                transcode_codec: next,
                transcode_bitrate: next === 'opus' && bitrate === '320' ? '256' : bitrate,
              });
            }}
            options={[
              { value: 'mp3', label: 'MP3' },
              { value: 'opus', label: 'Opus' },
              { value: 'aac', label: 'AAC (M4A)' },
            ]}
          />
        </Row>
        <Row field sub label="Bitrate" htmlFor="youtube-transcode-bitrate">
          <Select
            id="youtube-transcode-bitrate"
            value={bitrate}
            onChange={(event) => set('transcode_bitrate', event.target.value || '320')}
            options={[
              { value: '320', label: '320 kbps', disabled: codec === 'opus' },
              { value: '256', label: '256 kbps' },
              { value: '192', label: '192 kbps' },
              { value: '128', label: '128 kbps' },
            ]}
          />
        </Row>
      </div>

      {/* the yt-dlp updater lives with YouTube: it is what breaks YouTube
          downloads when it goes stale */}
      <SheetGroup title="yt-dlp">
        <span
          id="ytdlp-hint-badge"
          hidden={ytdlp.badge.hidden}
          title={ytdlp.badge.title || undefined}
        >
          <StatusWords tone="warn">{ytdlp.badge.title ? 'Needs a look' : ''}</StatusWords>
        </span>
      </SheetGroup>
      <Row label="Installed">
        <Value id="ytdlp-installed" tone={ytdlp.installedWarn ? 'warn' : undefined}>
          {ytdlp.installed}
        </Value>
      </Row>
      <Row label="Newest available">
        <Value id="ytdlp-latest" tone={ytdlp.latestWarn ? 'warn' : undefined}>
          {ytdlp.latest}
        </Value>
      </Row>
      <Row
        field
        label="Release channel"
        htmlFor="ytdlp-channel"
        help="YouTube changes how it serves video far more often than yt-dlp cuts a stable release, so nightly is what actually keeps downloads working. Stable is there if you'd rather move slowly."
      >
        <Select
          id="ytdlp-channel"
          value={channel}
          onChange={(event) => {
            setChannel(event.target.value);
            void loadYtdlp(event.target.value);
          }}
          options={[
            { value: 'nightly', label: 'Nightly (recommended)' },
            { value: 'stable', label: 'Stable' },
          ]}
        />
      </Row>
      <ActionRow
        label="Update yt-dlp"
        help="When YouTube downloads start failing with 403 Forbidden, an out-of-date yt-dlp is nearly always why. An update takes effect only after you restart SoulSync: the running process keeps the version it loaded at startup."
      >
        <Btn
          id="ytdlp-update-btn"
          small
          busy={updating}
          disabled={updating}
          onClick={() => void runUpdate()}
        >
          {updating ? 'Updating...' : 'Update yt-dlp'}
        </Btn>
      </ActionRow>
      <Say id="ytdlp-status" hidden={!update} tone={update?.tone}>
        {update?.text}
      </Say>
      <Pre id="ytdlp-detail" hidden={!detail}>
        {detail}
      </Pre>
    </Panel>
  );
}

/** the panels of the last slots in #source-config-home, by part */
export function MoreSourcePanels({ store, part, opened, active }: SettingsTabProps) {
  if (part === 'youtube') return <YoutubePanel store={store} opened={opened} active={active} />;
  if (part === 'prowlarr-torrent-usenet') {
    return (
      <>
        <ProwlarrPanel store={store} />
        <TorrentPanel store={store} />
        <UsenetPanel store={store} />
      </>
    );
  }
  return null;
}

export const MORE_SOURCE_PARTS = ['youtube', 'prowlarr-torrent-usenet'];
