import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { DialogFrame } from '@/components/dialog/dialog';
import { getShellProfileContext } from '@/platform/shell/bridge';

import { LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import {
  ARTIST_SCOPES,
  LOCK_QUERY_KEY,
  SCOPE_COPY,
  artistReviewQueryOptions,
  fetchArtistReview,
  lockQueryOptions,
  lockedIdsQueryOptions,
  removeLock,
  saveLock,
  type LockEntity,
  type LockScope,
  type LockState,
} from '../-review-lock.api';
import styles from './review-lock.module.css';

/** #2046: the padlock. A person goes through an album, gets it right and
 *  locks it: no job changes it after that, and it counts as reviewed. What
 *  they do by hand on the page still works. */

function isAdmin(): boolean {
  return getShellProfileContext()?.isAdmin === true;
}

/** sqlite's CURRENT_TIMESTAMP is utc with a space in it */
export function lockDate(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(
    `${value.replace(' ', 'T')}${/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? '' : 'Z'}`,
  );
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** an artist is matched to an artist and filed in a folder of its own */
const ARTIST_LABELS: Partial<Record<LockScope, string>> = {
  match: 'Artist match',
  files: 'Artist folder',
};

const VARIANT = { hero: styles.hero, row: styles.row, toolbar: styles.toolbar } as const;

export function Padlock({ locked, size = 16 }: { locked: boolean; size?: number }) {
  return (
    <svg
      className={`${styles.padlock} ${locked ? styles.padlockClosed : ''}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* the shackle swings shut when it locks */}
      <path className={styles.shackle} d="M8 11V7.5a4 4 0 0 1 8 0V11" />
      <rect x="5" y="11" width="14" height="10" rx="2.5" />
      <path d="M12 15v2.5" />
    </svg>
  );
}

function useInvalidateLocks() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: LOCK_QUERY_KEY });
  };
}

/** every locked album and artist id, shared by all the padlocks on a page */
export function useLockedIds() {
  const query = useQuery(lockedIdsQueryOptions());
  return {
    albums: new Set(query.data?.albums ?? []),
    artists: new Set(query.data?.artists ?? []),
  };
}

export function ReviewLockButton({
  entity,
  id,
  name,
  artistId,
  variant = 'row',
  onOpenAlbum,
}: {
  entity: LockEntity;
  id: number;
  name: string;
  /** the album's artist: gives the sheet "Lock and next" */
  artistId?: number;
  /** hero: beside a page title. row: on an album's line. toolbar: with the artist's tools */
  variant?: 'hero' | 'row' | 'toolbar';
  onOpenAlbum?: (albumId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ids = useLockedIds();
  const locked = entity === 'album' ? ids.albums.has(id) : ids.artists.has(id);
  const review = useQuery({
    ...artistReviewQueryOptions(entity === 'artist' ? id : (artistId ?? 0)),
    enabled: entity === 'artist' || (locked && Boolean(artistId)),
  });
  const changed =
    entity === 'album' && locked && (review.data?.changed_album_ids ?? []).includes(id);
  const admin = isAdmin();
  // someone who can't lock sees a padlock only where there is one
  if (!admin && !locked) return null;

  const counts = entity === 'artist' ? review.data : undefined;
  const label = locked
    ? changed
      ? `${name} changed since it was reviewed`
      : `${name} is reviewed and locked`
    : `Lock ${name} as reviewed`;
  return (
    <>
      <button
        type="button"
        className={[
          styles.button,
          VARIANT[variant],
          locked ? styles.buttonLocked : '',
          changed ? styles.buttonChanged : '',
        ].join(' ')}
        aria-label={label}
        aria-pressed={locked}
        title={label}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
      >
        <Padlock locked={locked} size={variant === 'row' ? 15 : 16} />
        {variant === 'toolbar' ? (
          <span className={styles.buttonText}>
            {locked ? 'Locked' : 'Lock'}
            {counts && counts.albums > 0 ? (
              <span className={styles.count}>
                {counts.reviewed} of {counts.albums} reviewed
              </span>
            ) : null}
          </span>
        ) : null}
        {variant === 'hero' ? (
          <span className={styles.buttonText}>
            {locked ? (changed ? 'Changed' : 'Reviewed') : 'Lock'}
          </span>
        ) : null}
      </button>
      {open ? (
        <ReviewLockSheet
          entity={entity}
          id={id}
          name={name}
          artistId={artistId}
          onOpenAlbum={onOpenAlbum}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

export function ReviewLockSheet({
  entity,
  id,
  name,
  artistId,
  onOpenAlbum,
  onClose,
}: {
  entity: LockEntity;
  id: number;
  name: string;
  artistId?: number;
  onOpenAlbum?: (albumId: number) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateLocks();
  const query = useQuery(lockQueryOptions(entity, id));
  const lock = query.data;
  const shown: LockScope[] =
    entity === 'artist' ? ARTIST_SCOPES : (lock?.scope_options ?? []).map((option) => option.scope);
  const [picked, setPicked] = useState<Set<LockScope> | null>(null);
  const [withAlbums, setWithAlbums] = useState(true);
  const [busy, setBusy] = useState(false);
  // focus lands on the name, not on Close with a ring round it
  const titleRef = useRef<HTMLHeadingElement>(null);

  // a lock that exists shows its own switches; a new one starts with all on
  useEffect(() => {
    if (!lock || picked) return;
    setPicked(new Set(lock.locked ? lock.scopes.filter((s) => shown.includes(s)) : shown));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lock]);

  const canLock = Boolean(lock?.can_lock) && isAdmin();
  const scopes = shown.filter((scope) => picked?.has(scope));
  const dirty =
    lock?.locked === true &&
    (scopes.length !== lock.scopes.filter((s) => shown.includes(s)).length ||
      scopes.some((scope) => !lock.scopes.includes(scope)));

  async function run(work: () => Promise<LockState>, done: string): Promise<boolean> {
    setBusy(true);
    try {
      const next = await work();
      queryClient.setQueryData(lockQueryOptions(entity, id).queryKey, next);
      invalidate();
      void queryClient.invalidateQueries({ queryKey: [...LIBRARY_V2_QUERY_KEY, 'history'] });
      window.showToast?.(done, 'success');
      return true;
    } catch (error) {
      window.showToast?.((error as Error).message || 'That did not work', 'error');
      return false;
    } finally {
      setBusy(false);
    }
  }

  const lockNow = () =>
    run(
      () =>
        saveLock(entity, id, {
          scopes,
          ...(entity === 'artist' && withAlbums ? { albums: true } : {}),
        }),
      `${name} is locked`,
    );

  async function lockAndNext() {
    if (!(await lockNow()) || !artistId) return;
    try {
      const review = await fetchArtistReview(artistId, id);
      if (review.next_album_id && onOpenAlbum) {
        onClose();
        onOpenAlbum(review.next_album_id);
      } else {
        window.showToast?.('That was the last one. Every album here is reviewed.', 'success');
        onClose();
      }
    } catch {
      onClose();
    }
  }

  const reviewed = lockDate(lock?.locked_at);
  const pinned = lock
    ? [
        lock.field_locks.artwork ? 'Artwork you picked' : '',
        lock.field_locks.genres ? 'Genres from a cleanup' : '',
        lock.field_locks.edition ? 'Edition you pinned' : '',
      ].filter(Boolean)
    : [];

  return (
    <DialogFrame
      open
      aria-label={lock?.locked ? `${name} is locked` : `Lock ${name}`}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      viewportClassName={styles.viewport}
      initialFocus={titleRef}
      className={styles.sheet}
    >
      <header className={styles.head}>
        <span className={`${styles.seal} ${lock?.locked ? styles.sealLocked : ''}`}>
          <Padlock locked={Boolean(lock?.locked)} size={26} />
        </span>
        <div className={styles.headText}>
          <p className={styles.eyebrow}>{entity === 'album' ? 'Album' : 'Artist'}</p>
          <h2 ref={titleRef} tabIndex={-1} className={styles.title} title={name}>
            {name}
          </h2>
          <p className={styles.sub}>
            {!lock
              ? ' '
              : lock.locked
                ? `Reviewed${reviewed ? ` ${reviewed}` : ''}. Nothing automated changes what is switched on below.`
                : 'Lock it once you have it right. Scans, enrichment and repair jobs leave it alone after that, and it counts as reviewed.'}
          </p>
        </div>
        <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
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
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </header>

      <div className={styles.body}>
        {query.isError ? (
          <p className={styles.note}>Could not read the lock.</p>
        ) : !lock || !picked ? (
          <div className={styles.loading} aria-hidden="true" />
        ) : (
          <>
            {lock.changed_since_review ? (
              <div className={styles.changed} role="status">
                <div>
                  <strong>Changed since your review</strong>
                  <span>A file was replaced, renamed, added or removed after you locked it.</span>
                </div>
                {canLock ? (
                  <button
                    type="button"
                    className={styles.quiet}
                    disabled={busy}
                    onClick={() =>
                      void run(() => saveLock(entity, id, { scopes }), 'Marked as reviewed again')
                    }
                  >
                    Mark reviewed again
                  </button>
                ) : null}
              </div>
            ) : null}

            <section>
              <h3 className={styles.label}>Left alone</h3>
              <div className={styles.switches}>
                {shown.map((scope) => {
                  const option = lock.scope_options.find((o) => o.scope === scope);
                  const on = picked.has(scope);
                  return (
                    <button
                      key={scope}
                      type="button"
                      role="switch"
                      aria-checked={on}
                      disabled={!canLock || busy}
                      className={styles.switchRow}
                      onClick={() =>
                        setPicked((current) => {
                          const next = new Set(current ?? []);
                          if (next.has(scope)) next.delete(scope);
                          else next.add(scope);
                          return next;
                        })
                      }
                    >
                      <span>
                        <strong>
                          {(entity === 'artist' && ARTIST_LABELS[scope]) || option?.label || scope}
                        </strong>
                        <small>{SCOPE_COPY[scope][entity]}</small>
                      </span>
                      <span className={styles.switch} aria-hidden="true" />
                    </button>
                  );
                })}
              </div>
              <p className={styles.note}>
                {entity === 'album'
                  ? 'Missing tracks still download. A missing or damaged file is still reported, and fixing it is left to you. Anything you do on this page yourself still works.'
                  : 'This covers the artist. Albums are locked one by one, so a new release still gets tagged.'}
              </p>
            </section>

            {entity === 'artist' && !lock.locked && canLock && (lock.review?.albums ?? 0) > 0 ? (
              <button
                type="button"
                role="switch"
                aria-checked={withAlbums}
                className={`${styles.switchRow} ${styles.also}`}
                onClick={() => setWithAlbums((current) => !current)}
              >
                <span>
                  <strong>And all current albums</strong>
                  <small>
                    {lock.review?.albums} with files today
                    {lock.review?.reviewed ? `, ${lock.review.reviewed} already reviewed` : ''}
                  </small>
                </span>
                <span className={styles.switch} aria-hidden="true" />
              </button>
            ) : null}

            {pinned.length ? (
              <section>
                <h3 className={styles.label}>Already kept</h3>
                <div className={styles.chips}>
                  {pinned.map((text) => (
                    <span key={text} className={styles.chip}>
                      {text}
                    </span>
                  ))}
                </div>
              </section>
            ) : null}

            {lock.locked ? (
              <section>
                <h3 className={styles.label}>Kept away</h3>
                {lock.skips.length ? (
                  <ul className={styles.skips}>
                    {lock.skips.map((skip) => (
                      <li key={`${skip.scope}:${skip.what}`}>
                        <span className={styles.skipWhat}>{skip.what}</span>
                        <span className={styles.skipMeta}>
                          {lock.scope_options.find((o) => o.scope === skip.scope)?.label ??
                            skip.scope}
                          {skip.times > 1 ? ` · ${skip.times} times` : ''}
                          {lockDate(skip.last_at) ? ` · ${lockDate(skip.last_at)}` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.note}>Nothing has tried to change it yet.</p>
                )}
              </section>
            ) : null}
          </>
        )}
      </div>

      {lock && picked && canLock ? (
        <footer className={styles.foot}>
          {lock.locked ? (
            <button
              type="button"
              className={styles.quiet}
              disabled={busy}
              onClick={async () => {
                if (await run(() => removeLock(entity, id), `${name} is unlocked`)) onClose();
              }}
            >
              Unlock
            </button>
          ) : (
            <span />
          )}
          <div className={styles.footEnd}>
            {lock.locked ? (
              <button
                type="button"
                className={styles.primary}
                disabled={busy || !dirty}
                onClick={() =>
                  void run(
                    () => saveLock(entity, id, { scopes, review: false }),
                    scopes.length ? 'Lock updated' : `${name} is unlocked`,
                  )
                }
              >
                Save
              </button>
            ) : (
              <>
                {entity === 'album' && artistId && onOpenAlbum ? (
                  <button
                    type="button"
                    className={styles.quiet}
                    disabled={busy || scopes.length === 0}
                    onClick={() => void lockAndNext()}
                  >
                    Lock and next
                  </button>
                ) : null}
                <button
                  type="button"
                  className={styles.primary}
                  disabled={busy || scopes.length === 0}
                  onClick={async () => {
                    if (await lockNow()) onClose();
                  }}
                >
                  <Padlock locked size={15} />
                  Lock
                </button>
              </>
            )}
          </div>
        </footer>
      ) : null}
    </DialogFrame>
  );
}
