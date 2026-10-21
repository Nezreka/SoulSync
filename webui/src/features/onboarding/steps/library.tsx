/**
 * Step: start with what you love. The artists you play most on your server
 * come pre-picked (art and catalogue ids are looked up in the background),
 * a search adds anyone else, and a pasted playlist link is handed to the
 * sync page once setup is done.
 */

import { useEffect, useRef, useState } from 'react';

import {
  DETECTED_SOURCE_SERVICE_LABELS,
  detectPlaylistUrl,
  isDetected,
} from '@/routes/sync/-sync.url-detect';

import type { FollowPick } from '../types';

import { searchArtists, topArtists, type ArtistHit } from '../api';
import { serverConfig, serverLabel } from '../plan';
import { useOnboarding } from '../store';
import { CheckIcon, s, Spinner, StepFrame } from '../ui';

const PRESELECT = 12;

function sameName(a: string, b: string) {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

export function LibraryStep() {
  const draft = useOnboarding((st) => st.draft);
  const update = useOnboarding((st) => st.update);
  const session = useOnboarding((st) => st.session);
  const server = draft.server;
  const fromServer = !!server.kind && server.kind !== 'soulsync' && server.connected;
  const [suggested, setSuggested] = useState<FollowPick[] | null>(fromServer ? null : []);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<ArtistHit[]>([]);
  const [searching, setSearching] = useState(false);
  const loaded = useRef(0);

  // the artists you play most, picked for you the first time round
  useEffect(() => {
    if (!fromServer || loaded.current === session) return;
    loaded.current = session;
    let live = true;
    void topArtists(server.kind as string, serverConfig(server), '').then(async (names) => {
      if (!live) return;
      const picks: FollowPick[] = names.map((name) => ({ name }));
      setSuggested(picks);
      if (!useOnboarding.getState().draft.follows.length && picks.length)
        update((d) => void (d.follows = picks.slice(0, PRESELECT)));
      // fill in art and ids a few at a time, the grid updates as they land
      const queue = [...picks];
      const worker = async () => {
        for (let p = queue.shift(); p && live; p = queue.shift()) {
          const found = (await searchArtists(p.name)).find((h) => sameName(h.name, p.name));
          if (!found || !live) continue;
          const enrich = (x: FollowPick) =>
            sameName(x.name, p.name) ? { ...x, id: found.id, image: found.image_url } : x;
          setSuggested((prev) => (prev ? prev.map(enrich) : prev));
          update((d) => void (d.follows = d.follows.map(enrich)));
        }
      };
      await Promise.all([worker(), worker(), worker()]);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromServer, session]);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setHits([]);
      return;
    }
    setSearching(true);
    const t = window.setTimeout(async () => {
      const found = await searchArtists(q);
      setHits(found.slice(0, 6));
      setSearching(false);
    }, 350);
    return () => window.clearTimeout(t);
  }, [query]);

  const followed = (name: string) => draft.follows.some((f) => sameName(f.name, name));
  const toggle = (pick: FollowPick) =>
    update((d) => {
      d.follows = d.follows.some((f) => sameName(f.name, pick.name))
        ? d.follows.filter((f) => !sameName(f.name, pick.name))
        : [...d.follows, pick];
    });

  // the grid: suggestions first, then anyone added from search
  const grid: FollowPick[] = [...(suggested ?? [])];
  for (const f of draft.follows) if (!grid.some((g) => sameName(g.name, f.name))) grid.push(f);

  const link = draft.playlistUrl.trim();
  const detection = detectPlaylistUrl(link);
  let linkHint = "SoulSync keeps a copy in step with the original and fetches what you're missing.";
  if (link && isDetected(detection))
    linkHint = `${DETECTED_SOURCE_SERVICE_LABELS[detection.source]} recognised. It opens on the Sync page when setup finishes, ready to mirror.`;
  else if (link && !isDetected(detection)) linkHint = detection.error;

  return (
    <StepFrame
      step="library"
      wide
      title="Start with what you love"
      lead={
        fromServer
          ? `Followed artists feed your Discover page, and their new releases download on their own. These are the ones you play most in ${serverLabel(server.kind)}.`
          : 'Followed artists feed your Discover page, and their new releases download on their own.'
      }
    >
      <section className={s.section} style={{ gap: 16 }}>
        <div className={s.sectionHead}>
          <h2 className={s.h2}>Follow artists</h2>
          <span className={s.sub}>{draft.follows.length} selected</span>
        </div>
        {suggested === null ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }} className={s.sub}>
            <Spinner label="Reading your library" />
            Reading what you play most
          </div>
        ) : null}
        {grid.length ? (
          <div className={s.artists}>
            {grid.map((a) => {
              const on = followed(a.name);
              return (
                <button
                  key={a.name}
                  type="button"
                  className={s.artist}
                  aria-pressed={on}
                  onClick={() => toggle(a)}
                >
                  <span className={s.disc}>
                    {a.image ? (
                      <img src={a.image} alt="" loading="lazy" />
                    ) : (
                      a.name.charAt(0).toUpperCase()
                    )}
                    <span className={s.discTick}>
                      <CheckIcon size={11} stroke={3.6} />
                    </span>
                  </span>
                  {a.name}
                </button>
              );
            })}
          </div>
        ) : null}
        <div className={s.field} style={{ maxWidth: 520 }}>
          <label htmlFor="ob-artist-search" className={s.label}>
            {grid.length ? 'Add someone else' : 'Search for an artist'}
          </label>
          <input
            id="ob-artist-search"
            className={s.input}
            placeholder="Search for an artist"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {searching ? <Spinner label="Searching" /> : null}
        {hits.length ? (
          <div className={s.results} style={{ maxWidth: 520 }}>
            {hits.map((h) => {
              const on = followed(h.name);
              return (
                <button
                  key={h.id}
                  type="button"
                  className={s.result}
                  aria-pressed={on}
                  onClick={() => toggle({ name: h.name, id: h.id, image: h.image_url })}
                >
                  {h.image_url ? (
                    <img src={h.image_url} alt="" />
                  ) : (
                    <span className={s.resultBlank} />
                  )}
                  <span style={{ flexGrow: 1 }}>{h.name}</span>
                  <span className={s.sub}>{on ? 'Following' : 'Follow'}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </section>

      <section className={s.section}>
        <h2 className={s.h2}>Bring a playlist</h2>
        <div className={s.field} style={{ maxWidth: 760 }}>
          <label htmlFor="ob-playlist" className={s.srOnly}>
            Playlist link
          </label>
          <input
            id="ob-playlist"
            type="url"
            className={s.input}
            placeholder="Paste a Spotify, Apple Music, Deezer or YouTube link"
            value={draft.playlistUrl}
            onChange={(e) => update((d) => void (d.playlistUrl = e.target.value))}
          />
        </div>
        <p className={s.note}>{linkHint}</p>
      </section>
    </StepFrame>
  );
}
