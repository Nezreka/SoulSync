/**
 * Settings, both sides: what's wrong on the tab you're on. Health
 * (/api/help/health, core/help/health.py) already says which Settings tab
 * fixes each finding (its fix of kind 'settings', with media 'video' for the
 * video side); the tab you're on comes from the page's own tab store
 * (features/settings/page-tabs.ts). The fix that would open this same tab is
 * dropped, you're already there.
 *
 * On the music Downloads tab two plain answers follow: the two folders, and
 * what Hybrid does with your sources in the order Health checked them.
 *
 * Settings is admin only (profile-nav.js), and Health is too.
 */

import { usePageTabs } from '@/features/settings/page-tabs';
import { TAB_TITLES } from '@/features/settings/shell';

import type { HealthFinding, HealthFix } from '../api';

import { registerPageHelp, type AnswerContext, type PageAnswer, type PageHeading } from './index';
import { Act, Acts, findingAnswers, readArticle, sentence, useHealthFindings } from './kit';

type Media = 'music' | 'video';

function fixesHere(f: HealthFinding, tab: string, media: Media): boolean {
  return (f.fix ?? []).some(
    (x) => x.kind === 'settings' && x.tab === tab && (x.media === 'video') === (media === 'video'),
  );
}

/** "HiFi connected", "Soulseek (slskd) isn't answering" → the source's name */
export function sourceName(title: string): string {
  return title.replace(/\s+(connected|isn't answering|isn't set up)$/i, '').trim();
}

/** the download sources in the order Health checked them (the hybrid order) */
export function hybridOrder(findings: HealthFinding[]): string[] {
  return findings
    .filter((f) => /^services\.download\.[^.]+$/.test(f.id))
    .map((f) => sourceName(f.title))
    .filter(Boolean);
}

function orderWords(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', then ')}, then ${names[names.length - 1]}`;
}

export function settingsAnswers(
  findings: HealthFinding[] | undefined,
  ctx: AnswerContext,
  tab: string,
  media: Media,
  recheck?: () => void,
): PageAnswer[] {
  if (!ctx.isAdmin) return [];
  const here = tab || 'connections';
  const skip = (fix: HealthFix) =>
    fix.kind === 'settings' && fix.tab === here && (fix.media === 'video') === (media === 'video');
  const out: PageAnswer[] = findingAnswers(findings, (f) => fixesHere(f, here, media), {
    skip,
    onDone: recheck,
  });

  if (media === 'music' && here === 'downloads') {
    out.push({
      id: 'two-folders',
      title: 'Download folder or transfer folder?',
      body: (
        <>
          <p>
            The download folder is where files arrive while they download. The transfer folder is
            where finished, tagged music is filed for your media server. Keep them as two different
            folders.
          </p>
          <Acts>
            <Act kind="ghost" onClick={() => readArticle('gs-folders')}>
              Folder setup
            </Act>
          </Acts>
        </>
      ),
    });
    const order = hybridOrder(findings ?? []);
    if (order.length > 1) {
      out.push({
        id: 'hybrid',
        title: 'What does Hybrid do?',
        body: (
          <p>
            It tries each source in your order and moves on when one has nothing good enough. Yours
            is {orderWords(order)}.
          </p>
        ),
      });
    }
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

function makeUseSettingsAnswers(media: Media) {
  return function useSettingsAnswers(ctx: AnswerContext): PageAnswer[] {
    const { tab } = usePageTabs();
    const health = useHealthFindings(ctx.isAdmin);
    if (!health.loaded) return [];
    return settingsAnswers(health.data, ctx, tab, media, health.reload);
  };
}

export const useMusicSettingsAnswers = makeUseSettingsAnswers('music');
export const useVideoSettingsAnswers = makeUseSettingsAnswers('video');

/**
 * "Settings, Downloads" and what that tab is for, in the page's own words
 * (shell.ts TAB_TITLES, the header the tab shows). A member's Settings is
 * their account, so they keep the plain heading.
 */
export function settingsHeading(tab: string, media: Media, isAdmin = true): PageHeading | null {
  const t = TAB_TITLES[tab];
  if (!t || !isAdmin) return null;
  return {
    title: `${media === 'video' ? 'Video Settings' : 'Settings'}, ${t.title}`,
    summary: sentence(t.sub),
  };
}

function makeUseSettingsHeading(media: Media) {
  return function useSettingsHeading(ctx: AnswerContext): PageHeading | null {
    const { tab } = usePageTabs();
    return settingsHeading(tab, media, ctx.isAdmin);
  };
}

export const useMusicSettingsHeading = makeUseSettingsHeading('music');
export const useVideoSettingsHeading = makeUseSettingsHeading('video');

registerPageHelp({
  id: 'settings',
  useAnswers: useMusicSettingsAnswers,
  useHeading: useMusicSettingsHeading,
});
registerPageHelp({
  id: 'video-settings',
  useAnswers: useVideoSettingsAnswers,
  useHeading: useVideoSettingsHeading,
});
