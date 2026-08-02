import type { SourceInfo } from '../-discover.your-albums-actions';

import {
  SOURCE_CONNECTED,
  SOURCE_NOT_CONNECTED,
  YOUR_ALBUMS_SOURCE_INFO,
} from '../-discover.your-albums-actions';
import { ARTISTS_SOURCE_INFO } from '../-discover.your-artists-actions';

/**
 * The two sources modals — Your Albums (discover.js 1605-1663) and Your
 * Artists (5608-5669).
 *
 * They are twins: the SAME `.ya-source-row` markup, the same footer, the same
 * overlay-click-to-close. What differs is data, so the view is shared and each
 * variant is a thin wrapper pinning its vanilla identity:
 *
 * - overlay id:     ya-albums-sources-modal-overlay  vs  ya-sources-modal-overlay
 * - row data attr:  data-yaa-source                  vs  data-source
 * - toggle id:      yaa-toggle-<id>                  vs  ya-toggle-<id>
 * - sources:        Spotify/Tidal/Deezer/DISCOGS     vs  Spotify/Tidal/LAST.FM/Deezer
 *
 * The disconnected behaviour is NOT decided here. The vanilla albums modal
 * toasts a per-source hint and the artists modal bails silently; the port
 * routes both through the module reducers (`toggleSource` /
 * `toggleArtistSource`), whose documented divergence gives the artists modal
 * the same hint. The component just forwards every click — including clicks on
 * a `.disconnected` row, exactly as the vanilla's onclick does — and lets the
 * reducer refuse.
 */

interface SourcesModalViewProps {
  overlayId: string;
  title: string;
  description: string;
  /** The vanilla's per-variant row attribute name. */
  dataAttr: 'data-yaa-source' | 'data-source';
  togglePrefix: string;
  sources: SourceInfo[];
  /** id → enabled, from `initialSourcesState`/`initialArtistSourcesState`. */
  state: Record<string, boolean>;
  connected: string[];
  onToggle: (id: string) => void;
  onSave: () => void;
  onClose: () => void;
}

function SourcesModalView({
  overlayId,
  title,
  description,
  dataAttr,
  togglePrefix,
  sources,
  state,
  connected,
  onToggle,
  onSave,
  onClose,
}: SourcesModalViewProps) {
  return (
    <div
      id={overlayId}
      className="modal-overlay"
      onClick={(e) => {
        // `if (e.target === overlay) overlay.remove()` (1631) — backdrop only.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ya-sources-modal">
        <h2>{title}</h2>
        <p className="ya-sources-desc">{description}</p>
        <div className="ya-sources-list">
          {sources.map((s) => {
            const isConnected = connected.includes(s.id);
            return (
              <div
                key={s.id}
                className={isConnected ? 'ya-source-row' : 'ya-source-row disconnected'}
                {...{ [dataAttr]: s.id }}
                onClick={() => onToggle(s.id)}
              >
                <div className="ya-source-row-left">
                  <span style={{ fontSize: 18 }}>{s.icon}</span>
                  <div>
                    <div className="ya-source-name">{s.label}</div>
                    <div className="ya-source-status">
                      {isConnected ? SOURCE_CONNECTED : SOURCE_NOT_CONNECTED}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  className={state[s.id] ? 'ya-source-toggle on' : 'ya-source-toggle'}
                  id={`${togglePrefix}${s.id}`}
                  onClick={(e) => {
                    // `event.stopPropagation()` (1646) — one toggle per click,
                    // not one from the button and one from the row.
                    e.stopPropagation();
                    onToggle(s.id);
                  }}
                />
              </div>
            );
          })}
        </div>
        <div className="ya-sources-footer">
          <button type="button" className="ya-sources-cancel-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ya-sources-save-btn" onClick={onSave}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

export interface SourcesModalProps {
  state: Record<string, boolean>;
  connected: string[];
  /** The caller applies `toggleSource`/`toggleArtistSource` and toasts any hint. */
  onToggle: (id: string) => void;
  onSave: () => void;
  onClose: () => void;
}

/** Your Albums Sources (1605). Offers Discogs; hints on disconnected rows. */
export function YourAlbumsSourcesModal(props: SourcesModalProps) {
  return (
    <SourcesModalView
      overlayId="ya-albums-sources-modal-overlay"
      title="Your Albums Sources"
      description="Choose which connected services contribute albums to this section."
      dataAttr="data-yaa-source"
      togglePrefix="yaa-toggle-"
      sources={YOUR_ALBUMS_SOURCE_INFO}
      {...props}
    />
  );
}

/** Your Artists Sources (5608). Offers Last.fm instead of Discogs. */
export function YourArtistsSourcesModal(props: SourcesModalProps) {
  return (
    <SourcesModalView
      overlayId="ya-sources-modal-overlay"
      title="Your Artists Sources"
      description="Choose which connected services contribute artists to this section."
      dataAttr="data-source"
      togglePrefix="ya-toggle-"
      sources={ARTISTS_SOURCE_INFO}
      {...props}
    />
  );
}
