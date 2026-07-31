import { createPortal } from 'react-dom';

/**
 * Renders into document.body, where the vanilla appended its overlays.
 *
 * This is not tidiness — it is required. `.artist-hero-section` sets
 * `backdrop-filter`, and a filtered element becomes the CONTAINING BLOCK for
 * any `position: fixed` descendant. An overlay rendered inside the hero has its
 * `inset: 0` clamped to the hero box: it lands in the wrong place, gets cut off
 * at the top, and a click anywhere else on the page never reaches the backdrop,
 * so it cannot be dismissed.
 *
 * Everything the stylesheet positions with `fixed` — .arec-overlay,
 * .modal-overlay, .enhanced-bulk-bar — has to come through here.
 */
export function BodyPortal({ children }: { children: React.ReactNode }) {
  return createPortal(children, document.body);
}
