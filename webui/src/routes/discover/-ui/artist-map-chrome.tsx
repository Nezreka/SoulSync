import { useEffect, useLayoutEffect, useRef } from 'react';

import type { ArtMapNode } from '../-discover.artist-map';
import type { ArtMapContextMenu, ArtMapTooltip } from '../-discover.artist-map.panel';

import { artMap } from '../-discover.artist-map';
import { artMapTooltipPosition, ARTMAP_SHORTCUTS } from '../-discover.artist-map.panel';

/**
 * The Artist Map's floating chrome: tooltip, context menu, shortcuts sheet and
 * the toolbar's search dropdown.
 *
 * Transcribed from discover.js 9241-9274 (search), 9304-9352 (tooltip),
 * 9370-9400 (shortcuts) and 10043-10081 (context menu).
 *
 * All four are absolutely positioned against the viewport and none of them is
 * part of the page flow, which is exactly why they are separated out: they are
 * driven by pointer coordinates rather than by layout, and every one of them has
 * an edge case where it would otherwise render off screen.
 */

// ── The hover tooltip ────────────────────────────────────────────────────────

export interface ArtMapTooltipViewProps {
  tip: ArtMapTooltip | null;
  /** The node the tooltip is for — its id keys the decoded-bitmap cache. */
  node: ArtMapNode | null;
  clientX: number;
  clientY: number;
}

/** The tooltip artwork is 88×88 (9318/9343). */
export const ARTMAP_TIP_IMG = 88;

export function ArtMapTooltipView({ tip, node, clientX, clientY }: ArtMapTooltipViewProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Prefer the ALREADY-DECODED bitmap over an <img src>.
  //
  // It is instant, and — the actual reason — it cannot churn-blank while the
  // pointer sweeps across dense zoomed-in bubbles, which a fresh image load
  // does on every single hover change.
  useEffect(() => {
    const canvas = canvasRef.current;
    const bmp = node ? artMap.images[node.id as string] : null;
    if (!canvas || !bmp) return;
    try {
      canvas
        .getContext('2d')
        ?.drawImage(bmp as CanvasImageSource, 0, 0, ARTMAP_TIP_IMG, ARTMAP_TIP_IMG);
    } catch {
      /* a broken bitmap leaves the canvas blank rather than throwing mid-hover */
    }
  }, [node]);

  // Position AFTER the content exists, not during render.
  //
  // The placement depends on the tooltip's own measured size — it is held back
  // from the right and bottom edges by its own width and height. During render
  // the ref is still null on the first paint, so a node hovered near an edge
  // would be positioned as if the tooltip were zero-sized and hang off screen
  // for a frame. The vanilla builds the markup and only then measures; a layout
  // effect is the same order, before the browser paints.
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const { left, top } = artMapTooltipPosition(
      clientX,
      clientY,
      box.offsetWidth,
      box.offsetHeight,
      window.innerWidth,
      window.innerHeight,
    );
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
  });

  if (!tip) return null;

  return (
    <div
      ref={boxRef}
      className="artist-map-tooltip"
      id="artist-map-tooltip"
      style={{ display: 'block' }}
    >
      <div className="artmap-tip-row">
        {tip.hasBitmap ? (
          <canvas
            ref={canvasRef}
            className="artmap-tip-img"
            width={ARTMAP_TIP_IMG}
            height={ARTMAP_TIP_IMG}
          />
        ) : tip.imageUrl ? (
          <img className="artmap-tip-img" src={tip.imageUrl} alt="" />
        ) : (
          <div className="artmap-tip-img artmap-tip-img-fallback">♫</div>
        )}
        <div className="artmap-tip-info">
          <div className="artmap-tip-name">{tip.name}</div>
          {tip.badge && <span className="artmap-tip-badge">{tip.badge}</span>}
          {tip.connectionText && <div className="artmap-tip-conn">{tip.connectionText}</div>}
          {tip.genres.length > 0 && (
            <div className="artmap-tip-genres">
              {tip.genres.map((g) => (
                <span key={g}>{g}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── The right-click menu ─────────────────────────────────────────────────────

export interface ArtMapContextMenuViewProps {
  menu: ArtMapContextMenu | null;
  node: ArtMapNode | null;
  clientX: number;
  clientY: number;
  onArtistInfo: (node: ArtMapNode) => void;
  onToggleWatchlist: (node: ArtMapNode, menu: ArtMapContextMenu) => void;
  onClose: () => void;
  buildDetailPath: (id: string, source: string) => string;
}

/** How far from the edges the menu is held back (10074-10075). */
export const ARTMAP_CTX_MARGIN_X = 200;
export const ARTMAP_CTX_MARGIN_Y = 200;

export function ArtMapContextMenuView({
  menu,
  node,
  clientX,
  clientY,
  onArtistInfo,
  onToggleWatchlist,
  onClose,
  buildDetailPath,
}: ArtMapContextMenuViewProps) {
  // Closes on the NEXT click anywhere, once. The vanilla delays binding by a
  // tick so the very click that opened the menu does not immediately close it.
  useEffect(() => {
    if (!menu) return;
    const timer = setTimeout(() => window.addEventListener('click', onClose, { once: true }), 10);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('click', onClose);
    };
  }, [menu, onClose]);

  if (!menu || !node) return null;

  const left = Math.min(clientX, window.innerWidth - ARTMAP_CTX_MARGIN_X);
  const top = Math.min(clientY, window.innerHeight - ARTMAP_CTX_MARGIN_Y);

  return (
    <div
      id="artist-map-context"
      className="artmap-context-menu"
      style={{ display: 'block', left, top }}
    >
      <button
        type="button"
        className="artmap-ctx-item"
        // Without one of the three provider ids there is nothing to look up, so
        // the row closes the menu and does nothing else.
        //
        // The inner `hasId` check is belt-and-braces behind `disabled`, which
        // already stops the click — no test can distinguish the two, and it is
        // kept so that removing `disabled` for styling reasons cannot silently
        // start firing lookups with no id.
        disabled={!menu.hasId}
        onClick={() => {
          onClose();
          if (menu.hasId) onArtistInfo(node);
        }}
      >
        <span>ⓘ</span> Artist Info
      </button>

      <a
        className="artmap-ctx-item"
        href={menu.bestId ? buildDetailPath(menu.bestId, menu.bestSource) : '#'}
        aria-disabled={menu.bestId ? undefined : 'true'}
        style={
          menu.bestId
            ? undefined
            : { pointerEvents: 'none', opacity: 0.5, textDecoration: 'none', color: 'inherit' }
        }
        onClick={onClose}
      >
        <span>💿</span> View Discography
      </a>

      <button
        type="button"
        className="artmap-ctx-item"
        onClick={() => {
          onClose();
          onToggleWatchlist(node, menu);
        }}
      >
        <span>👁</span> {menu.watchLabel}
      </button>
    </div>
  );
}

// ── The shortcuts sheet ──────────────────────────────────────────────────────

export function ArtMapShortcutsModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      id="artmap-shortcuts-overlay"
      className="modal-overlay"
      style={{ zIndex: 10002 }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="artmap-shortcuts-modal">
        <div className="artmap-shortcuts-header">
          <h3>Keyboard Shortcuts</h3>
          <button type="button" className="watch-all-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="artmap-shortcuts-grid">
          {ARTMAP_SHORTCUTS.map((s) => (
            <div className="artmap-shortcut" key={s.action}>
              {s.keys.map((k, i) => (
                <span key={k}>
                  {i > 0 && ' / '}
                  <kbd>{k}</kbd>
                </span>
              ))}
              <span>{s.action}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── The toolbar's search dropdown ────────────────────────────────────────────

export type ArtMapSearchState =
  | { kind: 'hidden' }
  | { kind: 'searching' }
  | { kind: 'empty' }
  | { kind: 'failed' }
  | { kind: 'results'; artists: { name: string }[] };

/** How many hits the dropdown lists (9261). */
export const ARTMAP_SEARCH_SHOWN = 8;

export function ArtMapSearchResults({
  state,
  onExplore,
}: {
  state: ArtMapSearchState;
  onExplore: (name: string) => void;
}) {
  if (state.kind === 'hidden') return null;

  const message =
    state.kind === 'searching'
      ? 'Searching…'
      : state.kind === 'empty'
        ? 'No artists found'
        : state.kind === 'failed'
          ? 'Search failed — try again'
          : null;

  return (
    <div
      className="artist-map-search-results"
      id="artist-map-search-results"
      style={{ display: 'block' }}
    >
      {message ? (
        <div className="artist-map-search-item artist-map-search-empty">{message}</div>
      ) : (
        state.kind === 'results' &&
        state.artists.slice(0, ARTMAP_SEARCH_SHOWN).map((a) => (
          <button
            type="button"
            key={a.name}
            className="artist-map-search-item"
            onClick={() => onExplore(a.name)}
          >
            <span className="artist-map-search-type similar">○</span>
            {a.name}
            <span className="artist-map-search-go">Explore →</span>
          </button>
        ))
      )}
    </div>
  );
}
