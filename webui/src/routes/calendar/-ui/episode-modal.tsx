import type { CSSProperties } from 'react';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { CalendarEpisode } from '../-calendar.types';

import { ignoreEpisode, retryEpisode, showDetailQueryOptions, wishEpisode } from '../-calendar.api';
import { backdropOf, openShowDetail, stillOf } from '../-calendar.art';
import { MO_FULL, WD_FULL, airMins, fmtMins, parseISO, showHue, whyLine } from '../-calendar.model';
import { Art } from './art';
import styles from './calendar.module.css';
import { StatePill } from './state-pill';

const toast = (message: string, type: string) => window.showToast?.(message, type);

function fullDate(iso: string): string {
  const d = parseISO(iso);
  return `${WD_FULL[d.getDay()]}, ${MO_FULL[d.getMonth()]} ${d.getDate()}`;
}

/**
 * One episode: when, where it stands and why, and the one or two things you
 * can do about it. The actions follow the state: a missing one can be wished,
 * a failed one retried, and an admin can stop monitoring either.
 */
export function EpisodeModal({
  ep,
  today,
  autoOn,
  isAdmin,
  canDownload,
  onClose,
  onChanged,
}: {
  ep: CalendarEpisode;
  today: string;
  autoOn: boolean;
  isAdmin: boolean;
  canDownload: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const detail = useQuery(showDetailQueryOptions(ep.not_in_library ? null : ep.show_id));
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, boolean>>({});

  const time = fmtMins(airMins(ep.airs_time));
  const backdrop = backdropOf(ep, 1000);
  const still = stillOf(ep, 600);
  const eyebrow = [
    detail.data?.network ?? ep.network,
    detail.data?.year ?? ep.show_year,
    detail.data?.status ?? ep.show_status,
    detail.data?.content_rating,
  ]
    .filter(Boolean)
    .join('  ·  ');
  const showOverview =
    detail.data?.overview && detail.data.overview !== ep.overview ? detail.data.overview : null;

  const owned = ep.acq === 'owned';
  const canWish =
    canDownload &&
    !!ep.show_tmdb_id &&
    !done.wish &&
    (ep.acq === 'missing' || (ep.acq === 'unaired' && !autoOn));
  // an upcoming episode is grabbed on air already; wishing it early is still
  // allowed (the drain waits for the date) but only offered when nothing else will
  const canPreWish =
    canDownload && !!ep.show_tmdb_id && !done.wish && ep.acq === 'unaired' && autoOn;
  const canRetry = canDownload && ep.acq === 'failed' && !done.retry;
  const canIgnore =
    isAdmin &&
    !owned &&
    !ep.not_in_library &&
    ep.acq !== 'ignored' &&
    ep.acq !== 'unaired' &&
    !done.ignore;

  const act = async (
    key: string,
    fn: () => Promise<{ success?: boolean; error?: string }>,
    ok: string,
    bad: string,
  ) => {
    setBusy(key);
    try {
      const r = await fn();
      if (r && (r.success === false || r.error)) throw new Error(r.error || bad);
      setDone((d) => ({ ...d, [key]: true }));
      toast(ok, 'success');
      document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
      onChanged();
    } catch (e) {
      toast(e instanceof Error && e.message ? e.message : bad, 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <DialogFrame
      open
      onOpenChange={(o) => !o && onClose()}
      aria-label={`${ep.show_title} S${ep.season_number}E${ep.episode_number}`}
      viewportClassName={styles.modalViewport}
      className={styles.modal}
    >
      <div style={{ '--vcal-h': showHue(ep.show_title) } as CSSProperties}>
        <button type="button" className={styles.modalClose} aria-label="Close" onClick={onClose}>
          ×
        </button>
        <div className={styles.modalHero}>
          {backdrop && (
            <div className={styles.modalHeroBg} style={{ backgroundImage: `url("${backdrop}")` }} />
          )}
          <div className={styles.modalHeroScrim} />
          <div className={styles.modalHeroContent}>
            {eyebrow && <div className={styles.modalEyebrow}>{eyebrow}</div>}
            <h2 className={styles.modalShow}>{ep.show_title}</h2>
            {!!detail.data?.genres?.length && (
              <div className={styles.modalGenres}>
                {detail.data.genres.slice(0, 4).map((g) => (
                  <span key={g}>{g}</span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className={styles.modalBody}>
          <div className={styles.modalEp}>
            <div className={styles.modalStill}>{still && <Art src={still} lazy={false} />}</div>
            <div className={styles.modalEpMain}>
              <div className={styles.modalSe}>
                S{ep.season_number} · E{ep.episode_number}
              </div>
              <h3 className={styles.modalEpTitle}>{ep.title || `Episode ${ep.episode_number}`}</h3>
              <div className={styles.modalWhen}>
                {fullDate(ep.air_date)} · {time || 'Anytime'}
                {ep.air_date === today ? ' · today' : ''}
              </div>
              <div className={styles.modalState}>
                <StatePill
                  state={ep.acq}
                  large
                  label={
                    ep.acq === 'downloading' ? `Downloading · ${ep.progress ?? 0}%` : undefined
                  }
                />
                <span className={styles.why}>{whyLine(ep, { autoOn })}</span>
              </div>
              <div className={styles.modalTags}>
                {ep.runtime_minutes ? <span>{ep.runtime_minutes} min</span> : null}
                {ep.rating ? <span>★ {Math.round(ep.rating * 10) / 10}</span> : null}
                {ep.not_in_library ? <span>Not in your library</span> : null}
              </div>
              <p className={ep.overview ? styles.modalOv : styles.modalOvNone}>
                {ep.overview || 'No episode synopsis yet.'}
              </p>
            </div>
          </div>
          {showOverview && (
            <div className={styles.modalAbout}>
              <h4>About the show</h4>
              <p>{showOverview}</p>
            </div>
          )}
        </div>
        <div className={styles.modalActions}>
          {canIgnore && (
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy !== null}
              title="Stop looking for this episode"
              onClick={() =>
                act(
                  'ignore',
                  () => ignoreEpisode(ep),
                  'No longer looking for that episode',
                  'Could not stop monitoring it',
                )
              }
            >
              Ignore
            </button>
          )}
          {canRetry && (
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy !== null}
              title="Clear the retry backoff and search every source again"
              onClick={() =>
                act(
                  'retry',
                  () => retryEpisode(ep),
                  'Searching every source again',
                  'Retry failed to start',
                )
              }
            >
              {busy === 'retry' ? 'Retrying…' : '↻ Retry now'}
            </button>
          )}
          {(canWish || canPreWish) && (
            <button
              type="button"
              className={canWish ? styles.btnPrimary : styles.btnGhost}
              disabled={busy !== null}
              title={
                canPreWish
                  ? 'It grabs on its own after it airs; this puts it on the wishlist now'
                  : undefined
              }
              onClick={() =>
                act(
                  'wish',
                  () => wishEpisode(ep),
                  `Added S${ep.season_number}E${ep.episode_number} to the wishlist`,
                  'Could not add to wishlist',
                )
              }
            >
              {busy === 'wish' ? 'Adding…' : '＋ Wishlist episode'}
            </button>
          )}
          {done.wish && <span className={styles.doneNote}>✓ On the wishlist</span>}
          <button
            type="button"
            className={canWish ? styles.btnGhost : styles.btnPrimary}
            onClick={() => {
              onClose();
              openShowDetail(ep);
            }}
          >
            Open show page →
          </button>
        </div>
      </div>
    </DialogFrame>
  );
}
