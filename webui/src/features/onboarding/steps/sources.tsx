/**
 * Step: download sources as an ordered chain, plus the quality preset.
 * Soulseek is put first when discovery found slskd. Tidal and Qobuz sign in
 * here with their real flows (they save their own session, so the preview
 * only pretends).
 */

import { useEffect, useRef, useState } from 'react';

import type { QualityPreset, SourceId } from '../types';

import { checkTidalLogin, probeService, qobuzLogin, startTidalLogin } from '../api';
import { ALL_SOURCES, SOURCE_INFO } from '../plan';
import { useOnboarding } from '../store';
import { CheckIcon, Chip, CloseIcon, Field, Option, s, Spinner, StepFrame } from '../ui';

const PRESETS: { id: QualityPreset; name: string; desc: string }[] = [
  { id: 'audiophile', name: 'Audiophile', desc: 'Lossless only, hi-res first' },
  { id: 'balanced', name: 'Balanced', desc: 'Lossless, falls back to 320' },
  { id: 'space_saver', name: 'Space saver', desc: 'Good MP3 and AAC' },
];

function SlskdBody() {
  const slskd = useOnboarding((st) => st.draft.slskd);
  const update = useOnboarding((st) => st.update);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function connect() {
    setBusy(true);
    setError('');
    const r = await probeService('slskd', { slskd_url: slskd.url, api_key: slskd.apiKey });
    setBusy(false);
    if (r.ok) update((d) => void (d.slskd.connected = true));
    else setError(r.message);
  }

  if (slskd.connected) return null;
  return (
    <div className={s.sourceBody}>
      <div className={s.grid2}>
        <Field
          id="ob-slskd-url"
          label="slskd address"
          placeholder="http://192.168.1.20:5030"
          value={slskd.url}
          onChange={(e) => update((d) => void (d.slskd.url = e.target.value))}
        />
        <Field
          id="ob-slskd-key"
          label="API key"
          type="password"
          placeholder="From slskd's settings.yml"
          value={slskd.apiKey}
          onChange={(e) => update((d) => void (d.slskd.apiKey = e.target.value))}
        />
      </div>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
        <button
          type="button"
          className={s.light}
          onClick={connect}
          disabled={busy || !slskd.url.trim()}
        >
          {busy ? <Spinner /> : null}
          Connect
        </button>
        {error ? (
          <span className={s.error} role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function TidalBody() {
  const preview = useOnboarding((st) => st.preview);
  const signed = useOnboarding((st) => st.draft.signedIn.tidal);
  const update = useOnboarding((st) => st.update);
  const [code, setCode] = useState<{ uri: string; code: string } | null>(null);
  const [error, setError] = useState('');
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearInterval(timer.current), []);
  if (signed) return null;

  async function begin() {
    setError('');
    if (preview) {
      update((d) => void (d.signedIn.tidal = true));
      return;
    }
    try {
      const started = await startTidalLogin();
      if (!started.user_code) throw new Error(started.error || "Tidal didn't answer");
      const uri = started.verification_uri?.startsWith('http')
        ? started.verification_uri
        : `https://${started.verification_uri ?? 'link.tidal.com'}`;
      setCode({ uri, code: started.user_code });
      window.open(uri, 'tidal-link', 'width=520,height=720');
      timer.current = window.setInterval(async () => {
        const r = await checkTidalLogin().catch(() => ({
          status: 'error',
          message: 'Tidal sign-in failed',
        }));
        if (r.status === 'completed') {
          window.clearInterval(timer.current);
          setCode(null);
          update((d) => void (d.signedIn.tidal = true));
        } else if (r.status === 'error') {
          window.clearInterval(timer.current);
          setCode(null);
          setError(r.message || 'Tidal sign-in failed');
        }
      }, 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Tidal sign-in failed');
    }
  }

  return (
    <div className={s.sourceBody}>
      {code ? (
        <p className={s.note}>
          A Tidal window opened. Approve SoulSync there, the code is{' '}
          <span className={s.code}>{code.code}</span>
        </p>
      ) : (
        <div>
          <button type="button" className={s.light} onClick={begin}>
            Sign in with Tidal
          </button>
        </div>
      )}
      {preview ? (
        <p className={s.note}>The preview pretends to sign in, nothing is saved.</p>
      ) : null}
      {error ? (
        <p className={s.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function QobuzBody() {
  const preview = useOnboarding((st) => st.preview);
  const signed = useOnboarding((st) => st.draft.signedIn.qobuz);
  const update = useOnboarding((st) => st.update);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (signed) return null;

  async function login() {
    setBusy(true);
    setError('');
    const r = preview ? { success: true } : await qobuzLogin(email, password);
    setBusy(false);
    if (r.success) update((d) => void (d.signedIn.qobuz = true));
    else setError('error' in r && r.error ? r.error : 'Qobuz sign-in failed');
  }

  return (
    <div className={s.sourceBody}>
      <div className={s.grid2}>
        <Field
          id="ob-qobuz-email"
          label="Qobuz email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          id="ob-qobuz-pass"
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
        <button
          type="button"
          className={s.light}
          onClick={login}
          disabled={busy || !email || !password}
        >
          {busy ? <Spinner /> : null}
          Sign in
        </button>
        {error ? (
          <span className={s.error} role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function DeezerBody() {
  const arl = useOnboarding((st) => st.draft.deezerArl);
  const update = useOnboarding((st) => st.update);
  return (
    <div className={s.sourceBody}>
      <Field
        id="ob-deezer-arl"
        label="ARL token"
        type="password"
        placeholder="The arl cookie from deezer.com"
        value={arl}
        onChange={(e) => update((d) => void (d.deezerArl = e.target.value))}
      />
    </div>
  );
}

function sourceStatus(id: SourceId, d: ReturnType<typeof useOnboarding.getState>['draft']) {
  if (id === 'soulseek')
    return d.slskd.connected ? (
      <Chip tone="ok">Connected</Chip>
    ) : (
      <Chip tone="warn">Needs connecting</Chip>
    );
  if (id === 'tidal' || id === 'qobuz')
    return d.signedIn[id] ? <Chip tone="ok">Signed in</Chip> : <Chip tone="warn">Sign in</Chip>;
  if (id === 'deezer_dl')
    return d.deezerArl.trim() ? (
      <Chip tone="ok">ARL added</Chip>
    ) : (
      <Chip tone="warn">Needs ARL</Chip>
    );
  return <Chip tone="ok">Ready</Chip>;
}

export function SourcesStep() {
  const draft = useOnboarding((st) => st.draft);
  const found = useOnboarding((st) => st.found);
  const update = useOnboarding((st) => st.update);

  // discovery found slskd: offer it first, address filled in
  useEffect(() => {
    if (found.slskd && !draft.slskd.url)
      update((d) => {
        d.slskd.url = found.slskd;
        if (!d.chain.includes('soulseek')) d.chain = ['soulseek', ...d.chain];
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [found.slskd]);

  const move = (i: number, by: number) =>
    update((d) => {
      const j = i + by;
      if (j < 0 || j >= d.chain.length) return;
      [d.chain[i], d.chain[j]] = [d.chain[j], d.chain[i]];
    });
  const spare = ALL_SOURCES.filter((x) => !draft.chain.includes(x));

  return (
    <StepFrame
      step="sources"
      wide
      title="Where should music come from?"
      lead="SoulSync tries your sources in order. When the first can't find a good copy, it moves to the next. The free ones work straight away."
    >
      <div className={s.split}>
        <section className={s.splitMain} aria-label="Your sources, in order">
          {draft.chain.map((id, i) => (
            <div key={id} className={s.source}>
              <div className={s.sourceHead}>
                <div className={s.moveButtons}>
                  <button
                    type="button"
                    className={s.iconButton}
                    aria-label={`Move ${SOURCE_INFO[id].name} up`}
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M6 15l6-6 6 6" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    className={s.iconButton}
                    aria-label={`Move ${SOURCE_INFO[id].name} down`}
                    disabled={i === draft.chain.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </button>
                </div>
                <span className={s.pos}>{i + 1}</span>
                <div className={s.grow} style={{ gap: 3 }}>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{SOURCE_INFO[id].name}</span>
                  <span className={s.sub}>{SOURCE_INFO[id].desc}</span>
                </div>
                {sourceStatus(id, draft)}
                <button
                  type="button"
                  className={s.iconButton}
                  aria-label={`Remove ${SOURCE_INFO[id].name}`}
                  onClick={() => update((d) => void (d.chain = d.chain.filter((x) => x !== id)))}
                >
                  <CloseIcon />
                </button>
              </div>
              {id === 'soulseek' ? <SlskdBody /> : null}
              {id === 'tidal' ? <TidalBody /> : null}
              {id === 'qobuz' ? <QobuzBody /> : null}
              {id === 'deezer_dl' ? <DeezerBody /> : null}
            </div>
          ))}
          {!draft.chain.length ? <p className={s.note}>Add a source from the list.</p> : null}

          <div className={s.section} style={{ marginTop: 14 }}>
            <span className={s.label}>Quality</span>
            <div className={s.grid3} role="radiogroup" aria-label="Quality">
              {PRESETS.map((p) => (
                <Option
                  key={p.id}
                  title={p.name}
                  desc={p.desc}
                  checked={draft.preset === p.id}
                  onSelect={() => update((d) => void (d.preset = p.id))}
                />
              ))}
            </div>
          </div>
        </section>

        <aside className={s.splitSide} aria-label="Add a source">
          <span className={s.label} style={{ color: '#a1a1aa' }}>
            Add a source
          </span>
          {spare.map((id) => (
            <button
              key={id}
              type="button"
              className={s.spare}
              onClick={() => update((d) => void (d.chain = [...d.chain, id]))}
            >
              <span className={s.grow} style={{ gap: 2 }}>
                <span style={{ fontSize: 14, fontWeight: 500 }}>{SOURCE_INFO[id].name}</span>
                <span className={s.sub}>{SOURCE_INFO[id].need}</span>
              </span>
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
          ))}
          {!spare.length ? (
            <span className={s.sub}>
              <CheckIcon /> Every source is in
            </span>
          ) : null}
          <p className={s.note} style={{ marginTop: 4 }}>
            Torrent, usenet and Lidarr need Prowlarr or Lidarr first. They are on your checklist
            after setup.
          </p>
        </aside>
      </div>
    </StepFrame>
  );
}
