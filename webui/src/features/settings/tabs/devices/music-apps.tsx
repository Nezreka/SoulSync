/**
 * Settings > Devices > Set up a music app: a music app that speaks the
 * Subsonic or OpenSubsonic API (api/subsonic.py) plays straight from
 * SoulSync as one profile. Setting one up makes a paired device with its
 * own app password and API key, shown once (and in a QR); it is unpaired
 * from Paired devices like any other.
 *
 * Below it, how music apps are served: the format songs are converted to
 * when an app asks for less, how many convert at once (one past that waits
 * its turn, never gets the bigger file instead), and, per media server, whether files SoulSync
 * can't read are played through that server.
 */

import { useCallback, useEffect, useState } from 'react';

import { QrImage } from '@/platform/qr/qr-image';

import {
  Btn,
  Card,
  CopyValue,
  FieldLine,
  Note,
  Row,
  Select,
  StatusWords,
  Switch,
  TextField,
} from '../../ui';
import {
  type BridgeServer,
  getMusicApps,
  type MusicApps,
  type NewMusicApp,
  saveMusicAppSettings,
  setUpMusicApp,
} from './api';
import css from './devices.module.css';

const toast = (message: string, type = 'info') =>
  (window as Window & { showToast?: (m: string, t?: string) => void }).showToast?.(message, type);

export const APP_HINTS: { apps: string; how: string }[] = [
  {
    apps: 'Symfonium, Amperfy, play:Sub, Substreamer, DSub, Tempo',
    how: 'Add a server of the Subsonic kind. Server, username and password from here.',
  },
  {
    apps: 'Feishin',
    how: 'Add a server, pick OpenSubsonic, and use the server, username and password.',
  },
  {
    apps: 'Music Assistant',
    how: 'Add the Subsonic music provider with the server, username and password.',
  },
  {
    apps: 'Apps that take an API key',
    how: 'Paste the API key and leave the username and password empty.',
  },
];

export function MusicAppsCard({ opened, onMade }: { opened: number; onMade: () => void }) {
  const [info, setInfo] = useState<MusicApps | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<NewMusicApp | null>(null);

  const load = useCallback(async () => {
    const r = await getMusicApps();
    if (!r.ok) {
      setError(r.message);
      return;
    }
    setError(null);
    setInfo(r.data);
    setProfile((p) => p ?? r.data.profiles[0]?.id ?? null);
  }, []);

  useEffect(() => {
    if (opened > 0) void load();
  }, [opened, load]);

  const setUp = async () => {
    if (profile == null) return;
    setBusy(true);
    const r = await setUpMusicApp(profile, name.trim());
    setBusy(false);
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    setMade(r.data);
    setName('');
    onMade();
  };

  return (
    <Card
      title="Set up a music app"
      hint="Listen in a music app that speaks the Subsonic or OpenSubsonic API. It plays one profile's music, can only play, and gets its own password. Unpair it under Paired devices and it stops."
    >
      <div className={css.pad}>
        {error ? <Note>{error}</Note> : null}
        {!info && !error ? <StatusWords tone="busy">Loading…</StatusWords> : null}
        {info && !made ? (
          info.profiles.length ? (
            <>
              <Row
                label="Whose music"
                htmlFor="music-app-profile"
                help="Only profiles with Use music apps are here."
              >
                <Select
                  id="music-app-profile"
                  value={profile ?? ''}
                  onChange={(e) => setProfile(Number(e.target.value))}
                  options={info.profiles.map((p) => ({ value: String(p.id), label: p.name }))}
                />
              </Row>
              <Row label="Name" htmlFor="music-app-name" help="So you can tell it apart later.">
                <TextField
                  id="music-app-name"
                  value={name}
                  placeholder="Phone, car, kitchen speaker"
                  maxLength={80}
                  onChange={(e) => setName(e.target.value)}
                />
              </Row>
              <FieldLine>
                <Btn
                  kind="primary"
                  id="music-app-set-up"
                  busy={busy}
                  disabled={profile == null}
                  onClick={() => void setUp()}
                >
                  Set it up
                </Btn>
              </FieldLine>
            </>
          ) : (
            <Note info>
              No profile may use music apps yet. Turn on Use music apps for a profile in People.
            </Note>
          )
        ) : null}
        {made ? <ShownApp app={made} onDone={() => setMade(null)} /> : null}
      </div>
      {info ? <Serving info={info} onSaved={() => void load()} /> : null}
    </Card>
  );
}

export function ShownApp({ app, onDone }: { app: NewMusicApp; onDone: () => void }) {
  return (
    <div role="region" aria-label="Music app sign-in">
      <div className={css.shown}>
        <div className={css.stack}>
          <Note>Copy these now. The password and API key are shown this once.</Note>
          <CopyValue label="Server" value={app.server} />
          <CopyValue label="Username" value={app.username} />
          <CopyValue id="music-app-password" label="Password" value={app.password} />
          <CopyValue label="API key" value={app.api_key} />
        </div>
        <QrImage className={css.qr} text={app.qr_text} label="Music app sign-in QR code" />
      </div>
      {app.addresses.length > 1 ? (
        <p className={css.guide}>
          Other addresses for this server: {app.addresses.map((a) => a.url).join(', ')}. At home the
          local one is faster.
        </p>
      ) : null}
      <ul className={css.guide} aria-label="How to add it">
        {APP_HINTS.map((h) => (
          <li key={h.apps}>
            <strong>{h.apps}:</strong> {h.how}
          </li>
        ))}
      </ul>
      <FieldLine>
        <Btn onClick={onDone}>Done</Btn>
      </FieldLine>
    </div>
  );
}

/** 1 to 8, then a few bigger steps, always with the saved one */
function slotChoices(info: MusicApps): number[] {
  const [low, high] = info.slots_range;
  const picks = new Set([1, 2, 3, 4, 5, 6, 8, 12, 16, info.transcode_slots]);
  return [...picks].filter((n) => n >= low && n <= high).sort((a, b) => a - b);
}

function Serving({ info, onSaved }: { info: MusicApps; onSaved: () => void }) {
  const save = async (body: Parameters<typeof saveMusicAppSettings>[0]) => {
    const r = await saveMusicAppSettings(body);
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    // read the card back from the server rather than trusting the reply's shape
    onSaved();
  };
  const servers = info.bridge.filter((b: BridgeServer) => b.active || b.on);
  return (
    <div className={css.pad}>
      <Row
        label="Convert to"
        htmlFor="music-app-format"
        help="When an app asks for a lower bitrate, songs are converted to this on the fly."
        warn={
          info.ffmpeg ? undefined : "ffmpeg isn't installed, so songs always go out as they are."
        }
      >
        <Select
          id="music-app-format"
          value={info.transcode_format}
          onChange={(e) => void save({ transcode_format: e.target.value })}
          options={info.formats.map((f) => ({ value: f, label: f === 'opus' ? 'Opus' : 'MP3' }))}
        />
      </Row>
      <Row
        label="Songs converted at once"
        htmlFor="music-app-slots"
        help="A song past this waits up to 20 seconds for its turn, then the app is told to try again. It never gets the bigger file instead. More uses more CPU."
      >
        <Select
          id="music-app-slots"
          value={String(info.transcode_slots)}
          onChange={(e) => void save({ transcode_slots: Number(e.target.value) })}
          options={slotChoices(info).map((n) => ({ value: String(n), label: String(n) }))}
        />
      </Row>
      {servers.map((b) => (
        <Row
          key={b.id}
          label={`Stream files SoulSync can't read through ${b.name}`}
          htmlFor={`music-app-bridge-${b.id}`}
          help={`Off: songs whose files SoulSync can't open say so. On: SoulSync plays them from ${b.name}, music only, and still records every play. A folder override in Settings > Library is the better fix when you can.`}
        >
          <Switch
            id={`music-app-bridge-${b.id}`}
            checked={b.on}
            onChange={(e) => void save({ bridge: { [b.id]: e.target.checked } })}
          />
        </Row>
      ))}
    </div>
  );
}
