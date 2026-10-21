/**
 * Step: movies and TV. Three numbered cards, each checked on its own: TMDB
 * for metadata, Prowlarr for searching, a download client for fetching.
 * Addresses come from discovery when it found them.
 */

import type { ReactNode } from 'react';

import { useEffect, useState } from 'react';

import type { ProbeResult } from '../types';

import { probeService } from '../api';
import { useOnboarding } from '../store';
import { CheckIcon, Chip, Field, s, Spinner, StepFrame } from '../ui';

type CardId = 'tmdb' | 'prowlarr' | 'client';

function Card({
  n,
  title,
  desc,
  result,
  children,
  onCheck,
  busy,
  canCheck,
}: {
  n: number;
  title: string;
  desc: string;
  result?: ProbeResult;
  children: ReactNode;
  onCheck: () => void;
  busy: boolean;
  canCheck: boolean;
}) {
  return (
    <section className={s.card} style={{ gap: 14, padding: '20px 22px' }}>
      <div className={s.cardRow}>
        <span
          className={s.badge}
          style={
            result?.ok
              ? {
                  width: 28,
                  height: 28,
                  background: 'var(--ob-accent)',
                  borderColor: 'transparent',
                  color: 'var(--ob-on-accent)',
                }
              : { width: 28, height: 28 }
          }
        >
          {result?.ok ? <CheckIcon /> : n}
        </span>
        <div className={s.grow} style={{ gap: 3 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>{title}</span>
          <span className={s.sub}>{desc}</span>
        </div>
        {result ? (
          <Chip tone={result.ok ? 'ok' : 'warn'}>
            {result.ok ? result.message : 'Not working yet'}
          </Chip>
        ) : null}
      </div>
      <div className={s.section} style={{ paddingLeft: 44, gap: 10 }}>
        {children}
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          <button type="button" className={s.light} onClick={onCheck} disabled={busy || !canCheck}>
            {busy ? <Spinner /> : null}
            Check
          </button>
          {result && !result.ok ? (
            <span className={s.error} role="alert">
              {result.message}
            </span>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export function VideoStep() {
  const video = useOnboarding((st) => st.draft.video);
  const found = useOnboarding((st) => st.found);
  const update = useOnboarding((st) => st.update);
  const [results, setResults] = useState<Partial<Record<CardId, ProbeResult>>>({});
  const [busy, setBusy] = useState<CardId | null>(null);

  useEffect(() => {
    update((d) => {
      if (!d.video.prowlarr.url && found.prowlarr) d.video.prowlarr.url = found.prowlarr;
      if (!d.video.client.url) {
        if (found.qbittorrent) {
          d.video.client.kind = 'qbittorrent';
          d.video.client.url = found.qbittorrent;
        } else if (found.sabnzbd) {
          d.video.client.kind = 'sabnzbd';
          d.video.client.url = found.sabnzbd;
        }
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [found]);

  async function check(id: CardId) {
    setBusy(id);
    const r =
      id === 'tmdb'
        ? await probeService('tmdb', { api_key: video.tmdbKey })
        : id === 'prowlarr'
          ? await probeService('prowlarr', {
              url: video.prowlarr.url,
              api_key: video.prowlarr.apiKey,
            })
          : await probeService(video.client.kind, {
              url: video.client.url,
              username: video.client.username,
              password: video.client.password,
              api_key: video.client.apiKey,
            });
    setResults((prev) => ({ ...prev, [id]: r }));
    setBusy(null);
  }

  const forget = (id: CardId) => setResults((prev) => ({ ...prev, [id]: undefined }));
  const c = video.client;

  return (
    <StepFrame
      step="video"
      skip
      title="Movies and TV"
      lead="Three things and SoulSync can find, fetch and file new films and episodes. Skip any of them and it waits on your checklist."
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Card
          n={1}
          title="TMDB"
          desc="Posters, cast, seasons and episodes. Free at themoviedb.org, under Settings, API."
          result={results.tmdb}
          busy={busy === 'tmdb'}
          canCheck={!!video.tmdbKey.trim()}
          onCheck={() => check('tmdb')}
        >
          <Field
            id="ob-tmdb"
            label="TMDB API key"
            type="password"
            value={video.tmdbKey}
            onChange={(e) => {
              forget('tmdb');
              update((d) => void (d.video.tmdbKey = e.target.value.trim()));
            }}
          />
        </Card>

        <Card
          n={2}
          title="Prowlarr"
          desc={
            found.prowlarr ? `Found at ${found.prowlarr}` : 'Searches your indexers for releases.'
          }
          result={results.prowlarr}
          busy={busy === 'prowlarr'}
          canCheck={!!video.prowlarr.url.trim()}
          onCheck={() => check('prowlarr')}
        >
          <div className={s.grid2}>
            <Field
              id="ob-prowlarr-url"
              label="Address"
              placeholder="http://192.168.1.20:9696"
              value={video.prowlarr.url}
              onChange={(e) => {
                forget('prowlarr');
                update((d) => void (d.video.prowlarr.url = e.target.value));
              }}
            />
            <Field
              id="ob-prowlarr-key"
              label="API key, from Settings, General"
              type="password"
              value={video.prowlarr.apiKey}
              onChange={(e) => {
                forget('prowlarr');
                update((d) => void (d.video.prowlarr.apiKey = e.target.value.trim()));
              }}
            />
          </div>
        </Card>

        <Card
          n={3}
          title={c.kind === 'qbittorrent' ? 'qBittorrent' : 'SABnzbd'}
          desc="Downloads what Prowlarr finds."
          result={results.client}
          busy={busy === 'client'}
          canCheck={!!c.url.trim()}
          onCheck={() => check('client')}
        >
          <div className={s.pills} role="radiogroup" aria-label="Download client">
            {(['qbittorrent', 'sabnzbd'] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={s.pill}
                aria-pressed={c.kind === k}
                onClick={() => {
                  forget('client');
                  update((d) => {
                    d.video.client.kind = k;
                    d.video.client.url = found[k] ?? '';
                  });
                }}
              >
                <span className={s.tick}>
                  <CheckIcon size={10} stroke={3.6} />
                </span>
                {k === 'qbittorrent' ? 'qBittorrent' : 'SABnzbd'}
              </button>
            ))}
          </div>
          <div className={c.kind === 'qbittorrent' ? s.grid3 : s.grid2}>
            <Field
              id="ob-client-url"
              label="Address"
              placeholder={
                c.kind === 'qbittorrent' ? 'http://192.168.1.20:8080' : 'http://192.168.1.20:8085'
              }
              value={c.url}
              onChange={(e) => {
                forget('client');
                update((d) => void (d.video.client.url = e.target.value));
              }}
            />
            {c.kind === 'qbittorrent' ? (
              <>
                <Field
                  id="ob-client-user"
                  label="Username"
                  value={c.username}
                  onChange={(e) => {
                    forget('client');
                    update((d) => void (d.video.client.username = e.target.value));
                  }}
                />
                <Field
                  id="ob-client-pass"
                  label="Password"
                  type="password"
                  value={c.password}
                  onChange={(e) => {
                    forget('client');
                    update((d) => void (d.video.client.password = e.target.value));
                  }}
                />
              </>
            ) : (
              <Field
                id="ob-client-key"
                label="API key"
                type="password"
                value={c.apiKey}
                onChange={(e) => {
                  forget('client');
                  update((d) => void (d.video.client.apiKey = e.target.value.trim()));
                }}
              />
            )}
          </div>
          <p className={s.note}>
            Transmission, Deluge or NZBGet? Pick them in Settings after setup.
          </p>
        </Card>
      </div>
    </StepFrame>
  );
}
