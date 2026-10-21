/**
 * Step: the media server. A server discovery found leads the page; Plex signs
 * in with its PIN flow (no token pasting), Jellyfin and Navidrome take their
 * address and key. Once connected, the libraries are listed with their sizes
 * so the user picks by name, not by guessing.
 */

import { useEffect, useRef, useState } from 'react';

import type { LibraryInfo, ServerKind, ServerLibraries } from '../types';

import { checkPlexPin, listLibraries, probeService, startPlexPin } from '../api';
import { hasMusic, hasVideo, serverConfig, serverLabel } from '../plan';
import { useOnboarding } from '../store';
import { CheckIcon, cx, Field, s, Spinner, StepFrame } from '../ui';

const SERVERS: {
  id: Exclude<ServerKind, 'soulsync'>;
  name: string;
  tint: string;
  glyph: string;
}[] = [
  { id: 'plex', name: 'Plex', tint: '#e5a00d', glyph: 'P' },
  { id: 'jellyfin', name: 'Jellyfin', tint: '#a95ce0', glyph: 'J' },
  { id: 'navidrome', name: 'Navidrome', tint: '#4aa3df', glyph: 'N' },
];

function LibraryPills({
  label,
  libs,
  value,
  onPick,
  unit,
}: {
  label: string;
  libs: LibraryInfo[];
  value: string | null;
  onPick: (title: string | null) => void;
  unit: string;
}) {
  if (!libs.length) return null;
  return (
    <div className={s.section} style={{ gap: 10 }}>
      <span className={s.label}>{label}</span>
      <div className={s.pills} role="radiogroup" aria-label={label}>
        {libs.map((lib) => {
          const on = value === lib.title;
          return (
            <button
              key={lib.id}
              type="button"
              className={s.pill}
              aria-pressed={on}
              onClick={() => onPick(on ? null : lib.title)}
            >
              <span className={s.tick}>
                <CheckIcon size={10} stroke={3.6} />
              </span>
              {lib.title}
              {lib.count != null ? (
                <span className={s.pillCount}>
                  {lib.count.toLocaleString()} {unit}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PlexSignIn({ discoveredUrl }: { discoveredUrl?: string }) {
  const update = useOnboarding((st) => st.update);
  const [pin, setPin] = useState<{ code: string; id: string } | null>(null);
  const [error, setError] = useState('');
  const [manual, setManual] = useState(false);
  const server = useOnboarding((st) => st.draft.server);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearInterval(timer.current), []);

  async function begin() {
    setError('');
    try {
      const started = await startPlexPin();
      if (!started.success) throw new Error(started.error || "Plex didn't answer");
      setPin({ code: started.code, id: started.request_id });
      window.open('https://plex.tv/link', 'plex-link', 'width=520,height=720');
      const deadline = Date.now() + 4 * 60 * 1000;
      timer.current = window.setInterval(async () => {
        if (Date.now() > deadline) {
          window.clearInterval(timer.current);
          setPin(null);
          setError('That code ran out. Try again.');
          return;
        }
        try {
          const status = await checkPlexPin(started.request_id);
          if (status.expired) throw new Error('That code ran out. Try again.');
          if (status.success && status.token) {
            window.clearInterval(timer.current);
            setPin(null);
            update((d) => {
              d.server.token = status.token ?? '';
              d.server.url = discoveredUrl || status.found_url || d.server.url;
            });
          }
        } catch (e) {
          window.clearInterval(timer.current);
          setPin(null);
          setError(e instanceof Error ? e.message : 'Plex sign-in failed');
        }
      }, 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Plex sign-in failed');
    }
  }

  if (manual) {
    return (
      <div className={s.grid2}>
        <Field
          id="ob-plex-url"
          label="Plex address"
          placeholder="http://192.168.1.20:32400"
          value={server.url}
          onChange={(e) => update((d) => void (d.server.url = e.target.value))}
        />
        <Field
          id="ob-plex-token"
          label="Plex token"
          type="password"
          value={server.token}
          onChange={(e) => update((d) => void (d.server.token = e.target.value))}
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {pin ? (
        <p className={s.note} style={{ color: '#a1a1aa' }}>
          A Plex window opened. Enter <span className={s.code}>{pin.code}</span> at plex.tv/link and
          this finishes by itself.
        </p>
      ) : (
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className={s.primary}
            style={{ background: '#e5a00d', color: '#1a1305' }}
            onClick={begin}
          >
            Sign in with Plex
          </button>
          <button type="button" className={s.quiet} onClick={() => setManual(true)}>
            Enter an address and token instead
          </button>
        </div>
      )}
      {pin ? <Spinner label="Waiting for Plex" /> : null}
      {error ? (
        <p className={s.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function ServerStep() {
  const draft = useOnboarding((st) => st.draft);
  const found = useOnboarding((st) => st.found);
  const update = useOnboarding((st) => st.update);
  const server = draft.server;
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState('');
  const [libs, setLibs] = useState<ServerLibraries | null>(null);
  const music = hasMusic(draft.purposes);
  const video = hasVideo(draft.purposes);

  // a discovered server is picked for the user on first visit
  useEffect(() => {
    if (server.kind) return;
    const first = SERVERS.find((x) => found[x.id]);
    if (first)
      update((d) => {
        d.server.kind = first.id;
        d.server.url = found[first.id];
      });
  }, [found, server.kind, update]);

  // connect as soon as plex hands back a token
  const plexReady = server.kind === 'plex' && !!server.token && !!server.url && !server.connected;
  useEffect(() => {
    if (plexReady) void connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plexReady]);

  // a reload lands here connected but without the list
  useEffect(() => {
    if (server.connected && !libs && server.kind && server.kind !== 'soulsync')
      void listLibraries(server.kind, serverConfig(server)).then(setLibs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.connected, server.kind]);

  async function connect() {
    if (!server.kind || server.kind === 'soulsync') return;
    setChecking(true);
    setMessage('');
    const current = useOnboarding.getState().draft.server;
    const result = await probeService(current.kind as string, serverConfig(current));
    if (!result.ok) {
      setMessage(result.message);
      setChecking(false);
      return;
    }
    const list = await listLibraries(current.kind as string, serverConfig(current));
    setLibs(list);
    update((d) => {
      d.server.connected = true;
      d.server.serverName = String(result.detail?.server_name ?? '');
      d.server.libraryPaths = Object.fromEntries(
        [...list.music, ...list.movies, ...list.tv].map((lib) => [lib.title, lib.locations]),
      );
      d.server.musicLibrary ??= list.music[0]?.title ?? null;
      if (video) {
        d.server.moviesLibrary ??= list.movies[0]?.title ?? null;
        d.server.tvLibrary ??= list.tv[0]?.title ?? null;
      }
    });
    setChecking(false);
  }

  function pick(kind: ServerKind) {
    setLibs(null);
    setMessage('');
    update((d) => {
      d.server = {
        ...d.server,
        kind,
        url: kind === 'soulsync' ? '' : (found[kind] ?? ''),
        token: '',
        apiKey: '',
        username: '',
        password: '',
        serverName: '',
        connected: false,
        musicLibrary: null,
        moviesLibrary: null,
        tvLibrary: null,
        libraryPaths: {},
      };
    });
  }

  const featured = SERVERS.find((x) => x.id === server.kind);
  const others = SERVERS.filter((x) => x.id !== server.kind);
  const set = (fn: (srv: typeof server) => void) => update((d) => fn(d.server));

  return (
    <StepFrame
      step="server"
      title="Where do you watch and listen?"
      lead="SoulSync tells your server when new things land, and checks what you already own before it downloads anything."
    >
      {featured ? (
        <section className={s.card}>
          <div className={s.cardRow}>
            <div className={s.logo} style={{ color: featured.tint }} aria-hidden="true">
              {featured.glyph}
            </div>
            <div className={s.grow}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span className={s.strong}>{server.serverName || featured.name}</span>
                {found[featured.id] ? <span className={s.found}>Found on your network</span> : null}
              </div>
              <span className={s.sub}>{server.url || 'Not found nearby, enter its address'}</span>
            </div>
            {server.connected ? (
              <span className={s.connected}>
                <CheckIcon size={16} stroke={2.6} />
                Connected
              </span>
            ) : null}
          </div>

          {!server.connected ? (
            <div className={s.cardDivider}>
              {featured.id === 'plex' ? <PlexSignIn discoveredUrl={found.plex} /> : null}
              {featured.id === 'jellyfin' ? (
                <div className={s.grid2}>
                  <Field
                    id="ob-jf-url"
                    label="Jellyfin address"
                    placeholder="http://192.168.1.20:8096"
                    value={server.url}
                    onChange={(e) => set((x) => void (x.url = e.target.value))}
                  />
                  <Field
                    id="ob-jf-key"
                    label="API key"
                    type="password"
                    placeholder="Dashboard, API Keys"
                    value={server.apiKey}
                    onChange={(e) => set((x) => void (x.apiKey = e.target.value))}
                  />
                </div>
              ) : null}
              {featured.id === 'navidrome' ? (
                <div className={s.grid3}>
                  <Field
                    id="ob-nd-url"
                    label="Navidrome address"
                    placeholder="http://192.168.1.20:4533"
                    value={server.url}
                    onChange={(e) => set((x) => void (x.url = e.target.value))}
                  />
                  <Field
                    id="ob-nd-user"
                    label="Username"
                    value={server.username}
                    onChange={(e) => set((x) => void (x.username = e.target.value))}
                  />
                  <Field
                    id="ob-nd-pass"
                    label="Password"
                    type="password"
                    value={server.password}
                    onChange={(e) => set((x) => void (x.password = e.target.value))}
                  />
                </div>
              ) : null}
              {featured.id !== 'plex' || (server.url && server.token) ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 14 }}>
                  <button type="button" className={s.light} onClick={connect} disabled={checking}>
                    {checking ? <Spinner /> : null}
                    Connect
                  </button>
                  {message ? (
                    <span className={s.error} role="alert">
                      {message}
                    </span>
                  ) : null}
                </div>
              ) : message ? (
                <p className={s.error} role="alert">
                  {message}
                </p>
              ) : null}
            </div>
          ) : (
            <div className={cx(s.cardDivider, s.section)} style={{ gap: 18 }}>
              {!libs ? <Spinner label="Reading libraries" /> : null}
              {libs && music ? (
                <LibraryPills
                  label="Music library"
                  libs={libs.music}
                  value={server.musicLibrary}
                  unit={server.kind === 'plex' || server.kind === 'jellyfin' ? 'albums' : ''}
                  onPick={(t) => set((x) => void (x.musicLibrary = t))}
                />
              ) : null}
              {libs && video ? (
                <>
                  <LibraryPills
                    label="Movies library"
                    libs={libs.movies}
                    value={server.moviesLibrary}
                    unit="films"
                    onPick={(t) => set((x) => void (x.moviesLibrary = t))}
                  />
                  <LibraryPills
                    label="TV library"
                    libs={libs.tv}
                    value={server.tvLibrary}
                    unit="shows"
                    onPick={(t) => set((x) => void (x.tvLibrary = t))}
                  />
                </>
              ) : null}
              {libs && !libs.music.length && !libs.movies.length && !libs.tv.length ? (
                <p className={s.note}>
                  No libraries came back. You can pick them later in Settings.
                </p>
              ) : null}
            </div>
          )}
        </section>
      ) : null}

      <section className={s.section}>
        <span className={s.label} style={{ color: '#a1a1aa' }}>
          {featured ? 'Or use something else' : 'Pick your server'}
        </span>
        <div className={s.grid3} role="radiogroup" aria-label="Media server">
          {others.map((x) => (
            <button
              key={x.id}
              type="button"
              className={s.option}
              role="radio"
              aria-checked={false}
              onClick={() => pick(x.id)}
            >
              <span className={s.optionTitle}>{x.name}</span>
              <span className={s.optionDesc}>
                {found[x.id] ? `Found at ${found[x.id]}` : 'Not found nearby. Enter its address'}
              </span>
            </button>
          ))}
          <button
            type="button"
            className={s.option}
            role="radio"
            aria-checked={server.kind === 'soulsync'}
            onClick={() => pick('soulsync')}
          >
            <span className={s.optionHead}>
              <span className={s.optionTitle}>No server</span>
              <span className={s.radio} aria-hidden="true" />
            </span>
            <span className={s.optionDesc}>
              SoulSync keeps the library and plays it in the browser
            </span>
          </button>
        </div>
        {server.kind === 'soulsync' ? (
          <p className={s.note}>
            Good choice to start with. You can add {serverLabel('plex')}, Jellyfin or Navidrome any
            time.
          </p>
        ) : null}
      </section>
    </StepFrame>
  );
}
