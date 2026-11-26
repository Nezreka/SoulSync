/**
 * Connections: the music side's hidden API Configuration group and the music
 * service cards not in connections-metadata.tsx (Concerts, Last.fm, Genius,
 * Pollinations, fanart.tv, AcoustID, ListenBrainz), on the settings parts.
 *
 * Nothing shows the group itself (style.css hides it): it is where the cards
 * live between visits to the service sheet. Each card portals into a wrapper
 * kept here (connections-services.tsx). Its Expand All and the row of six
 * Test buttons nobody could see went (settings-bic DESIGN 3.3).
 */

import { type ReactNode, useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../settings-store';

import {
  ActionRow,
  Btn,
  CopyButton,
  FieldLine,
  More,
  Note,
  Row,
  SecretField,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import {
  HydrabaseCard,
  MetadataServiceCards,
  MusicbrainzCard,
  ServiceCard,
  testService,
  useText,
} from './connections-metadata';
import { ServiceHome } from './connections-services';
import { useLoadedDraft } from './library-card';
import { REDACTED_SECRET_SENTINEL } from './parts';
import { SAVED_KEY_NOTE, secretNote, text, useSettings } from './source-panels';

type Section_ = Record<string, unknown>;
type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  showLoadingOverlay?: (message?: string) => void;
  hideLoadingOverlay?: () => void;
  saveSettings?: (quiet?: boolean) => Promise<void>;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);

function useLoads(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().loads);
}

/** a field the page saved trimmed (settings.js _cfgStr with trim): what is
 *  typed shows as typed, until the next load */
function useTrimmed(
  store: SettingsStore,
  section: string,
  key: string,
  shape: (value: string) => string = (v) => v,
) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const saved = text(((settings[section] || {}) as Section_)[key] || '');
  const draft = useLoadedDraft(store, saved, (raw) => store.set(section, key, shape(raw.trim())));
  return { value: loads ? draft.value : '', onChange: draft.onChange };
}

function useChecked(store: SettingsStore, section: string, key: string) {
  const settings = useSettings(store);
  return {
    checked: ((settings[section] || {}) as Section_)[key] === true,
    onChange: (event: { target: { checked: boolean } }) =>
      store.set(section, key, event.target.checked),
  };
}

// ── Concerts ──

function ConcertsCard({ store }: { store: SettingsStore }) {
  const tm = useTrimmed(store, 'concerts', 'ticketmaster_api_key');
  const country = useTrimmed(store, 'concerts', 'country', (v) => v.toUpperCase());
  const sl = useTrimmed(store, 'concerts', 'setlistfm_api_key');
  return (
    <ServiceCard
      service="concerts"
      colour="#00b4b3"
      title="Concerts"
      test={() => testService(store, 'concerts')}
    >
      <Note info>
        Adds a <strong>Live</strong> section to artist pages. Both keys are optional and
        independent: set up either, both, or neither.
      </Note>
      <SheetGroup title="Upcoming dates" />
      <SheetField
        label="Ticketmaster API key"
        htmlFor="concerts-ticketmaster-api-key"
        help="Upcoming dates, venues and ticket links. A free self-service key from developer.ticketmaster.com (5,000 calls a day)."
      >
        <TextField
          full
          id="concerts-ticketmaster-api-key"
          placeholder="Your Discovery API key"
          value={tm.value}
          onChange={(event) => tm.onChange(event.target.value)}
        />
      </SheetField>
      <Row
        label="Concerts near you"
        htmlFor="concerts-country"
        field
        help="Two letters, like US or GB. Keeps the concerts in your Discover inbox to one country. Leave it empty for everywhere. Artist pages always show every date."
      >
        <TextField
          id="concerts-country"
          className={s.fieldCode}
          maxLength={2}
          placeholder="US"
          autoComplete="off"
          value={country.value}
          onChange={(event) => country.onChange(event.target.value)}
        />
      </Row>
      <SheetGroup title="Setlists" />
      <SheetField
        label="Setlist.fm API key"
        htmlFor="concerts-setlistfm-api-key"
        help="What the artist played at recent shows, song by song, and a setlist can be played straight from your library. A free key from setlist.fm/settings/api (non-commercial use)."
      >
        <TextField
          full
          id="concerts-setlistfm-api-key"
          placeholder="Your setlist.fm API key"
          value={sl.value}
          onChange={(event) => sl.onChange(event.target.value)}
        />
      </SheetField>
    </ServiceCard>
  );
}

// ── Last.fm ──

/** From 1 December 2026 a Last.fm API key needs a Pro subscription. The
 *  server remembers a refused key (/api/lastfm/configured says so); the card
 *  then says it plainly instead of a bare "invalid key". */
export const LASTFM_PRO_NOTE =
  'Last.fm API keys need a Pro subscription from 1 December 2026. Without one, SoulSync fills in from free sources (ListenBrainz, Deezer).';

export interface LastfmKeyStatus {
  key_rejected: boolean;
  message: string;
}

export function useLastfmKeyStatus(): [LastfmKeyStatus, () => void] {
  const [status, setStatus] = useState<LastfmKeyStatus>({ key_rejected: false, message: '' });
  const refresh = useCallback(() => {
    fetch('/api/lastfm/configured')
      .then((resp) => (resp.ok ? resp.json() : null))
      .then((data: Partial<LastfmKeyStatus> | null) => {
        if (!data) return;
        const next = { key_rejected: !!data.key_rejected, message: data.message || '' };
        // unchanged: keep the same object, so nothing re-renders
        setStatus((prev) =>
          prev.key_rejected === next.key_rejected && prev.message === next.message ? prev : next,
        );
      })
      .catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);
  return [status, refresh];
}

function LastfmCard({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const key = useText(store, 'lastfm', 'api_key');
  const secret = useText(store, 'lastfm', 'api_secret');
  const user = useTrimmed(store, 'lastfm', 'username');
  const scrobble = useChecked(store, 'lastfm', 'scrobble_enabled');
  const authorized = !!((settings.lastfm || {}) as Section_).session_key;
  const [keyStatus, refreshKeyStatus] = useLastfmKeyStatus();

  // save first, so the API secret is stored
  async function authorize() {
    try {
      await shell().saveSettings?.();
      const resp = await fetch('/api/lastfm/auth-url');
      const data = (await resp.json()) as { success?: boolean; url?: string; error?: string };
      if (data.success && data.url) {
        window.open(data.url, '_blank', 'width=600,height=500');
        toast('Authorize SoulSync in the Last.fm window that opened', 'info');
      } else {
        toast(data.error || 'Could not generate auth URL', 'error');
      }
    } catch {
      toast('Failed to start Last.fm authorization', 'error');
    }
  }

  return (
    <ServiceCard
      service="lastfm"
      colour="#d51007"
      title="Last.fm"
      test={() => testService(store, 'lastfm').then(refreshKeyStatus)}
    >
      {keyStatus.key_rejected ? (
        <div id="lastfm-key-rejected" role="status">
          <Note>{keyStatus.message}</Note>
        </div>
      ) : null}
      <SheetGroup title="Your Last.fm API account" />
      <SheetField
        label="API key"
        htmlFor="lastfm-api-key"
        help={secretNote(key.value, 'Used for metadata enrichment.')}
        more={
          <>
            Get the key and secret from{' '}
            <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer">
              Last.fm API Account
            </a>
            . <span id="lastfm-pro-note">{LASTFM_PRO_NOTE}</span>
          </>
        }
      >
        <SecretField id="lastfm-api-key" placeholder="Last.fm API Key" {...key} />
      </SheetField>
      <SheetField
        label="API secret"
        htmlFor="lastfm-api-secret"
        help={secretNote(secret.value, 'Needed for scrobbling.')}
      >
        <SecretField
          id="lastfm-api-secret"
          placeholder="Last.fm API Secret (required for scrobbling)"
          {...secret}
        />
      </SheetField>
      <SheetField
        label="Username"
        htmlFor="lastfm-username"
        help="The Last.fm account whose listening history is imported. It can be corrected here or on the Stats page."
      >
        <TextField
          full
          id="lastfm-username"
          placeholder="Your Last.fm username"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
      </SheetField>
      <SheetGroup title="Scrobbling" />
      <Row
        label="Scrobble plays to Last.fm"
        htmlFor="lastfm-scrobble-enabled"
        help="ListenBrainz is the recommended scrobbler: free, no subscription."
      >
        <Switch id="lastfm-scrobble-enabled" {...scrobble} />
      </Row>
      <ActionRow
        id="lastfm-scrobble-actions"
        label="Allow scrobbling"
        help="Saves first, then opens Last.fm to let SoulSync scrobble for you."
      >
        <StatusWords id="lastfm-scrobble-status" tone={authorized ? 'ok' : 'idle'}>
          {loads ? (authorized ? 'Authorized' : 'Not authorized') : ''}
        </StatusWords>
        <Btn small onClick={() => void authorize()}>
          Authorize scrobbling
        </Btn>
      </ActionRow>
    </ServiceCard>
  );
}

// ── Genius, Pollinations, fanart.tv ──

function GeniusCard({ store }: { store: SettingsStore }) {
  const token = useText(store, 'genius', 'access_token');
  return (
    <ServiceCard
      service="genius"
      colour="#ffff64"
      title="Genius"
      test={() => testService(store, 'genius')}
    >
      <SheetField
        label="Client access token"
        htmlFor="genius-access-token"
        help={secretNote(token.value, 'Generate a Client Access Token. No sign-in flow needed.')}
        more={
          <>
            Get it from{' '}
            <a href="https://genius.com/api-clients" target="_blank" rel="noreferrer">
              Genius API Clients
            </a>{' '}
            with Generate Access Token.
          </>
        }
      >
        <SecretField id="genius-access-token" placeholder="Genius Client Access Token" {...token} />
      </SheetField>
    </ServiceCard>
  );
}

/** an optional key the page saved trimmed */
function OptionalKey({
  store,
  section,
  id,
  placeholder,
  help,
  more,
}: {
  store: SettingsStore;
  section: string;
  id: string;
  placeholder: string;
  help: string;
  more: ReactNode;
}) {
  const key = useTrimmed(store, section, 'api_key');
  return (
    <SheetField
      label="API key (optional)"
      htmlFor={id}
      help={secretNote(key.value, help)}
      more={more}
    >
      <SecretField id={id} placeholder={placeholder} autoComplete="off" {...key} />
    </SheetField>
  );
}

function PollinationsCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard
      service="pollinations"
      colour="#7ad3a7"
      title="Pollinations"
      test={() => testService(store, 'pollinations')}
    >
      <Note info>
        Makes cover art for playlists, from the ⋯ menu on any playlist. It works without a key: one
        image every 45 seconds, with a small Pollinations mark in the corner.
      </Note>
      <OptionalKey
        store={store}
        section="pollinations"
        id="pollinations-api-key"
        placeholder="Pollinations API key"
        help="A key removes the mark and the wait."
        more={
          <>
            A key from{' '}
            <a href="https://enter.pollinations.ai" target="_blank" rel="noopener noreferrer">
              enter.pollinations.ai
            </a>{' '}
            removes the mark and the wait.
          </>
        }
      />
    </ServiceCard>
  );
}

function FanartCard({ store }: { store: SettingsStore }) {
  return (
    <ServiceCard
      service="artist-fanart"
      colour="rgb(var(--accent-rgb))"
      title="fanart.tv"
      test={() => testService(store, 'fanart')}
    >
      <Note info>
        HD artist photos and the wide background Plex and Jellyfin show behind an artist page, used
        by <b>Update media server metadata</b> on the Tools page. Without a key SoulSync still tries
        TheAudioDB and Deezer.
      </Note>
      {/* the music card's own id: the video side's fanart.tv card owns fanart-api-key */}
      <OptionalKey
        store={store}
        section="fanart"
        id="artist-fanart-api-key"
        placeholder="fanart.tv personal API key"
        help="The video side's fanart.tv key is used when this is empty."
        more={
          <>
            A free key from{' '}
            <a href="https://fanart.tv/get-an-api-key/" target="_blank" rel="noopener noreferrer">
              fanart.tv
            </a>
            . The video side's fanart.tv key is used when this is empty.
          </>
        }
      />
    </ServiceCard>
  );
}

// ── AcoustID ──

/** /api/acoustid/account (#2318): never carries a key */
export interface AcoustidAccount {
  has_user_key: boolean;
  source: '' | 'linked' | 'pasted';
  state: '' | 'pending' | 'approved' | 'declined' | 'expired' | 'revoked' | 'unknown';
  code: string;
  url: string;
  expires: string;
  has_app_key: boolean;
  builtin_app_key: boolean;
  /** linking waits for SoulSync's own application key: a link is bound to the
   *  key that asked for it */
  can_link: boolean;
  link_unavailable_message: string;
  required_from: string;
  required_now: boolean;
  needs_link: boolean;
  message: string;
  error?: string;
}

const ACOUSTID_POLL_MS = 4000;

export const ACOUSTID_LINK_LATER =
  'Linking with one click arrives in a coming update; paste your key from acoustid.org for now.';

/** where AcoustID sends the person back to once they approve or decline */
export const acoustidReturnUrl = () =>
  `${window.location.origin}/settings?tab=connections&view=services&open=acoustid`;

async function acoustidCall(path: string, init?: RequestInit): Promise<AcoustidAccount | null> {
  const resp = await fetch(path, init);
  const data = (await resp.json().catch(() => null)) as
    | (Partial<AcoustidAccount> & { success?: boolean; error?: string })
    | null;
  if (!resp.ok || !data || data.success === false) {
    throw new Error(data?.error || 'AcoustID did not answer');
  }
  return data as AcoustidAccount;
}

/** the account, and while a link waits for approval, a check every few
 *  seconds until AcoustID answers or the code runs out */
export function useAcoustidAccount() {
  const [account, setAccount] = useState<AcoustidAccount | null>(null);
  const refresh = useCallback((check = false) => {
    acoustidCall(`/api/acoustid/account${check ? '?check=1' : ''}`)
      .then(setAccount)
      .catch(() => {});
  }, []);
  useEffect(() => refresh(true), [refresh]);
  const pending = account?.state === 'pending';
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => refresh(true), ACOUSTID_POLL_MS);
    return () => clearInterval(timer);
  }, [pending, refresh]);
  return { account, setAccount, refresh };
}

const LINK_WORDS: Record<string, [string, 'ok' | 'warn' | 'bad' | 'idle' | 'busy']> = {
  approved: ['Linked', 'ok'],
  pending: ['Waiting for you to approve on acoustid.org', 'busy'],
  declined: ['Declined on acoustid.org', 'bad'],
  expired: ['The code ran out. Link again', 'warn'],
  revoked: ['Disconnected on acoustid.org', 'bad'],
  unknown: ['AcoustID no longer knows that key. Link again', 'warn'],
};

export function acoustidStatusWords(
  account: AcoustidAccount | null,
): [string, 'ok' | 'warn' | 'bad' | 'idle' | 'busy'] {
  if (!account) return ['', 'idle'];
  if (account.source === 'pasted') return ['Your user key is saved', 'ok'];
  if (account.state && LINK_WORDS[account.state]) return LINK_WORDS[account.state];
  if (account.has_user_key) return ['Linked', 'ok'];
  return [
    account.needs_link ? 'Not linked: lookups stop' : 'Not linked',
    account.needs_link ? 'bad' : 'idle',
  ];
}

function AcoustidCard({ store }: { store: SettingsStore }) {
  const enabled = useChecked(store, 'acoustid', 'enabled');
  const key = useText(store, 'acoustid', 'api_key');
  const userKey = useText(store, 'acoustid', 'user_key');
  const { account, setAccount, refresh } = useAcoustidAccount();
  const [busy, setBusy] = useState(false);
  const [words, tone] = acoustidStatusWords(account);
  const canLink = account?.can_link === true;
  const pending = canLink && account?.state === 'pending';
  const linked = !!account?.has_user_key && !pending;
  const required = account?.required_from
    ? new Date(`${account.required_from}T00:00:00`).toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      })
    : '1 January 2027';
  // soulsync's own application key makes this field unneeded; one already
  // saved stays visible, so it can be seen and cleared
  const showAppKey = !account?.builtin_app_key || !!key.value;

  async function link() {
    setBusy(true);
    try {
      // a key typed above goes first, so the link uses it
      await shell().saveSettings?.(true);
      const next = await acoustidCall('/api/acoustid/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ return_url: acoustidReturnUrl() }),
      });
      setAccount(next);
      if (next?.url) window.open(next.url, '_blank', 'noopener');
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Could not start linking', 'error');
    } finally {
      setBusy(false);
    }
  }

  async function unlink() {
    setBusy(true);
    try {
      setAccount(await acoustidCall('/api/acoustid/unlink', { method: 'POST' }));
      toast('AcoustID key forgotten', 'success');
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Could not unlink', 'error');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ServiceCard
      service="acoustid"
      colour="#ba55d3"
      title="AcoustID Verification"
      test={() => Promise.resolve(testService(store, 'acoustid')).then(() => refresh())}
    >
      {account?.needs_link || (account?.message && !pending) ? (
        <div id="acoustid-needs-link" role="status">
          <Note>{account.message}</Note>
        </div>
      ) : !linked && !pending && canLink ? (
        <Note info>
          From {required} AcoustID only answers when your own AcoustID account is linked. Link it
          once below: it takes a minute and needs no copying.
        </Note>
      ) : null}
      {account && !canLink ? (
        <div id="acoustid-link-later">
          <Note info>
            From {required} AcoustID only answers with your own AcoustID user key.{' '}
            {account.link_unavailable_message || ACOUSTID_LINK_LATER}
          </Note>
        </div>
      ) : null}
      <SheetGroup title="Your AcoustID account" />
      {canLink ? (
        <ActionRow
          id="acoustid-link-row"
          label="Link your AcoustID account"
          help="Opens acoustid.org, where you sign in (MusicBrainz, Google or an email address) and approve SoulSync. Then it comes back here."
        >
          <StatusWords id="acoustid-link-status" tone={tone} live>
            {words}
          </StatusWords>
          {linked ? (
            <Btn small kind="ghost" busy={busy} onClick={() => void unlink()}>
              Unlink
            </Btn>
          ) : null}
          <Btn small kind={linked ? undefined : 'primary'} busy={busy} onClick={() => void link()}>
            {linked || pending ? 'Link again' : 'Link account'}
          </Btn>
        </ActionRow>
      ) : null}
      {pending ? (
        <div id="acoustid-link-pending">
          <SheetField
            label="Approve on acoustid.org"
            htmlFor="acoustid-link-code"
            help={
              <>
                Open{' '}
                <a href={account.url} target="_blank" rel="noreferrer">
                  the AcoustID link page
                </a>
                , or go to acoustid.org/link on any device and type this code. This page notices on
                its own once you approve.
              </>
            }
          >
            <TextField
              full
              mono
              readOnly
              id="acoustid-link-code"
              value={account.code}
              trailing={<CopyButton value={account.code} />}
            />
          </SheetField>
        </div>
      ) : null}
      <SheetField
        label={canLink ? 'Or paste your user API key' : 'Your user API key'}
        htmlFor="acoustid-user-key"
        help={
          userKey.value === REDACTED_SECRET_SENTINEL || account?.has_user_key ? (
            SAVED_KEY_NOTE
          ) : (
            <>
              The key on your{' '}
              <a href="https://acoustid.org/api-key" target="_blank" rel="noreferrer">
                acoustid.org account page
              </a>
              .{canLink ? ' Not needed once your account is linked.' : ''}
            </>
          )
        }
      >
        <SecretField id="acoustid-user-key" placeholder="AcoustID user API key" {...userKey} />
      </SheetField>
      {showAppKey ? (
        <SheetField
          label="Application API key"
          htmlFor="acoustid-api-key"
          help={secretNote(
            key.value,
            account?.builtin_app_key
              ? 'Not needed: SoulSync has its own application key.'
              : 'An application key, not your user account key.',
          )}
          more={
            <>
              Only for an application you registered yourself at{' '}
              <a href="https://acoustid.org/new-application" target="_blank" rel="noreferrer">
                acoustid.org/new-application
              </a>
              . Your user key above is still needed with it. The fpcalc fingerprint tool is
              downloaded when needed.
            </>
          }
        >
          <SecretField id="acoustid-api-key" placeholder="AcoustID Application API Key" {...key} />
        </SheetField>
      ) : null}
      <SheetGroup title="Options" />
      <Row
        label="Verify downloads"
        htmlFor="acoustid-enabled"
        help="Checks a downloaded file is the expected track by its fingerprint. A file that fails goes to the quarantine folder."
        more="Turning it off only stops the check on new downloads: the AcoustID Scanner in Tools still scans when you run it yourself. Clear the quarantine folder from System, Maintenance."
      >
        <Switch id="acoustid-enabled" {...enabled} />
      </Row>
    </ServiceCard>
  );
}

// ── ListenBrainz ──

/** self-hosted scrobblers that speak the ListenBrainz API, and the port
 *  each listens on out of the box */
export const SCROBBLER_PRESETS = [
  { id: 'maloja', label: 'Maloja', port: 42010 },
  { id: 'koito', label: 'Koito', port: 4110 },
] as const;

/** the ListenBrainz address on a self-hosted scrobbler: keeps the server
 *  already typed (and any reverse-proxy path before its api), else
 *  localhost on the scrobbler's own port */
export function scrobblerPresetUrl(current: string, preset: { port: number }): string {
  let root = (current || '').trim().replace(/\/+$/, '');
  root = root.replace(/\/apis\/(listenbrainz|lbrnz)(\/1)?$/i, '').replace(/\/1$/, '');
  if (!root || /api\.listenbrainz\.org/i.test(root)) root = `http://localhost:${preset.port}`;
  if (!/^https?:\/\//i.test(root)) root = `http://${root}`;
  return `${root}/apis/listenbrainz`;
}

function ListenbrainzCard({ store }: { store: SettingsStore }) {
  const base = useText(store, 'listenbrainz', 'base_url');
  const token = useText(store, 'listenbrainz', 'token');
  const user = useTrimmed(store, 'listenbrainz', 'username');
  const scrobble = useChecked(store, 'listenbrainz', 'scrobble_enabled');
  return (
    <ServiceCard
      service="listenbrainz"
      colour="#eb743b"
      title="ListenBrainz"
      test={() => testService(store, 'listenbrainz')}
    >
      <SheetField
        label="User token"
        htmlFor="listenbrainz-token"
        help={secretNote(token.value, 'From your ListenBrainz settings.')}
        more={
          <>
            Get it from{' '}
            <a href="https://listenbrainz.org/profile/" target="_blank" rel="noreferrer">
              ListenBrainz Settings
            </a>
            .
          </>
        }
      >
        <SecretField id="listenbrainz-token" placeholder="ListenBrainz User Token" {...token} />
      </SheetField>
      <SheetField
        label="Username (optional)"
        htmlFor="listenbrainz-username"
        help="Found from the token when left empty."
      >
        <TextField
          full
          id="listenbrainz-username"
          placeholder="Auto-detected from token if blank"
          value={user.value}
          onChange={(event) => user.onChange(event.target.value)}
        />
      </SheetField>
      <Row
        label="Scrobble plays to ListenBrainz"
        htmlFor="listenbrainz-scrobble-enabled"
        help={
          <span id="listenbrainz-recommended">
            Recommended for scrobbling: free and open, no subscription. Last.fm API keys need Pro
            from 1 December 2026.
          </span>
        }
      >
        <Switch id="listenbrainz-scrobble-enabled" {...scrobble} />
      </Row>
      <More label="More options" count={1} names="your own server, Maloja or Koito">
        <SheetField
          label="API base URL"
          htmlFor="listenbrainz-base-url"
          help="Self-hosted? Your server's address, for example http://localhost:8093. Leave it empty for the official one."
          more={
            <>
              Maloja: http://your-server:42010/apis/listenbrainz and your Maloja API key. Koito:
              http://your-server:4110/apis/listenbrainz and a Koito API key. Start the history sync
              from Stats.
            </>
          }
        >
          <FieldLine wrap>
            <TextField
              full
              mono
              id="listenbrainz-base-url"
              placeholder="Leave empty for official (api.listenbrainz.org)"
              value={base.value}
              onChange={(event) => base.onChange(event.target.value)}
            />
            {SCROBBLER_PRESETS.map((preset) => (
              <Btn
                key={preset.id}
                small
                id={`listenbrainz-preset-${preset.id}`}
                aria-label={`Fill in the address for ${preset.label}`}
                onClick={() => base.onChange(scrobblerPresetUrl(base.value, preset))}
              >
                {preset.label}
              </Btn>
            ))}
          </FieldLine>
        </SheetField>
      </More>
    </ServiceCard>
  );
}

// ── the group ──

/** the hidden home of the music cards: a tile opens one in the service sheet */
export function MusicApiGroup({ store }: { store: SettingsStore }) {
  const [home, setHome] = useState<HTMLElement | null>(null);
  return (
    <div data-music-only="" data-svc-legacy="" ref={setHome}>
      <ServiceHome.Provider value={home}>
        <Cards store={store} />
      </ServiceHome.Provider>
    </div>
  );
}

/** the cards, in the group's order (the wrappers keep it, see CARD_ORDER) */
function Cards({ store }: { store: SettingsStore }) {
  return (
    <>
      <MetadataServiceCards store={store} />
      <ConcertsCard store={store} />
      <LastfmCard store={store} />
      <GeniusCard store={store} />
      <PollinationsCard store={store} />
      <FanartCard store={store} />
      <MusicbrainzCard store={store} />
      <AcoustidCard store={store} />
      <ListenbrainzCard store={store} />
      <HydrabaseCard store={store} />
    </>
  );
}
