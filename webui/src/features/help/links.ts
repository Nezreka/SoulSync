/** where the sheet sends people: in-app pages, docs articles, the community */

import { isVideoSide } from './page';

export const DISCORD_URL = 'https://discord.gg/wGvKqVQwmy';
export const GITHUB_ISSUES_URL = 'https://github.com/Nezreka/SoulSync/issues';

export function goToPage(pageId: string, options?: Record<string, unknown>): void {
  // video pages are the video side's own (video-side.js); its nav button knows the way
  if (pageId.startsWith('video-')) {
    const safe = pageId.replace(/[^\w-]/g, '');
    document.querySelector<HTMLElement>(`.nav-button[data-video-page="${safe}"]`)?.click();
    return;
  }
  void window.navigateToPage?.(pageId, options);
}

/** a docs article, on the Help & Docs page (the sheet's own reader is P3's) */
export function openArticle(id: string): void {
  if (typeof window.navigateToDocsSection === 'function') window.navigateToDocsSection(id);
  else goToPage(isVideoSide() ? 'video-help' : 'help');
}

export function openDocsHome(): void {
  goToPage(isVideoSide() ? 'video-help' : 'help');
}

export function openExternal(url: string): void {
  window.open(url, '_blank', 'noopener,noreferrer');
}

/** the full release notes live behind the version button (downloads.js) */
export function openReleaseNotes(): void {
  document.querySelector<HTMLElement>('.version-button')?.click();
}

/** a Settings tab, e.g. 'logs'. the tab switch waits for the page to be there */
export function openSettingsTab(tab: string): void {
  goToPage('settings');
  let tries = 0;
  const attempt = () => {
    tries += 1;
    if (document.querySelector(`#settings-page [data-tab="${tab.replace(/[^\w-]/g, '')}"]`)) {
      window.switchSettingsTab?.(tab);
    } else if (tries < 20) {
      setTimeout(attempt, 100);
    }
  };
  setTimeout(attempt, 0);
}
