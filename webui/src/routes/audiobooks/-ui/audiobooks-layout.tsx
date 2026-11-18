import { Outlet } from '@tanstack/react-router';

import { MediaPlayerBar } from '@/components/media-player-bar/media-player-bar';
import { NotPartOfProfile } from '@/components/not-part-of-profile/not-part-of-profile';
import { useHasAccess } from '@/platform/shell/my-permissions';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import { sampleStreamUrl } from '../-audiobooks.api';
import { useAudiobookContext } from './audiobook-context';
import styles from './audiobooks-page.module.css';

/**
 * Layout shell for all /audiobooks/* routes.
 *
 * Owns the sample player bar so a preview started on the browse page keeps
 * playing when the listener opens a detail page. The provider itself lives in
 * route.tsx so it can wrap this component.
 */
export function AudiobooksLayout() {
  useReactPageShell('audiobooks');
  const { playback, togglePlay, closePlayer } = useAudiobookContext();
  const booksOn = useHasAccess('books');

  return (
    <div
      className={`page-shell pgf-shell ${styles.audiobooksContainer} ${playback ? styles.audiobooksContainerWithPlayer : ''}`}
    >
      {playback && (
        <MediaPlayerBar
          title={playback.title}
          subtitle={[playback.author, playback.narrator && `Narrated by ${playback.narrator}`]
            .filter(Boolean)
            .join(' · ')}
          artworkUrl={playback.coverUrl}
          // Through the server proxy: the sample CDNs send no CORS headers, and
          // the proxy forwards Range so the scrubber can actually seek.
          src={sampleStreamUrl(playback.sampleUrl)}
          isPlaying={playback.isPlaying}
          ariaLabel="Audiobook sample player"
          onTogglePlay={togglePlay}
          onClose={closePlayer}
        />
      )}
      <div className="pgf-page">
        {/* reached by a link after an admin turned books off (#2166): a calm
            card, not a page of controls the server refuses */}
        {booksOn ? <Outlet /> : <NotPartOfProfile media="books" />}
      </div>
    </div>
  );
}
