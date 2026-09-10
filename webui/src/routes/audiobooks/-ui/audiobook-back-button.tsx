import { Link, useCanGoBack, useRouter } from '@tanstack/react-router';

import styles from './audiobooks-page.module.css';

interface AudiobookBackButtonProps {
  /** Explicit action. Given one, the button always does this instead of popping history. */
  onBack?: () => void;
  label?: string;
}

/**
 * The one back control for /audiobooks/*.
 *
 * With no onBack it pops history, because the way into a detail page is usually
 * another audiobook — a series sibling, a "more by this narrator" card, a search
 * result — and sending all of those to the browse page would throw away where
 * the listener was. A page opened cold (a shared link, a bookmark, a reload) has
 * no history to pop, so that case falls back to a plain link rather than a
 * button that does nothing.
 *
 * Views that are a *state* of the browse page rather than a separate page —
 * search results, a single genre — pass onBack instead, since "back" there means
 * clearing that state, not walking the browser's history back through every
 * search the listener refined along the way.
 */
export function AudiobookBackButton({ onBack, label = 'Back' }: AudiobookBackButtonProps) {
  const router = useRouter();
  const canGoBack = useCanGoBack();

  const chevron = (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M15 5l-7 7 7 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );

  if (onBack) {
    return (
      <button type="button" className={styles.backBtn} onClick={onBack}>
        {chevron}
        {label}
      </button>
    );
  }

  if (!canGoBack) {
    return (
      <Link to="/audiobooks" className={styles.backBtn}>
        {chevron}
        {label}
      </Link>
    );
  }

  return (
    <button type="button" className={styles.backBtn} onClick={() => router.history.back()}>
      {chevron}
      {label}
    </button>
  );
}
