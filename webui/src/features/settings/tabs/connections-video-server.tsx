/**
 * Connections > the video side's Server Connection (SETTINGS_PORT.md, video
 * pass V3b): the Plex / Jellyfin toggle (it picks the server and shows its
 * fields), each server's own URL and secret with a note on what is missing,
 * Test, the Jellyfin user, and which Movies, TV and other TV libraries the
 * scan reads. Was index.html and video-settings.js; the state is
 * video-server.ts. A field saves when it is left, a pick or a tick at once.
 */

import type { ReactNode } from 'react';

import { useCommit } from './commit';
import { type ConnField, type Server, otherTv, useVideoServer, videoServer } from './video-server';

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
    <input
      type={type}
      data-video-conn={field}
      placeholder={placeholder}
      ref={left}
      value={conn[field]}
      onChange={(event) => videoServer.type(field, event.target.value)}
    />
  );
}

function Config({ server, children }: { server: Server; children: ReactNode }) {
  const { active } = useVideoServer();
  return (
    <div
      className={active === server ? 'server-config-container' : 'server-config-container hidden'}
      data-video-server-config={server}
    >
      {children}
    </div>
  );
}

function Note({ server }: { server: Server }) {
  const { notes } = useVideoServer();
  return (
    <div className="setting-help-text" data-video-conn-note={server}>
      {notes[server]}
    </div>
  );
}

function TestRow({ server }: { server: Server }) {
  return (
    <div className="form-actions">
      <button
        className="test-button"
        type="button"
        data-video-conn-test={server}
        onClick={() => void videoServer.test(server)}
      >
        Test Connection
      </button>
    </div>
  );
}

function LibrarySelect({ kind }: { kind: 'movies' | 'tv' }) {
  const { libs } = useVideoServer();
  if (!libs)
    return (
      <select data-video-lib-select={kind} value="" onChange={() => undefined}>
        <option value="">Loading...</option>
      </select>
    );
  const list = kind === 'movies' ? libs.movies : libs.tv;
  return (
    <select
      data-video-lib-select={kind}
      value={kind === 'movies' ? libs.movie : libs.show}
      onChange={(event) =>
        void videoServer.setLibs(
          kind === 'movies' ? { movie: event.target.value } : { show: event.target.value },
        )
      }
    >
      <option value="">— None —</option>
      {list.map((l) => (
        <option key={l.title} value={l.title}>
          {l.title}
        </option>
      ))}
    </select>
  );
}

/** the other TV libraries, each with a Scan and an Anime tick */
function OtherTv() {
  const { libs } = useVideoServer();
  const others = libs ? otherTv(libs) : [];
  const tick = (title: string, kind: 'scan' | 'anime', on: boolean) => {
    if (!libs) return;
    const has = (list: string[]) =>
      on ? [...list.filter((t) => t !== title), title] : list.filter((t) => t !== title);
    // a library ticked Anime is one you want scanned: Scan is ticked with it
    if (kind === 'anime')
      void videoServer.setLibs({
        anime: has(libs.anime),
        ...(on ? { tvMore: has(libs.tvMore) } : {}),
      });
    else void videoServer.setLibs({ tvMore: has(libs.tvMore) });
  };
  return (
    <div
      className="form-group video-library-selectors video-library-tv-more"
      data-video-lib-tv-more-row=""
      hidden={!others.length}
    >
      <label>Other TV Libraries:</label>
      <div className="video-library-tv-more-list" data-video-lib-tv-more="">
        {others.map((l) => (
          <div key={l.title} className="video-library-tv-more-item">
            <span>{l.title}</span>
            <label className="checkbox-label">
              <input
                type="checkbox"
                data-video-lib-tv-scan={l.title}
                checked={libs!.tvMore.includes(l.title)}
                onChange={(event) => tick(l.title, 'scan', event.target.checked)}
              />
              {' Scan'}
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                data-video-lib-tv-anime={l.title}
                checked={libs!.anime.includes(l.title)}
                onChange={(event) => tick(l.title, 'anime', event.target.checked)}
              />
              {' Anime'}
            </label>
          </div>
        ))}
      </div>
    </div>
  );
}

export function VideoServerConnection() {
  const { active, users, status } = useVideoServer();
  return (
    <div className="settings-group" data-stg="connections" data-video-only="" data-svc-side="video">
      <h3>Server Connection</h3>
      <p className="setting-help-text" style={{ margin: '-4px 0 14px' }}>
        Pick the server the video side uses. Fields are pre-filled from your Music settings — edit
        them to point video at a different server without touching Music.
      </p>

      <div className="server-toggle-container">
        <button
          className={active === 'plex' ? 'server-toggle-btn active' : 'server-toggle-btn'}
          type="button"
          data-video-server-toggle="plex"
          onClick={() => void videoServer.pick('plex')}
        >
          <img src="/static/img/brands/plex.png" alt="Plex" className="server-logo" />
        </button>{' '}
        <button
          className={active === 'jellyfin' ? 'server-toggle-btn active' : 'server-toggle-btn'}
          type="button"
          data-video-server-toggle="jellyfin"
          onClick={() => void videoServer.pick('jellyfin')}
        >
          <img src="/static/img/brands/jellyfin.png" alt="Jellyfin" className="server-logo" />
        </button>
      </div>

      <Config server="plex">
        <div className="form-group">
          <label>Plex Server URL:</label>
          <ConnInput field="plex-url" type="text" placeholder="http://localhost:32400" />
        </div>
        <div className="form-group">
          <label>Plex Token:</label>
          <ConnInput field="plex-token" type="password" placeholder="X-Plex-Token" />
        </div>
        <Note server="plex" />
        <TestRow server="plex" />
      </Config>

      <Config server="jellyfin">
        <div className="form-group">
          <label>Jellyfin Server URL:</label>
          <ConnInput field="jellyfin-url" type="text" placeholder="http://localhost:8096" />
        </div>
        <div className="form-group">
          <label>API Key:</label>
          <ConnInput field="jellyfin-key" type="password" placeholder="Jellyfin API Key" />
        </div>
        <Note server="jellyfin" />
        <TestRow server="jellyfin" />
        <div
          className="form-group"
          data-video-jellyfin-user-wrap=""
          style={{ display: users ? 'block' : 'none', marginTop: '14px' }}
        >
          <label>Jellyfin User</label>
          <select
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
          </select>
          <small style={{ color: '#999', fontSize: '0.9em', display: 'block', marginTop: '5px' }}>
            Which user&apos;s libraries to scan. The Movies/TV lists below reflect this user — pick
            an admin to see everything.
          </small>
        </div>
      </Config>

      {/* which libraries on the chosen server the video side scans, one row each */}
      <div className="form-group video-library-selectors">
        <label>Movies Library:</label>
        <LibrarySelect kind="movies" />
      </div>
      <div className="form-group video-library-selectors">
        <label>TV Shows Library:</label>
        <LibrarySelect kind="tv" />
      </div>
      <OtherTv />
      <div className="setting-help-text video-library-note">
        Which libraries the video side scans. A library ticked Anime files its shows&apos; new
        episodes in the Anime folder (Settings &gt; Library).{' '}
        <span data-video-lib-save-status="">{status}</span>
      </div>
    </div>
  );
}
