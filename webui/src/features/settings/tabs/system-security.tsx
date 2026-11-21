/**
 * System > Security & Access (SETTINGS_PORT.md, step 9c). Same markup,
 * classes and ids.
 *
 * The PIN, the admin password and the recovery question are saved through
 * their own profile endpoints, as init.js did; the switches and fields below
 * them are security.* settings keys, saved through the store. The server
 * refuses to turn on login without an admin password, whichever save asks.
 *
 * Two boxes belong to the shell (src/shell/oidc-signin.ts, oidc-only.ts):
 * the admin's own OIDC connection and the OIDC-only status. The shell is its
 * own bundle and drew them at start, when they were in index.html; React
 * leaves their contents alone and asks the shell, through window, to draw
 * them once they are on the page.
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { ServerFingerprint } from './devices/server-fingerprint';
import { useLoadedDraft } from './library-card';
import { Section, SecretInput } from './parts';
import { openPeopleSettings } from './people/people-tab';
import { text } from './source-panels';

const TAB = 'advanced';
type Section_ = Record<string, unknown>;

type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  SoulSyncURL?: { resolve: (path: string) => string };
};
const shell = () => window as Shell;

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}
function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

/** profilePinError (init.js) */
export function pinError(pin: string) {
  if (!/^\d*$/.test(pin)) return 'Use digits only';
  if (pin.length < 4) return 'Use at least 4 digits';
  if (pin.length > 20) return 'Use 20 digits at most';
  return '';
}

/** the cors_origins check saveSettings toasted: entries that are not a bare
 *  scheme://host[:port] (or *) */
export function badOrigins(raw: string) {
  return raw
    .replace(/\n/g, ',')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((e) => e !== '*' && !/^https?:\/\/[^\s/?#]+$/i.test(e));
}

export const RECOVERY_QUESTIONS = [
  'What was the name of your first pet?',
  'What city were you born in?',
  'What was your first concert?',
  'What is your all-time favorite album?',
  'What was the make of your first car?',
];

type AdminProfile = {
  has_pin?: boolean;
  has_password?: boolean;
  has_recovery?: boolean;
  recovery_question?: string;
};
type Msg = { text: string; ok: boolean } | null;

function Message({ id, msg }: { id: string; msg: Msg }) {
  return (
    <p
      id={id}
      className="setting-help-text"
      style={{
        marginTop: '6px',
        display: msg ? 'block' : 'none',
        ...(msg ? { color: msg.ok ? '#4caf50' : '#ff5252' } : {}),
      }}
    >
      {msg?.text}
    </p>
  );
}

const post = async (url: string, body: unknown) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await response.json()) as { success?: boolean; error?: string };
};

const MONO = { width: '100%', fontFamily: 'monospace', fontSize: '12px' };

/** the admin's OIDC connect button, as the markup had it: the shell replaces
 *  it with the connection's status once it knows the admin (oidc-signin.ts) */
const CONNECT_HTML =
  '<button type="button" class="auth-button" onclick="window.location.assign(window.SoulSyncURL.resolve(\'/api/profiles/me/oidc-connect/start\'))">Connect my admin account</button>';

/** a security.* text field: shows what is typed, saves it trimmed (_cfgStr
 *  with trim), empty before the settings load */
function useTrimmedField(store: SettingsStore, key: string) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const sec = (settings.security || {}) as Section_;
  const draft = useLoadedDraft(store, text(sec[key]), (raw) =>
    store.set('security', key, raw.trim()),
  );
  return {
    value: loads ? draft.value : '',
    onChange: (event: { target: { value: string } }) => draft.onChange(event.target.value),
  };
}

// one object each, for good: React compares dangerouslySetInnerHTML by
// identity, so a fresh object on every render would write these again over
// what the shell drew (the browser check caught it)
const CONNECT_BOX = { __html: CONNECT_HTML };
const EMPTY_BOX = { __html: '' };

export function SecuritySection({ store, opened }: SettingsTabProps) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const sec = (settings.security || {}) as Section_;
  const on = (key: string) => loads > 0 && sec[key] === true;
  const set = (key: string, value: unknown) => store.set('security', key, value);

  // ── who new sign-ups start as (#2166): a template under People ──
  const [signupTemplate, setSignupTemplate] = useState<string | null>(null);
  useEffect(() => {
    if (!loads) return;
    fetch('/api/permissions/templates')
      .then((r) => (r.ok ? r.json() : null))
      .then(
        (
          d: {
            signup_template_id?: number | null;
            templates?: Array<{ id: number; name: string }>;
          } | null,
        ) => {
          const t = (d?.templates || []).find((x) => x.id === d?.signup_template_id);
          setSignupTemplate(t ? t.name : null);
        },
      )
      .catch(() => {
        // the line still points at People without the name
      });
  }, [loads]);

  // ── the admin profile: PIN, password, recovery question ──
  const [admin, setAdmin] = useState<AdminProfile | null>(null);
  useEffect(() => {
    if (!loads) return;
    void (async () => {
      try {
        const data = (await (await fetch('/api/profiles')).json()) as {
          profiles?: Array<AdminProfile & { is_admin?: boolean }>;
        };
        const found = (data.profiles || []).find((p) => p.is_admin) || {};
        setAdmin(found);
        // the saved question shows in the picker, a custom one in its box
        const question = found.has_recovery ? found.recovery_question || '' : '';
        if (question) {
          if (RECOVERY_QUESTIONS.includes(question)) setRecovery({ pick: question, custom: '' });
          else setRecovery({ pick: '__custom__', custom: question });
        }
      } catch (error) {
        console.error('Error loading security settings:', error);
      }
    })();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  const hasPin = admin?.has_pin === true;
  const hasPassword = admin?.has_password === true;

  // PIN
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState({ pin: '', confirm: '' });
  const [pinMsg, setPinMsg] = useState<Msg>(null);
  const pinInput = useRef<HTMLInputElement>(null);
  const setupShown = admin !== null && (!hasPin || pinOpen);
  const pinLocked = admin !== null && !hasPin;
  const openSetup = () => {
    setPinOpen(true);
    setTimeout(() => pinInput.current?.focus(), 0);
  };
  async function savePin() {
    const problem = pinError(pin.pin);
    if (problem) return setPinMsg({ text: problem, ok: false });
    if (pin.pin !== pin.confirm) return setPinMsg({ text: 'PINs do not match', ok: false });
    try {
      const data = await post('/api/profiles/1/set-pin', { pin: pin.pin });
      if (data.success) {
        setPinMsg({ text: 'PIN saved! You can now enable the lock screen.', ok: true });
        setAdmin({ ...admin, has_pin: true });
        setPinOpen(false);
        setPin({ pin: '', confirm: '' });
      } else {
        setPinMsg({ text: data.error || 'Failed to save PIN', ok: false });
      }
    } catch {
      setPinMsg({ text: 'Connection error', ok: false });
    }
  }

  // the admin password
  const [password, setPassword] = useState({ password: '', confirm: '' });
  const [passwordMsg, setPasswordMsg] = useState<Msg>(null);
  async function savePassword() {
    if (!password.password || password.password.length < 6)
      return setPasswordMsg({ text: 'Password must be at least 6 characters', ok: false });
    if (password.password !== password.confirm)
      return setPasswordMsg({ text: 'Passwords do not match', ok: false });
    try {
      const data = await post('/api/profiles/1/set-password', { password: password.password });
      if (data.success) {
        setPasswordMsg({ text: 'Admin login password saved', ok: true });
        setPassword({ password: '', confirm: '' });
        // Step 1 done: Step 3 unlocks
        setAdmin({ ...admin, has_password: true });
      } else {
        setPasswordMsg({ text: data.error || 'Failed to save password', ok: false });
      }
    } catch {
      setPasswordMsg({ text: 'Connection error', ok: false });
    }
  }

  // the recovery question
  const [recovery, setRecovery] = useState({ pick: '', custom: '' });
  const [answer, setAnswer] = useState('');
  const [recoveryMsg, setRecoveryMsg] = useState<Msg>(null);
  async function saveRecovery() {
    const question = recovery.pick === '__custom__' ? recovery.custom.trim() : recovery.pick;
    if (!question) return setRecoveryMsg({ text: 'Pick or type a question', ok: false });
    if (!answer.trim()) return setRecoveryMsg({ text: 'Enter an answer', ok: false });
    try {
      const data = await post('/api/profiles/1/set-recovery', { question, answer });
      if (data.success) {
        setRecoveryMsg({ text: 'Recovery question saved', ok: true });
        setAnswer('');
        setAdmin({ ...admin, has_recovery: true, recovery_question: question });
      } else {
        setRecoveryMsg({ text: data.error || 'Failed to save', ok: false });
      }
    } catch {
      setRecoveryMsg({ text: 'Connection error', ok: false });
    }
  }

  // ── OIDC ──
  const oidc = on('oidc_signin');
  const [callback, setCallback] = useState<{ url: string; warn: boolean }>({
    url: '/api/auth/oidc/callback',
    warn: false,
  });
  useEffect(() => {
    if (!loads) return;
    // the browser's own address first, then what the server will really send
    // the provider: behind a proxy that hides https they differ
    setCallback({
      url:
        window.location.origin +
        (shell().SoulSyncURL?.resolve('/api/auth/oidc/callback') ?? '/api/auth/oidc/callback'),
      warn: false,
    });
    fetch('/api/auth/oidc/available')
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { callback_url?: string } | null) => {
        if (!d || !d.callback_url) return;
        const sent = d.callback_url.split(':')[0];
        const page = window.location.protocol.replace(':', '');
        setCallback({ url: d.callback_url, warn: sent !== page });
      })
      .catch(() => {
        // keep the browser's guess
      });
  }, [loads]);
  // the shell's two boxes, drawn once they are on the page
  useEffect(() => {
    window.initOidcAdminStatus?.();
    void window.refreshOidcOnlySettingsStatus?.();
  }, []);

  const issuer = useTrimmedField(store, 'oidc_issuer');
  const clientId = useTrimmedField(store, 'oidc_client_id');
  const buttonLabel = useTrimmedField(store, 'oidc_button_label');
  const iconUrl = useTrimmedField(store, 'oidc_icon_url');
  const group = useTrimmedField(store, 'oidc_required_group');
  const claim = useTrimmedField(store, 'oidc_groups_claim');
  const scopes = useTrimmedField(store, 'oidc_scopes');
  const redirect = useTrimmedField(store, 'oidc_redirect_uri');
  const authHeader = useTrimmedField(store, 'auth_proxy_header');
  const proxyTrusted = useTrimmedField(store, 'auth_proxy_trusted');

  // cors: saved trimmed; a malformed entry gets saveSettings' warning once the
  // typing stops, as it did on the save that followed
  const corsTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cors = useLoadedDraft(store, text(sec.cors_origins), (raw) => {
    set('cors_origins', raw.trim());
    clearTimeout(corsTimer.current);
    corsTimer.current = setTimeout(() => {
      const invalid = badOrigins(raw.trim());
      if (invalid.length)
        shell().showToast?.(
          `Allowed Origins: ${invalid.length} entr${invalid.length === 1 ? 'y looks' : 'ies look'} malformed (need full URL like https://soulsync.example.com, no trailing slash). Saving anyway — they'll be ignored.`,
          'warning',
        );
    }, 2000);
  });
  useEffect(() => () => clearTimeout(corsTimer.current), []);

  const toggle = (
    id: string,
    checked: boolean,
    save: (value: boolean) => void,
    label: ReactNode,
    extra?: { disabled?: boolean; style?: Record<string, string> },
  ) => (
    <label className="toggle-label" style={extra?.style}>
      <input
        type="checkbox"
        id={id}
        checked={checked}
        disabled={extra?.disabled}
        onChange={(event) => save(event.target.checked)}
      />{' '}
      <span>{label}</span>
    </label>
  );
  const help = (children: ReactNode, style?: Record<string, string>, id?: string) => (
    <div className="setting-help-text" style={style} id={id}>
      {children}
    </div>
  );

  return (
    <Section tab={TAB} title="Security & Access" hint="PIN · user accounts · reverse proxy">
      <div className="settings-group security-wrapper" data-stg={TAB}>
        {help(
          <>
            By default, anyone who can reach SoulSync on your network can use it. Protect access
            below with a simple shared <strong>PIN</strong>, or full <strong>user accounts</strong>{' '}
            with passwords — you only need one. The last group is just for exposing SoulSync over
            the internet.
          </>,
          { marginBottom: '18px' },
        )}

        {/* ── Method A: PIN ── */}
        <div className="security-subgroup">
          <h4 className="security-subhead">
            🔑 Lock with a PIN{' '}
            <span className="security-subhead-note">simple · one shared PIN</span>
          </h4>

          <div
            className="form-group"
            id="security-pin-setup"
            style={{ display: setupShown ? 'block' : 'none' }}
          >
            <label>Step 1 — Set admin PIN:</label>
            {help('Set a PIN before you can turn on the lock screen.', { marginBottom: '8px' })}
            <input
              ref={pinInput}
              type="password"
              id="security-new-pin"
              placeholder="4 to 20 digits"
              inputMode="numeric"
              maxLength={20}
              autoComplete="off"
              style={{ marginBottom: '6px' }}
              value={pin.pin}
              onChange={(event) => setPin({ ...pin, pin: event.target.value })}
            />
            <input
              type="password"
              id="security-confirm-pin"
              inputMode="numeric"
              placeholder="Confirm PIN"
              maxLength={20}
              autoComplete="off"
              style={{ marginBottom: '6px' }}
              value={pin.confirm}
              onChange={(event) => setPin({ ...pin, confirm: event.target.value })}
            />
            <button
              className="auth-button"
              id="security-save-pin-btn"
              onClick={() => void savePin()}
            >
              Save PIN
            </button>
            <Message id="security-pin-msg" msg={pinMsg} />
          </div>

          <div className="form-group">
            {toggle(
              'security-require-pin',
              on('require_pin_on_launch') && !pinLocked,
              (checked) => {
                // no PIN yet (or one being changed): set it first
                if (checked && (setupShown || pinLocked)) return openSetup();
                set('require_pin_on_launch', checked);
                // this one saved straight away (handleSecurityPinToggle)
                void store.flush();
              },
              'Require PIN to access SoulSync',
              { disabled: pinLocked },
            )}
            {help(
              'A lock screen appears on every page load, verified against the admin PIN. Closing the tab requires re-entry.',
            )}
          </div>

          <div
            className="form-group"
            id="security-change-pin-section"
            style={{ display: admin !== null && hasPin ? 'block' : 'none' }}
          >
            <button className="auth-button" onClick={openSetup}>
              Change PIN
            </button>
          </div>
        </div>

        {/* ── Method B: Login accounts ── */}
        <div className="security-subgroup">
          <h4 className="security-subhead">
            👤 User accounts (login){' '}
            <span className="security-subhead-note">per-person · best for public access</span>
          </h4>
          {help(
            <>
              Everyone signs in with their account name + password. Turning this on{' '}
              <strong>replaces</strong> the PIN and the profile picker. Set passwords for other
              people in <strong>Manage Profiles</strong>.
            </>,
            { marginBottom: '12px' },
          )}

          <div className="form-group">
            <label>Step 1 — Admin login password:</label>
            <div
              className="security-saved-status"
              id="security-login-password-status"
              style={{ display: hasPassword ? 'block' : 'none' }}
            >
              ✓ A login password is set
            </div>
            <input
              type="password"
              id="security-login-password"
              placeholder={
                hasPassword ? 'Enter a new password to change it' : 'Enter password (min 6)'
              }
              maxLength={200}
              autoComplete="new-password"
              style={{ margin: '6px 0' }}
              value={password.password}
              onChange={(event) => setPassword({ ...password, password: event.target.value })}
            />
            <input
              type="password"
              id="security-login-password-confirm"
              placeholder={hasPassword ? 'Confirm new password' : 'Confirm password'}
              maxLength={200}
              autoComplete="new-password"
              style={{ marginBottom: '6px' }}
              value={password.confirm}
              onChange={(event) => setPassword({ ...password, confirm: event.target.value })}
            />
            <button className="auth-button" onClick={() => void savePassword()}>
              Save Password
            </button>
            <Message id="security-login-password-msg" msg={passwordMsg} />
          </div>

          <div className="form-group">
            <label>
              Step 2 <span style={{ opacity: '0.6' }}>(recommended)</span> — Password recovery
              question:
            </label>
            {help('So you can reset a forgotten password by answering it on the sign-in screen.', {
              marginBottom: '8px',
            })}
            <div
              className="security-saved-status"
              id="security-recovery-status"
              style={{ display: admin?.has_recovery ? 'block' : 'none' }}
            >
              {admin?.has_recovery
                ? '✓ Recovery question saved' +
                  (admin.recovery_question ? ': “' + admin.recovery_question + '”' : '')
                : ''}
            </div>
            <select
              id="security-recovery-question"
              className="form-select"
              style={{ marginBottom: '6px' }}
              value={recovery.pick}
              onChange={(event) => setRecovery({ ...recovery, pick: event.target.value })}
            >
              <option value="">— Select a question —</option>
              {RECOVERY_QUESTIONS.map((q) => (
                <option key={q}>{q}</option>
              ))}
              <option value="__custom__">Custom question…</option>
            </select>
            <input
              type="text"
              id="security-recovery-custom"
              placeholder="Type your own question"
              maxLength={120}
              style={{
                display: recovery.pick === '__custom__' ? 'block' : 'none',
                marginBottom: '6px',
              }}
              value={recovery.custom}
              onChange={(event) => setRecovery({ ...recovery, custom: event.target.value })}
            />
            <input
              type="text"
              id="security-recovery-answer"
              placeholder={admin?.has_recovery ? 'Enter a new answer to change it' : 'Your answer'}
              maxLength={120}
              autoComplete="off"
              style={{ marginBottom: '6px' }}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
            />
            <button className="auth-button" onClick={() => void saveRecovery()}>
              Save Recovery Question
            </button>
            <Message id="security-recovery-msg" msg={recoveryMsg} />
          </div>

          {/* the switch is locked until the admin has a password (anti-lockout,
              made visible instead of a refused save) */}
          <div
            className={admin !== null && !hasPassword ? 'form-group security-locked' : 'form-group'}
            id="security-login-toggle-wrap"
          >
            {toggle(
              'security-require-login',
              on('require_login') && !(admin !== null && !hasPassword),
              (v) => set('require_login', v),
              'Step 3 — Require login (username + password)',
              { disabled: admin !== null && !hasPassword },
            )}
            {help(
              admin !== null && !hasPassword ? (
                <>
                  🔒 Set the admin password in <strong>Step 1</strong> first — then you can turn
                  this on.
                </>
              ) : (
                'Replaces the profile picker + PIN with a sign-in screen. Best for instances exposed to the internet.'
              ),
              undefined,
              'security-require-login-help',
            )}
          </div>

          <div className="form-group">
            {toggle(
              'security-plex-signin',
              on('plex_signin'),
              (v) => set('plex_signin', v),
              'Sign in with Plex',
            )}
            {help(
              'Adds a "Sign in with Plex" button to the sign-in screen. Anyone with access to your Plex server signs in with their Plex account (SoulSync never sees their password), and their playlists sync to their own Plex account. The server\'s owner signs in as the admin. New Plex profiles start as the template New people start as names, below. Needs Plex as a media server and internet access to plex.tv.',
            )}
            {toggle(
              'security-plex-signin-auto-create',
              loads ? sec.plex_signin_auto_create !== false : true,
              (v) => set('plex_signin_auto_create', v),
              'Let new Plex users sign up (off: only people who already have a Plex profile here can sign in)',
              { style: { marginTop: '8px' } },
            )}
          </div>

          <div className="form-group">
            {toggle(
              'security-oidc-signin',
              oidc,
              (v) => set('oidc_signin', v),
              'Sign in with OIDC',
            )}
            {help(
              <>
                Adds a sign-in button for your own identity provider (Pocket ID, Authentik,
                Authelia, Keycloak or any other OpenID Connect provider). SoulSync never sees
                passwords. Create a client at the provider with this callback URL:{' '}
                <code id="security-oidc-callback-url">{callback.url}</code>. New OIDC profiles start
                with what New sign-ups below sets. Accounts are matched by the provider's account
                id, never by email, and nobody becomes an admin from a provider claim: to sign in as
                the admin, save these settings, then connect your own account at the bottom.
              </>,
            )}
            <div
              className="setting-warning"
              id="security-oidc-callback-warn"
              role="status"
              hidden={!callback.warn}
            >
              SoulSync sees this connection as plain http, so that's the callback it sends your
              provider. Turn on Behind a reverse proxy below and restart, have your proxy send
              X-Forwarded-Proto, or set the Callback URL override to your https address.
            </div>
            {/* the provider's settings, shown while the switch is on; hidden
                fields keep their values and still save */}
            <div
              className="security-nested security-nested-block"
              id="security-oidc-provider"
              hidden={loads > 0 && !oidc}
            >
              <div className="form-group">
                <label htmlFor="security-oidc-issuer">Issuer URL:</label>
                <input
                  type="text"
                  id="security-oidc-issuer"
                  placeholder="https://id.example.com"
                  autoComplete="off"
                  style={MONO}
                  {...issuer}
                />
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-client-id">Client ID:</label>
                <input
                  type="text"
                  id="security-oidc-client-id"
                  autoComplete="off"
                  style={MONO}
                  {...clientId}
                />
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-client-secret">Client secret:</label>
                <SecretInput
                  id="security-oidc-client-secret"
                  autoComplete="new-password"
                  style={MONO}
                  value={loads ? text(sec.oidc_client_secret) : ''}
                  onChange={(v) => set('oidc_client_secret', v)}
                />
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-button-label">Button label:</label>
                <input
                  type="text"
                  id="security-oidc-button-label"
                  placeholder="Sign in with OIDC"
                  autoComplete="off"
                  maxLength={40}
                  style={{ width: '100%' }}
                  {...buttonLabel}
                />
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-icon-url">
                  Icon URL <span style={{ opacity: '0.6' }}>(optional)</span>:
                </label>
                <input
                  type="text"
                  id="security-oidc-icon-url"
                  placeholder="https://..."
                  autoComplete="off"
                  style={MONO}
                  {...iconUrl}
                />
                {help(
                  "Your provider's logo, shown on the sign-in card in My Account (https only). Leave empty for a plain key icon.",
                )}
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-required-group">
                  Required group <span style={{ opacity: '0.6' }}>(optional)</span>:
                </label>
                <input
                  type="text"
                  id="security-oidc-required-group"
                  autoComplete="off"
                  style={MONO}
                  {...group}
                />
                {help(
                  'Only accounts in this group get in. Leave empty to let every account at the provider sign in.',
                )}
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-groups-claim">Groups claim:</label>
                <input
                  type="text"
                  id="security-oidc-groups-claim"
                  placeholder="groups"
                  autoComplete="off"
                  style={MONO}
                  {...claim}
                />
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-scopes">Scopes:</label>
                <input
                  type="text"
                  id="security-oidc-scopes"
                  placeholder="openid profile email"
                  autoComplete="off"
                  style={MONO}
                  {...scopes}
                />
                {help(
                  <>
                    Add the scope your provider needs to send groups, if you set a required group
                    (Pocket ID: <code>groups</code>). Add <code>offline_access</code> on Authentik
                    or Authelia so signing someone out there signs them out here too.
                  </>,
                )}
              </div>
              <div className="form-group">
                <label htmlFor="security-oidc-redirect-uri">
                  Callback URL override <span style={{ opacity: '0.6' }}>(optional)</span>:
                </label>
                <input
                  type="text"
                  id="security-oidc-redirect-uri"
                  autoComplete="off"
                  style={MONO}
                  {...redirect}
                />
                {help("Only if your reverse proxy doesn't pass the public address through.")}
              </div>
              <div className="form-group">
                {toggle(
                  'security-oidc-signin-auto-create',
                  loads ? sec.oidc_signin_auto_create !== false : true,
                  (v) => set('oidc_signin_auto_create', v),
                  'Let new OIDC users sign up (off: only accounts already connected to a profile can sign in)',
                )}
              </div>
              {/* the admin's own connection: the shell draws it (oidc-signin.ts) */}
              <div
                className="form-group security-oidc-admin"
                id="security-oidc-admin"
                dangerouslySetInnerHTML={CONNECT_BOX}
              />
              {/* sign in with OIDC only (#1839; oidc-only.ts draws the status) */}
              <div className="form-group" id="security-oidc-only-wrap">
                {toggle(
                  'security-oidc-only',
                  on('oidc_only'),
                  (v) => set('oidc_only', v),
                  'OIDC only: turn off every other way to sign in',
                )}
                {help(
                  <>
                    Password sign-in, the recovery question, Sign in with Plex and switching
                    profiles with a password stop working, and anyone signed in some other way is
                    signed out. Invites send people to your provider instead of asking for a
                    password. Needs login mode, and you have to be signed in with OIDC yourself to
                    turn it on. Every profile needs an OIDC connection first. Passwords are kept and
                    work again if you turn this off. If the provider is ever down, set{' '}
                    <code>SOULSYNC_OIDC_ONLY_OVERRIDE=off</code> on the container and restart, then
                    sign in with the admin password.
                  </>,
                )}
                <div
                  className="setting-help-text"
                  id="security-oidc-only-status"
                  hidden
                  dangerouslySetInnerHTML={EMPTY_BOX}
                />
              </div>
            </div>
          </div>
          <div className="form-group" id="security-signup-template">
            <label htmlFor="security-signup-template-open">New people start as</label>
            <button
              type="button"
              id="security-signup-template-open"
              className="auth-button"
              onClick={() => openPeopleSettings('templates')}
            >
              {signupTemplate ?? 'Templates'}: change in People
            </button>
            {help(
              'Everyone who signs in with Plex or OIDC for the first time follows this template: the sides, pages and downloads they get and any request limit. Pick it, or change what it allows, under Settings > People > Templates.',
            )}
          </div>
        </div>

        {/* ── Reverse proxy & remote access ── */}
        <div className="security-subgroup">
          <h4 className="security-subhead">🌐 Reverse proxy &amp; remote access</h4>
          {help(
            <>
              Only needed if you expose SoulSync to the internet behind nginx / Caddy / Traefik. See{' '}
              <code>Support/REVERSE-PROXY.md</code>.
            </>,
            { marginBottom: '12px' },
          )}

          <div className="form-group">
            {toggle(
              'security-trust-proxy',
              on('trust_reverse_proxy'),
              (v) => set('trust_reverse_proxy', v),
              'Behind a reverse proxy',
            )}
            {help(
              <>
                Trusts the proxy's <code>X-Forwarded-*</code> headers, marks the session cookie
                HTTPS-only, and adds security headers.{' '}
                <strong>Leave OFF for direct / LAN access over http://</strong> — it would otherwise
                break plain-HTTP login. <strong>Takes effect after a restart.</strong>
              </>,
            )}
          </div>

          <div className="form-group security-nested">
            <label htmlFor="security-auth-proxy-header">
              ↳ Auth proxy user header <span style={{ opacity: '0.6' }}>(optional)</span>:
            </label>
            <input
              type="text"
              id="security-auth-proxy-header"
              placeholder="Remote-User"
              autoComplete="off"
              style={MONO}
              {...authHeader}
            />
            {help(
              <>
                If an auth proxy (Authelia / Authentik / oauth2-proxy) logs users in in front of
                SoulSync, enter the header it sets and SoulSync trusts it from the addresses below.{' '}
                <strong>
                  Only set this behind a proxy that strips any client-supplied copy of the header.
                </strong>{' '}
                Blank = off.
              </>,
            )}
          </div>

          <div className="form-group security-nested">
            <label htmlFor="security-auth-proxy-trusted">↳ Trusted proxy addresses:</label>
            <textarea
              id="security-auth-proxy-trusted"
              rows={2}
              placeholder={'172.18.0.2\n172.18.0.0/16'}
              style={MONO}
              value={proxyTrusted.value}
              onChange={proxyTrusted.onChange}
            />
            {help(
              <>
                Your proxy's IP or range, one per line or comma separated. The header only counts on
                requests that really come from one of these (never from a forwarded address).{' '}
                <strong>Empty = the header is ignored.</strong>
              </>,
            )}
            {authHeader.value && !proxyTrusted.value ? (
              <div className="setting-help-text" id="security-auth-proxy-untrusted" role="alert">
                <strong>The header is being ignored</strong> until you add your proxy's address
                here, so people get the PIN screen instead.
              </div>
            ) : null}
          </div>

          <div className="form-group">
            <label htmlFor="security-cors-origins">Allowed WebSocket Origins:</label>
            <textarea
              id="security-cors-origins"
              rows={3}
              placeholder={'https://soulsync.example.com\nhttp://192.168.1.5:8888'}
              style={MONO}
              value={loads ? cors.value : ''}
              onChange={(event) => cors.onChange(event.target.value)}
            />
            {help(
              <>
                Origins (full URL, no trailing slash) allowed to open WebSocket connections — one
                per line or comma-separated. Empty = same-origin only (the secure default; fine for
                direct access and most reverse-proxy setups). Add your public domain if live updates
                fail behind a proxy. <code>*</code> on its own line allows any origin (insecure).
              </>,
            )}
          </div>
        </div>

        {/* ── this server's own identity (PAIRING_SPEC.md 1) ── */}
        <div className="security-subgroup">
          <h4 className="security-subhead">🪪 Server identity</h4>
          <ServerFingerprint load={opened > 0} />
        </div>
      </div>
    </Section>
  );
}
