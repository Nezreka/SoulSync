/**
 * The last four Sources panels (SETTINGS_PORT.md, step 7g): YouTube with its
 * yt-dlp updater, Prowlarr, the torrent client and the usenet client. Same
 * markup, classes and ids as the vanilla panels, same load and save rules.
 *
 * The torrent and usenet panels hold three blocks the VIDEO side owns: the
 * torrent seeding goals with their per-indexer rows, and the usenet
 * remove-completed and redownload-failed settings. They are React too
 * (video-source-blocks.tsx, SETTINGS_PORT.md video pass V2a), keys of the
 * video downloads config.
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { HelpIcon, SecretInput, helpBits, useHelp } from './parts';
import {
  BAD,
  CHECKING,
  NumberField,
  OK,
  Panel,
  type Status,
  json,
  text,
  toast,
  useOnOpen,
  useSettings,
} from './source-panels';
import { type SourcesModel, sourcesModel } from './sources-model';
import { VideoSeedingGoals, VideoUsenetBlocks } from './video-source-blocks';

type ShellTools = Window & { saveSettings?: () => Promise<void> };

/** the vanilla page saved first, so the server tests what is on screen */
async function saveFirst() {
  await (window as ShellTools).saveSettings?.();
}

/** a status line's style */
const statusStyle = (status: Status) => ({
  marginLeft: '8px',
  ...(status.color ? { color: status.color } : {}),
});

/** the connection dot in the corner of the *arr-style panels */
type Dot = 'unknown' | 'connected' | 'error';
const DOT_TITLE: Record<Dot, string> = {
  unknown: 'Not tested',
  connected: 'Connected',
  error: 'Connection failed',
};
function StatusDot({ id, dot }: { id: string; dot: Dot }) {
  return <span className={`ind-status-dot ind-status-dot-${dot}`} id={id} title={DOT_TITLE[dot]} />;
}

/** an *arr-style frame's title row and its help, which has no help line */
function FrameTitle({ title, kind, help }: { title: string; kind: string; help: ReactNode }) {
  const state = useHelp();
  return (
    <>
      <div className="setting-row" style={{ padding: '14px 18px' }}>
        <h4
          className={`service-title ${kind}-title`}
          // the markup had these !important (they beat the card's own title
          // rules); a style object cannot say important, setProperty can
          ref={(el) => {
            if (!el) return;
            for (const prop of ['padding', 'border', 'margin'])
              el.style.setProperty(prop, '0', 'important');
          }}
          style={{ flex: 1 }}
        >
          ⬢ {title}
        </h4>
        <HelpIcon {...state} />
      </div>
      <div
        className="setting-help-text setting-help-body"
        style={{ marginBottom: '14px' }}
        hidden={!state.open}
      >
        {help}
      </div>
    </>
  );
}

/** a labelled field with an optional help text under it */
function Field({
  label,
  help,
  id,
  style,
  children,
}: {
  label: ReactNode;
  help?: ReactNode;
  id?: string;
  style?: Record<string, string>;
  children: ReactNode;
}) {
  return (
    <div className="form-group" id={id} style={style}>
      <label>{label}</label>
      {children}
      {help ? <div className="setting-help-text">{help}</div> : null}
    </div>
  );
}

/** a Test Connection row: save the page, ask the server, colour the dot */
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
        setStatus({ text: text(data.message) || 'Connected', color: OK });
        setDot('connected');
        after?.();
      } else {
        setStatus({ text: text(data.error) || 'Connection failed', color: BAD });
        setDot('error');
      }
    } catch {
      setStatus({ text: 'Connection error', color: BAD });
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

export function ProwlarrPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const p = (settings.prowlarr || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('prowlarr', key, value);
  const test = useConnectionTest('prowlarr');
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
      <>
        Connect to Prowlarr and click <strong>Refresh Indexer List</strong> to see the indexers
        SoulSync can search.
      </>
    );
  } else if (list.state === 'loading') {
    indexers = <em>Loading…</em>;
  } else if (list.state === 'error') {
    indexers = <em style={{ color: BAD }}>{list.text}</em>;
  } else if (list.state === 'empty') {
    indexers = <em>No indexers configured in Prowlarr yet. Add some in Prowlarr → Indexers.</em>;
  } else {
    indexers = list.rows.map((idx, i) => (
      <div
        key={`${text(idx.id)}-${i}`}
        className={idx.enable ? 'ind-indexer-card' : 'ind-indexer-card ind-indexer-card-disabled'}
      >
        <span className="ind-indexer-card-id">#{text(idx.id)}</span>
        <span className="ind-indexer-card-name">{text(idx.name)}</span>
        {idx.privacy ? <span className="ind-indexer-card-privacy">{idx.privacy}</span> : null}
        <span
          className={`ind-indexer-card-proto ${idx.protocol === 'usenet' ? 'ind-indexer-card-proto-usenet' : 'ind-indexer-card-proto-torrent'}`}
        >
          {idx.protocol === 'usenet' ? 'Usenet' : 'Torrent'}
        </span>
      </div>
    ));
  }

  return (
    <Panel id="prowlarr-settings-container">
      <StatusDot id="prowlarr-status-dot" dot={test.dot} />
      <div className="settings-group">
        <div className="api-service-frame">
          <FrameTitle
            title="Prowlarr"
            kind="prowlarr"
            help={
              <>
                Prowlarr manages your Usenet and torrent indexers and exposes them through one API.
                SoulSync uses Prowlarr to search across every indexer at once for the Torrent and
                Usenet download sources.
                <br />
                <br />
                Don't have it? Grab Prowlarr from <code>prowlarr.com</code> (or your *arr stack).
                You point Prowlarr at your indexers, then point SoulSync at Prowlarr.
              </>
            }
          />
          <Field
            label="Prowlarr URL:"
            help={
              <>
                Full URL to your Prowlarr instance (e.g. <code>http://192.168.1.100:9696</code>).
              </>
            }
          >
            <input
              type="text"
              id="prowlarr-url"
              placeholder="http://localhost:9696"
              value={str(p.url)}
              onChange={(event) => set('url', event.target.value)}
            />
          </Field>
          <Field
            label="API Key:"
            help="Found in Prowlarr → Settings → General → Security → API Key."
          >
            <SecretInput
              id="prowlarr-api-key"
              placeholder="Your Prowlarr API key"
              value={str(p.api_key)}
              onChange={(value) => set('api_key', value)}
            />
          </Field>
          <Field
            label="Restrict to indexer IDs (optional):"
            help="Comma-separated Prowlarr indexer IDs. Leave blank to search every enabled indexer. Restrict when you have a private tracker you want to prioritise or a noisy public one to exclude."
          >
            <input
              type="text"
              id="prowlarr-indexer-ids"
              placeholder="e.g. 1,3,7 (blank = all enabled indexers)"
              value={str(p.indexer_ids)}
              onChange={(event) => set('indexer_ids', event.target.value)}
            />
          </Field>
          {/* #2061: the pacing was always there (core/prowlarr_throttle.py), with
              no way to turn it down for a strict tracker. ?? not ||: 0 is off */}
          <Field
            label="Seconds between searches:"
            help="The shortest wait between two searches sent to Prowlarr, music and video together. Default 2. Raise it if a tracker answers 429 Too Many Requests or Prowlarr marks an indexer unhealthy: a tracker that asks for 11 seconds wants 12 or more here. 0 turns the wait off."
          >
            <NumberField
              store={store}
              id="prowlarr-search-min-gap"
              min="0"
              max="600"
              step="0.5"
              placeholder="2"
              value={text(p.search_min_gap_seconds ?? 2)}
              save={(raw) => set('search_min_gap_seconds', float(raw, 2))}
            />
          </Field>
          <Field
            label="Searches per minute, at most:"
            help="A ceiling on top of the wait above, so a long wishlist run cannot keep up a fast pace for hours. Default 20. 0 turns the ceiling off. A manual search waits its turn behind background searches."
          >
            <NumberField
              store={store}
              id="prowlarr-max-searches-per-minute"
              min="0"
              max="600"
              step="1"
              placeholder="20"
              value={text(p.max_searches_per_minute ?? 20)}
              save={(raw) => set('max_searches_per_minute', integer(raw, 20))}
            />
          </Field>
          <Field label="Status:">
            <div className="form-actions" style={{ marginTop: '4px' }}>
              <button
                className="test-button"
                id="prowlarr-test-btn"
                onClick={() => void test.run(() => void loadIndexers())}
              >
                Test Connection
              </button>
              <button
                className="test-button"
                id="prowlarr-refresh-indexers-btn"
                onClick={() => void loadIndexers()}
                style={{ marginLeft: '6px' }}
              >
                Refresh Indexer List
              </button>
              <span
                id="prowlarr-connection-status"
                className="setting-help-text"
                style={statusStyle(test.status)}
              >
                {test.status.text}
              </span>
            </div>
          </Field>
          <Field label="Configured Indexers:">
            <div id="prowlarr-indexer-list" className="ind-indexer-list">
              {indexers}
            </div>
          </Field>
        </div>
      </div>
    </Panel>
  );
}

// ── the torrent client ──

const SEED_HINT = (
  <>
    Both at 0 = never remove (the old behavior). <strong>SoulSync</strong>: SS polls the client and
    removes when a goal is hit (your imported library copy is separate and never touched).{' '}
    <strong>Torrent client</strong>: SS writes the ratio/time limit into the client (arr-style) and
    lets it enforce — it shows up in the client's share-limit dialog and works even when SS is off.
  </>
);

export function TorrentPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const t = (settings.torrent_client || {}) as Record<string, unknown>;
  const d = (settings.download_source || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('torrent_client', key, value);
  const setSource = (key: string, value: unknown) => store.set('download_source', key, value);
  const test = useConnectionTest('torrent_client');

  const stall = d.torrent_stall_timeout_seconds;
  const minSeeders = d.torrent_min_seeders;

  return (
    <Panel id="torrent-client-settings-container">
      <StatusDot id="torrent-client-status-dot" dot={test.dot} />
      <div className="settings-group">
        <div className="api-service-frame">
          <FrameTitle
            title="Torrent Downloader"
            kind="torrent"
            help="Where SoulSync sends torrents once Prowlarr finds them. Pick the client you already use — only one can be active at a time. Add the URL of the client's WebUI, log in if it asks for credentials, and hit Test Connection."
          />
          <div className="form-group">
            <label>Client Type:</label>
            <select
              id="torrent-client-type"
              className="form-select"
              value={str(t.type, 'qbittorrent')}
              onChange={(event) => set('type', event.target.value || 'qbittorrent')}
            >
              <option value="qbittorrent">qBittorrent</option>
              <option value="transmission">Transmission</option>
              <option value="deluge">Deluge 2.x</option>
              <option value="aria2">Aria2 (RPC)</option>
            </select>
            <div className="setting-help-text" id="torrent-client-help">
              qBittorrent: WebUI port (default 8080). Transmission: RPC port (default 9091). Deluge:
              WebUI port (default 8112). Aria2: RPC port (default 6800) — leave Username blank and
              put your <code>--rpc-secret</code> in the Password field.
            </div>
          </div>
          <Field label="WebUI URL:">
            <input
              type="text"
              id="torrent-client-url"
              placeholder="http://localhost:8080"
              value={str(t.url)}
              onChange={(event) => set('url', event.target.value)}
            />
          </Field>
          <Field
            label="Username:"
            id="torrent-client-username-group"
            help="qBittorrent and Transmission use username + password. Deluge uses password only — paste it in the password field below."
          >
            <input
              type="text"
              id="torrent-client-username"
              placeholder="leave blank if WebUI doesn't require auth"
              value={str(t.username)}
              onChange={(event) => set('username', event.target.value)}
            />
          </Field>
          <Field label="Password:">
            <SecretInput
              id="torrent-client-password"
              placeholder="WebUI password"
              value={str(t.password)}
              onChange={(value) => set('password', value)}
            />
          </Field>
          <Field
            label="Category / Label:"
            help="SoulSync tags every torrent it adds with this label so it's easy to spot in your client. Deluge needs the Label plugin installed for this to stick."
          >
            <input
              type="text"
              id="torrent-client-category"
              placeholder="soulsync"
              value={str(t.category, 'soulsync')}
              // blank saves the default, as _cfgStr's fallback did
              onChange={(event) => set('category', event.target.value || 'soulsync')}
            />
          </Field>
          <Field
            label="Save Path (optional):"
            help={
              <>
                Override where the torrent client writes downloads. This path is on the{' '}
                <strong>torrent client's</strong> machine, not SoulSync's.
              </>
            }
          >
            <input
              type="text"
              id="torrent-client-save-path"
              placeholder="leave blank to use the client's default"
              value={str(t.save_path)}
              onChange={(event) => set('save_path', event.target.value)}
            />
          </Field>
          <div className="form-group">
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                id="torrent-client-start-torrents"
                checked={t.start_torrents === true}
                onChange={(event) => set('start_torrents', event.target.checked)}
              />
              Start the torrents SoulSync adds
            </label>
            <div className="setting-help-text">
              qBittorrent only. Turn this on if your qBittorrent is set to add torrents stopped, or
              to stop them once the metadata is in. SoulSync's torrents then start anyway, and
              everything other apps add keeps your qBittorrent setting.
            </div>
          </div>
          <Field
            label="Completed Downloads Path — in SoulSync (optional):"
            help={
              <>
                Where <strong>SoulSync</strong> can read finished torrents — its own in-container
                path. Set this when your client saves to a category folder (e.g. a "Music" category
                at <code>/data/downloads/music</code>) that's mounted here at a different path.
                SoulSync finds the release by name under this folder. Blank = use the Soulseek
                download/transfer folders.
              </>
            }
          >
            <input
              type="text"
              id="torrent-download-path"
              placeholder="leave blank for the shared-volume default"
              value={str(d.torrent_download_path)}
              onChange={(event) => setSource('torrent_download_path', event.target.value)}
            />
          </Field>
          <Field
            label="Minimum seeders:"
            help={
              <>
                Skip releases the indexer reports as having fewer seeders than this, instead of
                queueing something nobody is serving and waiting for it to time out. Releases that
                report <strong>no</strong> seeder count (usenet, and indexers that omit the field)
                are never skipped. <strong>0 disables</strong>.
              </>
            }
          >
            <NumberField
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
          </Field>
          <Field
            label="Stalled torrent timeout (minutes):"
            help={
              <>
                Give up on a torrent that makes <strong>zero download progress</strong> for this
                long — a dead magnet stuck on "downloading metadata", or a swarm with no seeders.
                Without this, a dud torrent ties up a download slot for the full 6-hour limit.{' '}
                <strong>0 disables</strong> (wait the full limit).
              </>
            }
          >
            <NumberField
              store={store}
              id="torrent-stall-timeout"
              min="0"
              step="1"
              placeholder="10"
              // stored in seconds, shown in minutes
              value={text(
                stall === undefined || stall === null ? 10 : Math.round(Number(stall) / 60),
              )}
              save={(raw) => {
                const m = parseInt(raw, 10);
                setSource(
                  'torrent_stall_timeout_seconds',
                  (Number.isFinite(m) && m >= 0 ? m : 10) * 60,
                );
              }}
            />
          </Field>
          <Field
            label="When a torrent stalls:"
            help="Abandon frees the slot so the next source can try. Pause keeps the torrent in your client so you can inspect or resume it manually."
          >
            <select
              id="torrent-stall-action"
              className="form-select"
              value={str(d.torrent_stall_action, 'abandon')}
              onChange={(event) =>
                setSource('torrent_stall_action', event.target.value || 'abandon')
              }
            >
              <option value="abandon">
                Abandon — remove it &amp; its partial data, fail the download
              </option>
              <option value="pause">Pause — pause it in the client, leave it for me</option>
            </select>
          </Field>
          {/* seeding lifecycle, torrent grabs only. Music twin of the video side */}
          <div className="form-group" id="music-seeding-goals">
            <label>Seeding goals (torrents):</label>
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="settings-hint">Ratio</span>
              <NumberField
                store={store}
                id="music-seed-ratio"
                min="0"
                step="0.1"
                style={{ maxWidth: '90px' }}
                placeholder="0"
                value={text(t.seed_ratio_goal ?? 0)}
                save={(raw) => set('seed_ratio_goal', float(raw, 0))}
              />
              <span className="settings-hint">or hours seeded</span>
              <NumberField
                store={store}
                id="music-seed-hours"
                min="0"
                step="1"
                style={{ maxWidth: '90px' }}
                placeholder="0"
                value={text(t.seed_time_goal_hours ?? 0)}
                save={(raw) => set('seed_time_goal_hours', integer(raw, 0))}
              />
              <label
                style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12.5px' }}
              >
                <input
                  type="checkbox"
                  id="music-seed-remove-data"
                  checked={t.seed_remove_data !== false}
                  onChange={(event) => set('seed_remove_data', event.target.checked)}
                />{' '}
                also delete the client's copy
              </label>
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' }}>
              <span className="settings-hint">Enforced by</span>
              <select
                id="music-seed-mode"
                className="form-select"
                style={{ maxWidth: '260px' }}
                value={str(t.seed_mode, 'soulsync')}
                onChange={(event) => set('seed_mode', event.target.value || 'soulsync')}
              >
                <option value="soulsync">SoulSync (watches the client + removes)</option>
                <option value="client">Torrent client (writes its own seed limit)</option>
              </select>
            </div>
            <small className="settings-hint">{SEED_HINT}</small>
          </div>
          <Field label="Status:">
            <div className="form-actions" style={{ marginTop: '4px' }}>
              <button
                className="test-button"
                id="torrent-client-test-btn"
                onClick={() => void test.run()}
              >
                Test Connection
              </button>
              <span
                id="torrent-client-connection-status"
                className="setting-help-text"
                style={statusStyle(test.status)}
              >
                {test.status.text}
              </span>
            </div>
          </Field>
        </div>
      </div>
      {/* the video side's seeding goals (video-source-blocks.tsx) */}
      <div>
        <VideoSeedingGoals />
      </div>
    </Panel>
  );
}

// ── the usenet client ──

export function UsenetPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const u = (settings.usenet_client || {}) as Record<string, unknown>;
  const d = (settings.download_source || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('usenet_client', key, value);
  const test = useConnectionTest('usenet_client');
  const type = str(u.type, 'sabnzbd');
  const frame = useRef<HTMLDivElement>(null);

  return (
    <Panel id="usenet-client-settings-container">
      <StatusDot id="usenet-client-status-dot" dot={test.dot} />
      <div className="settings-group">
        <div className="api-service-frame" ref={frame}>
          <FrameTitle
            title="Usenet Downloader"
            kind="usenet"
            help="Where SoulSync sends NZBs once Prowlarr finds them. Pick one usenet downloader. SABnzbd uses an API key for auth; NZBGet uses a username + password."
          />
          <Field
            label="Client Type:"
            help="SABnzbd: default WebUI port 8080. NZBGet: default WebUI port 6789."
          >
            <select
              id="usenet-client-type"
              className="form-select"
              value={type}
              onChange={(event) => set('type', event.target.value || 'sabnzbd')}
            >
              <option value="sabnzbd">SABnzbd</option>
              <option value="nzbget">NZBGet</option>
            </select>
          </Field>
          <Field label="WebUI URL:">
            <input
              type="text"
              id="usenet-client-url"
              placeholder="http://localhost:8080"
              value={str(u.url)}
              onChange={(event) => set('url', event.target.value)}
            />
          </Field>
          {/* SABnzbd authenticates with a key, NZBGet with a login */}
          <Field
            label="API Key:"
            id="usenet-apikey-group"
            style={{ display: type === 'sabnzbd' ? '' : 'none' }}
            help="SABnzbd → Config → General → API Key. Used by SABnzbd only."
          >
            <SecretInput
              id="usenet-client-api-key"
              placeholder="SABnzbd API key"
              value={str(u.api_key)}
              onChange={(value) => set('api_key', value)}
            />
          </Field>
          <Field
            label="Username:"
            id="usenet-username-group"
            style={{ display: type === 'sabnzbd' ? 'none' : '' }}
          >
            <input
              type="text"
              id="usenet-client-username"
              placeholder="NZBGet WebUI username"
              value={str(u.username)}
              onChange={(event) => set('username', event.target.value)}
            />
          </Field>
          <Field
            label="Password:"
            id="usenet-password-group"
            style={{ display: type === 'sabnzbd' ? 'none' : '' }}
          >
            <SecretInput
              id="usenet-client-password"
              placeholder="NZBGet WebUI password"
              value={str(u.password)}
              onChange={(value) => set('password', value)}
            />
          </Field>
          <Field
            label="Category / Label:"
            help="SoulSync tags every NZB with this category so it ends up in a predictable post-processing folder."
          >
            <input
              type="text"
              id="usenet-client-category"
              placeholder="soulsync"
              value={str(u.category, 'soulsync')}
              onChange={(event) => set('category', event.target.value || 'soulsync')}
            />
          </Field>
          <Field
            label="Completed Downloads Path — in SoulSync (optional):"
            help={
              <>
                Where <strong>SoulSync</strong> can read finished NZB downloads — its own
                in-container path. Set this when SABnzbd/NZBGet writes that category to a folder
                (e.g. <code>/data/downloads/music</code>) mounted here at a different path. SoulSync
                finds the release by name under this folder. Blank = use the Soulseek
                download/transfer folders.
              </>
            }
          >
            <input
              type="text"
              id="usenet-download-path"
              placeholder="leave blank for the shared-volume default"
              value={str(d.usenet_download_path)}
              onChange={(event) =>
                store.set('download_source', 'usenet_download_path', event.target.value)
              }
            />
          </Field>
          <Field label="Status:">
            <div className="form-actions" style={{ marginTop: '4px' }}>
              <button
                className="test-button"
                id="usenet-client-test-btn"
                onClick={() => void test.run()}
              >
                Test Connection
              </button>
              <span
                id="usenet-client-connection-status"
                className="setting-help-text"
                style={statusStyle(test.status)}
              >
                {test.status.text}
              </span>
            </div>
          </Field>
          <Field
            label="When a usenet download has imported (music):"
            id="music-usenet-remove-completed-group"
            help="Music grabs copy the audio out, so without this the whole release stays in your client's completed folder. Only downloads SoulSync has imported are touched: an album goes once every track in it imported, and a failed or unmatched download stays for you."
          >
            <select
              id="music-usenet-remove-completed"
              className="form-select"
              value={str(u.remove_completed, 'off')}
              onChange={(event) => set('remove_completed', event.target.value || 'off')}
            >
              <option value="off">Leave it in the client</option>
              <option value="history">Remove it from the client's history</option>
              <option value="files">Remove it and delete what's left in its folder</option>
            </select>
          </Field>
          {/* the video side's two blocks follow the music ones (see the top) */}
          <VideoUsenetBlocks />
        </div>
      </div>
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

/** a setting-row: a tick box and its "i", the help under it, then its line */
function CheckRow({
  id,
  label,
  checked,
  onChange,
  help,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help: ReactNode;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      <div className="setting-row">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id={id}
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
          />
          {label}
        </label>
        {bits.icon}
      </div>
      <div className="setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

interface YtdlpState {
  installed: string;
  installedColor: string;
  latest: string;
  latestColor: string;
  badge: { hidden: boolean; title: string };
}

const YTDLP_LOADING: YtdlpState = {
  installed: 'Loading...',
  installedColor: '',
  latest: 'Loading...',
  latestColor: '',
  badge: { hidden: true, title: '' },
};

const AMBER = '#ffb300';

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
  const [update, setUpdate] = useState<{ text: string; color: string; background: string } | null>(
    null,
  );
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
          ? `${text(data.installed)} running — ${text(data.on_disk)} installed, restart to finish`
          : text(data.installed) || 'Not installed',
        installedColor: pending ? AMBER : '',
        // a PyPI outage is not "up to date"
        latest:
          text(data.latest) ||
          (data.lookup_error ? "Couldn't check — no connection to PyPI" : 'Unknown'),
        latestColor: data.behind ? AMBER : '',
        badge: {
          hidden: !(data.behind || pending),
          title: pending
            ? 'yt-dlp is updated on disk — restart SoulSync to use it'
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
      text: 'Installing the newest ' + channel + ' build — this can take a minute...',
      color: 'rgba(255,255,255,0.6)',
      background: 'rgba(255,255,255,0.04)',
    });
    try {
      const data = await json('/api/ytdlp/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel }),
      });
      setUpdate({
        text: text(data.message) || (data.success ? 'Done.' : 'Update failed.'),
        color: data.success ? (data.restart_required ? AMBER : OK) : '#ff5252',
        background: 'rgba(255,255,255,0.04)',
      });
      // pip's own words: each failure needs a different fix, and they say which
      if (data.detail) setDetail(text(data.detail));
      if (data.success) {
        toast(
          data.restart_required
            ? 'yt-dlp updated — restart SoulSync to use it'
            : text(data.message) || 'Already up to date',
          data.restart_required ? 'info' : 'success',
        );
      } else {
        toast('yt-dlp update failed — see Settings for details', 'error');
      }
      void loadYtdlp();
    } catch (error) {
      setUpdate({
        text: 'Update request failed: ' + String(error),
        color: '#ff5252',
        background: 'rgba(255,255,255,0.04)',
      });
      toast('yt-dlp update failed', 'error');
    } finally {
      setUpdating(false);
    }
  }

  // each settings load forgets a half-pasted cookie file, as the page reload did
  useEffect(() => setPaste(''), [loads]);

  const abe = windows && ABE_BROWSERS.includes(browser);
  const browserLabel = COOKIE_BROWSERS.find(([v]) => v === browser)?.[1] || browser;
  const channelHelp = useHelp();
  const channelText =
    "YouTube changes how it serves video far more often than yt-dlp cuts a stable release, so nightly is what actually keeps downloads working. Stable is there if you'd rather move slowly.";
  const channelBits = helpBits(channelText, channelHelp);

  return (
    <Panel id="youtube-settings-container" model={model}>
      <div className="form-group">
        <label>YouTube Browser Cookies:</label>
        <select
          id="youtube-cookies-browser"
          className="form-select"
          value={browser}
          onChange={(event) => set('cookies_browser', event.target.value)}
        >
          {COOKIE_BROWSERS.map(([value, label]) => (
            <option key={value} value={value}>
              {/* marked, not disabled: a selected disabled option reads as ''
                  and the next save would wipe the setting */}
              {windows && ABE_BROWSERS.includes(value)
                ? `${label} — not supported on Windows`
                : label}
            </option>
          ))}
        </select>
        <div
          className="setting-warning"
          id="youtube-cookie-abe-warning"
          role="status"
          hidden={!abe}
        >
          {abe
            ? `${browserLabel} seals its cookies with App-Bound Encryption on Windows, which yt-dlp cannot read. This will not work. Choose "Paste cookies.txt" instead, or "None" if you only download public videos.`
            : null}
        </div>
        <div className="setting-help-text">
          Only needed for private playlists, your Liked Music, age-gated videos and Premium audio
          qualities &mdash; public videos download fine with <strong>None</strong>. Reading a
          browser only works when that browser is on the same machine as SoulSync, so on a server or
          in Docker choose <strong>Paste cookies.txt</strong>. On Windows, Chrome and the other
          Chromium browsers seal their cookies in a way yt-dlp cannot read at all &mdash; use{' '}
          <strong>Firefox</strong> or <strong>Paste cookies.txt</strong>.
        </div>
      </div>
      <div
        className="form-group"
        id="youtube-cookies-paste-group"
        style={{ display: browser === 'custom' ? '' : 'none' }}
      >
        <label>Paste cookies.txt:</label>
        <textarea
          id="youtube-cookies-paste"
          className="form-input"
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
        <div className="setting-help-text">
          For server/Docker installs with no local browser. Paste a Netscape/Mozilla{' '}
          <code>cookies.txt</code>. Export with a "Get cookies.txt LOCALLY" extension and paste the
          whole file &mdash; the <code># Netscape HTTP Cookie File</code> header alone is not
          enough. Required for private playlists like your <strong>Liked Music</strong>, and for
          Premium audio itags. Cookies expire periodically &mdash; re-paste if YouTube stops
          working.
        </div>
      </div>
      <div className="form-group">
        <label>YouTube Download Delay:</label>
        <NumberField
          store={store}
          id="youtube-download-delay"
          min="0"
          max="30"
          step="1"
          placeholder="3"
          value={text(y.download_delay ?? 3)}
          save={(raw) => set('download_delay', parseInt(raw, 10) || 3)}
        />
        <div className="setting-help-text">
          Seconds between YouTube downloads to avoid bot detection. Default: 3
        </div>
      </div>
      <CheckRow
        id="youtube-album-bundle"
        label="Download whole albums from YouTube Music"
        // #1851: it lives in download_source because the album flow reads it
        checked={d.youtube_album_bundle === true}
        onChange={(checked) => store.set('download_source', 'youtube_album_bundle', checked)}
        help="For an album, find the album itself on YouTube Music and download its playlist, instead of searching for each song. Helps with albums whose songs never show up in a song search. The artist and album title must both match; if they don't, SoulSync searches track by track as usual. Some albums need YouTube Music Premium cookies. Off by default."
      />
      <CheckRow
        id="youtube-transcode"
        label="Re-encode YouTube audio"
        checked={transcode}
        onChange={(checked) => set('transcode', checked)}
        help="YouTube serves Opus or AAC, not FLAC or MP3. With this on, the quality list must include the converted format (default MP3 320). With it off, add Opus or AAC to the list, or turn Fallback on."
      />
      <div id="youtube-transcode-options" style={{ display: transcode ? 'block' : 'none' }}>
        <div className="form-group">
          <label>Codec:</label>
          <select
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
          >
            <option value="mp3">MP3</option>
            <option value="opus">Opus</option>
            <option value="aac">AAC (M4A)</option>
          </select>
        </div>
        <div className="form-group">
          <label>Bitrate:</label>
          <select
            id="youtube-transcode-bitrate"
            value={bitrate}
            onChange={(event) => set('transcode_bitrate', event.target.value || '320')}
          >
            <option value="320" disabled={codec === 'opus'}>
              320 kbps
            </option>
            <option value="256">256 kbps</option>
            <option value="192">192 kbps</option>
            <option value="128">128 kbps</option>
          </select>
        </div>
      </div>
      {/* the yt-dlp updater lives with YouTube: it is what breaks YouTube
          downloads when it goes stale */}
      <h4 className="settings-subheading">
        yt-dlp{' '}
        <span
          id="ytdlp-hint-badge"
          hidden={ytdlp.badge.hidden}
          style={{ color: AMBER }}
          title={ytdlp.badge.title || undefined}
        >
          &#9679;
        </span>
      </h4>
      <div className="settings-group">
        <div className="form-group">
          <label>Installed:</label>
          <span
            id="ytdlp-installed"
            className="readonly-field"
            style={ytdlp.installedColor ? { color: ytdlp.installedColor } : undefined}
          >
            {ytdlp.installed}
          </span>
        </div>
        <div className="form-group">
          <label>Newest available:</label>
          <span
            id="ytdlp-latest"
            className="readonly-field"
            style={ytdlp.latestColor ? { color: ytdlp.latestColor } : undefined}
          >
            {ytdlp.latest}
          </span>
        </div>
        <div className="form-group">
          <label>Release channel:</label>
          <select
            id="ytdlp-channel"
            value={channel}
            onChange={(event) => {
              setChannel(event.target.value);
              void loadYtdlp(event.target.value);
            }}
          >
            <option value="nightly">Nightly (recommended)</option>
            <option value="stable">Stable</option>
          </select>
          {channelBits.icon}
          {channelBits.line}
        </div>
        <div className="help-text setting-help-body" hidden={!channelHelp.open}>
          {channelText}
        </div>
        <div className="form-actions" style={{ flexWrap: 'wrap' }}>
          <button
            className="test-button"
            id="ytdlp-update-btn"
            disabled={updating}
            onClick={() => void runUpdate()}
          >
            {updating ? 'Updating...' : 'Update yt-dlp'}
          </button>
        </div>
        <div className="help-text">
          When YouTube downloads start failing with <strong>403 Forbidden</strong>, an out-of-date
          yt-dlp is nearly always the cause.{' '}
          <strong>An update only takes effect after you restart SoulSync</strong> &mdash; the
          running process keeps using the version it loaded at startup.
        </div>
        <div
          id="ytdlp-status"
          style={{
            display: update ? 'block' : 'none',
            marginTop: '8px',
            padding: '8px 12px',
            borderRadius: '8px',
            fontSize: '0.85em',
            ...(update ? { color: update.color, background: update.background } : {}),
          }}
        >
          {update?.text}
        </div>
        <pre
          id="ytdlp-detail"
          style={{
            display: detail ? 'block' : 'none',
            marginTop: '8px',
            maxHeight: '220px',
            overflow: 'auto',
            fontSize: '0.75em',
            whiteSpace: 'pre-wrap',
          }}
        >
          {detail}
        </pre>
      </div>
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
