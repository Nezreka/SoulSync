/**
 * The read-only server-order view and its two align actions —
 * _showServerOrder (pages-extra.js 385-446).
 *
 * Slice D. The compare columns render in SOURCE order, so a playlist holding
 * exactly the right tracks in the wrong sequence looks perfectly in sync. This
 * is where the user gets to SEE the server's actual order; source order stays
 * the source of truth, so nothing here is editable.
 *
 * The align buttons only reorder. They never add the missing tracks — that is a
 * normal sync's job, and the footer note says so.
 */

import { useState } from 'react';

import type { ServerOrderTrack } from '../-sync.server';

import { canAlignServer, orderModalTitle } from '../-sync.server';

/**
 * 395-397: the artwork falls back to a ♫ placeholder both when there is no
 * thumb at all AND when the one there is fails to load — the vanilla wires the
 * second case through an inline onerror that swaps the img for the same div.
 */
function OrderArt({ thumb }: { thumb?: string }) {
  const [failed, setFailed] = useState(false);
  if (!thumb || failed) {
    return <div className="server-order-art server-order-art-ph">♫</div>;
  }
  return (
    <img
      className="server-order-art"
      src={thumb}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export interface ServerOrderModalProps {
  /** `server_order` from the compare payload — the server's real sequence. */
  order: readonly ServerOrderTrack[];
  serverType: string | null | undefined;
  onClose: () => void;
  onAlign: (keepExtras: boolean) => void;
}

export function ServerOrderModal({ order, serverType, onClose, onAlign }: ServerOrderModalProps) {
  return (
    // 444: the backdrop closes. 433: the dialog stops the click reaching it.
    <div id="server-order-modal" className="server-order-overlay" onClick={onClose}>
      <div className="server-order-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="server-order-head">
          <div>
            <div className="server-order-h1">{orderModalTitle(serverType)}</div>
            <div className="server-order-sub">
              the actual order on your server · source order stays the source of truth
            </div>
          </div>
          <button type="button" className="server-order-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="server-order-list">
          {order.length > 0 ? (
            order.map((track, index) => (
              <div className="server-order-row" key={index}>
                {/* 400: numbered by POSITION IN THE SERVER's order — that is the
                    whole point of this view. */}
                <span className="server-order-num">{index + 1}</span>
                <OrderArt thumb={track.thumb} />
                <div className="server-order-meta">
                  <span className="server-order-title">{track.title || 'Unknown'}</span>
                  <span className="server-order-artist">{track.artist || ''}</span>
                </div>
              </div>
            ))
          ) : (
            <div className="server-order-empty">No server tracks.</div>
          )}
        </div>

        {/* 412-427: offered only where a reorder primitive exists. */}
        {canAlignServer(serverType) && (
          <div className="server-order-foot">
            <div className="server-order-foot-label">Align this playlist to the source order</div>
            <div className="server-order-actions">
              <button type="button" className="server-align-btn" onClick={() => onAlign(false)}>
                <span className="server-align-btn-t">Mirror source</span>
                <span className="server-align-btn-d">
                  reorder to match the source · remove server-only tracks
                </span>
              </button>
              <button type="button" className="server-align-btn" onClick={() => onAlign(true)}>
                <span className="server-align-btn-t">Keep extras</span>
                <span className="server-align-btn-d">
                  reorder to match the source · keep server-only tracks at the end
                </span>
              </button>
            </div>
            <div className="server-order-foot-note">
              Missing tracks aren&apos;t added here — run a normal sync for those.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
