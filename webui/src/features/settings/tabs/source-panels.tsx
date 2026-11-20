/**
 * The Sources tab's config panels, ported one at a time from index.html and
 * settings.js (SETTINGS_PORT.md, step 7c on). Same markup, classes and ids.
 *
 * A panel is parked, hidden, in #source-config-home until its tile opens it.
 * The old modal moved the panel's node into its body; a React panel portals
 * itself there instead while it is the open one, and is marked
 * data-react-panel so the modal and the page autosave leave it alone.
 */

import type React from 'react';

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

import type { SettingsTabProps } from '../host';

import { type SettingsStore, settingsStore } from '../settings-store';
import { checkQobuzAuthStatus, onQobuzAuth, type QobuzAuthState } from './connections';
import { REDACTED_SECRET_SENTINEL, SecretInput, useDraft } from './parts';
import { SOURCE_CONFIG_ID_BY_SRC, type SourcesModel, sourcesModel } from './sources-model';

export function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

export const text = (value: unknown) =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : '';

/** a panel: in the modal while open, in its hidden home otherwise */
export function Panel({
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
export interface Status {
  text: string;
  color?: string;
}

export const CHECKING: Status = { text: 'Checking...', color: '#aaa' };
export const OK = '#4caf50';
export const BAD = '#f44336';
export const WARN = '#ff9800';

export function TestRow({
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

export async function json(url: string, init?: RequestInit) {
  const response = await fetch(url, init);
  return (await response.json()) as Record<string, unknown>;
}

// ── Deezer ──

export function DeezerPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const arl = text(settings.deezer_download?.arl);
  const [status, setStatus] = useState<Status>({ text: '' });

  // the Connections tab's Deezer card holds the same token (the same key)
  const change = (value: string) => store.set('deezer_download', 'arl', value);

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

export function SoundcloudPanel({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const settings = useSettings(store);
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
      {/* #2116: a signed-in free account gets the original file where the
          uploader turned downloads on */}
      <div className="form-group">
        <label>SoundCloud OAuth Token (optional):</label>
        <SecretInput
          id="soundcloud-download-oauth-token"
          className="form-input"
          placeholder="Paste your oauth_token cookie here"
          value={text(settings.soundcloud_download?.oauth_token)}
          onChange={(value) => store.set('soundcloud_download', 'oauth_token', value)}
        />
        <div className="setting-help-text">
          With a free SoundCloud account signed in, tracks whose uploader turned on the free
          Download button come as the original file instead of the 128 kbps stream. Everything else
          still uses the stream. To get it: log into soundcloud.com, open DevTools (F12),
          Application, Cookies, and copy the <code>oauth_token</code> value.
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
      <div className="form-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id="lidarr-albums-only"
            checked={settings.lidarr_download?.albums_only === true}
            onChange={(event) => store.set('lidarr_download', 'albums_only', event.target.checked)}
          />
          Whole albums only
        </label>
        <div className="setting-help-text">
          Lidarr downloads only full-album requests. Single tracks and playlists go to the next
          source in hybrid mode (e.g. Soulseek). Off: Lidarr also tries single tracks, grabbing the
          whole album to keep one track.
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

export const toast = (message: string, type: string) => window.showToast?.(message, type);

/** opening this source's modal runs its check, as probeSourceOnOpen did */
export function useOnOpen(model: SourcesModel, id: string, check: () => void) {
  const open = useSyncExternalStore(model.subscribe, () => model.getState().open);
  useEffect(() => {
    if (open === id) check();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

// ── Tidal ──

export function TidalPanel({ model = sourcesModel }: { model?: SourcesModel }) {
  const [status, setStatus] = useState<Status>({ text: '' });
  const [button, setButton] = useState({ text: 'Link Tidal Account', disabled: false });
  const [code, setCode] = useState<{ uri: string; code: string } | null>(null);
  // the device link's poll runs until it completes or fails, Settings open or
  // not: the page's was never torn down, and its "linked" toast reached the
  // user wherever they were
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  async function check() {
    try {
      const data = await json('/api/tidal/download/auth/status');
      // the label only: a link still waiting keeps its button off
      // (checkTidalDownloadAuthStatus never touched it)
      if (data.authenticated) {
        setStatus({ text: 'Authenticated', color: OK });
        setButton((b) => ({ ...b, text: 'Re-link Tidal Account' }));
      } else {
        setStatus({ text: 'Not authenticated', color: WARN });
        setButton((b) => ({ ...b, text: 'Link Tidal Account' }));
      }
    } catch {
      setStatus((s) => ({ ...s, text: '' }));
    }
  }
  useOnOpen(model, 'tidal', () => void check());

  async function start() {
    setButton({ text: 'Starting...', disabled: true });
    setStatus((s) => ({ ...s, text: '' }));
    try {
      const response = await fetch('/api/tidal/download/auth/start', { method: 'POST' });
      const data = (await response.json()) as Record<string, unknown>;
      if (!response.ok || !data.success)
        throw new Error(text(data.error) || 'Failed to start auth');
      // the link and code to enter there
      setCode({ uri: text(data.verification_uri), code: text(data.user_code) });
      setButton({ text: 'Waiting for approval...', disabled: true });
      setStatus({ text: 'Waiting...', color: WARN });
      if (poll.current) clearInterval(poll.current);
      poll.current = setInterval(() => {
        void (async () => {
          try {
            const done = await json('/api/tidal/download/auth/check');
            if (done.status === 'completed') {
              if (poll.current) clearInterval(poll.current);
              poll.current = null;
              setCode(null);
              setStatus({ text: 'Authenticated', color: OK });
              setButton({ text: 'Re-link Tidal Account', disabled: false });
              toast('Tidal download account linked successfully', 'success');
            } else if (done.status === 'error') {
              if (poll.current) clearInterval(poll.current);
              poll.current = null;
              setCode(null);
              setStatus({ text: 'Auth failed', color: BAD });
              setButton({ text: 'Link Tidal Account', disabled: false });
              toast('Tidal auth failed: ' + (text(done.message) || 'Unknown error'), 'error');
            }
            // pending: keep polling
          } catch (error) {
            console.error('Tidal auth poll error:', error);
          }
        })();
      }, 3000);
    } catch (error) {
      console.error('Tidal download auth error:', error);
      toast(
        'Failed to start Tidal auth: ' + (error instanceof Error ? error.message : String(error)),
        'error',
      );
      setButton({ text: 'Link Tidal Account', disabled: false });
      setCode(null);
    }
  }

  return (
    <Panel id="tidal-download-settings-container" model={model}>
      <div className="form-group">
        <label>Tidal Download Auth:</label>
        <div className="form-actions" style={{ marginTop: '4px' }}>
          <button
            className="auth-button"
            id="tidal-download-auth-btn"
            disabled={button.disabled}
            onClick={() => void start()}
          >
            {button.text}
          </button>
          <span
            id="tidal-download-auth-status"
            className="setting-help-text"
            style={{ marginLeft: '8px', ...(status.color ? { color: status.color } : {}) }}
          >
            {status.text}
          </span>
        </div>
        <div
          id="tidal-download-auth-code"
          style={{ display: code ? 'block' : 'none', marginTop: '8px' }}
          className="setting-help-text"
        >
          {code ? (
            <>
              Go to{' '}
              <a href={code.uri} target="_blank" style={{ color: 'rgb(var(--accent-rgb))' }}>
                {code.uri}
              </a>{' '}
              and enter code: <strong>{code.code}</strong>
            </>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

// ── Qobuz ──

/** what checkQobuzAuthStatus last heard (connections.ts): the Connections
 *  tab's Qobuz card hears it too */
type QobuzAuth = QobuzAuthState;

const QOBUZ_ERR = '#ff5555';

const refreshQobuz = () => void checkQobuzAuthStatus();

export function QobuzPanel() {
  const [auth, setAuth] = useState<QobuzAuth | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [login, setLogin] = useState({ busy: false, status: { text: '' } as Status });
  const [byToken, setByToken] = useState({ busy: false, status: { text: '' } as Status });

  useEffect(() => onQobuzAuth(setAuth), []);

  async function connect() {
    if (!email.trim() || !password) {
      toast('Please enter your Qobuz email and password', 'warning');
      return;
    }
    setLogin({ busy: true, status: { text: '' } });
    let status: Status = { text: '' };
    try {
      const data = await json('/api/qobuz/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (data.success) {
        toast('Qobuz connected successfully!', 'success');
        setPassword('');
        refreshQobuz();
      } else {
        status = { text: text(data.error) || 'Login failed', color: QOBUZ_ERR };
        toast(text(data.error) || 'Qobuz login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz login error:', error);
      status = { text: 'Connection error', color: QOBUZ_ERR };
      toast('Failed to connect to Qobuz', 'error');
    }
    setLogin({ busy: false, status });
  }

  async function connectToken() {
    if (!token.trim()) {
      toast('Please paste your Qobuz auth token', 'warning');
      return;
    }
    setByToken({ busy: true, status: { text: '' } });
    let status: Status = { text: '' };
    try {
      const data = await json('/api/qobuz/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token.trim() }),
      });
      if (data.success) {
        toast('Qobuz connected via token!', 'success');
        setToken('');
        refreshQobuz();
      } else {
        status = { text: text(data.error) || 'Token login failed', color: QOBUZ_ERR };
        toast(text(data.error) || 'Qobuz token login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz token login error:', error);
      status = { text: 'Connection error', color: QOBUZ_ERR };
      toast('Failed to connect to Qobuz', 'error');
    }
    setByToken({ busy: false, status });
  }

  async function disconnect() {
    try {
      await fetch('/api/qobuz/auth/logout', { method: 'POST' });
      toast('Qobuz disconnected', 'success');
      refreshQobuz();
    } catch (error) {
      console.error('Qobuz logout error:', error);
    }
  }

  const user = auth?.user || {};
  const loggedIn = !!auth?.authenticated;
  const statusStyle = (status: Status) => ({
    marginLeft: '8px',
    ...(status.color ? { color: status.color } : {}),
  });

  return (
    <Panel id="qobuz-settings-container">
      <div className="form-group">
        <label>Qobuz Account:</label>
        <div
          id="qobuz-auth-logged-in"
          style={{ display: auth === null ? 'none' : loggedIn ? 'flex' : 'none', marginTop: '4px' }}
        >
          <span id="qobuz-auth-user-info" className="setting-help-text" style={{ color: OK }}>
            {loggedIn
              ? `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`
              : ''}
          </span>
          <button
            className="auth-button"
            onClick={() => void disconnect()}
            style={{ marginLeft: '8px' }}
          >
            Disconnect
          </button>
        </div>
        <div
          id="qobuz-auth-form"
          style={{ display: loggedIn ? 'none' : 'block', marginTop: '4px' }}
        >
          <input
            type="email"
            id="qobuz-email"
            className="form-input"
            placeholder="Qobuz email"
            autoComplete="email"
            style={{ marginBottom: '8px' }}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          {/* the two fields are inline-blocks: the space the old markup had
              between them counts in the flex row's width (measured, 4px) */}{' '}
          <input
            type="password"
            id="qobuz-password"
            className="form-input"
            placeholder="Qobuz password"
            autoComplete="current-password"
            style={{ marginBottom: '8px' }}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <div className="form-actions">
            <button
              className="auth-button"
              id="qobuz-login-btn"
              disabled={login.busy}
              onClick={() => void connect()}
            >
              {login.busy ? 'Connecting...' : 'Connect Qobuz'}
            </button>
            <span
              id="qobuz-auth-status"
              className="setting-help-text"
              style={statusStyle(login.status)}
            >
              {login.status.text}
            </span>
          </div>
        </div>
        <div className="setting-help-text" style={{ marginTop: '6px' }}>
          Requires a paid Qobuz subscription. Streams are DRM-free.
        </div>
        <div
          className="callback-info"
          style={{
            marginTop: '10px',
            borderTop: '1px solid rgba(255,255,255,0.05)',
            paddingTop: '10px',
          }}
        >
          <div className="callback-help" style={{ marginBottom: '8px' }}>
            <strong>Alternative: Auth Token</strong> — If email/password login fails (CAPTCHA),
            paste your token directly:
          </div>
        </div>
        <input
          type="password"
          id="qobuz-download-token"
          className="form-input"
          placeholder="Paste your Qobuz auth token here"
          style={{ marginBottom: '8px' }}
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
        <div className="form-actions">
          <button
            className="auth-button"
            id="qobuz-download-token-btn"
            disabled={byToken.busy}
            onClick={() => void connectToken()}
          >
            {byToken.busy ? 'Connecting...' : 'Connect with Token'}
          </button>
          <span
            id="qobuz-download-token-status"
            className="setting-help-text"
            style={statusStyle(byToken.status)}
          >
            {byToken.status.text}
          </span>
        </div>
        <div className="callback-info">
          <div className="callback-help">
            To get your token: log into{' '}
            <a href="https://play.qobuz.com" target="_blank" style={{ color: '#4285f4' }}>
              play.qobuz.com
            </a>{' '}
            → DevTools (F12) → Network tab → find any request to <code>www.qobuz.com/api.json</code>{' '}
            → look for <code>X-User-Auth-Token</code> in the request headers → copy that value.
          </div>
        </div>
      </div>
    </Panel>
  );
}

// ── HiFi ──

interface HifiInstance {
  url: string;
  enabled?: boolean;
}

interface HifiCheck {
  url: string;
  version?: string;
  can_download?: boolean;
  can_search?: boolean;
  status?: string;
}

const say = (message: string) => window.alert(message);
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** one instance's state in Check All Instances */
function checkState(inst: HifiCheck): ReactNode {
  if (inst.can_download) return <span style={{ color: OK }}>● Download</span>;
  if (inst.can_search) return <span style={{ color: WARN }}>● Search only</span>;
  if (inst.status === 'online') return <span style={{ color: WARN }}>● Online (limited)</span>;
  if (inst.status === 'ssl_error') return <span style={{ color: BAD }}>● SSL error</span>;
  if (inst.status === 'timeout') return <span style={{ color: BAD }}>● Timeout</span>;
  if (inst.status === 'offline') return <span style={{ color: BAD }}>● Offline</span>;
  return <span style={{ color: BAD }}>● {text(inst.status)}</span>;
}

const MUTED = { color: 'rgba(255,255,255,0.4)', fontSize: '0.85em' };

export function HifiPanel({
  store,
  model = sourcesModel,
}: {
  store: SettingsStore;
  model?: SourcesModel;
}) {
  const [status, setStatus] = useState<Status>({ text: '' });
  // null: not read yet; an error string: the read failed
  const [list, setList] = useState<HifiInstance[] | string | null>(null);
  const [adding, setAdding] = useState('');
  const [restoring, setRestoring] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<
    | { state: 'busy' }
    | { state: 'error'; text: string }
    | { state: 'done'; rows: HifiCheck[]; active?: string }
    | null
  >(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);

  async function test() {
    setStatus(CHECKING);
    try {
      const data = await json('/api/hifi/status');
      if (data.available)
        setStatus({ text: `Connected (v${text(data.version) || '?'})`, color: OK });
      else setStatus({ text: 'No instances reachable', color: WARN });
    } catch {
      setStatus({ text: 'Connection error', color: BAD });
    }
  }
  useOnOpen(model, 'hifi', () => void test());

  async function load() {
    try {
      const data = await json('/api/hifi/instances/list');
      setList(Array.isArray(data.instances) ? (data.instances as HifiInstance[]) : []);
    } catch (error) {
      setList(message(error));
    }
  }
  // the list is read with the settings, on each visit
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => {
    if (loads) void load();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  async function post(url: string, init: RequestInit, failed: string) {
    try {
      const data = await json(url, init);
      if (data.success) {
        void load();
        return data;
      }
      say(text(data.error) || failed);
    } catch (error) {
      say(`Error: ${message(error)}`);
    }
    return null;
  }

  const jsonPost = (body: unknown): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  async function add() {
    const url = adding.trim();
    if (!url) return;
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      say('URL must start with http:// or https://');
      return;
    }
    if (await post('/api/hifi/instances', jsonPost({ url }), 'Failed to add instance'))
      setAdding('');
  }

  async function restore() {
    setRestoring(true);
    try {
      const data = await post(
        '/api/hifi/instances/reset',
        { method: 'POST' },
        'Failed to restore defaults',
      );
      if (data) {
        const n = typeof data.restored === 'number' ? data.restored : 0;
        window.showToast?.(
          n
            ? `Restored ${n} default instance${n === 1 ? '' : 's'}`
            : 'All default instances are already present',
          'success',
        );
      }
    } finally {
      setRestoring(false);
    }
  }

  async function check() {
    setChecked({ state: 'busy' });
    setChecking(true);
    try {
      const data = await json('/api/hifi/instances');
      const rows = Array.isArray(data.instances) ? (data.instances as HifiCheck[]) : [];
      setChecked({ state: 'done', rows, active: text(data.active) });
    } catch (error) {
      setChecked({ state: 'error', text: message(error) });
    } finally {
      setChecking(false);
    }
  }

  async function reorder(from: number, to: number) {
    if (!Array.isArray(list) || from === to) return;
    const next = list.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setList(next);
    try {
      await fetch('/api/hifi/instances/reorder', jsonPost({ urls: next.map((i) => i.url) }));
    } catch (error) {
      console.error('Failed to save HiFi instance order:', error);
    }
  }

  let instances: ReactNode = null;
  if (typeof list === 'string') {
    instances = (
      <div style={{ color: BAD, fontSize: '0.85em' }}>Error loading instances: {list}</div>
    );
  } else if (list && !list.length) {
    instances = <div style={MUTED}>No instances configured.</div>;
  } else if (list) {
    instances = list.map((inst, i) => (
      <div
        key={inst.url}
        className={[
          'hifi-instance-item',
          inst.enabled ? '' : 'disabled',
          dragging === i ? 'dragging' : '',
          over === i ? 'drag-over' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        draggable
        data-url={inst.url}
        onDragStart={(event) => {
          setDragging(i);
          if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
        }}
        onDragEnd={() => {
          setDragging(null);
          setOver(null);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
          setOver(i);
        }}
        onDragLeave={() => setOver((o) => (o === i ? null : o))}
        onDrop={(event) => {
          event.preventDefault();
          setOver(null);
          if (dragging !== null) void reorder(dragging, i);
        }}
      >
        <span className="hifi-instance-grip">&#x2630;</span>
        <span className="hifi-instance-url">{inst.url}</span>
        <span
          className={inst.enabled ? 'hifi-instance-toggle on' : 'hifi-instance-toggle off'}
          data-url={inst.url}
          title={inst.enabled ? 'Click to disable' : 'Click to enable'}
          onClick={(event) => {
            event.preventDefault();
            void post(
              '/api/hifi/instances/toggle',
              jsonPost({ url: inst.url, enabled: !inst.enabled }),
              'Failed to toggle instance',
            );
          }}
        >
          {inst.enabled ? '\u2714' : '\u2718'}
        </span>
        <span
          className="hifi-instance-remove"
          data-url={inst.url}
          title="Remove instance"
          onClick={(event) => {
            event.preventDefault();
            void post(
              `/api/hifi/instances?url=${encodeURIComponent(inst.url)}`,
              { method: 'DELETE' },
              'Failed to remove instance',
            );
          }}
        >
          &#x2716;
        </span>
      </div>
    ));
  }

  let report: ReactNode = null;
  if (checked?.state === 'busy') {
    report = <div style={{ ...MUTED, padding: '8px 0' }}>Checking instances...</div>;
  } else if (checked?.state === 'error') {
    report = (
      <div style={{ color: BAD, fontSize: '0.85em' }}>Error checking instances: {checked.text}</div>
    );
  } else if (checked?.state === 'done' && !checked.rows.length) {
    report = <div style={{ color: WARN, fontSize: '0.85em' }}>No instances configured.</div>;
  } else if (checked?.state === 'done') {
    report = checked.rows.map((inst) => (
      <div
        key={inst.url}
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '5px 0',
          borderBottom: '1px solid rgba(255,255,255,0.04)',
          fontSize: '0.82em',
        }}
      >
        <span
          style={{
            color: 'rgba(255,255,255,0.6)',
            fontFamily: 'monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {inst.url}
          {inst.version ? ` v${inst.version}` : ''}
          {inst.url === checked.active ? (
            <>
              {' '}
              <span
                style={{ color: 'rgb(var(--accent-rgb))', fontWeight: 600, fontSize: '0.75em' }}
              >
                (ACTIVE)
              </span>
            </>
          ) : null}
        </span>
        <span style={{ flexShrink: 0, marginLeft: '12px' }}>{checkState(inst)}</span>
      </div>
    ));
  }

  return (
    <Panel id="hifi-download-settings-container" model={model}>
      <div className="form-group">
        <label>HiFi Status:</label>
        <div className="form-actions" style={{ marginTop: '4px' }}>
          <button className="test-button" id="hifi-test-btn" onClick={() => void test()}>
            Test Connection
          </button>
          <span
            id="hifi-connection-status"
            className="setting-help-text"
            style={{ marginLeft: '8px', ...(status.color ? { color: status.color } : {}) }}
          >
            {status.text}
          </span>
        </div>
        <div className="setting-help-text" style={{ marginTop: '6px' }}>
          Free lossless downloads via community-run hifi-api instances. No authentication or
          subscription needed.
        </div>
        <div
          className="hifi-preview-warning"
          style={{
            marginTop: '8px',
            padding: '9px 11px',
            borderRadius: '8px',
            background: 'rgba(241, 196, 15, 0.08)',
            border: '1px solid rgba(241, 196, 15, 0.28)',
            color: '#f1c40f',
            fontSize: '12px',
            lineHeight: 1.5,
          }}
        >
          <strong>Heads up:</strong> some HiFi instances only have 30-second Tidal previews for
          certain tracks, with headers faked to look like the full song. SoulSync decodes every HiFi
          download to catch these and never lets a shorter clip replace a good library file, but if
          a track just won't come through at full length here, grab it from another source.
        </div>
      </div>
      <div className="form-group">
        <label>API Instances:</label>
        <div id="hifi-instances-list" style={{ marginBottom: '8px' }}>
          {instances}
        </div>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <input
            type="url"
            id="hifi-new-instance"
            className="form-input"
            placeholder="https://example.com"
            style={{ flex: 1 }}
            value={adding}
            onChange={(event) => setAdding(event.target.value)}
          />
          <button className="test-button" onClick={() => void add()}>
            Add
          </button>
        </div>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px', flexWrap: 'wrap' }}>
          <button
            className="test-button"
            id="hifi-instances-check-btn"
            disabled={checking}
            onClick={() => void check()}
          >
            {checking ? 'Checking...' : 'Check All Instances'}
          </button>
          <button
            className="test-button"
            id="hifi-instances-restore-btn"
            title="Re-add any of the built-in default instances you've removed (keeps the ones you added)"
            disabled={restoring}
            onClick={() => void restore()}
          >
            {restoring ? 'Restoring…' : 'Restore Defaults'}
          </button>
        </div>
        <div id="hifi-instances-status-panel" style={{ display: checked ? 'block' : 'none' }}>
          {report}
        </div>
      </div>
    </Panel>
  );
}

// ── Soulseek ──

type ShellTools = Window & {
  testConnection?: (service: string) => Promise<void>;
  showLoadingOverlay?: (message: string) => void;
  hideLoadingOverlay?: () => void;
  _settingsAutosaveOff?: boolean;
};

/** a number field that shows what is typed and saves what it parses to,
 *  reset by each settings load */
export function NumberField({
  store,
  id,
  value,
  save,
  ...rest
}: {
  store: SettingsStore;
  id: string;
  value: string;
  save: (raw: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'id'>) {
  const draft = useDraft(value, save);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input
      {...rest}
      type="number"
      id={id}
      value={draft.value}
      onChange={(event) => draft.onChange(event.target.value)}
    />
  );
}

export const int = (raw: string, fallback: number) => parseInt(raw, 10) || fallback;

export function SoulseekPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const s = (settings.soulseek || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('soulseek', key, value);
  const num = (value: unknown, fallback: number, keepZero = false) => {
    const n = keepZero ? (value ?? fallback) : value || fallback;
    return text(n);
  };

  async function detect() {
    const w = window as ShellTools;
    try {
      w.showLoadingOverlay?.('Auto-detecting Soulseek (slskd) server...');
      const result = await json('/api/detect-soulseek', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (result.success) {
        set('slskd_url', text(result.found_url));
        toast(`Soulseek server detected: ${text(result.found_url)}`, 'success');
      } else {
        toast(text(result.error), 'error');
      }
    } catch (error) {
      console.error('Error auto-detecting Soulseek:', error);
      toast('Failed to auto-detect Soulseek server', 'error');
    } finally {
      w.hideLoadingOverlay?.();
    }
  }

  async function test() {
    const w = window as ShellTools;
    // a change still waiting on autosave goes out first, so the server tests
    // what is on screen (testConnection does the same for the page's own save)
    if (w._settingsAutosaveOff !== true && settingsStore.hasPending()) await settingsStore.flush();
    await w.testConnection?.('soulseek');
  }

  const hint = (children: ReactNode) => <small className="settings-hint">{children}</small>;

  return (
    <Panel id="soulseek-settings-container">
      <div className="form-group">
        <label>slskd URL:</label>
        <div className="path-input-group">
          <input
            type="url"
            id="soulseek-url"
            placeholder="http://localhost:5030"
            value={text(s.slskd_url)}
            onChange={(event) => set('slskd_url', event.target.value)}
          />
          <button className="detect-button" onClick={() => void detect()}>
            Auto-detect
          </button>
        </div>
      </div>
      <div className="form-group">
        <label>API Key:</label>
        <SecretInput
          id="soulseek-api-key"
          placeholder="Slskd API Key"
          value={text(s.api_key)}
          onChange={(value) => set('api_key', value)}
        />
      </div>
      <div className="form-group">
        <label>Search Timeout (seconds):</label>
        <NumberField
          store={store}
          id="soulseek-search-timeout"
          placeholder="60"
          min="15"
          max="300"
          value={num(s.search_timeout, 60)}
          save={(raw) => set('search_timeout', int(raw, 60))}
        />
        {hint('How long to search for tracks (15-300 seconds)')}
      </div>
      <div className="form-group">
        <label>Search Timeout Buffer (seconds):</label>
        <NumberField
          store={store}
          id="soulseek-search-timeout-buffer"
          placeholder="15"
          min="5"
          max="60"
          value={num(s.search_timeout_buffer, 15)}
          save={(raw) => set('search_timeout_buffer', int(raw, 15))}
        />
        {hint('Extra time to wait for late results (5-60 seconds)')}
      </div>
      <div className="form-group">
        <label>Minimum Delay Between Searches (seconds):</label>
        <NumberField
          store={store}
          id="soulseek-search-min-delay-seconds"
          placeholder="0"
          min="0"
          max="60"
          value={num(s.search_min_delay_seconds, 0, true)}
          save={(raw) => set('search_min_delay_seconds', int(raw, 0))}
        />
        {hint(
          'Forces a gap between consecutive searches. Smooths burst patterns that trip ISP anti-abuse (e.g. Bell Canada cuts the WAN after rapid peer-connection spikes). 0 disables.',
        )}
      </div>
      <div className="form-group">
        <label>Minimum Peer Upload Speed:</label>
        <select
          id="soulseek-min-peer-speed"
          value={num(s.min_peer_upload_speed, 0)}
          onChange={(event) => set('min_peer_upload_speed', int(event.target.value, 0))}
        >
          <option value="0">Any speed</option>
          <option value="1">1 Mbps</option>
          <option value="2">2 Mbps</option>
          <option value="3">3 Mbps</option>
          <option value="4">4 Mbps</option>
          <option value="5">5 Mbps</option>
          <option value="10">10 Mbps</option>
        </select>
        {hint('Ignore search results from peers with upload speed below this threshold')}
      </div>
      <div className="form-group">
        <label>Max Peer Queue Length:</label>
        <select
          id="soulseek-max-peer-queue"
          value={num(s.max_peer_queue, 0)}
          onChange={(event) => set('max_peer_queue', int(event.target.value, 0))}
        >
          <option value="0">No limit</option>
          <option value="5">5</option>
          <option value="10">10</option>
          <option value="20">20</option>
          <option value="50">50</option>
          <option value="100">100</option>
        </select>
        {hint(
          'Skip peers with a queue longer than this (prefers peers with shorter queues regardless)',
        )}
      </div>
      <div className="form-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id="soulseek-observed-speed-fallback-enabled"
            checked={s.observed_speed_fallback_enabled === true}
            onChange={(event) => set('observed_speed_fallback_enabled', event.target.checked)}
          />
          Retry slow Soulseek downloads
        </label>
        {hint(
          'Off by default. Applies to automatic tracks and album bundles when another eligible peer is available; not manually chosen files.',
        )}
      </div>
      <div className="form-group">
        <label>Minimum Observed Download Speed (KB/s):</label>
        <NumberField
          store={store}
          id="soulseek-min-observed-download-speed"
          placeholder="250"
          min="0"
          step="50"
          value={num(s.min_observed_download_speed_kbps, 250, true)}
          save={(raw) => {
            // _cfgInt: blank or junk saves the default, 0 stays 0
            const n = parseInt(raw, 10);
            set('min_observed_download_speed_kbps', Number.isFinite(n) ? n : 250);
          }}
        />
        {hint(
          "When enabled, try the next Soulseek candidate after roughly one minute below this observed speed. Uses actual byte progress rather than the peer's advertised speed. The final available candidate is always allowed to continue. 0 also disables.",
        )}
      </div>
      <div className="form-group">
        <label>Download Timeout (minutes):</label>
        <NumberField
          store={store}
          id="soulseek-download-timeout"
          placeholder="10"
          min="2"
          max="60"
          value={text(Math.round((Number(s.download_timeout) || 600) / 60))}
          // stored in seconds, shown in minutes
          save={(raw) => set('download_timeout', int(raw, 10) * 60)}
        />
        {hint('Abandon stuck downloads after this many minutes')}
      </div>
      <div className="form-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            id="soulseek-auto-clear-searches"
            checked={s.auto_clear_searches !== false}
            onChange={(event) => set('auto_clear_searches', event.target.checked)}
          />
          Auto-clear slskd search history
        </label>
        {hint(
          "Automatically cleans old searches from slskd when over 200 entries. Disable if you want to keep manual searches in slskd's queue.",
        )}
      </div>
      <div className="form-group">
        <label>slskd Cleanup Scope:</label>
        <select
          id="soulseek-cleanup-scope"
          value={s.cleanup_scope === 'all' ? 'all' : 'own'}
          onChange={(event) => set('cleanup_scope', event.target.value === 'all' ? 'all' : 'own')}
        >
          <option value="own">Only SoulSync's own transfers</option>
          <option value="all">Everything in slskd</option>
        </select>
        {hint(
          'Which transfers and searches cleanup removes. Leave on "own" if other apps (Lidarr, another SoulSync, slskd\'s own UI) share this slskd. "Everything" clears all clients\' finished downloads and is only safe when SoulSync is the only user.',
        )}
      </div>
      <div className="form-actions" style={{ marginTop: '8px' }}>
        <button className="test-button" onClick={() => void test()}>
          Test Soulseek Connection
        </button>
      </div>
    </Panel>
  );
}

/** the panels of one slot in #source-config-home, by part */
export function SourcePanels({ store, part }: SettingsTabProps) {
  if (part === 'soulseek') return <SoulseekPanel store={store} />;
  if (part === 'hifi') return <HifiPanel store={store} />;
  if (part === 'tidal-qobuz') {
    return (
      <>
        <TidalPanel />
        <QobuzPanel />
      </>
    );
  }
  if (part === 'deezer-soundcloud-lidarr') {
    return (
      <>
        <DeezerPanel store={store} />
        <SoundcloudPanel store={store} />
        <LidarrPanel store={store} />
      </>
    );
  }
  return null;
}
