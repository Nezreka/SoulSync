import { Link } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { AudiobookItem, AudiobookRole } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

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
