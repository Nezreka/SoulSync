/**
 * The files that imported without a match (#1202), sorted out in one place.
 *
 * the banner used to send you to a made-up "Unknown Artist" page and leave
 * you to work out what each file was. here every file is fingerprinted with
 * AcoustID, one after another, and looked up on the metadata source. each
 * row shows the release it most likely is, how sure that is and why (the
 * fingerprint, the length, the album the rest of its folder is on), and the
 * one action that settles it:
 *
 * - a likely release: accept it. re-identify copies the file in under that
 *   release and removes the original only once the import worked
 * - a song the library already has: remove the copy
 * - nothing to go on: search by hand, the search filled in from the name
 *
 * every file plays, so an untagged one can be heard before deciding.
 */
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog';

import type { LibrarySong } from '../-library-v2.songs';

import {
  identifyUnmatchedTrack,
  LIBRARY_V2_QUERY_KEY,
  libraryV2UnmatchedTracksQueryOptions,
  type LibraryV2UnmatchedCandidate,
  type LibraryV2UnmatchedIdentify,
  type LibraryV2UnmatchedTrack,
} from '../-library-v2.api';
import { deleteLibraryTrackRequest } from '../-library-v2.songs';
import { applyReidentifyRequest } from '../../artist-detail/-artist-detail.reidentify';
import styles from './unmatched-review-modal.module.css';

const RELEASE_TYPES = new Set(['album', 'single', 'ep', 'compilation']);

/** at or above this a match can be taken without opening it */
const CONFIDENT = 0.85;

function duration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return '';
  const s = Math.round(seconds > 10_000 ? seconds / 1000 : seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function play(track: LibraryV2UnmatchedTrack) {
  void window.SoulSyncWebShellBridge?.playLibraryTrack(
    {
      id: track.track_id,
      lib2_track_id: track.track_id,
      title: track.guess.title || track.title,
      file_path: track.file_path,
      exact_path: true,
    },
    track.guess.album ?? '',
    track.guess.artist ?? '',
  );
}

function identifyQuery(fileId: number) {
  return {
    // outside the library key: a refresh of the list must not fingerprint
    // every file again
    queryKey: ['library-unmatched-identify', fileId] as const,
    queryFn: () => identifyUnmatchedTrack(fileId),
    staleTime: 10 * 60_000,
    retry: false,
  };
}

function releaseLine(candidate: LibraryV2UnmatchedCandidate): string {
  return [
    candidate.album_name,
    candidate.artist_name,
    // a source that knows no release type answers "track"
    RELEASE_TYPES.has(candidate.album_type)
      ? candidate.album_type[0].toUpperCase() + candidate.album_type.slice(1)
      : '',
    candidate.year ? String(candidate.year) : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

function Confidence({ value }: { value: number }) {
  const tone = value >= CONFIDENT ? styles.sure : value >= 0.6 ? styles.likely : styles.unsure;
  const word = value >= CONFIDENT ? 'Strong match' : value >= 0.6 ? 'Likely' : 'Weak match';
  return (
    <span className={`${styles.confidence} ${tone}`} title={`${Math.round(value * 100)}% sure`}>
      <span className={styles.dot} aria-hidden="true" />
      {word}
    </span>
  );
}

function Cover({ url }: { url: string }) {
  return url ? (
    <img className={styles.cover} src={url} alt="" loading="lazy" />
  ) : (
    <span className={`${styles.cover} ${styles.coverEmpty}`} aria-hidden="true">
      ♪
    </span>
  );
}

type RowState =
  | { kind: 'identifying' }
  | { kind: 'filing'; album: string }
  | { kind: 'result'; result: LibraryV2UnmatchedIdentify }
  | { kind: 'failed'; message: string };

function UnmatchedRow({
  track,
  state,
  busy,
  onAccept,
  onRemove,
  onSearch,
}: {
  track: LibraryV2UnmatchedTrack;
  state: RowState;
  busy: boolean;
  onAccept: (candidate: LibraryV2UnmatchedCandidate) => void;
  onRemove: (already: { title: string; album_title: string }) => void;
  onSearch: () => void;
}) {
  const [more, setMore] = useState(false);
  const [fileAnyway, setFileAnyway] = useState(false);

  let panel;
  if (state.kind === 'identifying') {
    panel = (
      <div className={`${styles.panel} ${styles.listening}`} aria-live="polite">
        <span className={styles.wave} aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </span>
        Listening to the file and looking it up…
      </div>
    );
  } else if (state.kind === 'filing') {
    panel = (
      <div className={`${styles.panel} ${styles.filing}`} aria-live="polite">
        <span aria-hidden="true">✓</span> Filing under {state.album}. It leaves this list after the
        next import pass.
      </div>
    );
  } else if (state.kind === 'failed') {
    panel = (
      <div className={`${styles.panel} ${styles.quiet}`}>
        <p>{state.message}</p>
        <div className={styles.panelActions}>
          <button type="button" className={styles.secondary} onClick={onSearch}>
            Search
          </button>
        </div>
      </div>
    );
  } else {
    const { result } = state;
    const [best, ...others] = result.candidates;
    if (result.status === 'duplicate' && result.already_have && !fileAnyway) {
      const already = result.already_have;
      panel = (
        <div className={`${styles.panel} ${styles.duplicate}`}>
          <p>
            <strong>You already have this.</strong> “{already.title}” on {already.album_title} by{' '}
            {already.artist_name}
            {result.method === 'fingerprint' ? ' sounds the same.' : ' is in the same folder.'}
          </p>
          <div className={styles.panelActions}>
            {best ? (
              <button type="button" className={styles.ghost} onClick={() => setFileAnyway(true)}>
                Keep both
              </button>
            ) : null}
            <button
              type="button"
              className={styles.danger}
              disabled={busy}
              onClick={() => onRemove(already)}
            >
              {busy ? 'Removing…' : 'Remove duplicate'}
            </button>
          </div>
        </div>
      );
    } else if (best) {
      panel = (
        <div className={styles.panel}>
          <div className={styles.match}>
            <Cover url={best.image_url} />
            <div className={styles.matchInfo}>
              <div className={styles.matchTitle}>
                <span>{best.track_title}</span>
                <Confidence value={best.confidence} />
              </div>
              <div className={styles.matchRelease}>{releaseLine(best)}</div>
              {best.reasons.length ? (
                <ul className={styles.reasons} aria-label="Why">
                  {best.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
          <div className={styles.panelActions}>
            {others.length ? (
              <button
                type="button"
                className={styles.ghost}
                aria-expanded={more}
                onClick={() => setMore((value) => !value)}
              >
                {more
                  ? 'Fewer'
                  : `${others.length} other ${others.length === 1 ? 'match' : 'matches'}`}
              </button>
            ) : null}
            <button type="button" className={styles.ghost} onClick={onSearch}>
              Search
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={busy}
              onClick={() => onAccept(best)}
            >
              {busy ? 'Filing…' : 'Accept'}
            </button>
          </div>
          {more ? (
            <ul className={styles.others}>
              {others.map((candidate) => (
                <li key={`${candidate.source}:${candidate.track_id}`}>
                  <Cover url={candidate.image_url} />
                  <div className={styles.matchInfo}>
                    <div className={styles.otherTitle}>{candidate.track_title}</div>
                    <div className={styles.matchRelease}>{releaseLine(candidate)}</div>
                  </div>
                  <span className={styles.percent}>{Math.round(candidate.confidence * 100)}%</span>
                  <button
                    type="button"
                    className={styles.secondary}
                    disabled={busy}
                    onClick={() => onAccept(candidate)}
                  >
                    Use this
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      );
    } else {
      panel = (
        <div className={`${styles.panel} ${styles.quiet}`}>
          <p>{result.message}</p>
          <div className={styles.panelActions}>
            <button type="button" className={styles.secondary} onClick={onSearch}>
              Search
            </button>
          </div>
        </div>
      );
    }
  }

  return (
    <li className={styles.row}>
      <div className={styles.file}>
        <button
          type="button"
          className={styles.play}
          aria-label={`Play ${track.file_name}`}
          title="Play"
          onClick={() => play(track)}
        >
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
          </svg>
        </button>
        <div className={styles.info}>
          <div className={styles.name} title={track.file_path}>
            {track.file_name}
          </div>
          <div className={styles.meta}>
            {[track.format, duration(track.duration), track.folder].filter(Boolean).join(' · ')}
          </div>
        </div>
      </div>
      {panel}
    </li>
  );
}

export function UnmatchedReviewModal({
  onClose,
  onFindMatch,
}: {
  onClose: () => void;
  /** the review steps aside for the search; the banner brings it back */
  onFindMatch: (track: LibraryV2UnmatchedTrack) => void;
}) {
  const queryClient = useQueryClient();
  const query = useQuery(libraryV2UnmatchedTracksQueryOptions());
  const [busy, setBusy] = useState<Set<number>>(() => new Set());
  // accepted this visit: the list still has them until the import pass
  const [filed, setFiled] = useState<Record<number, string>>({});
  const close = useCallback(() => onClose(), [onClose]);
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(close);

  const tracks = query.data ?? [];
  const needsLookup = (track: LibraryV2UnmatchedTrack) =>
    !track.filing && filed[track.file_id] === undefined;
  // one lookup at a time, in list order: fingerprinting is CPU on the server
  // and AcoustID allows three requests a second
  const lookups = useQueries({
    queries: tracks.map((track) => ({ ...identifyQuery(track.file_id), enabled: false })),
  });
  const firstPending = tracks.findIndex(
    (track, n) => needsLookup(track) && lookups[n] && lookups[n].isPending,
  );
  const active = firstPending >= 0 ? tracks[firstPending] : null;
  useQuery({ ...identifyQuery(active?.file_id ?? -1), enabled: active !== null });

  const stateOf = (track: LibraryV2UnmatchedTrack, n: number): RowState => {
    if (track.filing) return { kind: 'filing', album: track.filing.album_name };
    if (filed[track.file_id] !== undefined) return { kind: 'filing', album: filed[track.file_id] };
    // the folder already says it's a copy: no need to listen first
    const lookup = lookups[n];
    if (lookup?.data) return { kind: 'result', result: lookup.data };
    if (track.duplicate_of) {
      return {
        kind: 'result',
        result: {
          status: 'duplicate',
          method: 'name',
          heard: null,
          candidates: [],
          already_have: {
            track_id: track.duplicate_of.track_id,
            title: track.duplicate_of.title,
            album_id: track.duplicate_of.album_id,
            album_title: track.duplicate_of.album_title,
            artist_name: track.duplicate_of.artist_name,
          },
          message: '',
        },
      };
    }
    if (lookup?.isError) {
      return {
        kind: 'failed',
        message:
          lookup.error instanceof Error ? lookup.error.message : 'Could not look this file up.',
      };
    }
    return { kind: 'identifying' };
  };

  const mark = (fileId: number, on: boolean) =>
    setBusy((previous) => {
      const next = new Set(previous);
      if (on) next.add(fileId);
      else next.delete(fileId);
      return next;
    });

  const accept = async (track: LibraryV2UnmatchedTrack, candidate: LibraryV2UnmatchedCandidate) => {
    mark(track.file_id, true);
    try {
      await applyReidentifyRequest(
        track.track_id,
        { source: candidate.source, track_id: candidate.track_id },
        true,
      );
      setFiled((previous) => ({ ...previous, [track.file_id]: candidate.album_name }));
      return true;
    } catch (error) {
      window.showToast?.(error instanceof Error ? error.message : 'Could not file it', 'error');
      return false;
    } finally {
      mark(track.file_id, false);
    }
  };

  const confident = tracks.flatMap((track, n) => {
    const state = stateOf(track, n);
    if (state.kind !== 'result' || state.result.status !== 'matched') return [];
    const best = state.result.candidates[0];
    return best && best.confidence >= CONFIDENT ? [{ track, best }] : [];
  });

  const acceptAll = async () => {
    let done = 0;
    for (const { track, best } of confident) {
      if (await accept(track, best)) done += 1;
    }
    if (done) {
      window.showToast?.(
        `Filing ${done} ${done === 1 ? 'track' : 'tracks'}. They update after the next import pass.`,
        'success',
      );
    }
  };

  const remove = async (
    track: LibraryV2UnmatchedTrack,
    already: { title: string; album_title: string },
  ) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Remove the duplicate?',
      message: `“${track.file_name}” is deleted from disk. “${already.title}” on ${already.album_title} stays as it is.`,
      confirmText: 'Delete duplicate',
      destructive: true,
    });
    if (!ok) return;
    mark(track.file_id, true);
    try {
      const result = await deleteLibraryTrackRequest(
        { album_id: track.album_id, file_ids: [track.file_id] } as LibrarySong,
        'delete_file',
      );
      window.showToast?.(
        result.tone === 'success' ? 'Duplicate removed' : result.message,
        result.tone,
      );
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (error) {
      window.showToast?.(
        error instanceof Error ? error.message : 'Could not remove the duplicate',
        'error',
      );
    } finally {
      mark(track.file_id, false);
    }
  };

  const open = tracks.filter((track, n) => stateOf(track, n).kind !== 'filing').length;

  return createPortal(
    <div
      className={styles.overlay}
      onClick={(e) => {
        e.stopPropagation();
        onBackdropClick(e);
      }}
    >
      <div
        ref={ref}
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-labelledby="unmatched-review-title"
      >
        <header className={styles.head}>
          <div>
            <h3 id="unmatched-review-title">Tracks without a match</h3>
            <p>
              {query.isSuccess
                ? open === 0
                  ? 'Everything here is sorted.'
                  : `${open} ${open === 1 ? 'file' : 'files'} SoulSync couldn’t place from the tags. Each one is fingerprinted and looked up.`
                : 'SoulSync couldn’t place these from the tags.'}
            </p>
          </div>
          <div className={styles.headActions}>
            {confident.length > 1 ? (
              <button
                type="button"
                className={styles.primary}
                disabled={busy.size > 0}
                onClick={() => void acceptAll()}
              >
                Accept {confident.length} strong matches
              </button>
            ) : null}
            <button type="button" className={styles.close} aria-label="Close" onClick={close}>
              &times;
            </button>
          </div>
        </header>

        <div className={styles.body} aria-busy={query.isLoading || undefined}>
          {query.isLoading ? (
            <p className={styles.state}>Looking at the files…</p>
          ) : query.isError ? (
            <p className={styles.state}>Couldn’t load them. Try again in a moment.</p>
          ) : tracks.length === 0 ? (
            <p className={styles.state}>All matched. Nothing left to review.</p>
          ) : (
            <ul className={styles.list}>
              {tracks.map((track, n) => (
                <UnmatchedRow
                  key={track.file_id}
                  track={track}
                  state={stateOf(track, n)}
                  busy={busy.has(track.file_id)}
                  onAccept={(candidate) =>
                    void accept(track, candidate).then((ok) => {
                      if (ok) {
                        window.showToast?.(
                          `Filing under “${candidate.album_name}”. It updates after the next import pass.`,
                          'success',
                        );
                      }
                    })
                  }
                  onRemove={(already) => void remove(track, already)}
                  onSearch={() => onFindMatch(track)}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
