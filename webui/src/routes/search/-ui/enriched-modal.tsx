import { Dialog } from '@base-ui/react/dialog';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { Assignment, EnrichedFile, EnrichedProvider, MatchResponse } from '../-basic.enriched';
import type { DownloadTarget } from '../-basic.types';
import type { SearchAlbum, SearchTrack } from '../-search.types';

import {
  fetchProviders,
  fileKey,
  matchRelease,
  ProviderUnavailable,
  searchProvider,
  startEnriched,
  toEnrichedFile,
} from '../-basic.enriched';
import { formatDuration } from '../-basic.helpers';
import styles from './enriched-modal.module.css';

/** at or above this the file→track match needs no second look */
const SURE = 0.8;

interface Candidate {
  id: string;
  name: string;
  sub: string;
  image: string;
  track?: SearchTrack;
}

function describeTarget(target: DownloadTarget) {
  if (target.kind === 'album') {
    const { album } = target;
    return {
      isAlbum: true,
      title: album.album_title || 'Unknown album',
      artist: album.artist || '',
      query: [album.artist, album.album_title].filter(Boolean).join(' '),
      files: (album.tracks ?? []).map((t) => toEnrichedFile(t, album.album_title)),
    };
  }
  const track = target.kind === 'track' ? target.track : target.album.tracks[target.trackIndex];
  const albumName = target.kind === 'albumTrack' ? target.album.album_title : '';
  const artist = track?.artist || (target.kind === 'albumTrack' ? target.album.artist : '') || '';
  return {
    isAlbum: false,
    title: track?.title || 'Unknown title',
    artist,
    query: [artist, track?.title].filter(Boolean).join(' '),
    files: track ? [toEnrichedFile(track, albumName)] : [],
  };
}

function year(date?: string) {
  return (date || '').slice(0, 4);
}

function albumCandidates(albums: SearchAlbum[]): Candidate[] {
  return albums
    .filter((a) => a.id != null)
    .map((a) => ({
      id: String(a.id),
      name: a.name || 'Untitled',
      sub: [
        a.artist || a.artists?.map((x) => x.name).join(', '),
        year(a.release_date),
        a.total_tracks ? `${a.total_tracks} tracks` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      image: a.image_url || a.images?.[0]?.url || '',
    }));
}

function trackCandidates(tracks: SearchTrack[]): Candidate[] {
  return tracks
    .filter((t) => t.id != null)
    .map((t) => ({
      id: String(t.id),
      name: t.name || 'Untitled',
      sub: [t.artist, t.album, formatDuration(t.duration_ms)].filter(Boolean).join(' · '),
      image: t.image_url || '',
      track: t,
    }));
}

/**
 * Enriched download: match the picked file(s) to a real release on one
 * provider, then download them tagged and filed as that release.
 */
export function EnrichedModal({
  target,
  onClose,
}: {
  target: DownloadTarget | null;
  onClose: () => void;
}) {
  const info = useMemo(() => (target ? describeTarget(target) : null), [target]);
  const [providers, setProviders] = useState<EnrichedProvider[]>([]);
  const [source, setSource] = useState('');
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [picked, setPicked] = useState<Candidate | null>(null);
  const [step, setStep] = useState<'release' | 'tracks'>('release');
  const [match, setMatch] = useState<MatchResponse | null>(null);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  // a fresh target is a fresh flow
  useEffect(() => {
    if (!info) return;
    setQuery(info.query);
    setPicked(null);
    setStep('release');
    setMatch(null);
    setAssignments([]);
    setError('');
    let live = true;
    fetchProviders()
      .then((list) => {
        if (!live) return;
        setProviders(list);
        setSource((current) => current || (list.find((p) => p.active) ?? list[0])?.source || '');
      })
      .catch(() => live && setSearchError('Could not load your metadata sources.'));
    return () => {
      live = false;
    };
  }, [info]);

  // search the chosen provider, a beat after typing stops
  useEffect(() => {
    if (!info || !source || !query.trim()) return;
    const timer = setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setSearching(true);
      setSearchError('');
      searchProvider(query.trim(), source, controller.signal)
        .then(({ albums, tracks }) => {
          if (controller.signal.aborted) return;
          const list = info.isAlbum ? albumCandidates(albums) : trackCandidates(tracks);
          setCandidates(list);
          setPicked(list[0] ?? null);
          if (!list.length)
            setSearchError(`Nothing on ${label(source)} for that. Try other words.`);
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          setCandidates([]);
          setPicked(null);
          setSearchError(
            err instanceof ProviderUnavailable
              ? `${label(source)} isn't available right now. Pick another source.`
              : 'Search failed. Try again.',
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 400);
    return () => clearTimeout(timer);
    // label() reads providers, which only changes when source does
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info, source, query]);

  if (!target || !info) return null;

  function label(name: string) {
    return providers.find((p) => p.source === name)?.label || name;
  }

  const files: EnrichedFile[] = info.files;
  const fileByKey = new Map(files.map((f) => [fileKey(f), f]));
  const chosen = assignments.filter((a) => a.track_index != null).length;

  async function toTracks() {
    if (!picked || !info) return;
    setBusy(true);
    setError('');
    try {
      const result = await matchRelease({
        source,
        album_id: picked.id,
        album_name: picked.name,
        artist: info.artist,
        files,
      });
      if (!result.success) {
        setError(result.error || 'Could not load that release.');
        return;
      }
      setMatch(result);
      setAssignments(result.assignments ?? []);
      setStep('tracks');
    } catch {
      setError('Could not load that release.');
    } finally {
      setBusy(false);
    }
  }

  async function start(ignoreBlocklist = false): Promise<void> {
    if (!picked || !info) return;
    setBusy(true);
    setError('');
    const payload: Record<string, unknown> = info.isAlbum
      ? {
          source,
          album_id: picked.id,
          album_name: picked.name,
          artist: info.artist,
          files,
          assignments: assignments.map((a) => ({
            file_key: a.file_key,
            track_index: a.track_index,
          })),
        }
      : { source, track_id: picked.id, track: picked.track ?? {}, files };
    if (ignoreBlocklist) payload.ignore_blocklist = true;
    try {
      const result = await startEnriched(payload);
      if (result.blocked && !ignoreBlocklist) {
        const name = result.blocked_name || 'This artist';
        const ok = await window.showConfirmDialog?.({
          title: 'On your blocklist',
          message: `${name} is on your blocklist. Download this anyway?`,
          confirmText: 'Download anyway',
          cancelText: 'Skip',
        });
        if (ok) return start(true);
        setBusy(false);
        return;
      }
      if (!result.success) {
        setError(result.error || 'Could not start the download.');
        setBusy(false);
        return;
      }
      window.showToast?.(result.message || 'Download started', 'success');
      onClose();
    } catch {
      setError('Could not start the download.');
      setBusy(false);
    }
  }

  const sure = assignments.filter((a) => a.track_index != null && a.confidence >= SURE).length;
  const unsure = assignments.filter((a) => a.track_index != null && a.confidence < SURE).length;

  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={styles.popup}>
      <div className={styles.head}>
        <div className={styles.meta}>
          <Dialog.Title className={styles.title}>
            {step === 'tracks'
              ? 'Check the tracks'
              : info.isAlbum
                ? 'Which release is this?'
                : 'Which track is this?'}
          </Dialog.Title>
          <div className={styles.sub}>
            {info.title}
            {info.isAlbum ? <span className={styles.sep}>·</span> : null}
            {info.isAlbum ? `${files.length} files` : null}
          </div>
        </div>
        {info.isAlbum ? (
          <ol className={styles.steps} aria-label="Steps">
            <li data-on={step === 'release' || undefined}>Release</li>
            <li data-on={step === 'tracks' || undefined}>Tracks</li>
          </ol>
        ) : null}
        <Dialog.Close className={styles.close} aria-label="Close">
          ×
        </Dialog.Close>
      </div>

      <div className={styles.body}>
        {step === 'release' ? (
          <>
            <div className={styles.providers} role="group" aria-label="Metadata source">
              {providers.map((p) => (
                <button
                  key={p.source}
                  type="button"
                  aria-pressed={p.source === source}
                  onClick={() => setSource(p.source)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <label className={styles.field}>
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.7" />
                <path
                  d="m10.5 10.5 3 3"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                />
              </svg>
              <input
                aria-label={info.isAlbum ? 'Search releases' : 'Search tracks'}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              {searching ? (
                <span className={styles.spinner} role="status" aria-label="Searching" />
              ) : null}
            </label>

            {searchError && !candidates.length ? (
              <p className={styles.notice}>{searchError}</p>
            ) : (
              <div
                className={styles.candidates}
                role="radiogroup"
                aria-label={info.isAlbum ? 'Releases' : 'Tracks'}
              >
                {candidates.map((c, i) => (
                  <button
                    key={c.id}
                    type="button"
                    role="radio"
                    aria-checked={picked?.id === c.id}
                    className={styles.candidate}
                    onClick={() => setPicked(c)}
                    onDoubleClick={() => (info.isAlbum ? void toTracks() : void start())}
                  >
                    <span className={styles.cover}>
                      {c.image ? <img src={c.image} alt="" loading="lazy" /> : null}
                      {i === 0 ? <span className={styles.best}>Best match</span> : null}
                    </span>
                    <span className={styles.candName}>{c.name}</span>
                    <span className={styles.candSub}>{c.sub}</span>
                  </button>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            <div className={styles.summary}>
              <b>
                {chosen} of {files.length}
              </b>{' '}
              files will download.{' '}
              {unsure
                ? `${unsure} ${unsure === 1 ? 'needs' : 'need'} a look.`
                : sure
                  ? 'All look right.'
                  : ''}
            </div>
            <div className={styles.tableWrap}>
              <table className={styles.map}>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Track</th>
                    <th>Match</th>
                  </tr>
                </thead>
                <tbody>
                  {assignments.map((a, row) => {
                    const file = fileByKey.get(a.file_key);
                    const name = file?.filename.replace(/\\/g, '/').split('/').pop() ?? a.file_key;
                    const low = a.track_index != null && a.confidence < SURE;
                    return (
                      <tr key={a.file_key}>
                        <td className={styles.fileName} title={name}>
                          {name}
                        </td>
                        <td>
                          <select
                            className={styles.pick}
                            aria-label={`Track for ${name}`}
                            value={a.track_index ?? ''}
                            onChange={(event) => {
                              const value = event.target.value;
                              setAssignments((prev) =>
                                prev.map((x, i) =>
                                  i === row
                                    ? {
                                        ...x,
                                        track_index: value === '' ? null : Number(value),
                                        // the user chose it, it needs no second look
                                        confidence: 1,
                                      }
                                    : x,
                                ),
                              );
                            }}
                          >
                            <option value="">Skip this file</option>
                            {(match?.tracks ?? []).map((t) => (
                              <option key={t.index} value={t.index}>
                                {(match?.tracks ?? []).some((x) => x.disc_number > 1)
                                  ? `${t.disc_number}-${t.track_number}`
                                  : t.track_number}
                                . {t.name}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <span
                            className={styles.conf}
                            data-level={a.track_index == null ? 'skip' : low ? 'low' : 'ok'}
                          >
                            <i aria-hidden="true" />
                            {a.track_index == null
                              ? 'Skipped'
                              : low
                                ? 'Check'
                                : `${Math.round(a.confidence * 100)}%`}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
        {error ? <p className={styles.error}>{error}</p> : null}
      </div>

      <div className={styles.foot}>
        <span className={styles.note}>
          {source ? `Tags, cover art and folder come from ${label(source)}.` : ''}
        </span>
        {step === 'tracks' ? (
          <button
            type="button"
            className={styles.button}
            onClick={() => setStep('release')}
            disabled={busy}
          >
            Back
          </button>
        ) : (
          <button type="button" className={styles.button} onClick={onClose}>
            Cancel
          </button>
        )}
        {info.isAlbum && step === 'release' ? (
          <button
            type="button"
            className={`${styles.button} ${styles.primary}`}
            disabled={!picked || busy}
            onClick={() => void toTracks()}
          >
            {busy ? 'Loading…' : 'Next'}
          </button>
        ) : (
          <button
            type="button"
            className={`${styles.button} ${styles.primary}`}
            disabled={!picked || busy || (info.isAlbum && !chosen)}
            onClick={() => void start()}
          >
            {busy
              ? 'Starting…'
              : info.isAlbum
                ? `Download ${chosen} ${chosen === 1 ? 'track' : 'tracks'}`
                : 'Download'}
          </button>
        )}
      </div>
    </DialogFrame>
  );
}
