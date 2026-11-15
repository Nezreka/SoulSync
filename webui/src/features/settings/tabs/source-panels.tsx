/**
 * The Sources tab's config panels, ported one at a time from index.html and
 * settings.js (SETTINGS_PORT.md, step 7c on). Same markup, classes and ids.
 *
 * A panel is parked, hidden, in #source-config-home until its tile opens it.
 * The old modal moved the panel's node into its body; a React panel portals
 * itself there instead while it is the open one, and is marked
 * data-react-panel so the modal and the page autosave leave it alone.
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';
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

const toast = (message: string, type: string) => window.showToast?.(message, type);

/** opening this source's modal runs its check, as probeSourceOnOpen did */
function useOnOpen(model: SourcesModel, id: string, check: () => void) {
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
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(
    () => () => {
      if (poll.current) clearInterval(poll.current);
    },
    [],
  );

  async function check() {
    try {
      const data = await json('/api/tidal/download/auth/status');
      if (data.authenticated) {
        setStatus({ text: 'Authenticated', color: OK });
        setButton({ text: 'Re-link Tidal Account', disabled: false });
      } else {
        setStatus({ text: 'Not authenticated', color: WARN });
        setButton({ text: 'Link Tidal Account', disabled: false });
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

/** what checkQobuzAuthStatus (settings.js) last heard: it still updates the
 *  Connections tab's Qobuz card too, and tells this panel by an event */
interface QobuzAuth {
  authenticated?: boolean;
  user?: { display_name?: string; subscription?: string };
}

const QOBUZ_ERR = '#ff5555';

function refreshQobuz() {
  (window as Window & { checkQobuzAuthStatus?: () => void }).checkQobuzAuthStatus?.();
}

export function QobuzPanel() {
  const [auth, setAuth] = useState<QobuzAuth | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [login, setLogin] = useState({ busy: false, status: { text: '' } as Status });
  const [byToken, setByToken] = useState({ busy: false, status: { text: '' } as Status });

  useEffect(() => {
    const heard = (event: Event) => setAuth((event as CustomEvent<QobuzAuth>).detail || {});
    window.addEventListener('soulsync:qobuz-auth', heard);
    return () => window.removeEventListener('soulsync:qobuz-auth', heard);
  }, []);

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

/** the panels of one slot in #source-config-home, by part */
export function SourcePanels({ store, part }: SettingsTabProps) {
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
        <SoundcloudPanel />
        <LidarrPanel store={store} />
      </>
    );
  }
  return null;
}
