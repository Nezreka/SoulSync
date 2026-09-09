import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import type { AudiobookPersonProfile, AudiobookRole } from '../-audiobooks.types';

import { fetchPersonProfile, followAuthor, unfollowAuthor } from '../-audiobooks.api';
import { AudiobookBackButton } from './audiobook-back-button';
import { AudiobookRail } from './audiobook-rail';
import { AudiobookWorks } from './audiobook-works';
import styles from './audiobooks-page.module.css';

interface AudiobookPersonPageProps {
  name: string;
  role: AudiobookRole;
}

/**
 * An author or narrator page.
 *
 * Deliberately not a result grid. A search for "Brandon Sanderson" returns
 * thirty titles in no useful order; what a listener actually wants is the shape
 * of the work — which series exist, what order they go in, what is standalone,
 * and who he works with. So the bibliography arrives grouped: a strip per series
 * in reading order, standalones in their own grid, and the collaborators as
 * links, because "the other books this narrator did with this author" is a real
 * question and one click from here.
 */
export function AudiobookPersonPage({ name, role }: AudiobookPersonPageProps) {
  const [profile, setProfile] = useState<AudiobookPersonProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [watching, setWatching] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setProfile(null);
    void (async () => {
      const found = await fetchPersonProfile(name, role);
      if (cancelled) return;
      setProfile(found);
      setWatching(Boolean(found?.watching));
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [name, role]);

  if (loading) {
    return (
      <div className={styles.personPage}>
        <AudiobookBackButton />
        <div className={styles.detailSkeleton} />
      </div>
    );
  }

  if (!profile || profile.total_books === 0) {
    return (
      <div className={styles.personPage}>
        <AudiobookBackButton />
        <div className={styles.emptyState}>
          <h3>Nothing found</h3>
          <p>
            The catalogue has no audiobooks credited to {name} as {role}.
          </p>
        </div>
      </div>
    );
  }

  /**
   * Following an author records TODAY as the cutoff, so their back catalogue is
   * never queued — only what they publish from here. A narrator has no release
   * of their own, so only authors can be followed.
   */
  const toggleFollow = async () => {
    setFollowBusy(true);
    const next = !watching;
    setWatching(next);
    const ok = next
      ? await followAuthor(profile.name, profile.highlights[0]?.cover_url ?? '')
      : await unfollowAuthor(profile.name);
    if (!ok) setWatching(!next);
    setFollowBusy(false);
  };

  const collaboratorRole: AudiobookRole = role === 'author' ? 'narrator' : 'author';
  const collaboratorLabel = role === 'author' ? 'Narrated by' : 'Writes with';
  // Their best-rated covers stand in for a portrait: Audible's catalogue has no
  // author images, and their own work is a truer likeness than an initial.
  const fan = profile.highlights
    .map((book) => book.cover_url_large || book.cover_url)
    .filter((cover): cover is string => Boolean(cover))
    .slice(0, 3);
  const backdrop = fan[0];
  // Rounded to hours: "1201 hrs 26 mins" is a receipt, not a headline.
  const listeningHours = Math.round(profile.total_runtime_minutes / 60);

  return (
    <div className={styles.personPage}>
      <AudiobookBackButton />

      <header className={styles.personHeader}>
        {backdrop && (
          <div className={styles.personBackdrop} style={{ backgroundImage: `url(${backdrop})` }} />
        )}
        <div className={styles.personScrim} />

        <div className={styles.personHeaderInner}>
          <div className={styles.personFan} aria-hidden="true">
            {fan.map((cover, index) => (
              <img
                key={cover}
                className={styles.personFanCover}
                style={{ ['--fan-index' as string]: index }}
                src={cover}
                alt=""
                loading="lazy"
              />
            ))}
          </div>

          <div className={styles.personMeta}>
            <span className={styles.personRole}>{role === 'author' ? 'Author' : 'Narrator'}</span>
            <h1 className={styles.personName}>{profile.name}</h1>

            <div className={styles.personFacts}>
              <span className={styles.personFact}>
                <strong>{profile.total_books.toLocaleString()}</strong>{' '}
                {profile.total_books === 1 ? 'title' : 'titles'}
              </span>
              {profile.series.length > 0 && (
                <span className={styles.personFact}>
                  <strong>{profile.series.length}</strong> series
                </span>
              )}
              {listeningHours > 0 && (
                <span className={styles.personFact}>
                  <strong>{listeningHours.toLocaleString()}</strong> hours
                </span>
              )}
            </div>

            {profile.genres.length > 0 && (
              <p className={styles.personGenreLine}>{profile.genres.slice(0, 4).join(' · ')}</p>
            )}
            {role === 'author' && (
              <button
                type="button"
                className={`library-artist-watchlist-btn${watching ? ' watching' : ''}`}
                disabled={followBusy}
                onClick={() => void toggleFollow()}
                title={
                  watching
                    ? 'Remove from Watchlist'
                    : 'Add to Watchlist — new releases are wishlisted automatically'
                }
              >
                <span className="watchlist-icon">👁️</span>
                <span className="watchlist-text">
                  {followBusy ? 'Updating…' : watching ? 'Watching' : 'Add to Watchlist'}
                </span>
              </button>
            )}

            {profile.collaborators.length > 0 && (
              <div className={styles.personCollaborators}>
                <span className={styles.personCollabLabel}>{collaboratorLabel}</span>
                <div className={styles.personCollabList}>
                  {profile.collaborators.slice(0, 5).map((collaborator) => (
                    <Link
                      key={collaborator.name}
                      to={
                        collaboratorRole === 'narrator'
                          ? '/audiobooks/narrator/$name'
                          : '/audiobooks/author/$name'
                      }
                      params={{ name: collaborator.name }}
                      className={styles.personCollabLink}
                    >
                      {collaborator.name}
                      <span className={styles.personCollabCount}>{collaborator.count}</span>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      <AudiobookWorks profile={profile} />

      {profile.highlights.length > 0 && (
        <AudiobookRail title="Highest rated" books={profile.highlights} />
      )}
    </div>
  );
}
