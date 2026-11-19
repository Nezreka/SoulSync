/**
 * Connections > the video side's media server (SETTINGS_PORT.md, video pass
 * V3b; on the settings parts in settings-bic phase 3, the same card pattern
 * as the music side's): Plex or Jellyfin as a pick-one row with its state in
 * words, Test and View Configuration in the card's head, and which Movies, TV
 * and other TV libraries the scan reads as rows under it. View Configuration
 * opens the server's fields (URL, token or key, the Jellyfin user) in the
 * side sheet (VideoServerFields). The state is video-server.ts. A field saves
 * when it is left, a pick or a switch at once.
 */

import {
  Btn,
  Card,
  Row,
  Select,
  SheetField,
  SheetGroup,
  StatusWords,
  Switch,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import { useCommit } from './commit';
import { type ConnField, type Server, otherTv, useVideoServer, videoServer } from './video-server';

export const VIDEO_SERVERS: Array<{ id: Server; name: string; logo: string }> = [
  { id: 'plex', name: 'Plex', logo: '/static/img/brands/plex.png' },
  { id: 'jellyfin', name: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
];
export const videoServerName = (server: Server | null) =>
  VIDEO_SERVERS.find((v) => v.id === server)?.name ?? 'Media server';

function ConnInput({
  field,
  type,
  placeholder,
}: {
  field: ConnField;
  type: string;
  placeholder: string;
}) {
  const { conn } = useVideoServer();
  const left = useCommit<HTMLInputElement>(() => void videoServer.saveConn());
  return (
    <TextField
      full
      mono={type !== 'password'}
      type={type}
      id={`video-conn-${field}`}
      data-video-conn={field}
      placeholder={placeholder}
      autoComplete="off"
      ref={left}
      value={conn[field]}
      onChange={(event) => videoServer.type(field, event.target.value)}
    />
  );
}

/** the chosen server's state in words: its last Test, else what is missing */
export function useVideoServerState(): { tone: 'ok' | 'bad' | 'warn' | 'idle'; text: string } {
  const { active, said, notes } = useVideoServer();
  if (!active) return { tone: 'idle', text: 'Loading' };
  const tested = said[active];
  if (tested) return tested;
  const note = notes[active] || '';
  if (/^Not connected|^Add the/.test(note)) return { tone: 'warn', text: 'Not set up' };
  return { tone: 'idle', text: 'Not tested yet' };
}

/** which library on the chosen server the video side scans for movies or TV */
function LibraryRow({ kind }: { kind: 'movies' | 'tv' }) {
  const { libs } = useVideoServer();
  const label = kind === 'movies' ? 'Movies library' : 'TV shows library';
  if (!libs)
    return (
      <Row label={label} htmlFor={`video-lib-${kind}`} field>
        <Select
          id={`video-lib-${kind}`}
          data-video-lib-select={kind}
          value=""
          onChange={() => undefined}
        >
          <option value="">Loading...</option>
        </Select>
      </Row>
    );
  const list = kind === 'movies' ? libs.movies : libs.tv;
  return (
    <Row label={label} htmlFor={`video-lib-${kind}`} field>
      <Select
        id={`video-lib-${kind}`}
        data-video-lib-select={kind}
        value={kind === 'movies' ? libs.movie : libs.show}
        onChange={(event) =>
          void videoServer.setLibs(
            kind === 'movies' ? { movie: event.target.value } : { show: event.target.value },
          )
        }
      >
        <option value="">None</option>
        {list.map((l) => (
          <option key={l.title} value={l.title}>
            {l.title}
          </option>
        ))}
      </Select>
    </Row>
  );
}

/** the other TV libraries, each with a Scan and an Anime switch */
function OtherTv() {
  const { libs } = useVideoServer();
  const others = libs ? otherTv(libs) : [];
  const tick = (title: string, kind: 'scan' | 'anime', on: boolean) => {
    if (!libs) return;
    const has = (list: string[]) =>
      on ? [...list.filter((t) => t !== title), title] : list.filter((t) => t !== title);
    // a library switched to Anime is one you want scanned: Scan comes on with it
    if (kind === 'anime')
      void videoServer.setLibs({
        anime: has(libs.anime),
        ...(on ? { tvMore: has(libs.tvMore) } : {}),
      });
    else void videoServer.setLibs({ tvMore: has(libs.tvMore) });
  };
  return (
    <div data-video-lib-tv-more-row="" hidden={!others.length}>
      <div data-video-lib-tv-more="">
        {others.map((l) => (
          <Row key={l.title} sub label={l.title} help="Another TV library on this server.">
            <label className={s.inlineSwitch}>
              Scan
              <Switch
                data-video-lib-tv-scan={l.title}
                aria-label={`Scan ${l.title}`}
                checked={libs!.tvMore.includes(l.title)}
                onChange={(event) => tick(l.title, 'scan', event.target.checked)}
              />
            </label>
            <label className={s.inlineSwitch}>
              Anime
              <Switch
                data-video-lib-tv-anime={l.title}
                aria-label={`${l.title} is anime`}
                checked={libs!.anime.includes(l.title)}
                onChange={(event) => tick(l.title, 'anime', event.target.checked)}
              />
            </label>
          </Row>
        ))}
      </div>
    </div>
  );
}

/** the card on the Connections page (video side) */
export function VideoServerConnection({ onView }: { onView?: () => void }) {
  const { active, status } = useVideoServer();
  const state = useVideoServerState();
  return (
    <Card
      title="Server Connections"
      hint="Pick one. Its fields start as your music side's: change them to point video at another server."
      id="stg-video-server-card"
      data-video-only=""
      data-svc-side="video"
      actions={
        <>
          <StatusWords tone={state.tone}>{`${videoServerName(active)}: ${state.text}`}</StatusWords>
          <Btn
            small
            data-video-conn-test={active ?? ''}
            disabled={!active}
            onClick={() => active && void videoServer.test(active)}
          >
            Test Server
          </Btn>
          <Btn small icon="gear" disabled={!active} onClick={() => onView?.()}>
            View Configuration
          </Btn>
        </>
      }
    >
      <div
        id="video-server-pick"
        className={s.servers}
        data-count="2"
        role="radiogroup"
        aria-label="Video media server"
      >
        {VIDEO_SERVERS.map((server) => (
          <button
            key={server.id}
            type="button"
            role="radio"
            aria-checked={server.id === active}
            data-video-server-toggle={server.id}
            className={server.id === active ? `${s.server} ${s.serverOn}` : s.server}
            onClick={() => void videoServer.pick(server.id)}
          >
            <img src={server.logo} alt="" className={s.serverLogo} />
            <span className={s.serverName}>{server.name}</span>
          </button>
        ))}
      </div>
      <div className={s.cardRows}>
        <LibraryRow kind="movies" />
        <LibraryRow kind="tv" />
        <OtherTv />
      </div>
      <p className={s.cardFoot}>
        Which libraries the video side scans. A library switched to Anime files its shows' new
        episodes in the Anime folder (Settings, Library).{' '}
        <span data-video-lib-save-status="">{status}</span>
      </p>
    </Card>
  );
}

/** the chosen server's fields, for the side sheet */
export function VideoServerFields() {
  const { active, users, notes } = useVideoServer();
  return (
    <div data-ui="" data-video-only="" data-video-server-fields="">
      <div data-video-server-config="plex" hidden={active !== 'plex'}>
        <SheetGroup title="Connection" />
        <SheetField label="Plex server URL" htmlFor="video-conn-plex-url">
          <ConnInput field="plex-url" type="text" placeholder="http://localhost:32400" />
        </SheetField>
        <SheetField
          label="Plex token"
          htmlFor="video-conn-plex-token"
          help={<span data-video-conn-note="plex">{notes.plex}</span>}
        >
          <ConnInput field="plex-token" type="password" placeholder="X-Plex-Token" />
        </SheetField>
      </div>
      <div data-video-server-config="jellyfin" hidden={active !== 'jellyfin'}>
        <SheetGroup title="Connection" />
        <SheetField label="Jellyfin server URL" htmlFor="video-conn-jellyfin-url">
          <ConnInput field="jellyfin-url" type="text" placeholder="http://localhost:8096" />
        </SheetField>
        <SheetField
          label="API key"
          htmlFor="video-conn-jellyfin-key"
          help={<span data-video-conn-note="jellyfin">{notes.jellyfin}</span>}
        >
          <ConnInput field="jellyfin-key" type="password" placeholder="Jellyfin API Key" />
        </SheetField>
        <div data-video-jellyfin-user-wrap="" hidden={!users}>
          <Row
            label="Jellyfin user"
            htmlFor="video-jellyfin-user"
            field
            help="Whose libraries are scanned. The library lists follow this user: pick an admin to see everything."
          >
            <Select
              id="video-jellyfin-user"
              data-video-jellyfin-user=""
              value={users?.selected ?? ''}
              onChange={(event) => void videoServer.pickUser(event.target.value)}
            >
              <option value="">Select User</option>
              {users?.list.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name + (u.admin ? ' (admin)' : '')}
                </option>
              ))}
            </Select>
          </Row>
        </div>
      </div>
    </div>
  );
}
