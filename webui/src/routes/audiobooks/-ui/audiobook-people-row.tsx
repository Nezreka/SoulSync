import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { AudiobookItem, AudiobookRole } from '../-audiobooks.types';

import { fetchFollowedAuthors, followAuthor, unfollowAuthor } from '../-audiobooks.api';
import styles from './audiobooks-page.module.css';

/** The tile is a link, so the badge has to swallow its own click or watching
 *  an author would navigate to their page instead. Same guard the library
 *  artist card uses. */
function badgeClickHandler(action?: () => void) {
  return (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    action?.();
  };
}

interface AudiobookPeopleRowProps {
  results: AudiobookItem[];
  /** Which credit to surface. Search mode decides; "keywords" shows both. */
  roles: AudiobookRole[];
  max?: number;
}

interface PersonHit {
  name: string;
  role: AudiobookRole;
  count: number;
  covers: string[];
  runtimeMinutes: number;
}

/**
 * The people behind a set of results, surfaced above the books.
 *
 * Searching "brandon sanderson" and getting only a wall of covers buries the
 * thing actually being looked for. This lifts the authors and narrators out of
 * the result set as entities, each a door to their own page.
 *
 * Derived from the results already on screen rather than a second request: the
 * people worth showing for a search are exactly the ones who turned up in it,
 * and no round trip could answer that better.
 *
 * A person tile is deliberately NOT shaped like a book. Books are square covers
 * in a grid; a person is a wide plate carrying their own cover art as an
 * out-of-focus wash with their name set over it. Audible's catalogue has no
 * author photographs, so their work is the only honest likeness available — and
 * blurred into a backdrop it reads as atmosphere rather than as a book you could
 * click.
 */
export function AudiobookPeopleRow({ results, roles, max = 6 }: AudiobookPeopleRowProps) {
  // Which authors are already watched. Fetched once for the whole row rather
  // than per tile: it is one list, and six tiles asking separately is five
  // requests for an answer we already have.
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    void fetchFollowedAuthors().then((authors) => {
      if (!cancelled) setWatched(new Set(authors.map((a) => a.name)));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleWatch = useCallback(
    async (name: string, coverUrl: string) => {
      setPending((prev) => new Set(prev).add(name));
      const next = !watched.has(name);
      const ok = next ? await followAuthor(name, coverUrl) : await unfollowAuthor(name);
      if (ok) {
        setWatched((prev) => {
          const updated = new Set(prev);
          if (next) updated.add(name);
          else updated.delete(name);
          return updated;
        });
      }
      setPending((prev) => {
        const updated = new Set(prev);
        updated.delete(name);
        return updated;
      });
    },
    [watched],
  );

  const people = useMemo(() => {
    const tally = new Map<string, PersonHit>();

    for (const book of results) {
      for (const role of roles) {
        const credited = role === 'author' ? book.authors : book.narrators;
        for (const person of credited) {
          const name = person.name.trim();
          if (!name) continue;
          const key = `${role}:${name.toLowerCase()}`;
          const hit = tally.get(key);
          if (hit) {
            hit.count += 1;
            hit.runtimeMinutes += book.runtime_minutes ?? 0;
            if (hit.covers.length < 3 && book.cover_url) hit.covers.push(book.cover_url);
          } else {
            tally.set(key, {
              name,
              role,
              count: 1,
              runtimeMinutes: book.runtime_minutes ?? 0,
              covers: book.cover_url ? [book.cover_url] : [],
            });
          }
        }
      }
    }

    return (
      [...tally.values()]
        // One credit is not a person worth surfacing, it is a book that happens
        // to have a name attached — the result grid already shows that.
        .filter((hit) => hit.count > 1)
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
        .slice(0, max)
    );
  }, [results, roles, max]);

  if (people.length === 0) return null;

  return (
    <section className={styles.peopleSection}>
      <header className={styles.railHeader}>
        <div>
          <h2 className={styles.railTitle}>People</h2>
          <p className={styles.railSubtitle}>Open a full catalogue for anyone here</p>
        </div>
      </header>

      <div className={styles.peopleRow}>
        {people.map((person) => {
          const hours = Math.round(person.runtimeMinutes / 60);
          return (
            <Link
              key={`${person.role}-${person.name}`}
              to={
                person.role === 'author' ? '/audiobooks/author/$name' : '/audiobooks/narrator/$name'
              }
              params={{ name: person.name }}
              className={styles.personTile}
            >
              {person.covers[0] && (
                <span
                  className={styles.personTileWash}
                  style={{ backgroundImage: `url(${person.covers[0]})` }}
                  aria-hidden="true"
                />
              )}
              <span className={styles.personTileScrim} aria-hidden="true" />

              <span className={styles.personTileSpines} aria-hidden="true">
                {person.covers.map((cover, index) => (
                  <img
                    key={cover}
                    className={styles.personTileSpine}
                    style={{ ['--spine' as string]: index }}
                    src={cover}
                    alt=""
                    loading="lazy"
                  />
                ))}
              </span>

              {/* Authors only: a narrator has no release of their own, they
                  appear on someone else's. Same badge, same container and the
                  same top-right corner as the library artist cards, so "watch
                  this person" looks identical wherever it appears. */}
              {person.role === 'author' && (
                <div className="card-badge-container">
                  <div
                    className={`watch-card-icon source-card-icon${
                      watched.has(person.name) ? ' watched' : ''
                    }`}
                    data-unwatched={watched.has(person.name) ? undefined : '1'}
                    style={watched.has(person.name) ? undefined : { opacity: 0.4 }}
                    title={
                      watched.has(person.name)
                        ? 'Remove from Watchlist'
                        : 'Add to Watchlist — new releases are wishlisted automatically'
                    }
                    onClick={badgeClickHandler(() => {
                      void toggleWatch(person.name, person.covers[0] ?? '');
                    })}
                  >
                    <span className="watch-icon-emoji">👁️</span>
                    <span className="watch-icon-label">
                      {pending.has(person.name)
                        ? '...'
                        : watched.has(person.name)
                          ? 'Watching'
                          : 'Watch'}
                    </span>
                  </div>
                </div>
              )}

              <span className={styles.personTileBody}>
                <span className={styles.personTileRole}>
                  {person.role === 'author' ? 'Author' : 'Narrator'}
                </span>
                <span className={styles.personTileName}>{person.name}</span>
                <span className={styles.personTileFacts}>
                  {person.count} {person.count === 1 ? 'title' : 'titles'}
                  {hours > 0 && ` · ${hours} hrs`}
                </span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
