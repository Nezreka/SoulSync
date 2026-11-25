/**
 * every name on a page that has a page of its own is a link: genres to that
 * section filtered by the genre, people to their person page, a studio, a
 * network or a label to everything you own from it.
 */

import { Link } from '@tanstack/react-router';

import { remoteImage } from '../-lib/images';
import { Art, styles as s } from './kit';

export function VideoGenreLinks({ kind, genres }: { kind: 'movies' | 'tv'; genres: string[] }) {
  return (
    <>
      {genres.map((g) =>
        kind === 'movies' ? (
          <Link key={g} to="/player/movies" search={{ genre: g }} className={s.metaLink} data-nav>
            {g}
          </Link>
        ) : (
          <Link key={g} to="/player/tv" search={{ genre: g }} className={s.metaLink} data-nav>
            {g}
          </Link>
        ),
      )}
    </>
  );
}

export function MusicGenreLinks({ genres }: { genres: string[] }) {
  return (
    <>
      {genres.map((g) => (
        <Link
          key={g}
          to="/player/music/genre/$name"
          params={{ name: g }}
          className={s.metaLink}
          data-nav
        >
          {g}
        </Link>
      ))}
    </>
  );
}

export function LabelLink({ label }: { label: string }) {
  return (
    <Link to="/player/music/label/$name" params={{ name: label }} className={s.metaLink} data-nav>
      {label}
    </Link>
  );
}

export function StudioLink({ studio }: { studio: string }) {
  return (
    <Link
      to="/player/movies/studio/$name"
      params={{ name: studio }}
      className={s.metaLink}
      data-nav
    >
      {studio}
    </Link>
  );
}

export function NetworkLink({ network }: { network: string }) {
  return (
    <Link to="/player/tv/network/$name" params={{ name: network }} className={s.metaLink} data-nav>
      {network}
    </Link>
  );
}

/**
 * the people behind a title, with their faces: directors, writers,
 * creators. each opens their person page when tmdb knows them.
 */
export function CrewList({
  people,
}: {
  people: { name: string; job: string; tmdb_id?: number | null; photo?: string | null }[];
}) {
  if (!people.length) return null;
  return (
    <div className={s.crewList}>
      {people.map((p, i) => {
        const body = (
          <>
            <span className={s.crewPhoto}>
              <Art src={remoteImage(p.photo)} alt={p.name.slice(0, 1)} />
            </span>
            <span className={s.crewText}>
              <span className={s.crewName}>{p.name}</span>
              <span className={s.crewJob}>{p.job}</span>
            </span>
          </>
        );
        return p.tmdb_id ? (
          <Link
            key={`${p.name}-${p.job}-${i}`}
            to="/player/person/$personId"
            params={{ personId: String(p.tmdb_id) }}
            className={s.crewRow}
            data-nav
          >
            {body}
          </Link>
        ) : (
          <div key={`${p.name}-${p.job}-${i}`} className={s.crewRow}>
            {body}
          </div>
        );
      })}
    </div>
  );
}

/** one entry per person, their jobs joined ("Director, Writer") */
export function mergeCrew<T extends { name: string; job: string; tmdb_id?: number | null }>(
  crew: T[],
): T[] {
  const byKey = new Map<string, T>();
  for (const c of crew) {
    const key = c.tmdb_id ? String(c.tmdb_id) : c.name;
    const prev = byKey.get(key);
    if (!prev) byKey.set(key, { ...c });
    else if (!prev.job.split(', ').includes(c.job)) prev.job = `${prev.job}, ${c.job}`;
  }
  return [...byKey.values()];
}
