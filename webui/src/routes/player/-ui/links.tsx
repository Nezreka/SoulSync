/**
 * every name on a page that has a page of its own is a link: genres to that
 * section filtered by the genre, people to their person page, a studio, a
 * network or a label to everything you own from it.
 */

import { Link } from '@tanstack/react-router';
import { Fragment } from 'react';

import { styles as s } from './kit';

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

/** "Jane Doe, John Roe" with each name opening their page when TMDB knows them */
export function PeopleLinks({ people }: { people: { name: string; tmdb_id?: number | null }[] }) {
  return (
    <>
      {people.map((p, i) => (
        <Fragment key={`${p.name}-${i}`}>
          {i ? ', ' : null}
          {p.tmdb_id ? (
            <Link
              to="/player/person/$personId"
              params={{ personId: String(p.tmdb_id) }}
              className={s.metaLink}
              data-nav
            >
              {p.name}
            </Link>
          ) : (
            p.name
          )}
        </Fragment>
      ))}
    </>
  );
}
