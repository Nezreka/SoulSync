/**
 * Connections > MetaSync: pairing SoulSync with the MetaSync companion (or a
 * pasted local key), the pool key handed over to it, its live status, and
 * disconnect. Every call goes to SoulSync's own /api/metasync endpoints, so
 * the browser never has to reach MetaSync. Its fields are not settings keys:
 * they save through PUT /api/metasync/settings, and a stored secret never
 * comes back here (an empty field means unchanged).
 *
 * It loads each time the Connections tab opens, as settings.js
 * loadMetaSyncSettings did from the tab switch. On the parts since
 * settings-bic phase 7: a hero, then one card per step, the status, and
 * Disconnect in a danger zone.
 */

import { Fragment, type ReactNode, useEffect, useRef, useState } from 'react';

import type { SettingsTabProps } from '../host';
import type { HeroPart } from './downloads/words';

import { Btn, Card, DangerRow, FieldNote, More, Note, Row, StatusWords, TextField } from '../ui';
import s from '../ui/settings.module.css';
import { LibHero } from './library-card';
import { text } from './source-panels';

type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  showConfirmDialog?: (o: {
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    destructive?: boolean;
  }) => Promise<boolean>;
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

type Answer = Record<string, unknown>;
async function msApi(method: string, path: string, body?: unknown): Promise<Answer> {
  const resp = await fetch(`/api/metasync${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await resp.json()) as Answer;
}

// the node's binding words, in plain language
const BINDING_TEXT: Record<string, string> = {
  bound: 'Bound to the pool',
  pending_approval: 'Waiting for approval in the portal',
  auth_failed: 'Pool authentication failed',
  unbound: 'Not bound yet',
  not_checked: 'Not checked yet',
  not_connected: 'No pool key on MetaSync',
  not_applicable: 'Direct key (no binding)',
};

interface Pool {
  name?: string;
  id?: string;
  url?: string;
  binding_status?: string;
  last_exchange_error?: string;
}

/** settings.js refreshMetaSyncStatus: the readout and its lines */
function statusLines(s: Answer, nodeUrl: string): ReactNode[] {
  const lines: ReactNode[] = [];
  if (s.node_fingerprint)
    lines.push(
      `MetaSync ID: ${text(s.node_fingerprint)} (the same ID MetaSync shows in its dashboard)`,
    );
  const pools = (Array.isArray(s.pools) ? s.pools : []) as Pool[];
  if (!pools.length)
    lines.push('Step 2: no pool joined yet. Send your pool key above to join one.');
  for (const p of pools) {
    const binding = BINDING_TEXT[p.binding_status || ''] || p.binding_status || 'Unknown';
    let line = `Pool ${p.name || p.id || 'Pool'} (${p.url || ''}): ${binding}`;
    if (p.last_exchange_error) line += `. Last error: ${p.last_exchange_error}`;
    lines.push(line);
  }
  // step 3: is this install counted? a node starts in dry run and sends
  // nothing; an older node does not report dry_run at all
  const open = /^https?:\/\//i.test(nodeUrl) ? (
    <>
      {' '}
      <a href={nodeUrl} target="_blank" rel="noopener noreferrer">
        Open MetaSync
      </a>
    </>
  ) : null;
  if (s.dry_run === true && pools.length) {
    const u = s.unsent_report as { week?: string; signal_count?: number } | undefined;
    lines.push(
      u && u.week ? (
        <>
          Step 3: dry run is on. Your report for {u.week} is ready ({text(u.signal_count)} anonymous
          album signals) and nothing was sent, so this install isn't counted yet. Check it and press
          Start sending in MetaSync.{open}
        </>
      ) : (
        <>
          Step 3: dry run is on, so MetaSync builds your weekly report but sends nothing yet.{open}
        </>
      ),
    );
  } else if (s.dry_run === false && pools.length) {
    lines.push('Step 3: sending. One anonymous report a week, nothing else.');
  }
  const net = s.network as
    | { installs?: number; publish_threshold?: number; week?: string }
    | undefined;
  if (net && Number.isInteger(net.installs) && Number.isInteger(net.publish_threshold)) {
    const installs = net.installs as number;
    const threshold = net.publish_threshold as number;
    lines.push(
      `The network: ${installs} install${installs === 1 ? '' : 's'} reported ${net.week || 'last week'}. ` +
        (installs < threshold
          ? `Charts from the network start at ${threshold} installs per album; until then they fill from ListenBrainz.`
          : `An album charts once ${threshold} installs report it in a week.`),
    );
  }
  if (s.pairing_status === 'pending')
    lines.push('A pairing request is waiting for approval in the MetaSync dashboard.');
  return lines;
}

// ── the hero ──

/** what the hero reads: the settings answer and the status answer */
export interface MetaSyncFacts {
  /** null until the settings answered */
  linked: boolean | null;
  version?: string;
  /** MetaSync can read this SoulSync */
  reading?: boolean;
  pools: Pool[];
  dryRun?: boolean;
}

const say = (t: string): HeroPart => ({ text: t });
const named = (t: string): HeroPart => ({ text: t, kind: 'name' });
const warn = (t: string): HeroPart => ({ text: t, kind: 'warn' });

/** MetaSync read back in one sentence. Pure */
export function metasyncSentence(f: MetaSyncFacts): HeroPart[] {
  if (f.linked === null) return [say('Loading MetaSync.')];
  if (!f.linked)
    return [
      say(
        'SoulSync is not connected to MetaSync. Connect pairs the two programs on this machine; joining a pool is a second step, and nothing is shared until you take it.',
      ),
    ];
  const parts: HeroPart[] = [
    say('SoulSync is connected to '),
    named(`MetaSync${f.version ? ` ${f.version}` : ''}`),
  ];
  if (f.reading === false) parts.push(say(', which '), warn('cannot read this SoulSync yet'));
  if (!f.pools.length) {
    parts.push(say('. It has not joined a pool, so nothing is shared.'));
    return parts;
  }
  parts.push(say('. It is in '));
  f.pools.forEach((p, i) => {
    if (i) parts.push(say(i === f.pools.length - 1 ? ' and ' : ', '));
    parts.push(named(p.name || p.id || 'a pool'));
  });
  const stuck = f.pools.filter(
    (p) =>
      p.binding_status && p.binding_status !== 'bound' && p.binding_status !== 'not_applicable',
  );
  if (stuck.length)
    parts.push(
      say(', '),
      warn(
        stuck.length === 1
          ? `${stuck[0].name || 'one pool'}: ${(BINDING_TEXT[stuck[0].binding_status || ''] || 'not bound').toLowerCase()}`
          : `${stuck.length} pools are not bound yet`,
      ),
    );
  if (f.dryRun === true) parts.push(say('. Dry run is on, so nothing is sent yet.'));
  else if (f.dryRun === false) parts.push(say('. It sends one anonymous report a week.'));
  else parts.push(say('.'));
  return parts;
}

function MetaSyncHero({ facts }: { facts: MetaSyncFacts }) {
  return (
    <LibHero
      parts={metasyncSentence(facts)}
      stats={
        facts.linked === null
          ? undefined
          : [
              { value: facts.linked ? 'Connected' : 'Not connected', label: 'MetaSync' },
              { value: facts.pools.length, label: facts.pools.length === 1 ? 'Pool' : 'Pools' },
              {
                value:
                  facts.dryRun === true ? 'Dry run' : facts.dryRun === false ? 'Sending' : 'None',
                label: 'Reports',
              },
            ]
      }
    />
  );
}

/** _msAutoDetectTried: one auto-detect a page load, not one a Settings visit
 *  (the page unmounts when it is left; SETTINGS_PORT.md step V4c) */
export const detectTried = { done: false };

export function MetaSyncSection({ opened }: Pick<SettingsTabProps, 'opened'>) {
  const [nodeUrl, setNodeUrl] = useState('');
  const [poolUrl, setPoolUrl] = useState('');
  const [poolKey, setPoolKey] = useState('');
  const [localKey, setLocalKey] = useState('');
  const [poolKeySet, setPoolKeySet] = useState(false);
  const [connected, setConnected] = useState('Not connected');
  const [pair, setPair] = useState<{ code: string; fingerprint: string } | null>(null);
  const [status, setStatus] = useState('Loading...');
  const [statusTone, setStatusTone] = useState<'idle' | 'ok' | 'warn'>('idle');
  const [detail, setDetail] = useState<ReactNode[]>([]);
  const [facts, setFacts] = useState<MetaSyncFacts>({ linked: null, pools: [] });
  const nodeField = useRef<HTMLInputElement>(null);
  const poolField = useRef<HTMLInputElement>(null);
  // what the status line reads for the Open MetaSync link: the field as it is
  const nodeNow = useRef('');
  nodeNow.current = nodeUrl;

  /** find the node in the usual places; fills the field on a find */
  async function detect() {
    try {
      const data = await msApi('POST', '/detect');
      if (data?.success && data.found && data.node_url) {
        setNodeUrl(text(data.node_url));
        nodeNow.current = text(data.node_url);
        return text(data.node_url);
      }
    } catch (error) {
      console.error('metasync detect failed', error);
    }
    return '';
  }

  async function refreshStatus() {
    try {
      const data = await msApi('GET', '/status');
      if (!data || data.success !== true) {
        setStatus(text(data?.error) || 'MetaSync status unavailable');
        setStatusTone('warn');
        setDetail([]);
        return;
      }
      const s = (data.status || {}) as Answer;
      setStatus(
        `Step 1: connected to MetaSync ${text(s.version)}, which ${
          s.soulsync_connected ? 'is reading this SoulSync' : 'is not reading this SoulSync yet'
        }`,
      );
      setStatusTone(s.soulsync_connected ? 'ok' : 'warn');
      setDetail(statusLines(s, nodeNow.current.trim()));
      setFacts((f) => ({
        ...f,
        version: text(s.version) || undefined,
        reading: !!s.soulsync_connected,
        pools: (Array.isArray(s.pools) ? s.pools : []) as Pool[],
        dryRun: typeof s.dry_run === 'boolean' ? s.dry_run : undefined,
      }));
    } catch {
      setStatus('MetaSync status unavailable');
      setStatusTone('warn');
      setDetail([]);
    }
  }

  async function load() {
    try {
      const data = await msApi('GET', '');
      if (!data || data.success !== true) {
        setStatus('Could not load MetaSync settings');
        setStatusTone('warn');
        return;
      }
      // a field being typed in is left alone
      if (document.activeElement !== nodeField.current) {
        setNodeUrl(text(data.node_url));
        nodeNow.current = text(data.node_url);
      }
      if (document.activeElement !== poolField.current) setPoolUrl(text(data.pool_url));
      setPoolKeySet(!!data.pool_key_set);
      setConnected(
        data.local_key_set
          ? `Connected${data.node_fingerprint ? `, MetaSync ID ${text(data.node_fingerprint)}` : ''}`
          : 'Not connected',
      );
      setFacts((f) => ({ ...f, linked: !!data.local_key_set }));
      await refreshStatus();
      // auto-detect once a page load, only while there is nothing to lose
      if (!detectTried.done && !data.local_key_set && !nodeNow.current.trim()) {
        detectTried.done = true;
        const found = await detect();
        if (found) {
          setStatus(`Found MetaSync on this machine at ${found}. Press Connect to pair with it.`);
          setStatusTone('idle');
        }
      }
    } catch (error) {
      console.error('metasync settings load failed', error);
      setStatus('Could not load MetaSync settings');
      setStatusTone('warn');
    }
  }

  useEffect(() => {
    if (opened > 0) void load();
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    const body: Answer = { node_url: nodeUrl.trim(), pool_url: poolUrl.trim() };
    if (poolKey.trim()) body.pool_key = poolKey.trim();
    if (localKey.trim()) body.local_api_key = localKey.trim();
    try {
      const data = await msApi('PUT', '/settings', body);
      if (data.success) {
        toast('MetaSync settings saved', 'success');
        setPoolKey('');
        setLocalKey('');
        void load();
      } else {
        toast(text(data.error) || 'Could not save MetaSync settings', 'error');
      }
    } catch (error) {
      toast(`Could not save MetaSync settings: ${errText(error)}`, 'error');
    }
  }

  async function pairRequest() {
    // the node URL goes with the request, so claim and handoff use the same one
    let url = nodeUrl.trim();
    if (!url) url = await detect();
    if (!url) {
      toast('Enter the MetaSync URL first', 'error');
      return;
    }
    try {
      const data = await msApi('POST', '/pair/request', { node_url: url });
      if (!data.success) {
        toast(text(data.error) || 'Pairing request failed', 'error');
        return;
      }
      setPair({ code: text(data.pairing_code), fingerprint: text(data.node_fingerprint) });
      toast('Approve the pairing in the MetaSync dashboard', 'success');
    } catch (error) {
      toast(`Pairing request failed: ${errText(error)}`, 'error');
    }
  }

  async function pairClaim() {
    try {
      const data = await msApi('POST', '/pair/claim');
      if (data.success) {
        toast('MetaSync connected', 'success');
        setPair(null);
        void load();
      } else {
        toast(text(data.error) || 'Could not finish connecting', 'error');
      }
    } catch (error) {
      toast(`Could not finish connecting: ${errText(error)}`, 'error');
    }
  }

  async function handoff() {
    // unsaved field values ride along; the server keeps what it used
    const body: Answer = { pool_url: poolUrl.trim() };
    if (poolKey.trim()) body.pool_key = poolKey.trim();
    try {
      const data = await msApi('POST', '/handoff', body);
      if (!data.success) {
        toast(text(data.error) || 'Handoff failed', 'error');
        return;
      }
      if (data.exchange_status === 'bound')
        toast('Pool key handed over. MetaSync is bound to the pool', 'success');
      else if (data.exchange_status === 'binding_pending')
        toast('Pool key handed over, waiting for approval in the portal', 'warning');
      else if (data.exchange_status === 'error')
        toast(
          `Pool key handed over, but the pool exchange failed: ${text(data.exchange_error) || 'unknown error'}`,
          'error',
        );
      else toast('Pool key handed over to MetaSync', 'success');
      void load();
    } catch (error) {
      toast(`Handoff failed: ${errText(error)}`, 'error');
    }
  }

  async function disconnect() {
    const ok = await shell().showConfirmDialog?.({
      title: 'Disconnect MetaSync?',
      message:
        'The pool key on MetaSync is cleared and the SoulSync key it holds is revoked. Your pool key stays saved here.',
      confirmText: 'Disconnect',
      cancelText: 'Cancel',
      destructive: true,
    });
    if (!ok) return;
    try {
      const data = await msApi('POST', '/disconnect');
      if (data.success) {
        toast(
          data.node_notified
            ? 'MetaSync disconnected'
            : 'Disconnected here. MetaSync could not be reached, so clear its pool key from its dashboard too',
          'success',
        );
        setPair(null);
        void load();
      } else {
        toast(text(data.error) || 'Disconnect failed', 'error');
      }
    } catch (error) {
      toast(`Disconnect failed: ${errText(error)}`, 'error');
    }
  }

  const linked = connected !== 'Not connected';

  return (
    <div data-metasync="">
      <MetaSyncHero facts={facts} />
      <div className={s.cards}>
        <div className={s.stack}>
          <Note info>
            MetaSync is a small companion program that runs next to SoulSync and joins pools for
            you. The first pool is SIN, the signal network. Step 1 connects the two programs on this
            machine; step 2 joins a pool, and MetaSync does the joining: SoulSync never talks to a
            pool itself, it only hands MetaSync your pool key. Installing MetaSync joins nothing by
            itself.
          </Note>
        </div>

        <Card
          title="Step 1: connect SoulSync and MetaSync"
          hint="On this machine. Nothing is shared yet."
          actions={
            <Btn kind={linked ? undefined : 'primary'} onClick={() => void pairRequest()}>
              Connect
            </Btn>
          }
        >
          <Row
            field
            label="Connection"
            help="Connect shows a code here and in MetaSync's dashboard. Approve it there and the two programs swap keys on their own."
            more="MetaSync gets a key to read this SoulSync, SoulSync gets a key to talk to MetaSync. You never type or see either key."
          >
            <StatusWords id="metasync-connect-state" tone={linked ? 'ok' : 'idle'}>
              {connected}
            </StatusWords>
          </Row>
          <div
            id="metasync-pair-box"
            className={s.cardPad}
            style={{ display: pair ? undefined : 'none' }}
          >
            <Note
              info
              action={
                <Btn kind="primary" onClick={() => void pairClaim()}>
                  I approved it, finish connecting
                </Btn>
              }
            >
              MetaSync is showing a pairing request. Code{' '}
              <strong id="metasync-pair-code" className={s.keyName}>
                {pair?.code || ''}
              </strong>
              , MetaSync&apos;s ID{' '}
              <code id="metasync-pair-fingerprint">{pair?.fingerprint || ''}</code>. Check both
              match its dashboard and approve it there, then finish here. Codes last 10 minutes, and
              the code on its own connects nothing.
            </Note>
          </div>
          <Row
            field
            label="MetaSync URL"
            htmlFor="metasync-node-url"
            help="Where the MetaSync program answers. On this same machine SoulSync finds it on its own (port 8790): type an address only if MetaSync lives somewhere else."
          >
            <TextField
              wide
              mono
              id="metasync-node-url"
              placeholder="http://localhost:8790"
              ref={nodeField}
              value={nodeUrl}
              onChange={(event) => setNodeUrl(event.target.value)}
            />
          </Row>
          <More label="Connect with a key instead" names="paste a local API key">
            <Row
              field
              label="MetaSync local API key"
              htmlFor="metasync-local-key"
              help="Pairing does this for you. The manual way: create a local API key in MetaSync's dashboard, paste it here, then Save below."
            >
              <TextField
                wide
                mono
                type="password"
                id="metasync-local-key"
                placeholder="Paste to connect manually"
                autoComplete="off"
                value={localKey}
                onChange={(event) => setLocalKey(event.target.value)}
              />
            </Row>
          </More>
        </Card>

        <Card
          title="Step 2: join a pool"
          hint="Optional. MetaSync joins and does all the talking."
          actions={
            <Btn kind={linked ? 'primary' : undefined} onClick={() => void handoff()}>
              Send pool key to MetaSync
            </Btn>
          }
        >
          <Row
            label="What a pool is"
            help="SIN, the SoulSync Intelligence Network, collects anonymous taste signals from SoulSync installs and turns them into shared playlists like Trending and Most Wanted."
            more={
              <>
                Bulk counts of what people watchlist and wishlist, never your personal data.
                MetaSync swaps your key for short-lived access tokens, so the key itself stays
                between you and MetaSync. Nothing is joined until you send the key. No key yet?{' '}
                <a href="https://metasync.ssync.net" target="_blank" rel="noopener">
                  Get one at the SIN front desk
                </a>
                : sign in there, create a key, then put it below.
              </>
            }
          />
          <Row field label="Pool URL" htmlFor="metasync-pool-url" help="From your pool account.">
            <TextField
              wide
              mono
              id="metasync-pool-url"
              placeholder="Pool URL from your pool account"
              ref={poolField}
              value={poolUrl}
              onChange={(event) => setPoolUrl(event.target.value)}
            />
          </Row>
          <Row
            field
            label="Pool key"
            htmlFor="metasync-pool-key"
            help="Your own key for the pool. SoulSync keeps it only to hand it to MetaSync when you press Send, and never uses it itself."
          >
            <TextField
              wide
              mono
              type="password"
              id="metasync-pool-key"
              autoComplete="off"
              placeholder={
                poolKeySet ? 'Saved, type a new key only to replace it' : 'Your pool account key'
              }
              value={poolKey}
              onChange={(event) => setPoolKey(event.target.value)}
            />
          </Row>
          <Row
            label="Keep these for later"
            help="Save stores the URLs and keys you typed without sending anything. Send pool key is what joins the pool."
          >
            <Btn onClick={() => void save()}>Save</Btn>
          </Row>
        </Card>

        <Card
          title="Status"
          hint="What MetaSync says now"
          actions={
            <Btn icon="refresh" onClick={() => void refreshStatus()}>
              Refresh status
            </Btn>
          }
        >
          <Row label="MetaSync" stack>
            <StatusWords id="metasync-status" tone={statusTone} className={s.stWrap}>
              {status}
            </StatusWords>
            <FieldNote id="metasync-status-detail">
              {detail.map((line, i) => (
                <Fragment key={i}>
                  {i ? <br /> : null}
                  {line}
                </Fragment>
              ))}
            </FieldNote>
          </Row>
        </Card>

        <Card title="Danger zone" danger>
          <DangerRow
            label="Disconnect MetaSync"
            help="Undoes step 1 and step 2: MetaSync leaves the pool, the key it reads this SoulSync with is revoked, and SoulSync forgets MetaSync's key. Your pool key stays saved above."
            action="Disconnect"
            onAction={() => void disconnect()}
          />
        </Card>
      </div>
    </div>
  );
}
