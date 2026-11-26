/**
 * Connections > the video side's media server (SETTINGS_PORT.md, video pass
 * V3b; on the settings parts in settings-bic phase 3, the same card pattern
 * as the music side's): Plex or Jellyfin as a pick-one row with its state in
 * words, Test and View configuration in the card's head, and which Movies, TV
 * and other movie and TV libraries the scan reads as rows under it. View configuration
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
import {
  type ConnField,
  type Server,
  libWords,
  linksWords,
  otherMovies,
  otherTv,
  scanKey,
  useVideoServer,
  videoServer,
} from './video-server';

export const VIDEO_SERVERS: Array<{ id: Server; name: string; logo: string }> = [
  { id: 'plex', name: 'Plex', logo: '/static/img/brands/plex.png' },
  { id: 'jellyfin', name: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
  // no media server: SoulSync scans its own Movies and TV folders (the music
  // side's Standalone, for video)
  { id: 'soulsync', name: 'Standalone', logo: '/static/trans2.png' },
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
  if (active === 'soulsync') return { tone: 'ok', text: 'no media server' };
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

/** a library's size, or how its read is going (Reading 4K Movies… 312 found) */
function LibState({ kind, title }: { kind: 'movie' | 'show'; title: string }) {
  const { libs, scans } = useVideoServer();
  const list = kind === 'movie' ? libs?.movies : libs?.tv;
  const lib = list?.find((l) => l.title === title) ?? { title };
  const scan = scans[scanKey(kind, title)];
  const words = libWords(kind, lib, scan);
  if (!words) return null;
  return (
    <span data-video-lib-state={scanKey(kind, title)} data-state={scan?.state ?? ''}>
      {words}.{' '}
    </span>
  );
}

/** the other movie libraries, each with a Scan switch */
function OtherMovies() {
  const { libs } = useVideoServer();
  const others = libs ? otherMovies(libs) : [];
  const tick = (title: string, on: boolean) => {
    if (!libs) return;
    const rest = libs.moviesMore.filter((t) => t !== title);
    void videoServer.setLibs({ moviesMore: on ? [...rest, title] : rest });
  };
  return (
    <div data-video-lib-movies-more-row="" hidden={!others.length}>
      <div data-video-lib-movies-more="">
        {others.map((l) => (
          <Row
            key={l.title}
            sub
            field
            label={l.title}
            help={
              <>
                <LibState kind="movie" title={l.title} />
                Another movie library on this server. Switched on, it is read at once.
              </>
            }
          >
            <label className={s.inlineSwitch}>
              Scan
              <Switch
                data-video-lib-movies-scan={l.title}
                aria-label={`Scan ${l.title}`}
                checked={libs!.moviesMore.includes(l.title)}
                onChange={(event) => tick(l.title, event.target.checked)}
              />
            </label>
          </Row>
        ))}
      </div>
    </div>
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
          <Row
            key={l.title}
            sub
            field
            label={l.title}
            help={
              <>
                <LibState kind="show" title={l.title} />
                Another TV library on this server. Scan reads it, at once. Anime is for a library of
                anime, numbered the way anime is; leave it off for anything else, like YouTube.
              </>
            }
          >
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

/** the home video libraries, each with a Scan switch: read into the player's
 *  home videos, never matched or renamed (core/video/home_videos.py) */
function HomeLibraries() {
  const { libs } = useVideoServer();
  const home = libs?.home ?? [];
  const tick = (title: string, on: boolean) => {
    if (!libs) return;
    const rest = (libs.homeMore ?? []).filter((t) => t !== title);
    void videoServer.setLibs({ homeMore: on ? [...rest, title] : rest });
  };
  if (!home.length) return null;
  return (
    <div data-video-lib-home-row="">
      {home.map((l) => (
        <Row
          key={l.title}
          sub
          field
          label={l.title}
          help={`Home videos${l.count != null ? ` (${l.count.toLocaleString()})` : ''}. Scan reads them into the player's Home videos, never looked up or renamed.`}
        >
          <label className={s.inlineSwitch}>
            Scan
            <Switch
              data-video-lib-home-scan={l.title}
              aria-label={`Scan ${l.title} as home videos`}
              checked={(libs!.homeMore ?? []).includes(l.title)}
              onChange={(event) => tick(l.title, event.target.checked)}
            />
          </label>
        </Row>
      ))}
    </div>
  );
}

/** plex and jellyfin as one library: shown only with both connected */
function LinkServers() {
  const { links } = useVideoServer();
  if (!links) return null;
  const other = links.other === 'plex' ? 'Plex' : 'Jellyfin';
  const running = links.last?.state === 'running';
  return (
    <Row
      label={`Keep ${other} in step`}
      htmlFor="video-link-servers"
      field
      help={
        <>
          Watched and unwatched follow across both servers, and switching the video server keeps
          your library. <span data-video-links-status="">{linksWords(links)}</span>
        </>
      }
    >
      <Btn
        small
        data-video-links-run=""
        disabled={!links.enabled || running}
        busy={running}
        onClick={() => void videoServer.linkNow()}
      >
        Link now
      </Btn>
      <Switch
        id="video-link-servers"
        data-video-links-switch=""
        aria-label={`Keep ${other} in step`}
        checked={links.enabled}
        onChange={(event) => void videoServer.setLinks(event.target.checked)}
      />
    </Row>
  );
}

/** the card on the Connections page (video side) */
export function VideoServerConnection({ onView }: { onView?: () => void }) {
  const { active, status } = useVideoServer();
  const state = useVideoServerState();
  const standalone = active === 'soulsync';
  return (
    <Card
      title="Server connections"
      hint="Pick one. A server's fields start as your music side's: change them to point video at another server. Standalone needs no server: SoulSync scans your own folders."
      id="stg-video-server-card"
      data-video-only=""
      data-svc-side="video"
      actions={
        <>
          <StatusWords tone={state.tone}>{`${videoServerName(active)}: ${state.text}`}</StatusWords>
          <Btn
            small
            data-video-conn-test={active ?? ''}
            disabled={!active || standalone}
            onClick={() => active && active !== 'soulsync' && void videoServer.test(active)}
          >
            Test server
          </Btn>
          <Btn small icon="gear" disabled={!active || standalone} onClick={() => onView?.()}>
            View configuration
          </Btn>
        </>
      }
    >
      <div
        id="video-server-pick"
        className={s.servers}
        data-count={VIDEO_SERVERS.length}
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
      {standalone ? (
        <p className={s.cardFoot} data-video-standalone="">
          SoulSync scans your Movies and TV folders (Settings, Library) and works out what each
          title is. A title it isn't sure of is listed under Tools as unmatched for you to pick,
          never guessed. Downloads appear in the library the moment they land.
        </p>
      ) : (
        <>
          <div className={s.cardRows}>
            <LibraryRow kind="movies" />
            <OtherMovies />
            <LibraryRow kind="tv" />
            <OtherTv />
            <HomeLibraries />
            <LinkServers />
          </div>
          <p className={s.cardFoot}>
            Which libraries the video side scans. A library switched to Anime files its shows' new
            episodes in the Anime folder (Settings, Library).{' '}
            <span data-video-lib-save-status="">{status}</span>
          </p>
        </>
      )}
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
