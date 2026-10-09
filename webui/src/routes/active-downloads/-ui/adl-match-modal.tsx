/**
 * Match & import: tell SoulSync what a download it didn't send is.
 *
 * Pick the type, search the catalogue that type lives in, pick the match. The
 * download is then handed to the same tracking a grab of that thing writes,
 * so it shows on the downloads page and imports like any other download of
 * its type. Nothing about the import itself is new.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { searchAudiobooks } from '@/routes/audiobooks/-audiobooks.api';
import { searchImportAlbums, searchImportTracks } from '@/routes/import/-import.api';

import type { MatchFiles, MatchKind, MatchOutcome, SoulseekFolder } from '../-adl.api';

import {
  adoptAudiobook,
  adoptVideo,
  fetchMatchFiles,
  fetchMatchSuggestion,
  matchMusic,
  searchVideoTitles,
} from '../-adl.api';
import styles from './adl-match.module.css';

export interface MatchTarget {
  client: 'torrent' | 'usenet' | 'soulseek';
  /** the client's job id; empty for a soulseek folder, which is its files */
  id: string;
  /** the release name the client shows, or the soulseek folder's name */
  name: string;
  size: number;
  /** soulseek: the folder's peer and every transfer in it */
  folder?: SoulseekFolder;
}

/** The soulseek extras each side's route takes, when the target is a folder. */
function soulseekNames(t: MatchTarget) {
  return t.folder
    ? { username: t.folder.username, files: t.folder.files.map((f) => f.filename) }
    : {};
}

const KINDS: { kind: MatchKind; label: string }[] = [
  { kind: 'album', label: 'Album' },
  { kind: 'track', label: 'Track' },
  { kind: 'movie', label: 'Movie' },
  { kind: 'episode', label: 'Episode' },
  { kind: 'season', label: 'Season' },
  { kind: 'audiobook', label: 'Audiobook' },
];

const SEARCHES: Record<MatchKind, string> = {
  album: 'Search your metadata source',
  track: 'Search your metadata source',
  movie: 'Search TMDB',
  episode: 'Search TMDB for the show',
  season: 'Search TMDB for the show',
  audiobook: 'Search Audible',
};

/** One result row, whatever catalogue it came from. */
interface Hit {
  key: string;
  title: string;
  meta: string;
  image: string | null;
  submit: (
    target: MatchTarget,
    numbers: { season: number; episode: number },
  ) => Promise<MatchOutcome>;
}

function initials(text: string): string {
  return text
    .split(/\s+/)
    .filter((w) => /^[A-Za-z0-9]/.test(w))
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('');
}

async function runSearch(kind: MatchKind, query: string): Promise<Hit[]> {
  if (!query.trim()) return [];
  if (kind === 'album') {
    const data = await searchImportAlbums(query).catch(() => null);
    return (data?.albums ?? []).map((a) => ({
      key: `${a.source}:${a.id}`,
      title: a.name,
      meta: [
        a.artist,
        a.release_date?.slice(0, 4),
        a.total_tracks ? `${a.total_tracks} tracks` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      image: a.image_url ?? null,
      submit: (t) =>
        matchMusic({
          client: t.client,
          id: t.id,
          ...soulseekNames(t),
          kind: 'album',
          match: {
            id: a.id,
            name: a.name,
            artist: a.artist,
            source: a.source,
            image_url: a.image_url,
          },
          release_title: t.name,
        }),
    }));
  }
  if (kind === 'track') {
    const data = await searchImportTracks(query).catch(() => null);
    return (data?.tracks ?? []).map((tr) => ({
      key: `${tr.source}:${tr.id}`,
      title: tr.name,
      meta: [tr.artist, tr.album].filter(Boolean).join(' · '),
      image: tr.image_url ?? null,
      submit: (t) =>
        matchMusic({
          client: t.client,
          id: t.id,
          ...soulseekNames(t),
          kind: 'track',
          match: {
            id: tr.id,
            name: tr.name,
            artist: tr.artist,
            source: tr.source,
            image_url: tr.image_url,
            album: tr.album,
          },
          release_title: t.name,
        }),
    }));
  }
  if (kind === 'audiobook') {
    const data = await searchAudiobooks(query);
    return data.results.map((b) => ({
      key: b.asin,
      title: b.title,
      meta: [
        b.author_names.join(', '),
        b.narrator_names.length ? `read by ${b.narrator_names.join(', ')}` : '',
        b.runtime_formatted,
      ]
        .filter(Boolean)
        .join(' · '),
      image: b.cover_url ?? null,
      submit: (t) =>
        adoptAudiobook({
          source: t.client,
          client_ref: t.id,
          ...soulseekNames(t),
          asin: b.asin,
          release_title: t.name,
          size_bytes: t.size,
        }),
    }));
  }
  const wanted = kind === 'movie' ? 'movie' : 'show';
  const hits = (await searchVideoTitles(query)).filter((h) => h.kind === wanted);
  return hits.map((h) => ({
    key: `tmdb:${h.tmdb_id}`,
    title: h.title,
    meta: [h.year, wanted === 'movie' ? 'Movie' : 'TV show'].filter(Boolean).join(' · '),
    image: h.poster ?? null,
    submit: (t, n) =>
      adoptVideo({
        source: t.client,
        client_ref: t.id,
        ...(t.folder ? { username: t.folder.username, files: t.folder.files } : {}),
        kind: wanted,
        title: h.title,
        year: h.year,
        media_id: h.tmdb_id,
        media_source: 'tmdb',
        poster_url: h.poster,
        release_title: t.name,
        size_bytes: t.size,
        search_ctx: {
          scope: kind === 'movie' ? 'movie' : kind === 'season' ? 'season' : 'episode',
          title: h.title,
          year: h.year,
          season: kind === 'movie' ? null : n.season,
          episode: kind === 'episode' ? n.episode : null,
        },
      }),
  }));
}

export function AdlMatchModal({
  target,
  onClose,
  onMatched,
}: {
  target: MatchTarget | null;
  onClose: () => void;
  /** called after SoulSync accepted the match */
  onMatched: (message: string) => void;
}) {
  const [kind, setKind] = useState<MatchKind | null>(null);
  const [query, setQuery] = useState('');
  const [season, setSeason] = useState(1);
  const [episode, setEpisode] = useState(1);
  const [hits, setHits] = useState<Hit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [picked, setPicked] = useState('');
  const [files, setFiles] = useState<MatchFiles | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // the latest search wins: a slow catalogue must not paint over a newer answer
  const searchId = useRef(0);

  const search = useCallback(async (forKind: MatchKind | null, text: string) => {
    if (!forKind || !text.trim()) {
      setHits([]);
      setSearched(false);
      return;
    }
    const id = ++searchId.current;
    setSearching(true);
    setPicked('');
    const found = await runSearch(forKind, text);
    if (id !== searchId.current) return;
    setHits(found);
    setSearched(true);
    setSearching(false);
  }, []);

  useEffect(() => {
    if (!target) return;
    let live = true;
    setKind(null);
    setQuery(target.name);
    setHits([]);
    setSearched(false);
    setPicked('');
    setError('');
    setFiles(null);
    void fetchMatchSuggestion(target.name).then((guess) => {
      if (!live) return;
      setKind(guess.kind);
      setQuery(guess.query);
      if (guess.season != null) setSeason(guess.season);
      if (guess.episode != null) setEpisode(guess.episode);
      void search(guess.kind, guess.query);
    });
    void fetchMatchFiles(target.client, target.id, target.folder).then((found) => {
      if (live) setFiles(found);
    });
    return () => {
      live = false;
    };
  }, [target, search]);

  const chooseKind = (next: MatchKind) => {
    setKind(next);
    setError('');
    void search(next, query);
  };

  const chosen = hits.find((h) => h.key === picked) ?? null;

  const submit = async () => {
    if (!target || !chosen || busy) return;
    setBusy(true);
    setError('');
    const outcome = await chosen.submit(target, { season, episode });
    setBusy(false);
    if (!outcome.ok) {
      setError(outcome.error);
      return;
    }
    onMatched(`Matched to ${chosen.title}. It imports once the download is complete.`);
  };

  const needsSeason = kind === 'episode' || kind === 'season';

  return (
    <DialogFrame
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={styles.popup}
    >
      <DialogHeader title="What is this download?" closeLabel="Close">
        <span className={styles.eyebrow}>Match &amp; import</span>
        <span className={styles.raw} title={target?.name}>
          {target?.name}
        </span>
      </DialogHeader>
      <DialogBody>
        <div className={styles.body}>
          <div className={styles.section}>
            <span className={styles.label} id="adl-match-type">
              Type
            </span>
            <div className={styles.types} role="radiogroup" aria-labelledby="adl-match-type">
              {KINDS.map((k) => (
                <button
                  key={k.kind}
                  type="button"
                  role="radio"
                  aria-checked={kind === k.kind}
                  className={styles.type}
                  data-on={kind === k.kind || undefined}
                  onClick={() => chooseKind(k.kind)}
                >
                  {k.label}
                </button>
              ))}
            </div>
          </div>

          <form
            className={styles.section}
            onSubmit={(event) => {
              event.preventDefault();
              void search(kind, query);
            }}
          >
            <label className={styles.label} htmlFor="adl-match-query">
              {kind ? SEARCHES[kind] : 'Pick a type to search'}
            </label>
            <div className={styles.searchRow}>
              <input
                id="adl-match-query"
                type="search"
                className={styles.input}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {needsSeason ? (
                <>
                  <label className={styles.number}>
                    <span>Season</span>
                    <input
                      type="number"
                      min={0}
                      value={season}
                      onChange={(event) => setSeason(Number(event.target.value) || 0)}
                    />
                  </label>
                  {kind === 'episode' ? (
                    <label className={styles.number}>
                      <span>Episode</span>
                      <input
                        type="number"
                        min={1}
                        value={episode}
                        onChange={(event) => setEpisode(Number(event.target.value) || 1)}
                      />
                    </label>
                  ) : null}
                </>
              ) : null}
              <button type="submit" className={styles.secondary} disabled={!kind}>
                Search
              </button>
            </div>
          </form>

          <div className={styles.results} role="radiogroup" aria-label="Matches">
            {searching ? <p className={styles.note}>Searching…</p> : null}
            {!searching && searched && hits.length === 0 ? (
              <p className={styles.note}>Nothing found. Try fewer words, or another type.</p>
            ) : null}
            {!searching
              ? hits.map((hit) => (
                  <button
                    key={hit.key}
                    type="button"
                    role="radio"
                    aria-checked={picked === hit.key}
                    className={styles.hit}
                    data-on={picked === hit.key || undefined}
                    onClick={() => setPicked(hit.key)}
                  >
                    {hit.image ? (
                      <img className={styles.art} src={hit.image} alt="" />
                    ) : (
                      <span className={styles.art} aria-hidden="true">
                        {initials(hit.title)}
                      </span>
                    )}
                    <span className={styles.hitText}>
                      <span className={styles.hitTitle}>{hit.title}</span>
                      {hit.meta ? <span className={styles.hitMeta}>{hit.meta}</span> : null}
                    </span>
                    <span className={styles.radio} aria-hidden="true" />
                  </button>
                ))
              : null}
          </div>

          {files ? (
            <div className={styles.files} data-ok={files.visible || undefined}>
              <span className={styles.filesTitle}>
                {files.visible ? 'SoulSync can see the files' : "SoulSync can't see the files yet"}
              </span>
              <span className={styles.filesPath}>
                {files.visible
                  ? 'They are copied for the import, so the download keeps seeding.'
                  : `The client reports ${files.reportedPath || 'no folder yet'}. The import waits until that folder is visible to SoulSync.`}
              </span>
            </div>
          ) : null}

          {error ? (
            <p className={styles.error} role="alert">
              {error}
            </p>
          ) : null}
        </div>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={styles.ghost} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.primary}
          disabled={!chosen || busy}
          onClick={() => void submit()}
        >
          {busy ? 'Matching…' : chosen ? 'Match & import' : 'Pick a match'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}
