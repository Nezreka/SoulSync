/**
 * Step: the catalogue every track is looked up in, and the accounts worth
 * bringing. Each account is one row with one action; its fields only open
 * when asked for.
 */

import { useState } from 'react';

import type { Catalogue } from '../types';

import { probeService } from '../api';
import { useOnboarding } from '../store';
import { CheckIcon, Field, Option, s, Spinner, StepFrame } from '../ui';

const CATALOGUES: { id: Catalogue; name: string; desc: string }[] = [
  { id: 'deezer', name: 'Deezer', desc: 'Recommended. No key needed, great artwork.' },
  {
    id: 'itunes',
    name: 'Apple Music',
    desc: 'No key needed. Strong on classical and soundtracks.',
  },
  { id: 'spotify', name: 'Spotify', desc: 'Needs a free developer app. Best for playlists.' },
];

type AccountId = 'spotify' | 'listenbrainz' | 'lastfm';

function SpotifyFields() {
  const spotify = useOnboarding((st) => st.draft.spotify);
  const update = useOnboarding((st) => st.update);
  return (
    <>
      <p className={s.note}>
        Create an app at developer.spotify.com/dashboard and add the redirect address
        http://127.0.0.1:8888/callback. You sign in to Spotify after setup.
      </p>
      <div className={s.grid2}>
        <Field
          id="ob-sp-id"
          label="Client ID"
          value={spotify.clientId}
          onChange={(e) => update((d) => void (d.spotify.clientId = e.target.value.trim()))}
        />
        <Field
          id="ob-sp-secret"
          label="Client secret"
          type="password"
          value={spotify.clientSecret}
          onChange={(e) => update((d) => void (d.spotify.clientSecret = e.target.value.trim()))}
        />
      </div>
    </>
  );
}

function ListenBrainzFields({ onLinked }: { onLinked: () => void }) {
  const lb = useOnboarding((st) => st.draft.listenbrainz);
  const update = useOnboarding((st) => st.update);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function check() {
    setBusy(true);
    setError('');
    const r = await probeService('listenbrainz', { token: lb.token });
    setBusy(false);
    if (r.ok) {
      const user = typeof r.detail?.user === 'string' ? r.detail.user : 'your account';
      update((d) => void (d.listenbrainz.user = user));
      onLinked();
    } else setError(r.message);
  }

  return (
    <>
      <div className={s.inputRow}>
        <Field
          id="ob-lb-token"
          label="User token, from listenbrainz.org/settings"
          type="password"
          value={lb.token}
          onChange={(e) => update((d) => void (d.listenbrainz.token = e.target.value.trim()))}
        />
        <button type="button" className={s.light} onClick={check} disabled={busy || !lb.token}>
          {busy ? <Spinner /> : null}
          Check
        </button>
      </div>
      {error ? (
        <p className={s.error} role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

function LastfmFields() {
  const lastfm = useOnboarding((st) => st.draft.lastfm);
  const update = useOnboarding((st) => st.update);
  return (
    <>
      <p className={s.note}>
        Get an API key at last.fm/api/account/create. Scrobbling is switched on later in Settings.
      </p>
      <div className={s.grid2}>
        <Field
          id="ob-lf-user"
          label="Last.fm username"
          value={lastfm.username}
          onChange={(e) => update((d) => void (d.lastfm.username = e.target.value.trim()))}
        />
        <Field
          id="ob-lf-key"
          label="API key"
          type="password"
          value={lastfm.apiKey}
          onChange={(e) => update((d) => void (d.lastfm.apiKey = e.target.value.trim()))}
        />
      </div>
    </>
  );
}

export function MetadataStep() {
  const draft = useOnboarding((st) => st.draft);
  const update = useOnboarding((st) => st.update);
  const [open, setOpen] = useState<AccountId | null>(
    draft.catalogue === 'spotify' ? 'spotify' : null,
  );

  const linked: Record<AccountId, boolean> = {
    spotify: !!(draft.spotify.clientId && draft.spotify.clientSecret),
    listenbrainz: !!draft.listenbrainz.user,
    lastfm: !!(draft.lastfm.apiKey && draft.lastfm.username),
  };
  const accounts: { id: AccountId; name: string; desc: string; tint: string; action: string }[] = [
    {
      id: 'spotify',
      name: 'Spotify',
      desc: 'Your playlists and Discover Weekly.',
      tint: '#1db954',
      action: 'Add app keys',
    },
    {
      id: 'listenbrainz',
      name: 'ListenBrainz',
      desc: 'Scrobbles and weekly recommendations.',
      tint: '#eb743b',
      action: 'Add token',
    },
    {
      id: 'lastfm',
      name: 'Last.fm',
      desc: 'Listening history import and radio.',
      tint: '#d51007',
      action: 'Add key',
    },
  ];

  return (
    <StepFrame
      step="metadata"
      title="Names, covers and your accounts"
      lead="SoulSync looks up every track in a music catalogue so titles, track numbers and artwork come out right."
    >
      <section className={s.section}>
        <h2 className={s.h2}>Main catalogue</h2>
        <div className={s.grid3} role="radiogroup" aria-label="Main catalogue">
          {CATALOGUES.map((c) => (
            <Option
              key={c.id}
              title={c.name}
              desc={c.desc}
              checked={draft.catalogue === c.id}
              onSelect={() => {
                update((d) => void (d.catalogue = c.id));
                if (c.id === 'spotify' && !linked.spotify) setOpen('spotify');
              }}
            />
          ))}
        </div>
      </section>

      <section className={s.section}>
        <div className={s.sectionHead}>
          <h2 className={s.h2}>Bring your accounts</h2>
          <span className={s.sub}>Optional. Each profile can link its own later.</span>
        </div>
        <div className={s.list}>
          {accounts.map((a) => (
            <div key={a.id} className={`${s.listRow} ${s.stack}`}>
              <div className={s.cardRow}>
                <span
                  className={s.logo}
                  style={{
                    width: 36,
                    height: 36,
                    fontSize: 15,
                    borderRadius: 10,
                    background: a.tint,
                    color: '#09090b',
                  }}
                  aria-hidden="true"
                >
                  {a.name.charAt(0)}
                </span>
                <div className={s.grow} style={{ gap: 3 }}>
                  <span style={{ fontSize: 15, fontWeight: 500 }}>{a.name}</span>
                  <span className={s.sub}>
                    {a.id === 'listenbrainz' && draft.listenbrainz.user
                      ? `Linked as ${draft.listenbrainz.user}`
                      : a.desc}
                  </span>
                </div>
                {linked[a.id] && open !== a.id ? (
                  <span className={s.connected} style={{ fontSize: 13 }}>
                    <CheckIcon size={14} stroke={2.6} />
                    Added
                  </span>
                ) : (
                  <button
                    type="button"
                    className={s.secondary}
                    aria-expanded={open === a.id}
                    onClick={() => setOpen(open === a.id ? null : a.id)}
                  >
                    {open === a.id ? 'Done' : a.action}
                  </button>
                )}
              </div>
              {open === a.id ? (
                <div className={s.section} style={{ paddingLeft: 52 }}>
                  {a.id === 'spotify' ? <SpotifyFields /> : null}
                  {a.id === 'listenbrainz' ? (
                    <ListenBrainzFields onLinked={() => setOpen(null)} />
                  ) : null}
                  {a.id === 'lastfm' ? <LastfmFields /> : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </section>
    </StepFrame>
  );
}
