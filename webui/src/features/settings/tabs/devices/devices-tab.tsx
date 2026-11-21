/**
 * Settings > Devices (PAIRING_SPEC.md 7): listen anywhere. One page:
 * "Add a device" (a code and its QR), the paired devices (a device that
 * hasn't picked its profile yet shown apart), the codes still waiting,
 * this server's fingerprint, addresses and reachability check, and the
 * person invites (the same list as People > Invites).
 *
 * Admins only: for anyone else, and when the API says no, nothing is drawn.
 */

import { useCallback, useEffect, useState } from 'react';

import { QrImage } from '@/platform/qr/qr-image';

import type { SettingsTabProps } from '../../host';

import { useViewer } from '../people/data';
import { InvitesScreen } from '../people/invites-activity';
import { ago } from '../people/model';
import ppl from '../people/people.module.css';
import {
  type Address,
  cancelCode,
  checkReachability,
  type CheckResult,
  createCode,
  type Device,
  getCodes,
  getDevices,
  getProbe,
  getServer,
  type LiveCode,
  type NewCode,
  type Probe,
  revokeDevice,
  saveAddresses,
  type ServerInfo,
} from './api';
import css from './devices.module.css';
import { groupFingerprint } from './server-fingerprint';

export const DEVICES_TAB = 'devices';

const toast = (message: string, type = 'info') =>
  (window as Window & { showToast?: (m: string, t?: string) => void }).showToast?.(message, type);

async function confirm(opts: {
  title: string;
  message: string;
  confirmText: string;
}): Promise<boolean> {
  const dialog = (
    window as Window & {
      showConfirmDialog?: (o: Record<string, unknown>) => Promise<boolean>;
    }
  ).showConfirmDialog;
  return dialog ? dialog({ ...opts, destructive: true }) : true;
}

/** seconds as "9:41" */
export function clock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** what a device row says about itself */
export function deviceLine(d: Device, now = Date.now()) {
  const what = [d.app, d.platform].filter(Boolean).join(' on ');
  const seen = d.last_seen_at ? `seen ${ago(d.last_seen_at, now)}` : '';
  const where = d.last_ip ? `from ${d.last_ip}` : '';
  return [what, [seen, where].filter(Boolean).join(' ')].filter(Boolean).join(' · ');
}

function useTick(on: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [on]);
}

/** a countdown that started at `seconds` when `since` was taken */
function left(seconds: number, since: number) {
  return seconds - (Date.now() - since) / 1000;
}

interface Data {
  devices: Device[];
  codes: LiveCode[];
  server: ServerInfo | null;
  at: number;
}

export function DevicesTab({ opened, active }: SettingsTabProps) {
  const me = useViewer();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [shown, setShown] = useState<{ code: NewCode; at: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [d, c, s] = await Promise.all([getDevices(), getCodes(), getServer()]);
    if (!d.ok || !c.ok) {
      const bad = !d.ok ? d : !c.ok ? c : null;
      if (bad && bad.status === 403) setForbidden(true);
      setError(bad ? bad.message : "That didn't work.");
      return;
    }
    setError(null);
    setData({
      devices: d.data.devices || [],
      codes: c.data.codes || [],
      server: s.ok ? s.data : null,
      at: Date.now(),
    });
  }, []);

  useEffect(() => {
    if (me.admin && opened > 0) void reload();
  }, [me.admin, opened, reload]);

  const waiting = !!data?.devices.some((d) => d.state === 'unprofiled');
  // while a code is up or a device is picking, and the tab is open, look
  // again now and then
  useEffect(() => {
    if (!active || (!shown && !waiting)) return;
    const t = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(t);
  }, [active, shown, waiting, reload]);
  useTick(active && (!!shown || !!data?.codes.length || waiting));

  if (!me.admin || forbidden) return null;

  const add = async () => {
    setBusy(true);
    const r = await createCode('');
    setBusy(false);
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    setShown({ code: r.data, at: Date.now() });
    void reload();
  };

  const cancel = async (id: number) => {
    const r = await cancelCode(id);
    if (!r.ok) toast(r.message, 'error');
    if (shown?.code.id === id) setShown(null);
    void reload();
  };

  const unpair = async (d: Device) => {
    const ok = await confirm({
      title: `Unpair ${d.name}?`,
      message: 'It stops working right away. To use it again, pair it with a new code.',
      confirmText: 'Unpair',
    });
    if (!ok) return;
    const r = await revokeDevice(d.id);
    toast(r.ok ? `${d.name} unpaired` : r.message, r.ok ? 'info' : 'error');
    void reload();
  };

  return (
    <section className={`${ppl.root} ${css.root}`} data-stg={DEVICES_TAB} aria-label="Devices">
      <div className={css.head}>
        <div>
          <h3 className={ppl.h2}>Listen anywhere</h3>
          <p className={ppl.paneSub}>
            Pair a phone, a car or a speaker app with a one-time code. Each device picks a profile
            and can only play: never settings, downloads or people.
          </p>
        </div>
        <button
          type="button"
          className={`${ppl.btn} ${ppl.primary}`}
          disabled={busy}
          onClick={() => void add()}
        >
          Add a device
        </button>
      </div>

      {shown ? (
        <ShownCode
          code={shown.code}
          since={shown.at}
          onCancel={() => void cancel(shown.code.id)}
          onDone={() => setShown(null)}
        />
      ) : null}

      {error ? <p className={ppl.error}>{error}</p> : null}
      {!data && !error ? <p className={ppl.empty}>Loading devices...</p> : null}

      {data ? (
        <>
          <div className={css.section}>
            <h3 className={ppl.h2}>Paired devices</h3>
            {!data.devices.length ? (
              <p className={ppl.empty}>No devices yet. Add one above.</p>
            ) : (
              <ul className={ppl.rows} aria-label="Paired devices">
                {data.devices.map((d) => (
                  <li key={d.id} className={ppl.lrow}>
                    <div className={ppl.lrowMain}>
                      <div className={ppl.t1}>{d.name}</div>
                      <div className={ppl.t2}>{deviceLine(d)}</div>
                    </div>
                    {d.state === 'unprofiled' ? (
                      <span className={css.badge}>
                        No profile picked yet · {clock(left(d.pick_seconds_left ?? 0, data.at))}{' '}
                        left
                      </span>
                    ) : (
                      <span className={`${ppl.state} ${ppl.stateOpen}`}>{d.profile_name}</span>
                    )}
                    <button type="button" className={ppl.link} onClick={() => void unpair(d)}>
                      Unpair
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {data.codes.length ? (
            <div className={css.section}>
              <h3 className={ppl.h2}>Codes waiting to be used</h3>
              <ul className={ppl.rows} aria-label="Codes waiting to be used">
                {data.codes.map((c) => (
                  <li key={c.id} className={ppl.lrow}>
                    <div className={ppl.lrowMain}>
                      <div className={ppl.t1}>{c.note || 'Device code'}</div>
                      <div className={ppl.t2}>
                        runs out in {clock(left(c.seconds_left, data.at))}
                        {c.wrong_tries ? ` · ${c.wrong_tries} wrong tries` : ''}
                      </div>
                    </div>
                    <button type="button" className={ppl.link} onClick={() => void cancel(c.id)}>
                      Cancel
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <ThisServer server={data.server} active={active} onSaved={() => void reload()} />

          <div className={css.section}>
            <h3 className={ppl.h2}>Invite a person</h3>
            <InvitesScreen people={[]} />
          </div>
        </>
      ) : null}
    </section>
  );
}

function ShownCode({
  code,
  since,
  onCancel,
  onDone,
}: {
  code: NewCode;
  since: number;
  onCancel: () => void;
  onDone: () => void;
}) {
  const secs = left(code.seconds_left, since);
  const fp = typeof code.payload.fingerprint === 'string' ? code.payload.fingerprint : '';
  return (
    <div className={css.codeCard} role="region" aria-label="Pairing code">
      <div>
        <div className={css.meta}>Type this into the app, or scan the square</div>
        <div className={css.code} data-testid="pairing-code">
          {code.code}
        </div>
        <div className={css.meta}>
          {secs > 0 ? `Works once, for ${clock(secs)} more.` : 'This code ran out. Make a new one.'}{' '}
          The app should show the fingerprint{' '}
          <span className={css.mono}>{groupFingerprint(fp)}</span>.
        </div>
        {code.warnings.map((w) => (
          <p key={w} className={ppl.look} role="alert">
            {w}
          </p>
        ))}
        <div className={css.actions}>
          <button type="button" className={`${ppl.btn} ${ppl.secondary}`} onClick={onDone}>
            Done
          </button>
          <button type="button" className={ppl.link} onClick={onCancel}>
            Cancel this code
          </button>
        </div>
      </div>
      <QrImage className={css.qr} text={code.payload_text} label="Pairing QR code" />
    </div>
  );
}

function AddressRow({ a }: { a: Address }) {
  return (
    <div className={css.addr}>
      <span className={css.kind}>{a.kind === 'custom' ? 'extra' : a.kind}</span>
      <span className={css.mono}>{a.url}</span>
    </div>
  );
}

function ThisServer({
  server,
  active,
  onSaved,
}: {
  server: ServerInfo | null;
  active: boolean;
  onSaved: () => void;
}) {
  const [remote, setRemote] = useState('');
  const [extra, setExtra] = useState('');
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);

  // the saved addresses fill the fields when they change, not on every
  // reload (the page looks again every few seconds while a code is up)
  const savedRemote = server?.remote_url ?? '';
  const savedExtra = (server?.custom_urls ?? []).join('\n');
  useEffect(() => {
    setRemote(savedRemote);
    setExtra(savedExtra);
  }, [savedRemote, savedExtra]);
  const firstProbe = server?.probe ?? null;
  useEffect(() => {
    setProbe((p) => p ?? firstProbe);
  }, [firstProbe]);

  // a phone check: look for its visit while the one-time link lives
  const nonce = probe?.nonce;
  useEffect(() => {
    if (!nonce || !active) return;
    const t = window.setInterval(() => {
      void getProbe().then((r) => {
        if (r.ok) setProbe(r.data.probe);
      });
    }, 4000);
    return () => window.clearInterval(t);
  }, [nonce, active]);

  if (!server) return null;

  const save = async () => {
    setSaving(true);
    const r = await saveAddresses(
      remote.trim(),
      extra
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    );
    setSaving(false);
    toast(r.ok ? 'Addresses saved' : r.message, r.ok ? 'info' : 'error');
    if (r.ok) onSaved();
  };

  const check = async () => {
    setChecking(true);
    setResult(null);
    const r = await checkReachability();
    setChecking(false);
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    setResult(r.data.result);
    setProbe(r.data.probe);
  };

  const outside = probe?.hits.filter((h) => h.via === 'outside') ?? [];
  return (
    <div className={css.section}>
      <h3 className={ppl.h2}>This server</h3>
      <p className={ppl.paneSub}>
        Fingerprint{' '}
        <span className={css.mono} title={`Lookup ID ${server.lookup_id}`}>
          {groupFingerprint(server.fingerprint)}
        </span>
        . A paired app shows the same, so you can tell it found this server.
      </p>

      <div className={ppl.rows} style={{ padding: '10px 16px' }}>
        {server.addresses.length ? (
          server.addresses.map((a) => <AddressRow key={a.url} a={a} />)
        ) : (
          <p className={ppl.empty}>No addresses found yet. Add the remote address below.</p>
        )}
      </div>
      {server.warnings.map((w) => (
        <p key={w} className={ppl.look} role="alert">
          {w}
        </p>
      ))}

      <label className={ppl.t2} htmlFor="devices-remote-url" style={{ marginTop: 14 }}>
        Remote address, for outside your home: your own domain, a tunnel or a forwarded port
      </label>
      <div className={css.form}>
        <input
          id="devices-remote-url"
          className={ppl.field}
          type="url"
          placeholder="https://music.example.com"
          value={remote}
          onChange={(e) => setRemote(e.target.value)}
        />
      </div>
      <label className={ppl.t2} htmlFor="devices-extra-urls" style={{ marginTop: 10 }}>
        Extra addresses, one per line (a Tailscale name, say)
      </label>
      <textarea
        id="devices-extra-urls"
        className={ppl.field}
        rows={2}
        style={{ height: 'auto', padding: 8 }}
        value={extra}
        onChange={(e) => setExtra(e.target.value)}
      />
      <div className={css.actions}>
        <button
          type="button"
          className={`${ppl.btn} ${ppl.secondary}`}
          disabled={saving}
          onClick={() => void save()}
        >
          Save addresses
        </button>
        <button
          type="button"
          className={`${ppl.btn} ${ppl.secondary}`}
          disabled={checking}
          onClick={() => void check()}
        >
          {checking ? 'Checking...' : 'Check reachability'}
        </button>
      </div>

      {result ? (
        <div className={`${css.result} ${result.ok ? css.ok : css.bad}`} role="status">
          <b>{result.ok ? 'It answered.' : 'It did not answer.'}</b> {result.words}
          {result.ok ? (
            <>
              {' '}
              That check ran from this server, so it can't prove the internet gets in. Use the phone
              link below to be sure.
            </>
          ) : null}
        </div>
      ) : null}

      {probe?.url ? (
        <div className={css.phone}>
          <QrImage text={probe.url} label="Reachability check link" />
          <div className={css.meta}>
            Turn wifi off on your phone and open this link (or scan the square):
            <div className={css.mono}>{probe.url}</div>
            {outside.length ? (
              <div className={ppl.t1} role="status">
                Reached from {outside[outside.length - 1].ip}
                {outside[outside.length - 1].from_home
                  ? ', inside your network: try it with wifi off.'
                  : ', outside your network. Devices can reach this server.'}
              </div>
            ) : (
              <div>Waiting for the phone... the link works for {clock(probe.seconds_left)}.</div>
            )}
          </div>
        </div>
      ) : null}

      {result && !result.ok ? (
        <ul className={css.guide} aria-label="Ways in from outside">
          {server.guidance.map((g) => (
            <li key={g.id}>
              <b>{g.title}.</b> {g.body}
            </li>
          ))}
          <li>
            More in the docs: <a href="#docs/devices-remote">Reaching SoulSync from outside</a>.
          </li>
        </ul>
      ) : null}
    </div>
  );
}
