import { createFileRoute } from '@tanstack/react-router';

import { AudiobookBackButton } from './-ui/audiobook-back-button';
import { AudiobookLibraryPanel } from './-ui/audiobook-library-modal';
import styles from './-ui/audiobooks-page.module.css';

/**
 * your audiobook library, as a page.
 *
 * it was a modal over the browse page: no url to link, reload or send,
 * escape threw the whole view away, and the scan history link had to close
 * it first. a library is a place, not a dialog.
 */
export const Route = createFileRoute('/audiobooks/library')({
  component: AudiobookLibraryRouteComponent,
});

function AudiobookLibraryRouteComponent() {
  return (
    <div className={styles.detailPage}>
      <AudiobookBackButton />
      <AudiobookLibraryPanel embedded />
    </div>
  );
}
