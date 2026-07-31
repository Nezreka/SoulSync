import type { DownloadState } from '../-discover.download-bar';

import { downloadBarView } from '../-discover.download-bar';

/**
 * The floating download bar.
 *
 * Transcribed from discover.js 11740-11770.
 *
 * The whole bar hides at zero rather than sitting there empty — it is a
 * transient overlay, not a permanent piece of chrome, and an empty one covers
 * page content for nothing.
 */

export interface DownloadBarProps {
  state: DownloadState;
  onOpen: (playlistId: string) => void;
}

export function DownloadBar({ state, onOpen }: DownloadBarProps) {
  const view = downloadBarView(state);
  if (view.hidden) return null;

  return (
    <div className="discover-download-bar" id="discover-download-bar">
      {view.bubbles.map((bubble) => (
        <button
          type="button"
          key={bubble.playlistId}
          className={
            bubble.completed
              ? 'discover-download-bubble completed'
              : 'discover-download-bubble'
          }
          data-playlist-id={bubble.playlistId}
          title={bubble.title}
          style={{ background: bubble.background }}
          onClick={() => onOpen(bubble.playlistId)}
        >
          <span className="discover-download-icon">{bubble.icon}</span>
          <span className="discover-download-name">{bubble.name}</span>
        </button>
      ))}
    </div>
  );
}
