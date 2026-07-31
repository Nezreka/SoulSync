import type { SyntheticEvent } from 'react';

/**
 * Hide an image that fails to load.
 *
 * The vanilla watchlist markup carried `onerror="this.style.display='none'"` on
 * every provider-hosted image — scan-deck art, release covers, wishlist
 * additions, ledger rows. Without it a dead CDN URL renders the browser's
 * broken-image glyph inside the layout. Same behaviour, as a shared handler.
 */
export function hideOnError(event: SyntheticEvent<HTMLImageElement>): void {
  event.currentTarget.style.display = 'none';
}
