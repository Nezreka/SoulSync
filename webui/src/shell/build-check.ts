/**
 * Stale tabs after an update. A tab left open across a server update keeps
 * running the old JS, and the first lazily loaded page then breaks. The page
 * carries the build it was served with (<meta name="soulsync-build">); every
 * socket connect the server says which build it runs now (core.js passes it
 * on as the `ss:server-build` event). When they differ, a toast offers a
 * reload. Never a forced one: someone may be mid-edit or mid-song.
 *
 * A lazy chunk that fails to load (vite:preloadError, the file is gone with
 * the old build) gets the same offer.
 */

export const SERVER_BUILD_EVENT = 'ss:server-build';
const MESSAGE = 'SoulSync was updated. Reload this tab to get the new version.';

let told = false;

export function pageBuild(doc: Document = document): string {
  return doc.querySelector('meta[name="soulsync-build"]')?.getAttribute('content')?.trim() || '';
}

/** true when the server runs a different build than this page was served with */
export function isStale(serverBuild: unknown, page: string = pageBuild()): boolean {
  const server = typeof serverBuild === 'string' ? serverBuild.trim() : '';
  return !!server && !!page && server !== page;
}

function offerReload(reload: () => void): void {
  if (told) return;
  told = true;
  window.showToast?.(MESSAGE, 'info', null, { action: { label: 'Reload', run: reload } });
}

export function checkServerBuild(
  serverBuild: unknown,
  reload: () => void = () => window.location.reload(),
): boolean {
  if (!isStale(serverBuild)) return false;
  offerReload(reload);
  return true;
}

/** for tests: forget that this page already offered */
export function resetBuildCheck(): void {
  told = false;
}

export function installBuildCheck(win: Window = window): void {
  win.addEventListener(SERVER_BUILD_EVENT, (e) => {
    checkServerBuild((e as CustomEvent<{ build?: unknown }>).detail?.build);
  });
  win.addEventListener('vite:preloadError', () => {
    offerReload(() => win.location.reload());
  });
}
