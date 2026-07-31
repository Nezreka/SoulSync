import type { DownloadState } from '../-discover.download-bar';

import { downloadBarView } from '../-discover.download-bar';

/**
 * The floating download sidebar.
 *
 * Transcribed from index.html 10644-10652 for the shell and discover.js
 * 11722-11783 for the bubbles.
 *
 * It is a SIDEBAR, not a bar — `.discover-download-sidebar`, with a header
 * carrying a live count and a `.discover-download-bubbles` list inside. The
 * first draft of this component invented `#discover-download-bar` and dropped
 * the header entirely, which type-checked and passed its tests and would have
 * rendered completely unstyled.
 *
 * Hiding is done with the `hidden` CLASS rather than by unmounting, because the
 * vanilla toggles that class and the stylesheet owns the transition.
 */

/**
 * Turn one CSS declaration into a React style object.
 *
 * `bubbleBackground` returns `background-image: url('…');` or a
 * `background: linear-gradient(…);` — a string built for the vanilla's
 * `style="…"` attribute. React's `style` takes an object, so the declaration
 * has to be split rather than passed through. Getting this wrong is silent:
 * an unparsed string is simply ignored and every bubble renders bare.
 */
export function declarationToStyle(declaration: string): React.CSSProperties {
  const [rawProp, ...rest] = declaration.replace(/;\s*$/, '').split(':');
  if (!rest.length) return {};
  const prop = rawProp.trim().replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
  return { [prop]: rest.join(':').trim() } as React.CSSProperties;
}

export interface DownloadBarProps {
  state: DownloadState;
  onOpen: (playlistId: string) => void;
}

export function DownloadBar({ state, onOpen }: DownloadBarProps) {
  const view = downloadBarView(state);

  return (
    <div
      className={view.hidden ? 'discover-download-sidebar hidden' : 'discover-download-sidebar'}
      id="discover-download-sidebar"
    >
      <div className="discover-download-sidebar-header">
        <span className="discover-download-sidebar-icon">🎵</span>
        <span className="discover-download-sidebar-title">Downloads</span>
        <span className="discover-download-sidebar-count" id="discover-download-count">
          {view.count}
        </span>
      </div>
      <div className="discover-download-bubbles" id="discover-download-bubbles">
        {view.bubbles.map((bubble) => (
          <div className="discover-download-bubble" key={bubble.playlistId}>
            <div
              className={
                bubble.completed
                  ? 'discover-download-bubble-card completed'
                  : 'discover-download-bubble-card'
              }
              data-playlist-id={bubble.playlistId}
              title={bubble.title}
              onClick={() => onOpen(bubble.playlistId)}
            >
              {/* The artwork is a background on its own layer, so the overlay
                  and the icon can sit above it without tinting the image. */}
              <div
                className="discover-download-bubble-image"
                style={declarationToStyle(bubble.background)}
              />
              <div className="discover-download-bubble-overlay" />
              <div className="discover-download-bubble-content">
                <span className="discover-download-bubble-icon">{bubble.icon}</span>
              </div>
            </div>
            <div className="discover-download-bubble-name">{bubble.name}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
