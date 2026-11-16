/**
 * Connections > MetaSync (SETTINGS_PORT.md, step 10d): pairing SoulSync with
 * the MetaSync companion (or a pasted local key), the pool key handed over to
 * it, its live status, and disconnect. Every call goes to SoulSync's own
 * /api/metasync endpoints, so the browser never has to reach MetaSync. Its
 * fields are not settings keys: they save through PUT /api/metasync/settings,
 * and a stored secret never comes back here (an empty field means unchanged).
 *
 * It loads each time the Connections tab opens, as settings.js
 * loadMetaSyncSettings did from the tab switch.
 */

import { Fragment, type ReactNode, useEffect, useRef, useState } from 'react';

import type { SettingsTabProps } from '../host';

import { Section } from './parts';
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
    lines.push('Step 2: no pool joined yet — send your pool key above to join one.');
  for (const p of pools) {
    const binding = BINDING_TEXT[p.binding_status || ''] || p.binding_status || 'Unknown';
    let line = `Pool — ${p.name || p.id || 'Pool'} (${p.url || ''}): ${binding}`;
    if (p.last_exchange_error) line += ` — last error: ${p.last_exchange_error}`;
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

export function MetaSyncSection({ opened }: Pick<SettingsTabProps, 'opened'>) {
  const [nodeUrl, setNodeUrl] = useState('');
  const [poolUrl, setPoolUrl] = useState('');
  const [poolKey, setPoolKey] = useState('');
  const [localKey, setLocalKey] = useState('');
  const [poolKeySet, setPoolKeySet] = useState(false);
  const [connected, setConnected] = useState('Not connected');
  const [pair, setPair] = useState<{ code: string; fingerprint: string } | null>(null);
  const [status, setStatus] = useState('Loading...');
  const [detail, setDetail] = useState<ReactNode[]>([]);
  const nodeField = useRef<HTMLInputElement>(null);
  const poolField = useRef<HTMLInputElement>(null);
  const tried = useRef(false);
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
        setDetail([]);
        return;
      }
      const s = (data.status || {}) as Answer;
      setStatus(
        `Step 1: connected — MetaSync ${text(s.version)}${
          s.soulsync_connected ? ' is reading this SoulSync' : ' is not reading this SoulSync yet'
        }`,
      );
      setDetail(statusLines(s, nodeNow.current.trim()));
    } catch {
      setStatus('MetaSync status unavailable');
      setDetail([]);
    }
  }

  async function load() {
    try {
      const data = await msApi('GET', '');
      if (!data || data.success !== true) {
        setStatus('Could not load MetaSync settings');
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
          ? `Connected${data.node_fingerprint ? ` — MetaSync ID ${text(data.node_fingerprint)}` : ''}`
          : 'Not connected',
      );
      await refreshStatus();
      // auto-detect once a page load, only while there is nothing to lose
      if (!tried.current && !data.local_key_set && !nodeNow.current.trim()) {
        tried.current = true;
        const found = await detect();
        if (found)
          setStatus(`Found MetaSync on this machine at ${found} — click Connect to pair with it.`);
      }
    } catch (error) {
      console.error('metasync settings load failed', error);
      setStatus('Could not load MetaSync settings');
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
        toast('Pool key handed over — MetaSync is bound to the pool', 'success');
      else if (data.exchange_status === 'binding_pending')
        toast('Pool key handed over — waiting for approval in the portal', 'warning');
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
            : 'Disconnected here — MetaSync could not be reached, so clear its pool key from its dashboard too',
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

  return (
    <Section
      tab="connections"
      title="MetaSync"
      hint="Join SIN, the signal network, through the MetaSync companion"
    >
      <div className="settings-group" data-stg="connections">
        <div className="help-text" style={{ marginBottom: '12px' }}>
          MetaSync is a small companion program that runs next to SoulSync and joins pools for you —
          the first pool is SIN, the signal network from step 2 below. Two separate steps happen on
          this page, and they are not the same thing:
          <br />
          <strong>Step 1</strong> connects SoulSync and MetaSync to each other, on this machine.
          <br />
          <strong>Step 2</strong> joins a pool. MetaSync does the joining — SoulSync itself never
          talks to a pool, it only hands MetaSync your pool key.
          <br />
          Installing MetaSync joins nothing by itself.
        </div>

        <div className="form-group">
          <label>Step 1 — Connect SoulSync and MetaSync:</label>
          <div className="help-text" style={{ marginBottom: '6px' }}>
            Connecting pairs the two programs. When you click Connect, a code appears here and in
            MetaSync's dashboard; approve it there and the two programs swap keys automatically —
            MetaSync gets a key to read this SoulSync, SoulSync gets a key to talk to MetaSync. You
            never type or see either key.
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button className="test-button" onClick={() => void pairRequest()}>
              Connect
            </button>{' '}
            <span id="metasync-connect-state" className="readonly-field">
              {connected}
            </span>
          </div>
          <div
            id="metasync-pair-box"
            style={{ display: pair ? undefined : 'none', marginTop: '10px' }}
          >
            <div className="help-text">
              MetaSync is showing a pairing request. Open the MetaSync dashboard, check the code
              matches, and approve it there — then finish here.
            </div>
            <div
              style={{
                display: 'flex',
                gap: '16px',
                alignItems: 'center',
                flexWrap: 'wrap',
                marginTop: '6px',
              }}
            >
              <span>
                Code:{' '}
                <strong id="metasync-pair-code" style={{ fontSize: '18px', letterSpacing: '2px' }}>
                  {pair?.code || ''}
                </strong>
              </span>{' '}
              <span>
                MetaSync's ID: <code id="metasync-pair-fingerprint">{pair?.fingerprint || ''}</code>
              </span>{' '}
              <button className="test-button" onClick={() => void pairClaim()}>
                I've approved it — finish connecting
              </button>
            </div>
            <div className="help-text">
              Check the code and the ID match what MetaSync's dashboard shows. Codes last 10
              minutes, and SoulSync can only finish after you approve — the code on its own connects
              nothing.
            </div>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="metasync-node-url">MetaSync URL:</label>
          <input
            type="text"
            id="metasync-node-url"
            placeholder="http://localhost:8790"
            ref={nodeField}
            value={nodeUrl}
            onChange={(event) => setNodeUrl(event.target.value)}
          />
          <div className="help-text">
            Where the MetaSync program answers. If it runs on this same machine, SoulSync finds it
            on its own (port 8790) — type an address only if MetaSync lives somewhere else.
          </div>
        </div>

        <div className="form-group">
          <details>
            <summary style={{ cursor: 'pointer' }}>
              Rather type a key? Connect manually instead of pairing
            </summary>
            <div className="help-text" style={{ marginTop: '6px' }}>
              Pairing above does this for you. The manual way: in MetaSync's dashboard, create a
              local API key and paste it here, then Save. SoulSync ends up holding the same kind of
              key either way.
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
              <input
                type="password"
                id="metasync-local-key"
                placeholder="MetaSync local API key (paste to connect manually)"
                style={{ flex: 1 }}
                value={localKey}
                onChange={(event) => setLocalKey(event.target.value)}
              />
            </div>
          </details>
        </div>

        <div className="form-group">
          <label>Step 2 — Join a pool (optional):</label>
          <div className="help-text">
            A pool is a network your MetaSync can join. The first pool is SIN, the SoulSync
            Intelligence Network: it collects anonymous taste signals from SoulSync installs — bulk
            counts of what people watchlist and wishlist, never your personal data — and turns them
            into shared playlists, like Trending and Most Wanted, which MetaSync brings back into
            this SoulSync. Get a key from your pool account, put it below, and send it to MetaSync.
            MetaSync joins and does all the talking — it swaps your key for short-lived access
            tokens, so the key itself stays between you and MetaSync. Nothing is joined until you
            send the key.
          </div>
          <div className="help-text">
            No key yet?{' '}
            <a href="https://metasync.ssync.net" target="_blank" rel="noopener">
              Get one at the SIN front desk
            </a>{' '}
            — sign in there, create a key, then come back and put it below.
          </div>
        </div>
        <div className="form-group">
          <label htmlFor="metasync-pool-url">Pool URL:</label>
          <input
            type="text"
            id="metasync-pool-url"
            placeholder="Pool URL from your pool account"
            ref={poolField}
            value={poolUrl}
            onChange={(event) => setPoolUrl(event.target.value)}
          />
        </div>
        <div className="form-group">
          <label htmlFor="metasync-pool-key">Pool key:</label>
          <input
            type="password"
            id="metasync-pool-key"
            placeholder={
              poolKeySet ? 'Saved — type a new key only to replace it' : 'Your pool account key'
            }
            value={poolKey}
            onChange={(event) => setPoolKey(event.target.value)}
          />
          <div className="help-text">
            Your own key for the pool, from your pool account. SoulSync stores it here only so it
            can hand it to MetaSync when you click Send — SoulSync never uses it itself.
          </div>
        </div>

        <div className="form-group">
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button className="test-button" onClick={() => void handoff()}>
              Send pool key to MetaSync
            </button>{' '}
            <button className="test-button" onClick={() => void save()}>
              Save
            </button>{' '}
            <button className="test-button" onClick={() => void refreshStatus()}>
              Refresh status
            </button>
          </div>
          <div className="help-text">
            Send pool key is the button that joins the pool. Save only stores what you typed above,
            for later.
          </div>
        </div>

        <div className="form-group">
          <label>Status:</label>
          <span id="metasync-status" className="readonly-field">
            {status}
          </span>
          <div id="metasync-status-detail" className="help-text">
            {detail.map((line, i) => (
              <Fragment key={i}>
                {i ? <br /> : null}
                {line}
              </Fragment>
            ))}
          </div>
        </div>

        <div className="form-group">
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <button className="test-button" onClick={() => void disconnect()}>
              Disconnect
            </button>{' '}
            <span className="help-text" style={{ margin: 0 }}>
              Undoes step 1 and step 2: MetaSync leaves the pool, the key it reads this SoulSync
              with is revoked, and SoulSync forgets MetaSync's key. Your pool key stays saved above.
            </span>
          </div>
        </div>
      </div>
    </Section>
  );
}
