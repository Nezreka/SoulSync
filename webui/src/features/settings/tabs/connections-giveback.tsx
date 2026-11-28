/**
 * Connections > Give back (#2335): send what SoulSync identified back to
 * MusicBrainz and AcoustID, slowly, under the person's own accounts. Three
 * switches, all off until turned on:
 *
 *   AcoustID   fingerprints of songs SoulSync is sure of, with their
 *              MusicBrainz recording, under the linked AcoustID account
 *   ISRCs      an ISRC in a file's tags that its MusicBrainz recording lacks,
 *              under the person's MusicBrainz account (OAuth, a pasted code)
 *   Albums     albums MusicBrainz doesn't have: Add to MusicBrainz opens its
 *              add release form filled in, and the person submits it
 *
 * Nothing here is a settings key: everything goes through /api/giveback,
 * and no answer carries a token.
 */

import { useEffect, useState } from 'react';

import type { SettingsTabProps } from '../host';

import {
  Btn,
  Card,
  Item,
  Items,
  itemClass,
  Note,
  Row,
  StatusWords,
  Switch,
  TextField,
  Value,
} from '../ui';
import s from '../ui/settings.module.css';

type Shell = Window & { showToast?: (message: string, type?: string) => void };
const toast = (message: string, type: string) => (window as Shell).showToast?.(message, type);
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export type Switches = { acoustid: boolean; isrc: boolean; releases: boolean };
type Counts = Record<'submitted' | 'present' | 'skipped' | 'rejected', number>;

export interface GiveBack {
  switches: Switches;
  acoustid: { ready: boolean; message: string };
  musicbrainz: { available: boolean; connected: boolean; username: string; message: string };
  counts: { acoustid: Counts; isrc: Counts };
  running: boolean;
  last_at: number | null;
}

export interface MissingRelease {
  id: number;
  title: string;
  artist: string;
  year: string | null;
  source: string;
  source_url: string;
}

async function gbApi<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`/api/giveback${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = (await resp.json().catch(() => null)) as (T & { error?: string }) | null;
  if (!resp.ok || !data) throw new Error(data?.error || 'SoulSync did not answer');
  return data;
}

/** "1 fingerprint", "12 fingerprints" */
export const many = (n: number, one: string, more = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : more}`;

const SEED_WINDOW = 'soulsync-mb-release-add';

/** open musicbrainz's add release form with the fields filled in. A form
 *  POST, as the seeding page asks; the window opens on the click itself so
 *  a popup blocker lets it through, and the form lands in it */
export async function openReleaseEditor(albumId: number) {
  try {
    window.open('about:blank', SEED_WINDOW);
  } catch {
    // no window (a test, a locked down browser): the form opens its own
  }
  const seed = await gbApi<{ action: string; fields: [string, string][] }>(
    'GET',
    `/releases/${albumId}/seed`,
  );
  const form = document.createElement('form');
  form.method = 'post';
  form.action = seed.action;
  form.target = SEED_WINDOW;
  form.acceptCharset = 'UTF-8';
  form.hidden = true;
  for (const [name, value] of seed.fields) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
  form.remove();
}

export function GiveBackSection({
  opened,
  active = true,
}: Pick<SettingsTabProps, 'opened'> & {
  /** its pill is the one showing: nothing loads until it is */
  active?: boolean;
}) {
  const [data, setData] = useState<GiveBack | null>(null);
  const [error, setError] = useState('');
  const [releases, setReleases] = useState<MissingRelease[]>([]);
  const [linking, setLinking] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState('');

  async function load() {
    try {
      setData(await gbApi<GiveBack>('GET', ''));
      setError('');
    } catch (e) {
      setError(errText(e));
    }
  }

  async function loadReleases() {
    try {
      const r = await gbApi<{ releases: MissingRelease[] }>('GET', '/releases');
      setReleases(r.releases || []);
    } catch {
      setReleases([]);
    }
  }

  useEffect(() => {
    if (active) void load();
  }, [opened, active]);

  const releasesOn = !!data?.switches.releases;
  useEffect(() => {
    if (releasesOn && active) void loadReleases();
    else if (!releasesOn) setReleases([]);
  }, [releasesOn, active]);

  async function flip(key: keyof Switches, on: boolean) {
    try {
      setData(await gbApi<GiveBack>('PUT', '/settings', { [key]: on }));
    } catch (e) {
      toast(errText(e), 'error');
    }
  }

  async function connect() {
    setBusy('connect');
    try {
      const r = await gbApi<{ url: string }>('POST', '/musicbrainz/connect');
      window.open(r.url, '_blank', 'noopener');
      setLinking(true);
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy('');
    }
  }

  async function finish() {
    setBusy('code');
    try {
      setData(await gbApi<GiveBack>('POST', '/musicbrainz/code', { code: code.trim() }));
      setLinking(false);
      setCode('');
      toast('MusicBrainz account connected', 'success');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy('');
    }
  }

  async function disconnect() {
    setBusy('disconnect');
    try {
      setData(await gbApi<GiveBack>('POST', '/musicbrainz/disconnect'));
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy('');
    }
  }

  async function runNow() {
    setBusy('run');
    try {
      await gbApi('POST', '/run');
      toast('A batch goes out in the background', 'success');
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy('');
    }
  }

  async function addRelease(id: number) {
    try {
      await openReleaseEditor(id);
    } catch (e) {
      toast(errText(e), 'error');
    }
  }

  if (!data)
    return (
      <div className={s.cards}>
        <StatusWords tone={error ? 'warn' : 'busy'}>{error || 'Loading...'}</StatusWords>
      </div>
    );

  const mb = data.musicbrainz;
  const fp = data.counts.acoustid;
  const isrc = data.counts.isrc;

  return (
    <div className={s.cards} id="giveback">
      <Note info>
        SoulSync names your music with MusicBrainz and AcoustID, two open databases people build
        together. These send back what SoulSync has worked out, under your own accounts, a little at
        a time. Everything here is off until you turn it on, and nothing is sent twice.
      </Note>

      <Card
        title="AcoustID fingerprints"
        hint="Helps AcoustID recognise songs, for everyone who uses it"
      >
        <Row
          label="Send fingerprints"
          htmlFor="giveback-acoustid"
          help="Only for songs SoulSync is sure of: matched by fingerprint or by MusicBrainz tags, and the file's length agrees with the song's. A small batch every 15 minutes or so, under your linked AcoustID account."
          warn={data.switches.acoustid && !data.acoustid.ready ? data.acoustid.message : undefined}
        >
          <Switch
            id="giveback-acoustid"
            checked={data.switches.acoustid}
            onChange={(e) => void flip('acoustid', e.currentTarget.checked)}
          />
        </Row>
        <Row label="Given so far">
          <Value id="giveback-acoustid-count">{many(fp.submitted, 'fingerprint')}</Value>
        </Row>
      </Card>

      <Card
        title="ISRCs to MusicBrainz"
        hint="The recording codes in your files' tags, added where MusicBrainz lacks them"
      >
        <Row
          label="Send ISRCs"
          htmlFor="giveback-isrc"
          help="When a file SoulSync is sure of carries an ISRC its MusicBrainz recording doesn't have, it is added under your MusicBrainz account, the same way taggers like Picard do. One request a second at most."
          off={!mb.available}
        >
          <Switch
            id="giveback-isrc"
            checked={data.switches.isrc}
            disabled={!mb.available}
            onChange={(e) => void flip('isrc', e.currentTarget.checked)}
          />
        </Row>
        {!mb.available ? (
          <Note info>{mb.message}</Note>
        ) : mb.connected ? (
          <Row label="MusicBrainz account" help={`Connected as ${mb.username || 'your account'}`}>
            <Btn
              small
              kind="ghost"
              busy={busy === 'disconnect'}
              id="giveback-mb-disconnect"
              onClick={() => void disconnect()}
            >
              Disconnect
            </Btn>
          </Row>
        ) : (
          <>
            <Row
              label="MusicBrainz account"
              help="Connect opens MusicBrainz. Approve SoulSync there, then paste the code it shows you here."
            >
              <Btn
                small
                icon="link"
                busy={busy === 'connect'}
                id="giveback-mb-connect"
                onClick={() => void connect()}
              >
                Connect
              </Btn>
            </Row>
            {linking ? (
              <Row label="Code from MusicBrainz" htmlFor="giveback-mb-code" field sub>
                <TextField
                  id="giveback-mb-code"
                  mono
                  value={code}
                  autoComplete="off"
                  onChange={(e) => setCode(e.currentTarget.value)}
                />
                <Btn
                  small
                  kind="primary"
                  busy={busy === 'code'}
                  disabled={!code.trim()}
                  onClick={() => void finish()}
                >
                  Finish
                </Btn>
              </Row>
            ) : null}
          </>
        )}
        <Row label="Given so far">
          <Value id="giveback-isrc-count">
            {many(isrc.submitted, 'ISRC')}
            {isrc.present ? `, ${isrc.present.toLocaleString()} already there` : ''}
          </Value>
        </Row>
      </Card>

      <Card
        title="Albums MusicBrainz doesn't have"
        hint="Found on Spotify, Deezer and others, missing from MusicBrainz"
      >
        <Row
          label="Show albums to add"
          htmlFor="giveback-releases"
          help="Add to MusicBrainz opens MusicBrainz's add release form already filled in from the source. You check it, fix what is wrong and submit it yourself; SoulSync never adds anything on its own."
        >
          <Switch
            id="giveback-releases"
            checked={data.switches.releases}
            onChange={(e) => void flip('releases', e.currentTarget.checked)}
          />
        </Row>
        {data.switches.releases ? (
          releases.length ? (
            <Items id="giveback-release-list">
              {releases.map((r) => (
                <Item key={r.id}>
                  <span className={itemClass.main}>
                    {r.title}{' '}
                    <span className={itemClass.dim}>
                      {r.artist}
                      {r.year ? `, ${r.year}` : ''}, from{' '}
                      <a href={r.source_url} target="_blank" rel="noopener noreferrer">
                        {r.source}
                      </a>
                    </span>
                  </span>
                  <span className={itemClass.cells}>
                    <Btn small icon="plus" onClick={() => void addRelease(r.id)}>
                      Add to MusicBrainz
                    </Btn>
                  </span>
                </Item>
              ))}
            </Items>
          ) : (
            <Row label="Nothing to add" help="Every album SoulSync checked is on MusicBrainz." />
          )
        ) : null}
      </Card>

      {data.switches.acoustid || data.switches.isrc ? (
        <Row
          label="Send a batch now"
          help="Instead of waiting for the next turn. It still goes at the same gentle pace."
        >
          <Btn
            small
            icon="refresh"
            busy={busy === 'run' || data.running}
            onClick={() => void runNow()}
          >
            Send now
          </Btn>
        </Row>
      ) : null}
    </div>
  );
}
