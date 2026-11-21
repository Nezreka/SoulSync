/**
 * Settings > Devices (PAIRING_SPEC.md 7): listen anywhere. One page, built
 * from the settings ui kit like every other tab: "Add a device" (a code and
 * its QR), the paired devices (one that hasn't picked its profile yet shown
 * apart), the codes still waiting, this server's fingerprint, addresses and
 * reachability check, and the person invites (the same list as People >
 * Invites).
 *
 * Admins only: for anyone else, and when the API says no, nothing is drawn.
 * Each thing saves with its own button, so the page's save status hides here.
 */

import { useCallback, useEffect, useState } from 'react';

import { QrImage } from '@/platform/qr/qr-image';

import type { SettingsTabProps } from '../../host';

import { useNav } from '../../nav';
import {
  Btn,
  Card,
  CopyValue,
  FieldLine,
  Item,
  itemClass,
  Items,
  Note,
  Row,
  Say,
  StatusWords,
  Tag,
  TextArea,
  TextField,
} from '../../ui';
import s from '../../ui/settings.module.css';
import { ViewPanel } from '../downloads/downloads-page';
import { useViewer } from '../people/data';
import { InvitesScreen } from '../people/invites-activity';
import { ago } from '../people/model';
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
  renameDevice,
  revokeDevice,
  saveAddresses,
  type ServerInfo,
} from './api';
import css from './devices.module.css';
import { MusicAppsCard } from './music-apps';
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
  const n = Math.max(0, Math.floor(seconds));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
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

export function DevicesPage({ opened, active }: SettingsTabProps) {
  const me = useViewer();
  const here = useNav().tab === DEVICES_TAB;
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [shown, setShown] = useState<{ code: NewCode; at: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [d, c, sv] = await Promise.all([getDevices(), getCodes(), getServer()]);
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
      server: sv.ok ? sv.data : null,
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

  return (
    <div data-stg-new={DEVICES_TAB} hidden={!here}>
      <ViewPanel tab={DEVICES_TAB} view="all" open={here}>
        <div className={s.cards} aria-label="Devices">
          <Card
            title="Pair a device"
            hint="Pair a phone, a car or a speaker app with a one-time code. It picks a profile and can only play: never settings, downloads or people."
            actions={
              <Btn kind="primary" id="devices-add" busy={busy} onClick={() => void add()}>
                Add a device
              </Btn>
            }
          >
            {shown ? (
              <ShownCode
                code={shown.code}
                since={shown.at}
                onCancel={() => void cancel(shown.code.id)}
                onDone={() => setShown(null)}
              />
            ) : null}
          </Card>

          <MusicAppsCard opened={opened} onMade={() => void reload()} />

          {error ? <Note>{error}</Note> : null}
          {!data && !error ? <StatusWords tone="busy">Loading devices…</StatusWords> : null}

          {data ? (
            <>
              <Card title="Paired devices" hint="Unpair one and it stops working right away.">
                <div className={css.pad}>
                  {!data.devices.length ? (
                    <Note info>No devices yet. Add one above.</Note>
                  ) : (
                    <Items aria-label="Paired devices">
                      {data.devices.map((d) => (
                        <DeviceItem
                          key={d.id}
                          d={d}
                          since={data.at}
                          onChanged={() => void reload()}
                        />
                      ))}
                    </Items>
                  )}
                </div>
              </Card>

              {data.codes.length ? (
                <Card title="Codes waiting to be used">
                  <Items className={css.pad} aria-label="Codes waiting to be used">
                    {data.codes.map((c) => (
                      <Item key={c.id}>
                        <span className={itemClass.main}>
                          {c.note || 'Device code'}{' '}
                          <span className={itemClass.dim}>
                            runs out in {clock(left(c.seconds_left, data.at))}
                            {c.wrong_tries ? ` · ${c.wrong_tries} wrong tries` : ''}
                          </span>
                        </span>
                        <span className={itemClass.cells}>
                          <Btn small kind="ghost" onClick={() => void cancel(c.id)}>
                            Cancel
                          </Btn>
                        </span>
                      </Item>
                    ))}
                  </Items>
                </Card>
              ) : null}

              <ThisServer server={data.server} active={active} onSaved={() => void reload()} />

              <Card title="Invite a person">
                <div className={css.pad}>
                  <InvitesScreen people={[]} />
                </div>
              </Card>
            </>
          ) : null}
        </div>
      </ViewPanel>
    </div>
  );
}

function DeviceItem({ d, since, onChanged }: { d: Device; since: number; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(d.name);
  const [saving, setSaving] = useState(false);

  const unpair = async () => {
    const ok = await confirm({
      title: `Unpair ${d.name}?`,
      message: 'It stops working right away. To use it again, pair it with a new code.',
      confirmText: 'Unpair',
    });
    if (!ok) return;
    const r = await revokeDevice(d.id);
    toast(r.ok ? `${d.name} unpaired` : r.message, r.ok ? 'info' : 'error');
    onChanged();
  };

  const stop = () => {
    setEditing(false);
    setName(d.name);
  };

  const rename = async () => {
    const next = name.trim();
    if (!next || next === d.name) {
      stop();
      return;
    }
    setSaving(true);
    const r = await renameDevice(d.id, next);
    setSaving(false);
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    setEditing(false);
    onChanged();
  };

  return (
    <Item data-device={d.id}>
      <span className={`${itemClass.main} ${css.deviceMain}`}>
        {editing ? (
          <FieldLine wrap>
            <TextField
              id={`devices-name-${d.id}`}
              aria-label={`New name for ${d.name}`}
              value={name}
              maxLength={80}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void rename();
                if (e.key === 'Escape') stop();
              }}
            />
            <Btn small kind="primary" busy={saving} onClick={() => void rename()}>
              Save
            </Btn>
            <Btn small kind="ghost" onClick={stop}>
              Cancel
            </Btn>
          </FieldLine>
        ) : (
          <>
            {d.name}
            <span className={itemClass.dim}> {deviceLine(d)}</span>
          </>
        )}
      </span>
      <span className={itemClass.cells}>
        {d.state === 'unprofiled' ? (
          <StatusWords tone="warn">
            {`No profile picked yet · ${clock(left(d.pick_seconds_left ?? 0, since))} left`}
          </StatusWords>
        ) : (
          <Tag accent>{d.profile_name}</Tag>
        )}
        {editing ? null : (
          <Btn small kind="ghost" onClick={() => setEditing(true)}>
            Rename
          </Btn>
        )}
        <Btn small kind="danger" onClick={() => void unpair()}>
          Unpair
        </Btn>
      </span>
    </Item>
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
    <div className={css.pad}>
      <div className={css.shown} role="region" aria-label="Pairing code">
        <div className={css.stack}>
          <div>
            <div className={itemClass.dim} role="status">
              {secs > 0
                ? `Type this into the app, or scan the square. Works once, for ${clock(secs)} more.`
                : 'This code ran out. Make a new one.'}
            </div>
            <div className={css.code} data-testid="pairing-code">
              {code.code}
            </div>
            <div className={itemClass.dim}>
              The app should show the fingerprint{' '}
              <span className={css.mono}>{groupFingerprint(fp)}</span>.
            </div>
          </div>
          <FieldLine>
            <Btn onClick={onDone}>Done</Btn>
            <Btn kind="ghost" onClick={onCancel}>
              Cancel this code
            </Btn>
          </FieldLine>
        </div>
        <QrImage className={css.qr} text={code.payload_text} label="Pairing QR code" />
      </div>
    </div>
  );
}

function AddressItem({ a }: { a: Address }) {
  return (
    <Item>
      <span className={css.kind}>{a.kind === 'custom' ? 'extra' : a.kind}</span>
      <span className={`${itemClass.main} ${css.mono}`}>{a.url}</span>
    </Item>
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
        .map((v) => v.trim())
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
  const last = outside[outside.length - 1];
  return (
    <Card title="This server" hint="Where paired devices find this server, home addresses first.">
      <Row
        label="Server fingerprint"
        help="A paired app shows the same 12 characters, so you can tell it found this server."
      >
        <span className={css.mono} title={`Lookup ID ${server.lookup_id}`}>
          {groupFingerprint(server.fingerprint)}
        </span>
      </Row>
      <Row label="Addresses" stack help="What a device tries, in this order.">
        {server.addresses.length ? (
          <Items aria-label="Addresses">
            {server.addresses.map((a) => (
              <AddressItem key={a.url} a={a} />
            ))}
          </Items>
        ) : (
          <Note info>No addresses found yet. Add the remote address below.</Note>
        )}
      </Row>
      <Row
        field
        label="Remote address"
        htmlFor="devices-remote-url"
        help="For outside your home: your own domain, a tunnel or a forwarded port."
        warn={server.warnings.length ? server.warnings.join(' ') : undefined}
      >
        <TextField
          wide
          mono
          id="devices-remote-url"
          type="url"
          placeholder="https://music.example.com"
          value={remote}
          onChange={(e) => setRemote(e.target.value)}
        />
      </Row>
      <Row
        stack
        label="Extra addresses"
        htmlFor="devices-extra-urls"
        help="One per line, a Tailscale name, say."
      >
        <TextArea
          mono
          id="devices-extra-urls"
          rows={2}
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
        />
      </Row>
      <Row
        stack
        label="Reachability"
        help="This server fetches a one-time link through the remote address."
      >
        <FieldLine>
          <Btn id="devices-save-addresses" busy={saving} onClick={() => void save()}>
            Save addresses
          </Btn>
          <Btn
            id="devices-check-reachability"
            icon="refresh"
            busy={checking}
            onClick={() => void check()}
          >
            {checking ? 'Checking…' : 'Check reachability'}
          </Btn>
        </FieldLine>
      </Row>

      <div className={css.pad}>
        {result ? (
          <Say tone={result.ok ? 'ok' : 'bad'}>
            <b>{result.ok ? 'It answered.' : 'It did not answer.'}</b> {result.words}
            {result.ok
              ? " That check ran from this server, so it can't prove the internet gets in. Use the phone link below to be sure."
              : null}
          </Say>
        ) : null}

        {probe?.url ? (
          <div className={css.phone}>
            <QrImage className={css.phoneQr} text={probe.url} label="Reachability check link" />
            <div className={css.stack}>
              <div className={itemClass.dim}>
                Turn wifi off on your phone and open this link, or scan the square.
              </div>
              <CopyValue value={probe.url} />
              {last ? (
                <StatusWords tone={last.from_home ? 'warn' : 'ok'} live>
                  {`Reached from ${last.ip}${
                    last.from_home
                      ? ', inside your network: try it with wifi off.'
                      : ', outside your network. Devices can reach this server.'
                  }`}
                </StatusWords>
              ) : (
                <StatusWords tone="busy">
                  {`Waiting for the phone… the link works for ${clock(probe.seconds_left)}.`}
                </StatusWords>
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
    </Card>
  );
}

/** the Devices tab: one page (settings-bic layout) */
export function DevicesTab(props: SettingsTabProps) {
  if (props.part === 'page') return <DevicesPage {...props} />;
  return null;
}
