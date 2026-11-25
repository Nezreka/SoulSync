/**
 * The source panels shown in a source's side sheet (Downloads > Sources),
 * built on the settings parts (ui/): connection fields first, label over a
 * full-width field; tuning as rows, the control on the right; the rare ones
 * behind "More options". The sheet's foot has the one Test; a panel keeps its
 * own button only where it shows more than pass or fail (an account, a list).
 *
 * A panel is parked, hidden, in #source-config-home until its row opens it,
 * and portals itself into the sheet body while it is the open one. Every
 * control keeps its id, its key and when it saves.
 */

import type React from 'react';

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

import type { SettingsTabProps } from '../host';

import { type SettingsStore } from '../settings-store';
import {
  ActionRow,
  Btn,
  FieldLine,
  Item,
  Items,
  More,
  NumberField as NumberBox,
  Note,
  Row,
  SecretField,
  Select,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextField,
  type Tone,
  itemClass,
} from '../ui';
import { Icon } from '../ui/icons';
import { checkQobuzAuthStatus, onQobuzAuth, type QobuzAuthState } from './connections';
import { REDACTED_SECRET_SENTINEL, useDraft } from './parts';
import { SOURCE_CONFIG_ID_BY_SRC, type SourcesModel, sourcesModel } from './sources-model';

export function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

export const text = (value: unknown) =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : '';

/** a panel: in the sheet while open, in its hidden home otherwise */
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
    <div id={id} className="src-config-panel" data-react-panel="" data-ui="">
      {children}
    </div>
  );
  const body =
    open && SOURCE_CONFIG_ID_BY_SRC[open] === id ? document.getElementById('src-modal-body') : null;
  return body ? createPortal(node, body) : node;
}

/** what a panel's own check said, in words and a tone */
export interface Status {
  text: string;
  tone?: Tone;
}

export const CHECKING: Status = { text: 'Checking...', tone: 'busy' };

export async function json(url: string, init?: RequestInit) {
  const response = await fetch(url, init);
  return (await response.json()) as Record<string, unknown>;
}

/** under a saved key: why it shows as dots */
export const SAVED_KEY_NOTE =
  'Saved keys are never sent back to the browser. Type a new one to replace it.';

/** a secret's note: the saved-key words while one is saved, else its own */
export const secretNote = (value: string, own?: string): ReactNode =>
  // a node, not a string: the whole sentence shows, it is not split behind an "i"
  value === REDACTED_SECRET_SENTINEL ? <>{SAVED_KEY_NOTE}</> : own;

/** a number as typed, saved as it parses, shown again by each settings load;
 *  drawn as the parts' number field, the unit inside it */
export function DraftNumber({
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
  unit?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'id'>) {
  const draft = useDraft(value, save);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  useEffect(() => draft.reset(), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <NumberBox
      {...rest}
      id={id}
      value={draft.value}
      onChange={(event) => draft.onChange(event.target.value)}
    />
  );
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
          tone: 'ok',
        });
      } else {
        setStatus({ text: text(data.error) || 'Authentication failed', tone: 'bad' });
      }
    } catch {
      setStatus({ text: 'Connection error', tone: 'bad' });
    }
  }

  return (
    <Panel id="deezer-download-settings-container">
      <SheetGroup title="Account" />
      <SheetField
        label="ARL token"
        htmlFor="deezer-download-arl"
        help={secretNote(arl, 'The token that signs SoulSync in to your Deezer account.')}
        more={
          <>
            To get it: sign in at{' '}
            <a href="https://www.deezer.com" target="_blank" rel="noreferrer">
              deezer.com
            </a>
            , open the browser's DevTools (F12), then Application, Cookies, and copy the{' '}
            <code>arl</code> value.
          </>
        }
      >
        <SecretField
          id="deezer-download-arl"
          placeholder="Paste your ARL cookie token here"
          value={arl}
          onChange={change}
        />
      </SheetField>
      <ActionRow label="Account" help="Signs in with the token and says whose account it is.">
        <StatusWords id="deezer-download-status" tone={status.tone} live>
          {status.text}
        </StatusWords>
        <Btn id="deezer-download-test-btn" small onClick={() => void test()}>
          Check account
        </Btn>
      </ActionRow>
    </Panel>
  );
}

// ── SoundCloud ──

/** settings.js testSoundcloudConnection; also the probe when its sheet opens */
export async function soundcloudStatus(): Promise<Status> {
  try {
    const data = await json('/api/soundcloud/status');
    if (data.available && data.reachable) return { text: 'Connected (anonymous)', tone: 'ok' };
    // the client is up but the live probe failed: an outage or a passing
    // yt-dlp parse error
    if (data.available) return { text: 'Reachable check failed. Try again.', tone: 'warn' };
    return { text: text(data.error) || 'Unavailable', tone: 'bad' };
  } catch {
    return { text: 'Connection error', tone: 'bad' };
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
  const token = text(settings.soundcloud_download?.oauth_token);

  async function test() {
    setStatus(CHECKING);
    setStatus(await soundcloudStatus());
  }

  // opening SoundCloud's sheet checks it, as probeSourceOnOpen did
  useEffect(() => {
    if (open === 'soundcloud') void test();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Panel id="soundcloud-download-settings-container" model={model}>
      <Note info>
        SoundCloud downloads run anonymously, no account needed. Use it for DJ mixes, remixes and
        tracks that are not on Spotify, Tidal or Deezer. <strong>Quality:</strong> 128 kbps MP3 or
        AAC, depending on the upload. SoundCloud does not offer lossless audio to anyone.
      </Note>
      <ActionRow label="SoundCloud" help="Whether SoundCloud answers right now.">
        <StatusWords id="soundcloud-connection-status" tone={status.tone} live>
          {status.text}
        </StatusWords>
        <Btn id="soundcloud-test-btn" small onClick={() => void test()}>
          Check status
        </Btn>
      </ActionRow>
      {/* #2116: a signed-in free account gets the original file where the
          uploader turned downloads on */}
      <SheetGroup title="Account (optional)" />
      <SheetField
        label="OAuth token"
        htmlFor="soundcloud-download-oauth-token"
        help="With a free account signed in, a track whose uploader turned on the free Download button comes as the original file instead of the 128 kbps stream."
        more={
          <>
            With a free SoundCloud account signed in, tracks whose uploader turned on the free
            Download button come as the original file instead of the 128 kbps stream. Everything
            else still uses the stream. To get it: sign in at soundcloud.com, open DevTools (F12),
            Application, Cookies, and copy the <code>oauth_token</code> value.
          </>
        }
      >
        <SecretField
          id="soundcloud-download-oauth-token"
          placeholder="Paste your oauth_token cookie here"
          value={token}
          onChange={(value) => store.set('soundcloud_download', 'oauth_token', value)}
        />
      </SheetField>
    </Panel>
  );
}

// ── Lidarr ──

export function LidarrPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const key = text(settings.lidarr_download?.api_key);

  return (
    <Panel id="lidarr-download-settings-container">
      <SheetGroup title="Connection" />
      <SheetField
        label="Lidarr URL"
        htmlFor="lidarr-url"
        help="The full address of your Lidarr, for example http://192.168.1.100:8686."
      >
        <TextField
          full
          id="lidarr-url"
          placeholder="http://localhost:8686"
          value={text(settings.lidarr_download?.url)}
          onChange={(event) => store.set('lidarr_download', 'url', event.target.value)}
        />
      </SheetField>
      <SheetField
        label="API key"
        htmlFor="lidarr-api-key"
        help={secretNote(key, 'In Lidarr: Settings, General, Security, API Key.')}
      >
        <SecretField
          id="lidarr-api-key"
          placeholder="Your Lidarr API key"
          value={key}
          onChange={(value) => store.set('lidarr_download', 'api_key', value)}
        />
      </SheetField>
      <Note info>
        Lidarr downloads through its own indexers and download clients (usenet and torrent).{' '}
        <strong>Best for whole albums.</strong> Lidarr is album-first: a search by track name
        usually finds nothing, so for playlists it mostly passes the search on to the next source in
        the order. Put it first in the music order only when you want whole albums through your
        indexers.
      </Note>
      <SheetGroup title="Albums and folders" />
      <Row
        label="Whole album via Lidarr, on by default"
        htmlFor="lidarr-whole-album-default"
        help={
          'Where the download dialogs\' and the wishlist\'s "Whole album via Lidarr" toggle starts, and what automatic wishlist runs do.'
        }
        more="The toggle is there in hybrid mode, with Lidarr and another source. On, Lidarr downloads the whole album; off, the next source (Soulseek, say) gets the tracks."
      >
        <Switch
          id="lidarr-whole-album-default"
          checked={settings.lidarr_download?.whole_album_default !== false}
          onChange={(event) =>
            store.set('lidarr_download', 'whole_album_default', event.target.checked)
          }
        />
      </Row>
      <SheetField
        label="Lidarr imports folder"
        htmlFor="lidarr-transfer-path"
        help="Where tracks that came from Lidarr are imported. Empty imports them like every other download."
        more="Lidarr already keeps the whole album in its own root folder; if that folder is your library, the copy SoulSync imports is a second one."
      >
        <TextField
          full
          mono
          id="lidarr-transfer-path"
          placeholder="empty: the music library folder"
          value={text(settings.lidarr_download?.transfer_path)}
          onChange={(event) => store.set('lidarr_download', 'transfer_path', event.target.value)}
        />
      </SheetField>
      <Row
        label="Lidarr imports every download"
        htmlFor="lidarr-import-through-lidarr"
        help="Downloads from every source wait in the Lidarr imports folder, and Lidarr imports them into its root folder when the batch is done."
        more="For a library that Lidarr keeps: Lidarr names the files and keeps one copy of each track, so an album SoulSync got from Soulseek and Lidarr got later is never there twice. Lidarr has to read the Lidarr imports folder at the same path, and it needs that folder set."
      >
        <Switch
          id="lidarr-import-through-lidarr"
          checked={settings.lidarr_download?.import_through_lidarr === true}
          onChange={(event) =>
            store.set('lidarr_download', 'import_through_lidarr', event.target.checked)
          }
        />
      </Row>
      <SheetField
        label="Lidarr download folder"
        htmlFor="lidarr-download-path"
        help="Where SoulSync copies a track out of Lidarr before it imports it."
      >
        <TextField
          full
          mono
          id="lidarr-download-path"
          placeholder="empty: the Soulseek download folder"
          value={text(settings.lidarr_download?.download_path)}
          onChange={(event) => store.set('lidarr_download', 'download_path', event.target.value)}
        />
      </SheetField>
    </Panel>
  );
}

export const toast = (message: string, type: string) => window.showToast?.(message, type);

/** opening this source's sheet runs its check, as probeSourceOnOpen did */
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
        setStatus({ text: 'Authenticated', tone: 'ok' });
        setButton((b) => ({ ...b, text: 'Re-link Tidal Account' }));
      } else {
        setStatus({ text: 'Not authenticated', tone: 'warn' });
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
      setStatus({ text: 'Waiting...', tone: 'warn' });
      if (poll.current) clearInterval(poll.current);
      poll.current = setInterval(() => {
        void (async () => {
          try {
            const done = await json('/api/tidal/download/auth/check');
            if (done.status === 'completed') {
              if (poll.current) clearInterval(poll.current);
              poll.current = null;
              setCode(null);
              setStatus({ text: 'Authenticated', tone: 'ok' });
              setButton({ text: 'Re-link Tidal Account', disabled: false });
              toast('Tidal download account linked successfully', 'success');
            } else if (done.status === 'error') {
              if (poll.current) clearInterval(poll.current);
              poll.current = null;
              setCode(null);
              setStatus({ text: 'Auth failed', tone: 'bad' });
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
      <SheetGroup title="Account" />
      <ActionRow
        label="Tidal download account"
        help="Link the account downloads use. You approve it on Tidal's own page with a code."
      >
        <StatusWords id="tidal-download-auth-status" tone={status.tone} live>
          {status.text}
        </StatusWords>
        <Btn
          id="tidal-download-auth-btn"
          small
          kind={status.tone === 'ok' ? undefined : 'primary'}
          disabled={button.disabled}
          onClick={() => void start()}
        >
          {button.text}
        </Btn>
      </ActionRow>
      <div id="tidal-download-auth-code" hidden={!code}>
        {code ? (
          <Note info>
            Go to{' '}
            <a href={code.uri} target="_blank" rel="noreferrer">
              {code.uri}
            </a>{' '}
            and enter code: <strong>{code.code}</strong>
          </Note>
        ) : null}
      </div>
    </Panel>
  );
}

// ── Qobuz ──

/** what checkQobuzAuthStatus last heard (connections.ts): the Connections
 *  tab's Qobuz card hears it too */
type QobuzAuth = QobuzAuthState;

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
        status = { text: text(data.error) || 'Login failed', tone: 'bad' };
        toast(text(data.error) || 'Qobuz login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz login error:', error);
      status = { text: 'Connection error', tone: 'bad' };
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
        status = { text: text(data.error) || 'Token login failed', tone: 'bad' };
        toast(text(data.error) || 'Qobuz token login failed', 'error');
      }
    } catch (error) {
      console.error('Qobuz token login error:', error);
      status = { text: 'Connection error', tone: 'bad' };
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

  return (
    <Panel id="qobuz-settings-container">
      <SheetGroup title="Account" />
      <Row
        id="qobuz-auth-logged-in"
        hidden={auth === null || !loggedIn}
        label="Qobuz account"
        help={
          <StatusWords id="qobuz-auth-user-info" tone="ok">
            {loggedIn
              ? `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`
              : ''}
          </StatusWords>
        }
      >
        <Btn small onClick={() => void disconnect()}>
          Disconnect
        </Btn>
      </Row>
      <div id="qobuz-auth-form" hidden={loggedIn}>
        <SheetField label="Email" htmlFor="qobuz-email">
          <TextField
            full
            type="email"
            id="qobuz-email"
            placeholder="Qobuz email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </SheetField>
        <SheetField
          label="Password"
          htmlFor="qobuz-password"
          help="Needs a paid Qobuz subscription. Streams are DRM-free."
        >
          <TextField
            full
            type="password"
            id="qobuz-password"
            placeholder="Qobuz password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </SheetField>
        <ActionRow
          label="Sign in"
          help="Signs in once; SoulSync keeps the session, not the password."
        >
          <StatusWords id="qobuz-auth-status" tone={login.status.tone} live>
            {login.status.text}
          </StatusWords>
          <Btn
            id="qobuz-login-btn"
            small
            kind="primary"
            disabled={login.busy}
            onClick={() => void connect()}
          >
            {login.busy ? 'Connecting...' : 'Connect Qobuz'}
          </Btn>
        </ActionRow>
      </div>
      <More label="Sign in with a token instead" names="when email sign-in asks for a CAPTCHA">
        <SheetField
          label="Auth token"
          htmlFor="qobuz-download-token"
          help="If email and password sign-in fails (a CAPTCHA), paste your token here."
          more={
            <>
              To get your token: sign in at{' '}
              <a href="https://play.qobuz.com" target="_blank" rel="noreferrer">
                play.qobuz.com
              </a>
              , open DevTools (F12), the Network tab, find any request to{' '}
              <code>www.qobuz.com/api.json</code>, and copy <code>X-User-Auth-Token</code> from its
              request headers.
            </>
          }
        >
          <TextField
            full
            type="password"
            id="qobuz-download-token"
            placeholder="Paste your Qobuz auth token here"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </SheetField>
        <ActionRow label="Sign in with the token">
          <StatusWords id="qobuz-download-token-status" tone={byToken.status.tone} live>
            {byToken.status.text}
          </StatusWords>
          <Btn
            id="qobuz-download-token-btn"
            small
            disabled={byToken.busy}
            onClick={() => void connectToken()}
          >
            {byToken.busy ? 'Connecting...' : 'Connect with Token'}
          </Btn>
        </ActionRow>
      </More>
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

const say = (message: string) => window.showToast?.(message, 'error');
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** one instance's state in Check All Instances */
function checkState(inst: HifiCheck): Status {
  if (inst.can_download) return { text: 'Download', tone: 'ok' };
  if (inst.can_search) return { text: 'Search only', tone: 'warn' };
  if (inst.status === 'online') return { text: 'Online (limited)', tone: 'warn' };
  if (inst.status === 'ssl_error') return { text: 'SSL error', tone: 'bad' };
  if (inst.status === 'timeout') return { text: 'Timeout', tone: 'bad' };
  if (inst.status === 'offline') return { text: 'Offline', tone: 'bad' };
  return { text: text(inst.status), tone: 'bad' };
}

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
        setStatus({ text: `Connected (v${text(data.version) || '?'})`, tone: 'ok' });
      else setStatus({ text: 'No instances reachable', tone: 'warn' });
    } catch {
      setStatus({ text: 'Connection error', tone: 'bad' });
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
    instances = <StatusWords tone="bad">{`Error loading instances: ${list}`}</StatusWords>;
  } else if (list && !list.length) {
    instances = <span className={itemClass.dim}>No instances configured.</span>;
  } else if (list) {
    // the browser's own drag for now; the shared order list comes to it in
    // a later phase (it is the same control as the source order)
    instances = list.map((inst, i) => (
      <Item
        key={inst.url}
        off={!inst.enabled}
        lift={dragging === i}
        over={over === i}
        draggable
        data-url={inst.url}
        data-hifi-instance=""
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
        <span className={itemClass.grip} aria-hidden="true" title="Drag to reorder">
          <Icon name="grip" size={14} />
        </span>
        <span className={`${itemClass.main} ${itemClass.mono}`}>{inst.url}</span>
        <Btn
          small
          kind="ghost"
          data-hifi-toggle=""
          aria-pressed={!!inst.enabled}
          aria-label={`${inst.enabled ? 'Turn off' : 'Turn on'} ${inst.url}`}
          title={inst.enabled ? 'In use. Turn it off to skip it' : 'Off. Turn it on to use it'}
          onClick={() =>
            void post(
              '/api/hifi/instances/toggle',
              jsonPost({ url: inst.url, enabled: !inst.enabled }),
              'Failed to toggle instance',
            )
          }
        >
          {inst.enabled ? 'On' : 'Off'}
        </Btn>
        <Btn
          small
          kind="ghost"
          iconOnly
          icon="x"
          data-hifi-remove=""
          aria-label={`Remove ${inst.url}`}
          onClick={() =>
            void post(
              `/api/hifi/instances?url=${encodeURIComponent(inst.url)}`,
              { method: 'DELETE' },
              'Failed to remove instance',
            )
          }
        />
      </Item>
    ));
  }

  let report: ReactNode = null;
  if (checked?.state === 'busy') {
    report = <span className={itemClass.dim}>Checking instances...</span>;
  } else if (checked?.state === 'error') {
    report = <StatusWords tone="bad">{`Error checking instances: ${checked.text}`}</StatusWords>;
  } else if (checked?.state === 'done' && !checked.rows.length) {
    report = <StatusWords tone="warn">No instances configured.</StatusWords>;
  } else if (checked?.state === 'done') {
    report = checked.rows.map((inst) => {
      const state = checkState(inst);
      return (
        <Item key={inst.url} data-hifi-check="">
          <span className={`${itemClass.main} ${itemClass.mono}`}>
            {inst.url}
            {inst.version ? ` v${inst.version}` : ''}
          </span>
          {inst.url === checked.active ? <span className={itemClass.dim}>Active</span> : null}
          <StatusWords tone={state.tone}>{state.text}</StatusWords>
        </Item>
      );
    });
  }

  return (
    <Panel id="hifi-download-settings-container" model={model}>
      <Note info>
        Free lossless downloads through community-run hifi-api instances. No account or subscription
        needed.
      </Note>
      <Note>
        <strong>Heads up:</strong> some HiFi instances only have 30-second Tidal previews of some
        tracks, with headers faked to look like the full song. SoulSync decodes every HiFi download
        to catch these and never lets a shorter clip replace a good library file, but if a track
        will not come through at full length here, get it from another source.
      </Note>
      <ActionRow label="HiFi" help="Whether any instance answers, and its version.">
        <StatusWords id="hifi-connection-status" tone={status.tone} live>
          {status.text}
        </StatusWords>
        <Btn id="hifi-test-btn" small onClick={() => void test()}>
          Check status
        </Btn>
      </ActionRow>
      <SheetGroup title="Instances" />
      <p className={itemClass.dim}>Tried from the top. Drag to change the order.</p>
      <Items id="hifi-instances-list">{instances}</Items>
      <SheetField label="Add an instance" htmlFor="hifi-new-instance">
        <FieldLine>
          <TextField
            full
            type="url"
            id="hifi-new-instance"
            placeholder="https://example.com"
            value={adding}
            onChange={(event) => setAdding(event.target.value)}
          />
          <Btn icon="plus" onClick={() => void add()}>
            Add
          </Btn>
        </FieldLine>
      </SheetField>
      <ActionRow label="Every instance" help="What each one can do right now, and which is in use.">
        <Btn
          id="hifi-instances-check-btn"
          small
          busy={checking}
          disabled={checking}
          onClick={() => void check()}
        >
          {checking ? 'Checking...' : 'Check All Instances'}
        </Btn>
        <Btn
          id="hifi-instances-restore-btn"
          small
          kind="ghost"
          title="Re-add any of the built-in default instances you have removed (keeps the ones you added)"
          disabled={restoring}
          onClick={() => void restore()}
        >
          {restoring ? 'Restoring…' : 'Restore Defaults'}
        </Btn>
      </ActionRow>
      <Items id="hifi-instances-status-panel" hidden={!checked}>
        {report}
      </Items>
    </Panel>
  );
}

// ── Soulseek ──

type ShellTools = Window & {
  showLoadingOverlay?: (message: string) => void;
  hideLoadingOverlay?: () => void;
};

/** a number field that shows what is typed and saves what it parses to,
 *  reset by each settings load (the old field look: Library > Folders uses
 *  it until that tab is rebuilt) */
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

const PEER_SPEEDS = [
  { value: '0', label: 'Any speed' },
  { value: '1', label: '1 Mbps' },
  { value: '2', label: '2 Mbps' },
  { value: '3', label: '3 Mbps' },
  { value: '4', label: '4 Mbps' },
  { value: '5', label: '5 Mbps' },
  { value: '10', label: '10 Mbps' },
];

const PEER_QUEUES = [
  { value: '0', label: 'No limit' },
  { value: '5', label: '5' },
  { value: '10', label: '10' },
  { value: '20', label: '20' },
  { value: '50', label: '50' },
  { value: '100', label: '100' },
];

export function SoulseekPanel({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const s = (settings.soulseek || {}) as Record<string, unknown>;
  const set = (key: string, value: unknown) => store.set('soulseek', key, value);
  const num = (value: unknown, fallback: number, keepZero = false) => {
    const n = keepZero ? (value ?? fallback) : value || fallback;
    return text(n);
  };
  const key = text(s.api_key);

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

  return (
    <Panel id="soulseek-settings-container">
      <SheetGroup title="Connection" />
      <SheetField label="slskd URL" htmlFor="soulseek-url">
        <FieldLine>
          <TextField
            full
            type="url"
            id="soulseek-url"
            placeholder="http://localhost:5030"
            value={text(s.slskd_url)}
            onChange={(event) => set('slskd_url', event.target.value)}
          />
          <Btn onClick={() => void detect()}>Auto-detect</Btn>
        </FieldLine>
      </SheetField>
      <SheetField label="API key" htmlFor="soulseek-api-key" help={secretNote(key)}>
        <SecretField
          id="soulseek-api-key"
          placeholder="Slskd API Key"
          value={key}
          onChange={(value) => set('api_key', value)}
        />
      </SheetField>

      <SheetGroup title="Searching" />
      <Row
        field
        label="Search timeout"
        htmlFor="soulseek-search-timeout"
        help="How long to search for tracks (15 to 300 seconds)."
      >
        <DraftNumber
          store={store}
          id="soulseek-search-timeout"
          unit="seconds"
          placeholder="60"
          min="15"
          max="300"
          value={num(s.search_timeout, 60)}
          save={(raw) => set('search_timeout', int(raw, 60))}
        />
      </Row>
      <Row
        field
        label="Minimum peer upload speed"
        htmlFor="soulseek-min-peer-speed"
        help="Ignore results from peers slower than this."
      >
        <Select
          id="soulseek-min-peer-speed"
          options={PEER_SPEEDS}
          value={num(s.min_peer_upload_speed, 0)}
          onChange={(event) => set('min_peer_upload_speed', int(event.target.value, 0))}
        />
      </Row>
      <Row
        field
        label="Max peer queue length"
        htmlFor="soulseek-max-peer-queue"
        help="Skip peers with a longer queue. Peers with shorter queues are preferred either way."
      >
        <Select
          id="soulseek-max-peer-queue"
          options={PEER_QUEUES}
          value={num(s.max_peer_queue, 0)}
          onChange={(event) => set('max_peer_queue', int(event.target.value, 0))}
        />
      </Row>
      <Row
        label="Retry slow Soulseek downloads"
        htmlFor="soulseek-observed-speed-fallback-enabled"
        help="Off by default. Tries another peer when one is slow. Applies to automatic tracks and album bundles when another eligible peer is there, never to files you picked by hand."
      >
        <Switch
          id="soulseek-observed-speed-fallback-enabled"
          checked={s.observed_speed_fallback_enabled === true}
          onChange={(event) => set('observed_speed_fallback_enabled', event.target.checked)}
        />
      </Row>
      <More
        count={7}
        names="timeout buffer, search delay, slow download speed, download timeout, login wait, cleanup"
      >
        <Row
          field
          label="Search timeout buffer"
          htmlFor="soulseek-search-timeout-buffer"
          help="Extra time to wait for late results (5 to 60 seconds)."
        >
          <DraftNumber
            store={store}
            id="soulseek-search-timeout-buffer"
            unit="seconds"
            placeholder="15"
            min="5"
            max="60"
            value={num(s.search_timeout_buffer, 15)}
            save={(raw) => set('search_timeout_buffer', int(raw, 15))}
          />
        </Row>
        <Row
          field
          label="Minimum delay between searches"
          htmlFor="soulseek-search-min-delay-seconds"
          help="Forces a gap between searches. Smooths out bursts that trip an ISP's anti-abuse (Bell Canada cuts the line after rapid spikes in peer connections). 0 turns it off."
        >
          <DraftNumber
            store={store}
            id="soulseek-search-min-delay-seconds"
            unit="seconds"
            placeholder="0"
            min="0"
            max="60"
            value={num(s.search_min_delay_seconds, 0, true)}
            save={(raw) => set('search_min_delay_seconds', int(raw, 0))}
          />
        </Row>
        <Row
          field
          label="Minimum observed download speed"
          htmlFor="soulseek-min-observed-download-speed"
          help="With Retry slow Soulseek downloads on, the next candidate is tried after about a minute below this speed. It reads the bytes that actually arrive, not the speed a peer advertises. The last candidate is always allowed to finish. 0 also turns it off."
        >
          <DraftNumber
            store={store}
            id="soulseek-min-observed-download-speed"
            unit="KB/s"
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
        </Row>
        <Row
          field
          label="Download timeout"
          htmlFor="soulseek-download-timeout"
          help="Give up on a stuck download after this long."
        >
          <DraftNumber
            store={store}
            id="soulseek-download-timeout"
            unit="minutes"
            placeholder="10"
            min="2"
            max="60"
            value={text(Math.round((Number(s.download_timeout) || 600) / 60))}
            // stored in seconds, shown in minutes
            save={(raw) => set('download_timeout', int(raw, 10) * 60)}
          />
        </Row>
        <Row
          field
          label="Wait for the Soulseek login"
          htmlFor="soulseek-login-wait-minutes"
          help="When slskd loses its Soulseek login (a server ban, a reconnect), Soulseek downloads wait this long for it without using up their retries, then move on to your next source."
          more="0 never waits."
        >
          <DraftNumber
            store={store}
            id="soulseek-login-wait-minutes"
            unit="minutes"
            placeholder="45"
            min="0"
            max="1440"
            value={num(s.login_wait_minutes, 45, true)}
            save={(raw) => {
              const n = parseInt(raw, 10);
              set('login_wait_minutes', Number.isFinite(n) && n >= 0 ? n : 45);
            }}
          />
        </Row>
        <Row
          label="Auto-clear slskd search history"
          htmlFor="soulseek-auto-clear-searches"
          help="Cleans old searches out of slskd when there are more than 200. Turn it off to keep searches you ran by hand in slskd's list."
        >
          <Switch
            id="soulseek-auto-clear-searches"
            checked={s.auto_clear_searches !== false}
            onChange={(event) => set('auto_clear_searches', event.target.checked)}
          />
        </Row>
        <Row
          field
          label="slskd cleanup scope"
          htmlFor="soulseek-cleanup-scope"
          help="Which transfers and searches cleanup removes. Keep it on SoulSync's own if other apps (Lidarr, another SoulSync, slskd's own page) share this slskd. Everything clears every client's finished downloads and is only safe when SoulSync is the only one using it."
        >
          <Select
            id="soulseek-cleanup-scope"
            value={s.cleanup_scope === 'all' ? 'all' : 'own'}
            onChange={(event) => set('cleanup_scope', event.target.value === 'all' ? 'all' : 'own')}
            options={[
              { value: 'own', label: "Only SoulSync's own transfers" },
              { value: 'all', label: 'Everything in slskd' },
            ]}
          />
        </Row>
      </More>
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
