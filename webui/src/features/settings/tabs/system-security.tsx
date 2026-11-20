/**
 * System > Security (settings-bic phase 6), on the parts: the hero says who
 * can get in, then three cards: Lock with a PIN, Sign-in (the admin
 * password, the recovery question, Require sign-in, Plex, OIDC with its
 * provider fields shown while it is on, who new people start as) and
 * Reverse proxy. Every control, id and key is the one it was.
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

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  Btn,
  Card,
  CopyValue,
  FieldLine,
  FieldNote,
  More,
  Note,
  Row,
  Say,
  SecretField,
  Select,
  Switch,
  Tag,
  TextArea,
  TextField,
  Value,
} from '../ui';
import s from '../ui/settings.module.css';
import { LibHero, useLoadedDraft } from './library-card';
import { openPeopleSettings } from './people/people-tab';
import { text } from './source-panels';
import { securitySentence } from './system/words';

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

/** what a save said, under its fields */
function Message({ id, msg }: { id: string; msg: Msg }) {
  return (
    <Say id={id} tone={msg ? (msg.ok ? 'ok' : 'bad') : undefined} hidden={!msg}>
      {msg?.text}
    </Say>
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

export function SecurityView({ store }: { store: SettingsStore }) {
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

  const pinOn = on('require_pin_on_launch') && !pinLocked;
  const loginLocked = admin !== null && !hasPassword;
  const loginOn = on('require_login') && !loginLocked;
  const plex = on('plex_signin');
  const oidcOnly = on('oidc_only');
  const proxy = on('trust_reverse_proxy');
  const corsBad = loads ? badOrigins(cors.value.trim()) : [];
  const ways = [loginOn && 'Password', loginOn && plex && 'Plex', loginOn && oidc && 'OIDC'].filter(
    Boolean,
  ).length;

  return (
    <>
      <LibHero
        parts={securitySentence({
          hasPin: admin ? hasPin : null,
          hasPassword: admin ? hasPassword : null,
          requirePin: pinOn,
          requireLogin: loginOn,
          plex,
          oidc,
          oidcOnly,
          proxy,
        })}
        stats={[
          { value: pinOn ? 'On' : hasPin ? 'Set, off' : 'Not set', label: 'PIN lock' },
          { value: loginOn ? 'On' : 'Off', label: 'Sign-in' },
          { value: loginOn ? (oidcOnly && oidc ? 1 : ways) : 0, label: 'Ways to sign in' },
        ]}
      />
      <div className={s.cards}>
        <Card
          title="Lock with a PIN"
          hint="One shared PIN for the whole app. Simple, and fine on a home network."
          actions={
            <span id="security-change-pin-section" hidden={!(admin !== null && hasPin)}>
              <Btn small onClick={openSetup}>
                Change PIN
              </Btn>
            </span>
          }
        >
          <Row
            off={pinLocked}
            label="Require the PIN to open SoulSync"
            htmlFor="security-require-pin"
            help={
              pinLocked
                ? 'Set a PIN below first.'
                : 'A lock screen on every page load, checked against the admin PIN.'
            }
            more="Closing the tab asks again."
          >
            <Switch
              id="security-require-pin"
              checked={pinOn}
              disabled={pinLocked}
              onChange={(event) => {
                const checked = event.target.checked;
                // no PIN yet (or one being changed): set it first
                if (checked && (setupShown || pinLocked)) return openSetup();
                set('require_pin_on_launch', checked);
                // this one saved straight away (handleSecurityPinToggle)
                void store.flush();
              }}
            />
          </Row>
          <div id="security-pin-setup" hidden={!setupShown}>
            <Row
              stack
              label={hasPin ? 'New admin PIN' : 'Admin PIN'}
              htmlFor="security-new-pin"
              help="4 to 20 digits. Set it before you turn the lock on."
            >
              <FieldLine wrap>
                <TextField
                  ref={pinInput}
                  type="password"
                  id="security-new-pin"
                  placeholder="4 to 20 digits"
                  inputMode="numeric"
                  maxLength={20}
                  autoComplete="off"
                  value={pin.pin}
                  onChange={(event) => setPin({ ...pin, pin: event.target.value })}
                />
                <TextField
                  type="password"
                  id="security-confirm-pin"
                  aria-label="Confirm PIN"
                  inputMode="numeric"
                  placeholder="Confirm PIN"
                  maxLength={20}
                  autoComplete="off"
                  value={pin.confirm}
                  onChange={(event) => setPin({ ...pin, confirm: event.target.value })}
                />
                <Btn small kind="primary" id="security-save-pin-btn" onClick={() => void savePin()}>
                  Save PIN
                </Btn>
              </FieldLine>
            </Row>
          </div>
          <div className={s.cardPad} hidden={!pinMsg}>
            <Message id="security-pin-msg" msg={pinMsg} />
          </div>
        </Card>

        <Card
          title="Sign-in"
          hint="Everyone signs in with their own account. It replaces the PIN and the profile picker. Set other people's passwords in People."
        >
          <Row
            stack
            label={
              <>
                Admin password{' '}
                <span id="security-login-password-status" hidden={!hasPassword}>
                  <Tag accent>Set</Tag>
                </span>
              </>
            }
            htmlFor="security-login-password"
            help={
              hasPassword
                ? 'A password is set. Type a new one to change it.'
                : 'At least 6 characters. Sign-in can be turned on once it is set.'
            }
          >
            <FieldLine wrap>
              <TextField
                type="password"
                id="security-login-password"
                placeholder={hasPassword ? 'New password' : 'Password, 6 or more'}
                maxLength={200}
                autoComplete="new-password"
                value={password.password}
                onChange={(event) => setPassword({ ...password, password: event.target.value })}
              />
              <TextField
                type="password"
                id="security-login-password-confirm"
                aria-label="Confirm password"
                placeholder={hasPassword ? 'Confirm new password' : 'Confirm password'}
                maxLength={200}
                autoComplete="new-password"
                value={password.confirm}
                onChange={(event) => setPassword({ ...password, confirm: event.target.value })}
              />
              <Btn small kind="primary" onClick={() => void savePassword()}>
                Save password
              </Btn>
            </FieldLine>
            <Message id="security-login-password-msg" msg={passwordMsg} />
          </Row>

          <Row
            stack
            label={
              <>
                Recovery question <Tag>Recommended</Tag>
              </>
            }
            htmlFor="security-recovery-question"
            help="So you can reset a forgotten password by answering it on the sign-in screen."
          >
            <div id="security-recovery-status" hidden={!admin?.has_recovery}>
              <FieldNote>
                {admin?.has_recovery
                  ? 'Saved' +
                    (admin.recovery_question ? `: “${admin.recovery_question}”` : '') +
                    '. Answer a new one below to change it.'
                  : ''}
              </FieldNote>
            </div>
            <FieldLine wrap>
              <Select
                id="security-recovery-question"
                value={recovery.pick}
                onChange={(event) => setRecovery({ ...recovery, pick: event.target.value })}
              >
                <option value="">Pick a question</option>
                {RECOVERY_QUESTIONS.map((q) => (
                  <option key={q}>{q}</option>
                ))}
                <option value="__custom__">My own question…</option>
              </Select>
              <span className={s.grow} hidden={recovery.pick !== '__custom__'}>
                <TextField
                  full
                  id="security-recovery-custom"
                  aria-label="Your own question"
                  placeholder="Type your own question"
                  maxLength={120}
                  value={recovery.custom}
                  onChange={(event) => setRecovery({ ...recovery, custom: event.target.value })}
                />
              </span>
            </FieldLine>
            <FieldLine wrap>
              <TextField
                id="security-recovery-answer"
                aria-label="Answer"
                placeholder={admin?.has_recovery ? 'A new answer, to change it' : 'Your answer'}
                maxLength={120}
                autoComplete="off"
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
              />
              <Btn small kind="primary" onClick={() => void saveRecovery()}>
                Save recovery question
              </Btn>
            </FieldLine>
            <Message id="security-recovery-msg" msg={recoveryMsg} />
          </Row>

          {/* the switch is locked until the admin has a password (anti-lockout,
              made visible instead of a refused save) */}
          <Row
            id="security-login-toggle-wrap"
            off={loginLocked}
            label="Require sign-in"
            htmlFor="security-require-login"
            help={
              <span id="security-require-login-help">
                {loginLocked
                  ? 'Set the admin password above first, then you can turn this on.'
                  : 'A sign-in screen in place of the profile picker and the PIN. Best when SoulSync is reachable from the internet.'}
              </span>
            }
          >
            <Switch
              id="security-require-login"
              checked={loginOn}
              disabled={loginLocked}
              onChange={(event) => set('require_login', event.target.checked)}
            />
          </Row>

          <Row
            label="Sign in with Plex"
            htmlFor="security-plex-signin"
            help="Adds a Sign in with Plex button. Anyone with access to your Plex server signs in with their Plex account."
            more="SoulSync never sees their password, and their playlists sync to their own Plex account. The server's owner signs in as the admin. New Plex profiles start as the template below. Needs Plex as a media server and internet access to plex.tv."
          >
            <Switch
              id="security-plex-signin"
              checked={plex}
              onChange={(event) => set('plex_signin', event.target.checked)}
            />
          </Row>
          <Row
            sub
            off={!plex}
            label="Let new Plex users sign up"
            htmlFor="security-plex-signin-auto-create"
            help="Off: only people who already have a Plex profile here can sign in."
          >
            <Switch
              id="security-plex-signin-auto-create"
              checked={loads ? sec.plex_signin_auto_create !== false : true}
              onChange={(event) => set('plex_signin_auto_create', event.target.checked)}
            />
          </Row>

          <Row
            label="Sign in with OIDC"
            htmlFor="security-oidc-signin"
            help="A sign-in button for your own identity provider: Pocket ID, Authentik, Authelia, Keycloak or any other OpenID Connect provider."
            more="SoulSync never sees passwords. Accounts are matched by the provider's account id, never by email, and nobody becomes an admin from a provider claim: to sign in as the admin, save these settings, then connect your own account below. New OIDC profiles start as the template below."
          >
            <Switch
              id="security-oidc-signin"
              checked={oidc}
              onChange={(event) => set('oidc_signin', event.target.checked)}
            />
          </Row>
          {/* the provider's settings, shown while the switch is on; hidden
              fields keep their values and still save */}
          <div id="security-oidc-provider" hidden={loads > 0 && !oidc}>
            <Row
              sub
              stack
              label="Callback URL"
              help="Create a client at your provider with this address."
            >
              <CopyValue id="security-oidc-callback-url" value={callback.url} />
              <div id="security-oidc-callback-warn" role="status" hidden={!callback.warn}>
                <Note>
                  SoulSync sees this connection as plain http, so that's the callback it sends your
                  provider. Turn on Behind a reverse proxy below and restart, have your proxy send
                  X-Forwarded-Proto, or set the callback URL override to your https address.
                </Note>
              </div>
            </Row>
            <Row sub field label="Issuer URL" htmlFor="security-oidc-issuer">
              <TextField
                wide
                mono
                id="security-oidc-issuer"
                placeholder="https://id.example.com"
                autoComplete="off"
                {...issuer}
              />
            </Row>
            <Row sub field label="Client ID" htmlFor="security-oidc-client-id">
              <TextField wide mono id="security-oidc-client-id" autoComplete="off" {...clientId} />
            </Row>
            <Row sub field label="Client secret" htmlFor="security-oidc-client-secret">
              <SecretField
                wide
                id="security-oidc-client-secret"
                autoComplete="new-password"
                value={loads ? text(sec.oidc_client_secret) : ''}
                onChange={(v) => set('oidc_client_secret', v)}
              />
            </Row>
            <Row
              sub
              field
              label="Button label"
              htmlFor="security-oidc-button-label"
              help="What the sign-in button says."
            >
              <TextField
                wide
                id="security-oidc-button-label"
                placeholder="Sign in with OIDC"
                autoComplete="off"
                maxLength={40}
                {...buttonLabel}
              />
            </Row>
            <Row
              sub
              label="Let new OIDC users sign up"
              htmlFor="security-oidc-signin-auto-create"
              help="Off: only accounts already connected to a profile can sign in."
            >
              <Switch
                id="security-oidc-signin-auto-create"
                checked={loads ? sec.oidc_signin_auto_create !== false : true}
                onChange={(event) => set('oidc_signin_auto_create', event.target.checked)}
              />
            </Row>
            <More count={5} names="icon, required group, groups claim, scopes, callback override">
              <Row
                sub
                field
                label="Icon URL"
                htmlFor="security-oidc-icon-url"
                help="Your provider's logo on the sign-in card in My Account, https only. Empty shows a plain key."
              >
                <TextField
                  wide
                  mono
                  id="security-oidc-icon-url"
                  placeholder="https://..."
                  autoComplete="off"
                  {...iconUrl}
                />
              </Row>
              <Row
                sub
                field
                label="Required group"
                htmlFor="security-oidc-required-group"
                help="Only accounts in this group get in. Empty lets every account at the provider sign in."
              >
                <TextField
                  wide
                  mono
                  id="security-oidc-required-group"
                  autoComplete="off"
                  {...group}
                />
              </Row>
              <Row sub field label="Groups claim" htmlFor="security-oidc-groups-claim">
                <TextField
                  wide
                  mono
                  id="security-oidc-groups-claim"
                  placeholder="groups"
                  autoComplete="off"
                  {...claim}
                />
              </Row>
              <Row
                sub
                field
                label="Scopes"
                htmlFor="security-oidc-scopes"
                help="Add the scope your provider needs to send groups, if you set a required group."
                more={
                  <>
                    Pocket ID: <code>groups</code>. Add <code>offline_access</code> on Authentik or
                    Authelia so signing someone out there signs them out here too.
                  </>
                }
              >
                <TextField
                  wide
                  mono
                  id="security-oidc-scopes"
                  placeholder="openid profile email"
                  autoComplete="off"
                  {...scopes}
                />
              </Row>
              <Row
                sub
                field
                label="Callback URL override"
                htmlFor="security-oidc-redirect-uri"
                help="Only if your reverse proxy doesn't pass the public address through."
              >
                <TextField
                  wide
                  mono
                  id="security-oidc-redirect-uri"
                  autoComplete="off"
                  {...redirect}
                />
              </Row>
            </More>
            {/* the admin's own connection: the shell draws it (oidc-signin.ts) */}
            <Row
              sub
              stack
              label="Your admin account"
              help="Connect it to sign in as the admin with OIDC."
            >
              <div
                className="security-oidc-admin"
                id="security-oidc-admin"
                dangerouslySetInnerHTML={CONNECT_BOX}
              />
            </Row>
            {/* sign in with OIDC only (#1839; oidc-only.ts draws the status) */}
            <Row
              sub
              id="security-oidc-only-wrap"
              label="OIDC only"
              htmlFor="security-oidc-only"
              help="Turns off every other way to sign in. Needs sign-in on, and you signed in with OIDC yourself."
              more={
                <>
                  Password sign-in, the recovery question, Sign in with Plex and switching profiles
                  with a password stop working, and anyone signed in some other way is signed out.
                  Invites send people to your provider instead of asking for a password. Every
                  profile needs an OIDC connection first. Passwords are kept and work again if you
                  turn this off. If the provider is ever down, set{' '}
                  <code>SOULSYNC_OIDC_ONLY_OVERRIDE=off</code> on the container and restart, then
                  sign in with the admin password.
                </>
              }
            >
              <Switch
                id="security-oidc-only"
                checked={oidcOnly}
                onChange={(event) => set('oidc_only', event.target.checked)}
              />
            </Row>
            <div
              className={`setting-help-text ${s.oidcStatus}`}
              id="security-oidc-only-status"
              hidden
              dangerouslySetInnerHTML={EMPTY_BOX}
            />
          </div>

          <Row
            field
            id="security-signup-template"
            label="New people start as"
            help="Everyone who signs in with Plex or OIDC for the first time follows this template: the sides, pages and downloads they get, and any request limit."
          >
            <Value>{signupTemplate ?? 'A template'}</Value>
            <Btn
              small
              id="security-signup-template-open"
              onClick={() => openPeopleSettings('templates')}
            >
              Change in People
            </Btn>
          </Row>
        </Card>

        <Card
          title="Reverse proxy"
          hint={
            <>
              Only for SoulSync on the internet behind nginx, Caddy or Traefik. See{' '}
              <code>Support/REVERSE-PROXY.md</code>.
            </>
          }
        >
          <Row
            label={
              <>
                Behind a reverse proxy <Tag>Restart</Tag>
              </>
            }
            htmlFor="security-trust-proxy"
            help="Trusts the proxy's X-Forwarded headers, makes the session cookie HTTPS only and adds security headers. Takes effect after a restart."
            more="Leave it off for direct or home network access over http://: it would break plain-HTTP sign-in."
          >
            <Switch
              id="security-trust-proxy"
              checked={proxy}
              onChange={(event) => set('trust_reverse_proxy', event.target.checked)}
            />
          </Row>
          <Row
            sub
            field
            label="Auth proxy user header"
            htmlFor="security-auth-proxy-header"
            help="If an auth proxy (Authelia, Authentik, oauth2-proxy) signs people in in front of SoulSync, the header it sets. SoulSync trusts it from the addresses below. Empty is off."
            more="Only set this behind a proxy that strips any copy of the header a client sends, or anyone could sign in as anyone."
          >
            <TextField
              wide
              mono
              id="security-auth-proxy-header"
              placeholder="Remote-User"
              autoComplete="off"
              {...authHeader}
            />
          </Row>
          <Row
            sub
            stack
            label="Trusted proxy addresses"
            htmlFor="security-auth-proxy-trusted"
            help="Your proxy's IP or range, one per line or comma separated. Empty means the header is ignored."
            more="The header only counts on requests that really come from one of these, never from a forwarded address."
            error={
              authHeader.value && !proxyTrusted.value ? (
                <span id="security-auth-proxy-untrusted">
                  The header is being ignored until you add your proxy&apos;s address here, so
                  people get the PIN screen instead.
                </span>
              ) : null
            }
          >
            <TextArea
              mono
              id="security-auth-proxy-trusted"
              rows={2}
              invalid={!!authHeader.value && !proxyTrusted.value}
              placeholder={'172.18.0.2\n172.18.0.0/16'}
              value={proxyTrusted.value}
              onChange={proxyTrusted.onChange}
            />
          </Row>
          <Row
            stack
            label="Allowed WebSocket origins"
            htmlFor="security-cors-origins"
            help="Addresses allowed to open live-update connections, one per line or comma-separated. Empty is same-origin only, the safe default."
            more={
              <>
                A full address with no trailing slash. Add your public domain if live updates fail
                behind a proxy. <code>*</code> on its own line allows any origin, which is not safe.
              </>
            }
            error={
              corsBad.length
                ? `${corsBad.length === 1 ? `"${corsBad[0]}" is` : `${corsBad.length} entries are`} not a full address like https://soulsync.example.com, so ${corsBad.length === 1 ? 'it is' : 'they are'} ignored.`
                : null
            }
          >
            <TextArea
              mono
              id="security-cors-origins"
              rows={3}
              invalid={corsBad.length > 0}
              placeholder={'https://soulsync.example.com\nhttp://192.168.1.5:8888'}
              value={loads ? cors.value : ''}
              onChange={(event) => cors.onChange(event.target.value)}
            />
          </Row>
        </Card>
      </div>
    </>
  );
}
