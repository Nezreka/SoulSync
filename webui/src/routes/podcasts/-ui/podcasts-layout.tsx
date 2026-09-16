import { Outlet } from '@tanstack/react-router';

import { MediaPlayerBar } from '@/components/media-player-bar/media-player-bar';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import { usePodcastContext } from './podcast-context';
import styles from './podcasts-page.module.css';

/**
 * Layout shell for all /podcasts/* routes.
 *
 * Owns the floating player bar and wraps child routes (browse index +
 * detail $podcastId) inside the shared PodcastProvider context.
 * The provider itself lives in route.tsx so it can wrap this component.
 */
export function PodcastsLayout() {
  useReactPageShell('podcasts');
  const { activePlayback, handleTogglePlay, closePlayer, updateProgress } = usePodcastContext();

  return (
    <div
      className={`page-shell ${styles.podcastsContainer} ${activePlayback ? styles.podcastsContainerWithTopPlayer : ''}`}
    >
      {/* Top Floating Audio Player Bar — shared with the audiobooks page. */}
      {activePlayback && (
        <MediaPlayerBar
          title={activePlayback.episode.title}
          subtitle={activePlayback.showTitle}
          artworkUrl={activePlayback.episode.artwork_url || activePlayback.showArtwork}
          src={activePlayback.episode.enclosure_url}
          isPlaying={activePlayback.isPlaying}
          durationHint={activePlayback.episode.duration_seconds}
          fallbackIcon="🎙️"
          ariaLabel="Podcast Audio Player"
          onTogglePlay={handleTogglePlay}
          onClose={closePlayer}
          onProgress={updateProgress}
        />
      )}

      <Outlet />
    </div>
  );
}
