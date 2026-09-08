import { Link } from '@tanstack/react-router';
import { Fragment } from 'react';

import type { AudiobookPerson, AudiobookRole } from '../-audiobooks.types';

import styles from './audiobooks-page.module.css';

interface PersonLinksProps {
  people: AudiobookPerson[];
  role: AudiobookRole;
  className?: string;
  /** Cap the list and add "+N" — cast recordings can credit a dozen narrators. */
  max?: number;
}

/**
 * Comma-separated credits, each one a link to that person's page.
 *
 * Routed on the name because that is the only key Audible actually filters on;
 * the author ASIN it hands out is accepted as a query parameter and then
 * ignored.
 */
export function AudiobookPersonLinks({ people, role, className, max }: PersonLinksProps) {
  if (people.length === 0) return null;
  const shown = max ? people.slice(0, max) : people;
  const hidden = people.length - shown.length;
  const to = role === 'author' ? '/audiobooks/author/$name' : '/audiobooks/narrator/$name';

  return (
    <span className={className}>
      {shown.map((person, index) => (
        <Fragment key={`${person.name}-${index}`}>
          {index > 0 && ', '}
          <Link
            to={to}
            params={{ name: person.name }}
            className={styles.personLink}
            onClick={(event) => event.stopPropagation()}
          >
            {person.name}
          </Link>
        </Fragment>
      ))}
      {hidden > 0 && <span className={styles.personMore}> +{hidden}</span>}
    </span>
  );
}
