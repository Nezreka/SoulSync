import { Outlet } from '@tanstack/react-router';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import { PodcastPlayerBar } from './podcast-player-bar';
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
      {/* Top Floating Audio Player Bar */}
      {activePlayback && (
        <PodcastPlayerBar
          playback={activePlayback}
          onTogglePlay={handleTogglePlay}
          onClose={closePlayer}
          onUpdateProgress={updateProgress}
        />
      )}

      <Outlet />
    </div>
  );
}
