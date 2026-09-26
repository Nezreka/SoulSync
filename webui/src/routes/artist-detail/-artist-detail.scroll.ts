/**
 * Artist detail always opens at the top.
 *
 * `body` is `overflow: hidden; height: 100vh`, so the WINDOW never scrolls —
 * `.main-content` does, with the React host inside it. window.scrollTo(0, 0)
 * would be a silent no-op here.
 */
export const SCROLL_CONTAINER_SELECTOR = '.main-content';

export function scrollArtistDetailToTop(): void {
  const container = document.querySelector(SCROLL_CONTAINER_SELECTOR);
  if (container) {
    container.scrollTop = 0;
    return;
  }
  // No shell around us (a bare route render, or a test): fall back to the
  // window so the behaviour is still "top" rather than "nothing happened".
  window.scrollTo(0, 0);
}
