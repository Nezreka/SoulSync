import { useQuery } from '@tanstack/react-query';

import {
  routedNamesQueryOptions,
  useAudiobookFolderConfigured,
  useSetAudiobookRoute,
} from './audiobook-routes.queries';

type Props = {
  /** The artist's display name; the server normalises it. */
  name: string;
  className?: string;
};

/**
 * "Treat as audiobook": the artist's music downloads are filed in the audiobook
 * library instead of the music library. For spoken-word releases (children's
 * audio plays and the like) that the music search finds but Audible does not list.
 */
export function AudiobookRouteToggle({ name, className }: Props) {
  const query = useQuery(routedNamesQueryOptions([name]));
  const toggle = useSetAudiobookRoute(name);
  const routed = (query.data ?? []).includes(name);
  const unknown = query.isPending || query.isError;
  // Switching an artist off must always work; switching one on needs a folder to file into.
  const noFolder = !useAudiobookFolderConfigured() && !routed;

  return (
    <button
      type="button"
      className={`${className ?? ''}${routed ? ' watching' : ''}`.trim()}
      id="library-artist-audiobook-btn"
      aria-pressed={routed}
      disabled={unknown || noFolder || toggle.isPending}
      title={
        noFolder
          ? 'Set the audiobook folder first (Settings > Library > Folders).'
          : routed
            ? 'Downloads by this artist are filed in your audiobook library. Click to file them as music again.'
            : 'File downloads by this artist in your audiobook library instead of the music library.'
      }
      onClick={() => toggle.mutate(!routed)}
    >
      <span className="watchlist-icon">📖</span>
      <span className="watchlist-text">
        {toggle.isPending ? 'Saving...' : routed ? 'Audiobook' : 'Treat as audiobook'}
      </span>
    </button>
  );
}
