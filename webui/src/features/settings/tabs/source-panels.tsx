/**
 * The Sources tab's config panels, ported one at a time from index.html and
 * settings.js (SETTINGS_PORT.md, step 7c on). Same markup, classes and ids.
 *
 * A panel is parked, hidden, in #source-config-home until its tile opens it.
 * The old modal moved the panel's node into its body; a React panel portals
 * itself there instead while it is the open one, and is marked
 * data-react-panel so the modal and the page autosave leave it alone.
 */

import { type ReactNode, useEffect, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { REDACTED_SECRET_SENTINEL, SecretInput } from './parts';
import { SOURCE_CONFIG_ID_BY_SRC, type SourcesModel, sourcesModel } from './sources-model';

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

const text = (value: unknown) =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : '';

/** a panel: in the modal while open, in its hidden home otherwise */
function Panel({
  id,
  model = sourcesModel,
  children,
}: {
  id: string;
  model?: SourcesModel;
  children: ReactNode;
}) {
  const open = useSyncExternalStore(model.subscribe, () => model.getState().open);
  const node = (
    <div id={id} className="src-config-panel" data-react-panel="">
      {children}
    </div>
  );
  const body =
    open && SOURCE_CONFIG_ID_BY_SRC[open] === id ? document.getElementById('src-modal-body') : null;
  return body ? createPortal(node, body) : node;
}

/** a status line next to a Test Connection button */
interface Status {
  text: string;
  color?: string;
}

const CHECKING: Status = { text: 'Checking...', color: '#aaa' };
const OK = '#4caf50';
const BAD = '#f44336';
const WARN = '#ff9800';

function TestRow({
  label,
  buttonId,
  statusId,
  status,
  onTest,
  children,
}: {
  label: string;
  buttonId: string;
  statusId: string;
  status: Status;
  onTest: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="form-group">
      <label>{label}</label>
      <div className="form-actions" style={{ marginTop: '4px' }}>
        <button className="test-button" id={buttonId} onClick={onTest}>
          Test Connection
        </button>
        <span
          id={statusId}
          className="setting-help-text"
          style={{ marginLeft: '8px', ...(status.color ? { color: status.color } : {}) }}
        >
          {status.text}
        </span>
      </div>
      {children}
    </div>
  );
}

async function json(url: string, init?: RequestInit) {
  const response = await fetch(url, init);
  return (await response.json()) as Record<string, unknown>;
}

// ── Deezer ──

export function DeezerPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const arl = text(settings.deezer_download?.arl);
  const [status, setStatus] = useState<Status>({ text: '' });

  const change = (value: string) => {
    store.set('deezer_download', 'arl', value);
    // the Connections tab has the same token in its Deezer card (still
    // vanilla): the two fields mirror each other, as before
    const conn = document.getElementById('deezer-connection-arl') as HTMLInputElement | null;
    if (conn) conn.value = value;
  };

  async function test() {
    setStatus(CHECKING);
    try {
      // an untouched field holds the mask: send empty so the server tests
      // the SAVED token, not the mask (#870)
      const sent = arl === REDACTED_SECRET_SENTINEL ? '' : arl;
      const data = await json('/api/deezer-download/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arl: sent }),
      });
      if (data.success) {
        setStatus({
          text: `Connected as ${text(data.user) || 'Unknown'} (${text(data.tier) || 'Free'})`,
          color: OK,
        });
      } else {
        setStatus({ text: text(data.error) || 'Authentication failed', color: BAD });
      }
    } catch {
      setStatus({ text: 'Connection error', color: BAD });
    }
  }

  return (
    <Panel id="deezer-download-settings-container">
      <div className="form-group">
        <label>Deezer ARL Token:</label>
        <SecretInput
          id="deezer-download-arl"
          className="form-input"
          placeholder="Paste your ARL cookie token here"
          value={arl}
          onChange={change}
        />
        <div className="setting-help-text">
          Your ARL token authenticates with Deezer. To get it: log into{' '}
          <a
            href="https://www.deezer.com"
            target="_blank"
            style={{ color: 'rgb(var(--accent-rgb))' }}
          >
            deezer.com
          </a>{' '}
          → open browser DevTools (F12) → Application tab → Cookies → copy the <code>arl</code>{' '}
          value.
        </div>
      </div>
      <TestRow
        label="Deezer Status:"
        buttonId="deezer-download-test-btn"
        statusId="deezer-download-status"
        status={status}
        onTest={() => void test()}
      />
    </Panel>
  );
}

// ── SoundCloud ──

/** settings.js testSoundcloudConnection; also the probe when its modal opens */
export async function soundcloudStatus(): Promise<Status> {
  try {
    const data = await json('/api/soundcloud/status');
    if (data.available && data.reachable) return { text: 'Connected (anonymous)', color: OK };
    // the client is up but the live probe failed: an outage or a passing
    // yt-dlp parse error
    if (data.available) return { text: 'Reachable check failed — try again', color: WARN };
    return { text: text(data.error) || 'Unavailable', color: BAD };
  } catch {
    return { text: 'Connection error', color: BAD };
  }
}

export function SoundcloudPanel({ model = sourcesModel }: { model?: SourcesModel }) {
  const [status, setStatus] = useState<Status>({ text: '' });
  const open = useSyncExternalStore(model.subscribe, () => model.getState().open);

  async function test() {
    setStatus(CHECKING);
    setStatus(await soundcloudStatus());
  }

  // opening SoundCloud's modal checks it, as probeSourceOnOpen did
  useEffect(() => {
    if (open === 'soundcloud') void test();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Panel id="soundcloud-download-settings-container" model={model}>
      <div className="form-group">
        <div className="setting-help-text">
          SoundCloud downloads run anonymously — no account required. Use this for DJ mixes,
          remixes, and tracks that aren't on Spotify/Tidal/Deezer.
          <br />
          <br />
          <strong>Quality:</strong> 128 kbps MP3 / AAC depending on the upload (anonymous tier).
          SoundCloud doesn't expose lossless audio to anyone.
        </div>
      </div>
      <TestRow
        label="SoundCloud Status:"
        buttonId="soundcloud-test-btn"
        statusId="soundcloud-connection-status"
        status={status}
        onTest={() => void test()}
      />
    </Panel>
  );
}

// ── Lidarr ──

export function LidarrPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const [status, setStatus] = useState<Status>({ text: '' });

  async function test() {
    setStatus(CHECKING);
    try {
      // save first, so the server tests the URL and key on screen
      await (window as Window & { saveSettings?: () => Promise<void> }).saveSettings?.();
      const data = await json('/api/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'lidarr' }),
      });
      if (data.success) setStatus({ text: 'Connected', color: OK });
      else setStatus({ text: text(data.error) || 'Connection failed', color: BAD });
    } catch {
      setStatus({ text: 'Connection error', color: BAD });
    }
  }

  return (
    <Panel id="lidarr-download-settings-container">
      <div className="form-group">
        <label>Lidarr URL:</label>
        <input
          type="text"
          id="lidarr-url"
          placeholder="http://localhost:8686"
          value={text(settings.lidarr_download?.url)}
          onChange={(event) => store.set('lidarr_download', 'url', event.target.value)}
        />
        <div className="setting-help-text">
          Full URL to your Lidarr instance (e.g. http://192.168.1.100:8686)
        </div>
      </div>
      <div className="form-group">
        <label>API Key:</label>
        <SecretInput
          id="lidarr-api-key"
          placeholder="Your Lidarr API key"
          value={text(settings.lidarr_download?.api_key)}
          onChange={(value) => store.set('lidarr_download', 'api_key', value)}
        />
        <div className="setting-help-text">
          Found in Lidarr → Settings → General → Security → API Key
        </div>
      </div>
      <TestRow
        label="Lidarr Status:"
        buttonId="lidarr-test-btn"
        statusId="lidarr-connection-status"
        status={status}
        onTest={() => void test()}
      >
        <div className="setting-help-text" style={{ marginTop: '6px' }}>
          Uses Lidarr's indexers and download clients (Usenet/torrent) for album downloads.
          <br />
          <br />
          <strong>Best for full-album grabs.</strong> Lidarr is album-first by design — track-name
          searches usually return nothing useful, so in hybrid mode Lidarr is mostly a no-op for
          playlists and falls through to your other sources. Use Lidarr Only when you specifically
          want to grab whole albums via your indexers.
        </div>
      </TestRow>
    </Panel>
  );
}

/** the panels of one slot in #source-config-home, by part */
export function SourcePanels({ store, part }: SettingsTabProps) {
  if (part === 'deezer-soundcloud-lidarr') {
    return (
      <>
        <DeezerPanel store={store} />
        <SoundcloudPanel />
        <LidarrPanel store={store} />
      </>
    );
  }
  return null;
}
