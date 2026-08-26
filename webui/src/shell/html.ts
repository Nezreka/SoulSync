/**
 * Shell html helpers.
 *
 * escapeHtml matches the vanilla implementation byte for byte (both
 * shared-helpers.js and downloads.js carry the same div.textContent version).
 * Shell modules use THIS one instead of reaching through window: the vanilla
 * files resolved it through the global scope chain and would throw if it were
 * missing - a window-optional fallback that passed text through UNESCAPED
 * would fail open on exactly the input escaping exists for.
 */

export function escapeHtml(text: unknown): string {
  const div = document.createElement('div');
  div.textContent = String(text ?? '');
  return div.innerHTML;
}

export const toast = (message: string, kind: string): void => window.showToast?.(message, kind);
