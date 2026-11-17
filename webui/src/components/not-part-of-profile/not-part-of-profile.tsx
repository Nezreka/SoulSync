import type { ReactNode } from 'react';

import {
  type PermMedia,
  hasAccessTo,
  offTitle,
  stillYours,
  useAdminName,
  useMyPermissions,
} from '@/platform/shell/my-permissions';

import styles from './not-part-of-profile.module.css';

const ICONS: Record<PermMedia, ReactNode> = {
  music: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  ),
  movies: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="2" y="3" width="20" height="18" rx="2" />
      <path d="M7 3v18M17 3v18M2 9h5M17 9h5M2 15h5M17 15h5" />
    </svg>
  ),
  tv: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M17 2l-5 5-5-5" />
    </svg>
  ),
  books: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z" />
      <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" />
    </svg>
  ),
  podcasts: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8" />
    </svg>
  ),
};

/**
 * The calm card for something that isn't part of this profile (#2166):
 * "Audiobooks aren't part of your profile. Ask Boulder if you'd like them.
 * Music and movies are all still yours." Never a dead button, never a 403.
 */
export function NotPartOfProfile({
  media,
  message,
  children,
}: {
  media: PermMedia;
  /** the server's own words, when it refused something */
  message?: string;
  /** an action under the text, e.g. Close in a dialog */
  children?: ReactNode;
}) {
  const perms = useMyPermissions();
  const admin = useAdminName();
  const title = message || offTitle(perms, media);
  const rest = stillYours(perms, media);
  // "Audiobooks aren't ... if you'd like them"; "Getting books isn't ... it"
  const them = !message && media !== 'music' && !hasAccessTo(perms, media);
  return (
    <div className={styles.calm} role="status" data-testid="not-part-of-profile">
      <div className={styles.icon}>{ICONS[media]}</div>
      <h3 className={styles.title}>{title}</h3>
      <p className={styles.text}>
        Ask {admin || 'your admin'} if you’d like {them ? 'them' : 'it'}.{rest ? ` ${rest}` : ''}
      </p>
      {children ? <div className={styles.actions}>{children}</div> : null}
    </div>
  );
}
